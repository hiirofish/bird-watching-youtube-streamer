#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Live配信スクリプト（自動再接続対応版）
YouTube側の切断に対応する自動再接続機能付き
診断ログ機能追加版
"""

import os
import sys
import signal
import subprocess
import datetime
import time
import logging
import psutil
import gc
import traceback
from pathlib import Path

# ログ設定
LOG_DIR = Path("stream_logs")
LOG_DIR.mkdir(exist_ok=True)
log_filename = LOG_DIR / f"daily_stream_{datetime.datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.DEBUG,  # DEBUGレベルに変更
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class YouTubeStreamer:
    def __init__(self):
        self.stream_key = self._get_stream_key()
        self.stream_url = f"rtmp://a.rtmp.youtube.com/live2/{self.stream_key}"
        self.ffmpeg_process = None
        self.start_time = None
        self.end_time = None
        self.use_audio = True
        self.max_session_duration = 8 * 3600  # 8時間で自動再接続
        self.reconnect_delay = 30  # 再接続待機時間（秒）
        self.max_reconnect_attempts = 5
        self.session_start_time = None
        self.total_stream_time = 0

    def _get_stream_key(self):
        """ストリームキーを環境変数またはconfig.txtから取得"""
        stream_key = os.environ.get('YOUTUBE_STREAM_KEY')
        if not stream_key and os.path.exists('config.txt'):
            try:
                with open('config.txt', 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.startswith('STREAM_KEY='):
                            stream_key = line.split('=', 1)[1].strip()
                            break
            except Exception as e:
                logger.error(f"config.txt読み込みエラー: {e}")
        
        if not stream_key:
            logger.error("ストリームキーが見つかりません。")
            sys.exit(1)
        return stream_key
    
    def check_camera(self):
        """カメラデバイスの存在を確認"""
        if not os.path.exists('/dev/video0'):
            logger.error("カメラデバイス /dev/video0 が見つかりません")
            return False
        return True
    
    def get_audio_input(self):
        """利用可能な音声入力を取得"""
        if not self.use_audio:
            logger.info("音声は無効化されています。無音で配信します。")
            return ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']

        audio_inputs = [
            ['-f', 'alsa', '-ac', '1', '-i', 'plughw:2,0'],
            ['-f', 'alsa', '-i', 'plughw:2,0'],
            ['-f', 'alsa', '-ac', '1', '-ar', '48000', '-i', 'hw:2,0'],
            ['-f', 'pulse', '-i', 'default'],
        ]
        
        for audio_input in audio_inputs:
            test_cmd = ['ffmpeg'] + audio_input + ['-t', '2', '-f', 'null', '-']
            try:
                logger.info(f"音声入力をテスト中: {' '.join(audio_input)}")
                subprocess.run(test_cmd, capture_output=True, text=True, timeout=5, check=True, encoding='utf-8')
                logger.info(f"音声入力を検出: {audio_input}")
                return audio_input
            except subprocess.CalledProcessError:
                logger.debug(f"テスト失敗: {' '.join(audio_input)}")
            except Exception as e:
                logger.debug(f"テスト中に予期せぬエラー: {e}")
                continue
                
        logger.warning("有効な音声入力が見つかりませんでした。無音で配信します。")
        return ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']

    def get_drawtext_filter(self, font_file):
        """時刻表示用のdrawtextフィルタを生成"""
        
        # 方法1: localtime形式を試す（実際の時刻を表示）
        localtime_formats = [
            # 基本形式
            "text='%{localtime}'",
            # 日付時刻フォーマット指定
            "text='%{localtime\\:%Y-%m-%d %H\\:%M\\:%S}'",
            # エスケープ違い
            "text='%{localtime\\\\:%Y-%m-%d %H\\\\:%M\\\\:%S}'",
        ]
        
        base_params = (
            f"fontfile={font_file}:"
            f"fontsize=24:fontcolor=white:box=1:boxcolor=black@0.7:"
            f"boxborderw=5:x=10:y=10"
        )
        
        # localtimeフォーマットをテスト
        for fmt in localtime_formats:
            test_cmd = [
                'ffmpeg', '-f', 'lavfi', '-i', 'testsrc2=duration=1:size=320x240:rate=1',
                '-vf', f"drawtext={base_params}:{fmt}", '-f', 'null', '-'
            ]
            try:
                result = subprocess.run(test_cmd, capture_output=True, timeout=2)
                if result.returncode == 0:
                    logger.info(f"実時刻表示フィルタを使用: {fmt}")
                    return f"drawtext={base_params}:{fmt}"
            except:
                continue
        
        # localtimeが動作しない場合は、gmtime（UTC）を試す
        logger.warning("localtime形式が動作しません。gmtime（UTC）を試します。")
        
        gmtime_formats = [
            "text='%{gmtime}'",
            "text='%{gmtime\\:%Y-%m-%d %H\\:%M\\:%S UTC}'",
        ]
        
        for fmt in gmtime_formats:
            test_cmd = [
                'ffmpeg', '-f', 'lavfi', '-i', 'testsrc2=duration=1:size=320x240:rate=1',
                '-vf', f"drawtext={base_params}:{fmt}", '-f', 'null', '-'
            ]
            try:
                result = subprocess.run(test_cmd, capture_output=True, timeout=2)
                if result.returncode == 0:
                    logger.info(f"UTC時刻表示フィルタを使用: {fmt}")
                    return f"drawtext={base_params}:{fmt}"
            except:
                continue
        
        # どちらも動作しない場合は、経過時間にフォールバック
        logger.warning("実時刻表示が動作しません。経過時間表示にフォールバックします。")
        return f"drawtext={base_params}:text='Streaming\\: %{{pts\\:hms}}'"

    def start_stream_session(self):
        """配信セッションを開始（内部用）"""
        logger.info("配信セッションを開始します...")
        
        if not self.check_camera():
            return False
        
        audio_cmd = self.get_audio_input()

        ffmpeg_cmd = [
            'ffmpeg',
            '-nostdin',  # 標準入力を無効化
            '-thread_queue_size', '512',
            '-f', 'v4l2', 
            '-framerate', '30', 
            '-video_size', '1280x720', 
            '-input_format', 'mjpeg', 
            '-i', '/dev/video0',
        ]
        ffmpeg_cmd.extend(['-thread_queue_size', '512'])
        ffmpeg_cmd.extend(audio_cmd)

        # ビデオフィルタの設定
        video_filters = []
        video_filters.append('crop=720:720:280:0')
        
        # フォントファイルの検索
        font_paths = [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
            '/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc',
        ]
        font_file = next((path for path in font_paths if os.path.exists(path)), None)

        if font_file:
            drawtext_filter = self.get_drawtext_filter(font_file)
            if drawtext_filter:
                video_filters.append(drawtext_filter)
        else:
            logger.warning("フォントファイルが見つかりません。時刻表示なしで配信します。")
        
        if video_filters:
            ffmpeg_cmd.extend(['-vf', ','.join(video_filters)])
        
        # エンコード設定（再接続対応のため調整）
        ffmpeg_cmd.extend([
            '-c:v', 'libx264', 
            '-preset', 'ultrafast',
            '-tune', 'zerolatency',
            '-b:v', '1200k',
            '-maxrate', '1200k', 
            '-bufsize', '2400k',  # バッファサイズを小さめに
            '-g', '60',
            '-keyint_min', '60', 
            '-sc_threshold', '0',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100',
            '-ac', '2',
            '-reconnect', '1',  # 再接続を有効化
            '-reconnect_at_eof', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '2',
            '-f', 'flv',
            self.stream_url
        ])
        
        try:
            logger.info(f"FFmpegコマンド: {' '.join(ffmpeg_cmd)}")
            
            self.ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                encoding='utf-8',
                bufsize=1
            )
            
            self.session_start_time = time.time()
            logger.info("FFmpegプロセスを開始しました")
            time.sleep(5)
            
            # 起動確認
            if self.ffmpeg_process and self.ffmpeg_process.poll() is not None:
                stderr = ''
                if self.ffmpeg_process.stderr:
                    stderr = self.ffmpeg_process.stderr.read()
                logger.error(f"FFmpegが起動直後にエラーで終了しました:\n{stderr}")
                self.ffmpeg_process = None
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"配信開始エラー: {e}")
            return False

    def start_stream(self):
        """配信を開始（再接続ループ付き）"""
        logger.info("YouTube Live配信を開始します...")
        logger.info("自動再接続対応版: 8時間ごとまたは切断時に自動再接続")
        
        reconnect_count = 0
        
        while True:
            if reconnect_count > 0:
                logger.info(f"再接続試行 {reconnect_count}/{self.max_reconnect_attempts}")
                logger.info(f"{self.reconnect_delay}秒待機中...")
                time.sleep(self.reconnect_delay)
            
            if self.start_stream_session():
                reconnect_count = 0  # 成功したらカウンタをリセット
                
                # 配信を監視
                result = self.monitor_stream()
                
                if result == "session_timeout":
                    logger.info("セッションタイムアウトのため再接続します")
                    self.stop_stream_session()
                    continue
                elif result == "end_time_reached":
                    logger.info("終了時刻に達しました")
                    self.stop_stream_session()  # 確実に停止
                    break
                elif result == "connection_lost":
                    reconnect_count += 1
                    if reconnect_count > self.max_reconnect_attempts:
                        logger.error("最大再接続試行回数に達しました")
                        self.stop_stream_session()
                        break
                    self.stop_stream_session()
                    continue
                else:
                    self.stop_stream_session()  # その他の場合も確実に停止
                    break
            else:
                reconnect_count += 1
                if reconnect_count > self.max_reconnect_attempts:
                    logger.error("配信開始に失敗しました")
                    break
        
        # ループを抜けた後の最終確認
        if self.ffmpeg_process:
            logger.info("配信ループ終了。最終的な停止処理を実行します")
            self.stop_stream_session()

    def stop_stream_session(self):
        """配信セッションを停止（内部用）"""
        logger.info("配信セッションを停止します...")
        
        # 停止前のシステム状態を記録
        self.log_system_resources()
        
        if self.session_start_time:
            session_duration = time.time() - self.session_start_time
            self.total_stream_time += session_duration
            logger.info(f"セッション時間: {int(session_duration//3600)}時間{int((session_duration%3600)//60)}分")
            logger.info(f"総配信時間: {int(self.total_stream_time//3600)}時間{int((self.total_stream_time%3600)//60)}分")
        
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            logger.info("FFmpegプロセスを停止します...")
            logger.debug(f"FFmpegプロセスPID: {self.ffmpeg_process.pid}")
            
            # プロセスの詳細情報を記録
            try:
                proc = psutil.Process(self.ffmpeg_process.pid)
                logger.debug(f"FFmpegプロセス状態: {proc.status()}")
                logger.debug(f"FFmpegプロセスのCPU使用率: {proc.cpu_percent()}%")
                logger.debug(f"FFmpegプロセスのメモリ使用量: {proc.memory_info().rss / 1024 / 1024:.1f} MB")
                
                # 子プロセスの確認
                children = proc.children(recursive=True)
                if children:
                    logger.debug(f"FFmpegの子プロセス数: {len(children)}")
                    for child in children:
                        logger.debug(f"子プロセス PID {child.pid}: {child.name()}")
            except Exception as e:
                logger.debug(f"プロセス情報取得エラー: {e}")
            
            # まずqコマンドを送信（正常終了を試みる）
            try:
                if self.ffmpeg_process.stdin and not self.ffmpeg_process.stdin.closed:
                    self.ffmpeg_process.stdin.write('q')
                    self.ffmpeg_process.stdin.flush()
                    logger.info("FFmpegに'q'コマンドを送信しました")
                    time.sleep(2)  # 少し待つ
            except:
                pass
            
            # まだ生きている場合はSIGTERMを送信
            if self.ffmpeg_process.poll() is None:
                self.ffmpeg_process.terminate()
                logger.info("FFmpegプロセスにSIGTERMを送信しました")
                try:
                    self.ffmpeg_process.wait(timeout=10)
                    logger.info("FFmpegプロセスが正常に終了しました。")
                except subprocess.TimeoutExpired:
                    logger.warning("FFmpegプロセスが応答しません。強制終了します。")
                    self.ffmpeg_process.kill()
                    try:
                        self.ffmpeg_process.wait(timeout=5)
                        logger.info("FFmpegプロセスを強制終了しました。")
                    except subprocess.TimeoutExpired:
                        logger.error("FFmpegプロセスの強制終了も失敗しました")
                        # OSレベルでの強制終了を試みる
                        try:
                            import os
                            import signal
                            os.kill(self.ffmpeg_process.pid, signal.SIGKILL)
                            logger.info("OSレベルでプロセスを強制終了しました")
                        except:
                            logger.error("プロセスの終了に完全に失敗しました")
            
            # プロセスが終了したことを確認
            try:
                proc = psutil.Process(self.ffmpeg_process.pid)
                if proc.is_running():
                    logger.error(f"警告: FFmpegプロセス（PID: {self.ffmpeg_process.pid}）がまだ実行中です")
            except psutil.NoSuchProcess:
                logger.debug("FFmpegプロセスが正しく終了したことを確認しました")
            
            self.ffmpeg_process = None
            
            # ガベージコレクションを実行
            gc.collect()
            logger.debug("ガベージコレクションを実行しました")
            
            # 停止後のシステム状態を記録
            self.log_system_resources()
            
        else:
            logger.info("停止対象のFFmpegプロセスが存在しません")

    def stop_stream(self):
        """配信を完全に停止"""
        self.stop_stream_session()
        logger.info("配信停止処理が完了しました。")

    def monitor_stream(self):
        """配信を監視"""
        last_resource_check = time.time()
        last_progress_log = time.time()
        error_count = 0
        non_monotonous_count = 0
        
        while self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            try:
                current_time = time.time()
                
                # セッションタイムアウトチェック
                if self.session_start_time and (current_time - self.session_start_time) > self.max_session_duration:
                    return "session_timeout"
                
                if self.ffmpeg_process.stderr:
                    import select
                    ready, _, _ = select.select([self.ffmpeg_process.stderr], [], [], 0.1)
                    if ready:
                        line = self.ffmpeg_process.stderr.readline()
                        if line:
                            line = line.strip()
                            
                            # エラーの種類によって処理
                            if 'broken pipe' in line.lower():
                                logger.error("Broken pipe検出: YouTube側から切断されました")
                                return "connection_lost"
                            elif 'connection reset' in line.lower():
                                logger.error("Connection reset検出: 接続がリセットされました")
                                return "connection_lost"
                            elif 'non-monotonous' in line.lower():
                                non_monotonous_count += 1
                                if non_monotonous_count % 100 == 0:
                                    logger.warning(f"タイムスタンプ警告が{non_monotonous_count}回発生")
                            elif 'error' in line.lower():
                                error_count += 1
                                logger.error(f"FFmpeg Error ({error_count}): {line}")
                                if error_count > 50:
                                    logger.error("エラーが多発しています")
                                    return "too_many_errors"
                            elif 'frame=' in line and current_time - last_progress_log > 60:
                                session_elapsed = current_time - self.session_start_time
                                total_elapsed = self.total_stream_time + session_elapsed
                                logger.info(f"配信中 (セッション: {int(session_elapsed//3600)}時間{int((session_elapsed%3600)//60)}分, 総計: {int(total_elapsed//3600)}時間{int((total_elapsed%3600)//60)}分): {line}")
                                last_progress_log = current_time
                                error_count = 0
                                
            except Exception as e:
                logger.warning(f"出力読み取りエラー: {e}")
            
            # リソースチェック（10分ごと）
            if current_time - last_resource_check > 600:
                self.log_system_resources()
                last_resource_check = current_time
                
            # 終了時刻チェック
            if self.end_time and datetime.datetime.now() >= self.end_time:
                return "end_time_reached"
                
            time.sleep(0.1)
        
        # プロセスが終了した場合
        if self.ffmpeg_process and self.ffmpeg_process.poll() is not None:
            return_code = self.ffmpeg_process.poll()
            logger.error(f"FFmpegプロセスが予期せず終了しました（リターンコード: {return_code}）")
            
            # エラー出力を確認
            if self.ffmpeg_process.stderr:
                remaining_errors = self.ffmpeg_process.stderr.read()
                if remaining_errors:
                    if 'broken pipe' in remaining_errors.lower():
                        return "connection_lost"
                    
                    error_lines = remaining_errors.strip().split('\n')
                    if len(error_lines) > 20:
                        logger.error("FFmpegエラー詳細（最後の20行）:")
                        for line in error_lines[-20:]:
                            logger.error(line)
                    else:
                        logger.error(f"FFmpegエラー詳細:\n{remaining_errors}")
                        
            return "process_died"

    def log_system_resources(self):
        """システムリソース使用状況をログに記録"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            temp_str = ""
            
            try:
                temp_file = '/sys/class/thermal/thermal_zone0/temp'
                if os.path.exists(temp_file):
                    with open(temp_file, 'r') as f:
                        temp = float(f.read()) / 1000
                        temp_str = f", 温度: {temp:.1f}°C"
            except:
                pass
                
            logger.info(
                f"システム状態 - CPU: {cpu_percent}%, "
                f"メモリ: {memory.percent}%, "
                f"ディスク: {disk.percent}%{temp_str}"
            )
            
            # 詳細情報
            logger.debug(
                f"メモリ詳細 - 総量: {memory.total//1024//1024}MB, "
                f"使用中: {memory.used//1024//1024}MB, "
                f"利用可能: {memory.available//1024//1024}MB, "
                f"キャッシュ: {memory.cached//1024//1024}MB"
            )
            
            # プロセス情報
            try:
                process = psutil.Process()
                logger.debug(
                    f"自プロセス - CPU: {process.cpu_percent()}%, "
                    f"メモリ: {process.memory_info().rss//1024//1024}MB, "
                    f"スレッド数: {process.num_threads()}"
                )
            except:
                pass
                
            # ファイルディスクリプタ数
            try:
                pid = os.getpid()
                fd_count = len(os.listdir(f'/proc/{pid}/fd'))
                logger.debug(f"ファイルディスクリプタ数: {fd_count}")
            except:
                pass
                
        except Exception as e:
            logger.error(f"リソース情報取得エラー: {e}")
            logger.debug(traceback.format_exc())
            
    def schedule_stream(self, start_time_str=None, end_time_str=None):
        """スケジュール配信"""
        now = datetime.datetime.now()
        
        # デフォルト時刻の設定
        DEFAULT_START_HOUR = 4  # 4:00
        DEFAULT_END_HOUR = 20   # 20:00
        
        if start_time_str:
            try:
                hour, minute = map(int, start_time_str.split(':'))
                self.start_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except ValueError:
                logger.error(f"開始時刻の形式が無効です: {start_time_str}。HH:MM形式で指定してください。")
                return
        else:
            # デフォルトの開始時刻を4:00に設定
            self.start_time = now.replace(hour=DEFAULT_START_HOUR, minute=0, second=0, microsecond=0)
            
        if end_time_str:
            try:
                hour, minute = map(int, end_time_str.split(':'))
                self.end_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except ValueError:
                logger.error(f"終了時刻の形式が無効です: {end_time_str}。HH:MM形式で指定してください。")
                self.end_time = None
                return
        else:
            # デフォルトの終了時刻を20:00に設定
            self.end_time = now.replace(hour=DEFAULT_END_HOUR, minute=0, second=0, microsecond=0)
        
        # 配信時間の妥当性チェックと調整
        current_time = now.time()
        start_time_only = self.start_time.time()
        end_time_only = self.end_time.time()
        
        # 現在時刻が配信時間外の場合の処理
        if start_time_only < end_time_only:  # 通常のケース（例: 4:00-20:00）
            if current_time < start_time_only:
                # 現在時刻が開始時刻前 → 今日の開始時刻
                pass
            elif current_time >= end_time_only:
                # 現在時刻が終了時刻後 → 翌日の開始時刻
                self.start_time += datetime.timedelta(days=1)
                self.end_time += datetime.timedelta(days=1)
            else:
                # 現在時刻が配信時間内 → すぐに開始
                self.start_time = now
        else:  # 日を跨ぐケース（例: 22:00-02:00）
            if start_time_only <= current_time or current_time < end_time_only:
                # 配信時間内 → すぐに開始
                self.start_time = now
                if current_time >= start_time_only:
                    # 終了時刻は翌日
                    self.end_time += datetime.timedelta(days=1)
            else:
                # 配信時間外 → 今日の開始時刻
                if self.end_time < self.start_time:
                    self.end_time += datetime.timedelta(days=1)
        
        logger.info(f"配信スケジュール - 開始: {self.start_time.strftime('%Y-%m-%d %H:%M')}, 終了: {self.end_time.strftime('%Y-%m-%d %H:%M')}")
        
        # 開始時刻まで待機
        if self.start_time > now:
            wait_seconds = (self.start_time - now).total_seconds()
            wait_hours = wait_seconds / 3600
            logger.info(f"開始時刻まで {wait_seconds:.0f} 秒（約 {wait_hours:.1f} 時間）待機します...")
            try:
                time.sleep(wait_seconds)
            except KeyboardInterrupt:
                logger.info("待機中に割り込みがありました。プログラムを終了します。")
                return
        
        self.start_stream()

