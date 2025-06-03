#!/usr/bin/env python3

import subprocess
import time
import os
from datetime import datetime, time as dt_time, timedelta
import logging
import threading
import re
import signal
import sys
import psutil

class DailyYouTubeStreamer:
    """
    指定時間にYouTube Liveへ自動配信し、安定動作を目指すスクリプト。
    22分問題を解決し、長時間配信に対応。
    """

    def __init__(self, stream_key, start_time_str="05:00", end_time_str="20:00"):
        self.stream_key = stream_key
        self.rtmp_url = f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
        
        self.start_time = datetime.strptime(start_time_str, "%H:%M").time()
        self.end_time = datetime.strptime(end_time_str, "%H:%M").time()

        self.logger = None 
        self.setup_logging()

        self.ffmpeg_process = None
        self.stop_event = threading.Event()
        self.monitor_thread_instance = None

        self.is_streaming_successfully = False
        self.restart_attempts = 0
        self.max_restarts_per_session = 5
        self.restart_delay_seconds = 30

        # シグナルハンドラ設定
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        self._globally_stopped = False

    def setup_logging(self):
        log_dir = "stream_logs"
        os.makedirs(log_dir, exist_ok=True)
        log_filename = os.path.join(log_dir, f'daily_stream_{datetime.now().strftime("%Y%m%d")}.log')
        
        if self.logger:
            for handler in self.logger.handlers[:]:
                self.logger.removeHandler(handler)
                handler.close()

        self.logger = logging.getLogger('DailyYouTubeStreamer')
        self.logger.setLevel(logging.INFO)
        
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s')

        fh = logging.FileHandler(log_filename, encoding='utf-8')
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        self.logger.addHandler(ch)
        
        self.logger.info(f"ロギング開始。ログファイル: {log_filename}")
        self.logger.info(f"配信スケジュール: {self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}")

    def _monitor_ffmpeg_stderr(self):
        """ffmpegのstderrを監視し、統計情報をログ記録"""
        if not self.ffmpeg_process or not self.ffmpeg_process.stderr:
            self.logger.error("ffmpegプロセスまたはstderrが存在せず、監視を開始できません。")
            return

        self.logger.info("ffmpeg stderr監視スレッド開始。")
        bitrate_pattern = re.compile(r"bitrate=\s*(\d+\.?\d*)\s*kbits/s")
        fps_pattern = re.compile(r"fps=\s*(\d+\.?\d*)")
        speed_pattern = re.compile(r"speed=\s*(\d+\.?\d*)x")
        last_stats_log_time = time.time()

        try:
            for line in iter(self.ffmpeg_process.stderr.readline, ''):
                if self.stop_event.is_set():
                    break
                
                line = line.strip()
                if not line: 
                    continue

                # デバッグレベルでRAW出力を記録（必要に応じてコメントアウト）
                # self.logger.debug(f"FFMPEG_RAW: {line}")

                current_time = time.time()
                if current_time - last_stats_log_time > 60:  # 1分ごとに統計情報ログ
                    bitrate_match = bitrate_pattern.search(line)
                    fps_match = fps_pattern.search(line)
                    speed_match = speed_pattern.search(line)
                    
                    stats_msg = "FFMPEG_STATS: "
                    has_stats = False
                    if bitrate_match:
                        stats_msg += f"Bitrate={float(bitrate_match.group(1)):.2f}kbits/s "
                        has_stats = True
                    if fps_match:
                        stats_msg += f"FPS={float(fps_match.group(1)):.1f} "
                        has_stats = True
                    if speed_match:
                        stats_msg += f"Speed={float(speed_match.group(1)):.2f}x "
                        has_stats = True
                    
                    if has_stats:
                        self.logger.info(stats_msg)
                        last_stats_log_time = current_time
            
            # プロセス終了後、残りの出力を読む
            remaining_stderr = self.ffmpeg_process.stderr.read()
            if remaining_stderr.strip():
                self.logger.debug(f"FFMPEG_RAW_REMAINING: {remaining_stderr.strip()}")

        except Exception as e:
            if not self.stop_event.is_set():
                self.logger.error(f"ffmpeg stderr監視スレッドでエラー: {e}", exc_info=True)
        finally:
            self.logger.info("ffmpeg stderr監視スレッド終了。")

    def start_stream_attempt(self):
        """ffmpeg配信プロセスを開始する試み"""
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            self.logger.warning("既にffmpegプロセスが実行中です。新規開始はしません。")
            return True
            
        self.stop_event.clear()
        self.logger.info(f"配信開始試行 (試行回数: {self.restart_attempts + 1}/{self.max_restarts_per_session + 1})")

        # 成功したFFmpegコマンド設定
        cmd = [
            'ffmpeg',
            '-nostdin',
            '-f', 'v4l2',
            '-input_format', 'mjpeg',
            '-framerate', '30',
            '-video_size', '1280x720',
            '-i', '/dev/video0',
            '-f', 'alsa',
            '-i', 'default',
            '-filter:v', 'crop=720:720:0:60',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-tune', 'zerolatency',
            '-x264-params', "bitrate=1200:vbv-maxrate=1200:vbv-bufsize=2400:nal-hrd=cbr",
            '-g', '60',  # 2秒キーフレーム
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            '-threads', '2',
            '-loglevel', 'info',
            '-stats',
            '-f', 'flv',
            self.rtmp_url
        ]
        
        self.logger.info(f"実行するFFmpegコマンド: {' '.join(cmd[:15])}...")  # URLは隠す

        try:
            self.ffmpeg_process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
            )
            self.logger.info(f"ffmpegプロセス開始 (PID: {self.ffmpeg_process.pid})。")

            # stderr監視スレッドを開始
            self.monitor_thread_instance = threading.Thread(
                target=self._monitor_ffmpeg_stderr, name="FFmpegMonitor"
            )
            self.monitor_thread_instance.daemon = True
            self.monitor_thread_instance.start()
            
            # 短時間待ってプロセスが生きているか確認
            time.sleep(10)
            if self.ffmpeg_process.poll() is None:
                self.logger.info("ffmpegプロセスは開始後10秒経過時点で正常に動作中。")
                self.is_streaming_successfully = True
                return True
            else:
                self.logger.error(f"ffmpegプロセスが開始直後に終了しました。リターンコード: {self.ffmpeg_process.returncode}")
                stderr_output = self.ffmpeg_process.stderr.read()
                self.logger.error(f"FFmpeg stderr (開始直後): {stderr_output[-1000:]}")
                self.is_streaming_successfully = False
                self.cleanup_ffmpeg_process()
                return False

        except Exception as e:
            self.logger.error(f"ffmpegプロセスの開始に失敗: {e}", exc_info=True)
            self.is_streaming_successfully = False
            self.cleanup_ffmpeg_process()
            return False

    def stop_stream_attempt(self, reason="不明な理由"):
        """ffmpeg配信プロセスを停止する試み"""
        self.logger.info(f"配信停止試行開始 ({reason}のため)。")
        self.stop_event.set()

        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            self.logger.info(f"ffmpegプロセス (PID: {self.ffmpeg_process.pid}) にSIGTERMを送信します。")
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=15)
                self.logger.info(f"ffmpegプロセスは正常にterminateされました。リターンコード: {self.ffmpeg_process.returncode}")
            except subprocess.TimeoutExpired:
                self.logger.warning("ffmpegプロセスがSIGTERMに15秒応答しませんでした。SIGKILLを送信します。")
                self.ffmpeg_process.kill()
                self.ffmpeg_process.wait(timeout=5)
                self.logger.info("ffmpegプロセスはSIGKILLで強制終了されました。")
            except Exception as e:
                self.logger.error(f"ffmpeg停止中の例外: {e}")
        else:
            self.logger.info("停止対象のffmpegプロセスが存在しないか、既に終了しています。")

        self.cleanup_ffmpeg_process()
        self.is_streaming_successfully = False
        self.logger.info("配信停止試行完了。")

    def cleanup_ffmpeg_process(self):
        """ffmpegプロセスと関連スレッドのクリーンアップ"""
        if self.monitor_thread_instance and self.monitor_thread_instance.is_alive():
            self.logger.debug("監視スレッドの終了を待機中...")
            self.monitor_thread_instance.join(timeout=5)
            if self.monitor_thread_instance.is_alive():
                self.logger.warning("監視スレッドがタイムアウト後もアクティブです。")
        self.monitor_thread_instance = None
        self.ffmpeg_process = None

    def run_scheduler(self):
        """メインスケジューラ: 指定時間に配信を開始・停止し、状態を監視"""
        self.logger.info("配信スケジューラ起動。")
        last_resource_check_time = time.time()
        last_wait_log_time = 0  # 待機ログの重複を防ぐ

        try:
            while not self._globally_stopped:
                now = datetime.now()
                current_time = now.time()
                
                # リソースチェック（10分ごと）
                if time.time() - last_resource_check_time > 600:
                    self.log_system_resources()
                    last_resource_check_time = time.time()

                # 配信時間内かどうか
                if self.start_time <= current_time < self.end_time:
                    if not self.is_streaming_successfully:
                        if self.restart_attempts <= self.max_restarts_per_session:
                            self.logger.info(f"配信時間内です。現在のストリーミング状態: {self.is_streaming_successfully}。開始/再起動を試みます。")
                            if self.start_stream_attempt():
                                self.restart_attempts = 0
                            else:
                                self.restart_attempts += 1
                                self.logger.warning(f"配信開始/再起動に失敗。{self.restart_delay_seconds}秒後に再試行します。(試行 {self.restart_attempts}/{self.max_restarts_per_session +1})")
                                self.stop_event.wait(self.restart_delay_seconds)
                        else:
                            self.logger.error(f"最大再起動回数 ({self.max_restarts_per_session +1}) に達しました。本日の配信はこれ以上試行しません。")
                            # 配信終了時刻まで待つ
                            sleep_until_end = (datetime.combine(now.date(), self.end_time) - now).total_seconds()
                            if sleep_until_end > 0:
                                self.logger.info(f"次の配信終了時刻まで {sleep_until_end/3600:.1f} 時間スリープします。")
                                self.stop_event.wait(sleep_until_end)
                            self.is_streaming_successfully = True  # ループ回避のためフラグを立てる
                    else:
                        # 正常にストリーミング中の確認
                        if self.ffmpeg_process and self.ffmpeg_process.poll() is not None:
                            self.logger.warning(f"ストリーミング中にffmpegプロセスが予期せず終了しました。リターンコード: {self.ffmpeg_process.returncode}")
                            self.is_streaming_successfully = False
                            self.cleanup_ffmpeg_process()
                        else:
                            # 5分ごとに生存確認ログ
                            if int(now.minute) % 5 == 0 and now.second < 5:
                                self.logger.info(f"配信は正常に継続中です (PID: {self.ffmpeg_process.pid if self.ffmpeg_process else 'N/A'})。")
                
                # 配信時間外
                else:
                    if self.is_streaming_successfully:
                        self.logger.info("配信終了時刻になりました。ストリームを停止します。")
                        self.stop_stream_attempt("スケジュールされた終了")
                    
                    # 日付が変わったらリセット
                    if current_time >= self.end_time and self.restart_attempts > 0:
                        self.logger.info("日付変更（または配信終了後）のため、再起動カウンターをリセットします。")
                        self.restart_attempts = 0
                    
                    # 次の開始時刻まで待機（ログの重複を防ぐ）
                    if current_time >= self.end_time or current_time < self.start_time:
                        next_start_dt = datetime.combine(now.date(), self.start_time)
                        if current_time >= self.start_time:
                            next_start_dt += timedelta(days=1)
                        
                        wait_seconds = (next_start_dt - now).total_seconds()
                        if wait_seconds > 0:
                            # 1時間に1回だけログを出力
                            current_hour = now.hour
                            if current_hour != last_wait_log_time:
                                self.logger.info(f"次の配信開始 ({next_start_dt.strftime('%Y-%m-%d %H:%M')}) まで約 {wait_seconds/3600:.1f} 時間待機します。")
                                last_wait_log_time = current_hour
                            
                            # 60秒ごとに起きてCtrl+Cに反応しやすくする
                            self.stop_event.wait(timeout=60)

                if not self._globally_stopped:
                    self.stop_event.wait(timeout=10)  # 10秒ごとにメインループチェック

        except Exception as e:
            self.logger.critical(f"スケジューラで致命的なエラー発生: {e}", exc_info=True)
        finally:
            self.logger.info("スケジューラ終了処理。")
            if self.is_streaming_successfully:
                self.stop_stream_attempt("スケジューラ終了")

    def log_system_resources(self):
        """システムリソースの状態をログに記録"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory_info = psutil.virtual_memory()
            disk_info = psutil.disk_usage('/')
            self.logger.info(
                f"リソース状況: CPU {cpu_percent:.1f}%, "
                f"メモリ {memory_info.percent:.1f}% (使用量 {memory_info.used / (1024**3):.2f}GB), "
                f"ディスク {disk_info.percent:.1f}%"
            )
        except Exception as e:
            self.logger.warning(f"リソース情報の取得に失敗: {e}")

    def signal_handler(self, signum, frame):
        """シグナルハンドラー"""
        signal_name = signal.Signals(signum).name
        self.logger.warning(f"シグナル {signal_name} を受信。シャットダウン処理を開始します...")
        self._globally_stopped = True
        self.stop_event.set()

        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            self.logger.info("メインのシグナルハンドラからffmpegを直接停止します。")
            current_pid = self.ffmpeg_process.pid
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=10)
                self.logger.info(f"FFmpeg (PID: {current_pid}) はterminateで停止しました。")
            except subprocess.TimeoutExpired:
                self.logger.warning(f"FFmpeg (PID: {current_pid}) はterminateに応答せず、killします。")
                self.ffmpeg_process.kill()
        
        self.logger.info("シャットダウン処理完了。プログラムを終了します。")
        sys.exit(0)

def main():
    stream_key = os.getenv('YOUTUBE_STREAM_KEY')
    if not stream_key:
        print("エラー: 環境変数 YOUTUBE_STREAM_KEY が設定されていません。")
        print("設定例: export YOUTUBE_STREAM_KEY='あなたのストリームキー'")
        sys.exit(1)

    # コマンドライン引数から時刻を取得（修正部分）
    start_time_str = "05:00"  # デフォルト
    end_time_str = "20:00"    # デフォルト
    
    # 引数が指定されている場合は上書き
    if len(sys.argv) >= 2:
        start_time_str = sys.argv[1]
    if len(sys.argv) >= 3:
        end_time_str = sys.argv[2]
    
    # 既存の環境変数チェックは削除またはコメントアウト
    # start_time_str = os.getenv('STREAM_START_TIME', "05:00")
    # end_time_str = os.getenv('STREAM_END_TIME', "20:00")
    
    try:
        datetime.strptime(start_time_str, "%H:%M")
        datetime.strptime(end_time_str, "%H:%M")
    except ValueError:
        print("エラー: 時刻の形式が無効です。HH:MM形式で指定してください。")
        print("使用例: python gemini2.py 9:00 12:00")
        sys.exit(1)

    print(f"YouTube配信スケジューラを開始します")
    print(f"配信時間: {start_time_str} - {end_time_str}")
    print(f"ストリームキー: {stream_key[:8]}...")
    print("-" * 50)

    streamer = DailyYouTubeStreamer(stream_key, start_time_str, end_time_str)
    streamer.run_scheduler()


if __name__ == "__main__":
    main()