def signal_handler(signum, frame):
    """シグナルハンドラー"""
    signame = signal.strsignal(signum) if hasattr(signal, 'strsignal') else f"Signal {signum}"
    logger.info(f"シグナル {signame} を受信しました。")
    logger.debug(f"シグナルハンドラー実行中 - PID: {os.getpid()}")
    
    # 現在のスレッド情報を記録
    import threading
    logger.debug(f"アクティブスレッド数: {threading.active_count()}")
    for thread in threading.enumerate():
        logger.debug(f"スレッド: {thread.name} (alive: {thread.is_alive()})")
    
    if hasattr(signal_handler, 'streamer') and signal_handler.streamer:
        try:
            signal_handler.streamer.stop_stream()
        except Exception as e:
            logger.error(f"停止処理中にエラー: {e}")
            logger.debug(traceback.format_exc())
    
    logger.info("シグナルハンドラー処理完了")
    sys.exit(0)

def cleanup_handler():
    """プログラム終了時のクリーンアップハンドラー"""
    logger.info("=== クリーンアップ処理開始 ===")
    
    try:
        # システムリソース状態の最終記録
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        logger.info(f"終了時のシステム状態 - CPU: {cpu_percent}%, メモリ: {memory.percent}%")
        
        # 全プロセスのチェック
        current_process = psutil.Process()
        children = current_process.children(recursive=True)
        if children:
            logger.warning(f"子プロセスが {len(children)} 個残っています")
            for child in children:
                logger.warning(f"残存子プロセス PID {child.pid}: {child.name()}")
                try:
                    child.terminate()
                    logger.info(f"子プロセス PID {child.pid} を終了しました")
                except:
                    pass
        
        # ファイルディスクリプタのチェック
        try:
            pid = os.getpid()
            fd_list = os.listdir(f'/proc/{pid}/fd')
            logger.debug(f"終了時のファイルディスクリプタ数: {len(fd_list)}")
            if len(fd_list) > 10:  # 通常より多い場合は詳細を記録
                logger.warning("ファイルディスクリプタが多数開いています:")
                for fd in fd_list[:20]:  # 最初の20個まで
                    try:
                        link = os.readlink(f'/proc/{pid}/fd/{fd}')
                        logger.debug(f"  FD {fd}: {link}")
                    except:
                        pass
        except:
            pass
        
        # グローバルオブジェクトのクリーンアップ
        if hasattr(cleanup_handler, 'streamer') and cleanup_handler.streamer:
            if hasattr(cleanup_handler.streamer, 'ffmpeg_process') and cleanup_handler.streamer.ffmpeg_process:
                logger.warning("クリーンアップ時にFFmpegプロセスがまだ存在します")
                try:
                    cleanup_handler.streamer.stop_stream_session()
                except Exception as e:
                    logger.error(f"最終クリーンアップ中のエラー: {e}")
        
        # ガベージコレクション
        import gc
        gc.collect()
        logger.debug("最終ガベージコレクションを実行しました")
        
    except Exception as e:
        logger.error(f"クリーンアップ中にエラー: {e}")
        logger.debug(traceback.format_exc())
    
    logger.info("=== クリーンアップ処理完了 ===")

def main():
    """メイン関数"""
    logger.info("=== YouTube配信プログラム（自動再接続対応版）===")
    logger.info("8時間ごとまたは切断時に自動再接続します")
    logger.info("デフォルト配信時間: 4:00-20:00")
    logger.info(f"プログラムPID: {os.getpid()}")
    logger.info(f"Python version: {sys.version}")
    
    # 初期システム状態の記録
    try:
        cpu_count = psutil.cpu_count()
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        logger.info(f"システム情報 - CPU数: {cpu_count}, メモリ総量: {memory.total//1024//1024}MB, ディスク総量: {disk.total//1024//1024//1024}GB")
    except Exception as e:
        logger.error(f"システム情報取得エラー: {e}")
    
    streamer = None
    
    try:
        streamer = YouTubeStreamer()
        
        # オプション処理
        if '--no-audio' in sys.argv:
            streamer.use_audio = False
            logger.info("音声を無効化しました")
        
        # セッション時間のカスタマイズ
        if '--session-hours' in sys.argv:
            try:
                idx = sys.argv.index('--session-hours')
                hours = float(sys.argv[idx + 1])
                streamer.max_session_duration = hours * 3600
                logger.info(f"セッション時間を{hours}時間に設定しました")
            except (IndexError, ValueError):
                logger.warning("--session-hoursの値が無効です。デフォルト値を使用します")
        
        # シグナルハンドラーの設定
        signal_handler.streamer = streamer
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # クリーンアップハンドラーの設定
        cleanup_handler.streamer = streamer
        import atexit
        atexit.register(cleanup_handler)
        
        # スケジュール設定
        args = [arg for arg in sys.argv[1:] if not arg.startswith('--')]
        start_time = args[0] if len(args) >= 1 else None
        end_time = args[1] if len(args) >= 2 else None
        
        # 配信開始
        streamer.schedule_stream(start_time, end_time)
        
    except Exception as e:
        logger.error(f"メイン処理中に予期せぬエラー: {e}")
        logger.error(traceback.format_exc())
    finally:
        logger.info("=== プログラム終了処理 ===")
        
        # 最終的なリソース状態を記録
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            logger.info(f"最終システム状態 - CPU: {cpu_percent}%, メモリ: {memory.percent}%")
            
            # プロセスツリーの確認
            current_process = psutil.Process()
            children = current_process.children(recursive=True)
            if children:
                logger.warning(f"終了時に子プロセスが {len(children)} 個残存")
        except Exception as e:
            logger.error(f"最終状態記録エラー: {e}")
        
        logger.info("プログラムを終了しました")
        
        # 明示的な終了
        sys.exit(0)

if __name__ == "__main__":
    main()