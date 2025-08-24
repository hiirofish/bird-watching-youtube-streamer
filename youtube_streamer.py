#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Live配信スクリプト（UDP出力・テキスト表示対応版）
topic.txt, visit_info.txt, stream.txtから情報を読み込んで表示
"""

import os
import sys
import signal
import subprocess
import datetime
import time
import logging
import json
from pathlib import Path

# ログ設定
LOG_DIR = Path("stream_logs")
LOG_DIR.mkdir(exist_ok=True)
log_filename = LOG_DIR / f"daily_stream_{datetime.datetime.now().strftime('%Y%m%d')}.log"

# デバッグモードの確認
DEBUG_MODE = '--debug' in sys.argv

# ログレベル設定
log_level = logging.DEBUG if DEBUG_MODE else logging.WARNING

logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler(sys.stdout) if DEBUG_MODE else logging.NullHandler()
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
        self.reconnect_delay = 30
        self.max_reconnect_attempts = 5
        self.session_start_time = None
        self.total_stream_time = 0
        
        # デフォルトの配信時間
        self.default_start_time = "5:10"
        self.default_end_time = "19:20"
        
        # UDP出力設定
        self.enable_udp = '--no-udp' not in sys.argv
        self.udp_port = 1234
        self.udp_address = "127.0.0.1"
        
        # テキストファイル設定
        self.topic_file = "topic.txt"
        self.visit_info_file = "visit_info.txt"
        self.stream_text_file = "stream.txt"
        
        # テキスト内容
        self.current_topic = ""
        self.current_visit_info = ""
        self.current_stream_text = ""

    def _get_stream_key(self):
        """ストリームキーを取得"""
        stream_key = os.environ.get('YOUTUBE_STREAM_KEY')
        if not stream_key and os.path.exists('config.txt'):
            try:
                with open('config.txt', 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.startswith('STREAM_KEY='):
                            stream_key = line.split('=', 1)[1].strip()
                            break
            except Exception as e:
                if DEBUG_MODE:
                    logger.error(f"config.txt読み込みエラー: {e}")
        
        if not stream_key:
            print("エラー: ストリームキーが見つかりません")
            sys.exit(1)
        return stream_key
    
    def init_text_files(self):
        """テキストファイルを初期化"""
        # topic.txt
        if not os.path.exists(self.topic_file):
            with open(self.topic_file, 'w', encoding='utf-8') as f:
                f.write("2回目の産卵! 8月22日巣立ち予定!!")
            print(f"デフォルトトピックファイルを作成: {self.topic_file}")
        
        # visit_info.txt (JSON形式で初期化)
        if not os.path.exists(self.visit_info_file):
            initial_data = {
                'count': 0,
                'last_duration': 0,
                'last_visit_time': '',
                'total_duration': 0,
                'visits': []
            }
            with open(self.visit_info_file, 'w', encoding='utf-8') as f:
                json.dump(initial_data, f, ensure_ascii=False, indent=2)
            print(f"訪問情報ファイルを初期化: {self.visit_info_file}")
        
        # stream.txt
        if not os.path.exists(self.stream_text_file):
            with open(self.stream_text_file, 'w', encoding='utf-8') as f:
                f.write("Youtube登録お願いします")
            print(f"デフォルト自由記述ファイルを作成: {self.stream_text_file}")
    
    def read_text_files(self):
        """テキストファイルを読み込む"""
        # topic.txt
        try:
            with open(self.topic_file, 'r', encoding='utf-8') as f:
                self.current_topic = f.readline().strip()
            if DEBUG_MODE:
                logger.info(f"トピック: {self.current_topic}")
        except Exception as e:
            if DEBUG_MODE:
                logger.error(f"トピックファイル読み込みエラー: {e}")
            self.current_topic = "配信中"
        
        # visit_info.txt
        try:
            with open(self.visit_info_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            count = data.get('count', 0)
            last_duration = data.get('last_duration', 0)
            last_visit_time = data.get('last_visit_time', '')
            
            if count > 0 and last_visit_time:
                self.current_visit_info = f"訪問回数: {count}回  滞在時間: {last_duration:.1f}秒  直近: {last_visit_time}"
            elif count > 0:
                self.current_visit_info = f"訪問回数: {count}回  総滞在: {data.get('total_duration', 0):.0f}秒"
            else:
                self.current_visit_info = f"訪問回数: 0回  待機中..."
            
            if DEBUG_MODE:
                logger.info(f"訪問情報: {self.current_visit_info}")
        except json.JSONDecodeError as e:
            if DEBUG_MODE:
                logger.error(f"訪問情報ファイルのJSON解析エラー: {e}")
            self.current_visit_info = "訪問情報取得中..."
        except Exception as e:
            if DEBUG_MODE:
                logger.error(f"訪問情報ファイル読み込みエラー: {e}")
            self.current_visit_info = "訪問情報取得中..."
        
        # stream.txt
        try:
            with open(self.stream_text_file, 'r', encoding='utf-8') as f:
                self.current_stream_text = f.readline().strip()
            if DEBUG_MODE:
                logger.info(f"自由記述: {self.current_stream_text}")
        except Exception as e:
            if DEBUG_MODE:
                logger.error(f"自由記述ファイル読み込みエラー: {e}")
            self.current_stream_text = ""
    
    def check_camera(self):
        """カメラデバイスの存在を確認"""
        if not os.path.exists('/dev/video0'):
            print("エラー: カメラが見つかりません")
            return False
        return True
    
    def get_audio_input(self):
        """音声入力を取得"""
        if not self.use_audio:
            return ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']

        audio_inputs = [
            ['-f', 'alsa', '-ac', '1', '-i', 'plughw:2,0'],
            ['-f', 'alsa', '-i', 'plughw:2,0'],
            ['-f', 'alsa', '-i', 'plughw:1,0'],
            ['-f', 'alsa', '-i', 'hw:2,0'],
        ]
        
        for audio_input in audio_inputs:
            test_cmd = ['ffmpeg'] + audio_input + ['-t', '1', '-f', 'null', '-']
            try:
                result = subprocess.run(test_cmd, capture_output=True, timeout=3)
                if result.returncode == 0:
                    if DEBUG_MODE:
                        logger.info(f"音声入力を検出: {audio_input}")
                    return audio_input
            except:
                continue
        
        if DEBUG_MODE:
            logger.warning("音声入力が見つかりません。無音で配信します")
        return ['-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100']
    
    def start_stream_session(self):
        """配信セッションを開始"""
        print("配信を開始します...")
        if self.enable_udp:
            print(f"UDP出力: 有効 (ポート {self.udp_port})")
        
        if not self.check_camera():
            return False
        
        # テキストファイルの初期化と読み込み
        self.init_text_files()
        self.read_text_files()
        
        # 読み込んだ情報を表示
        print(f"トピック: {self.current_topic}")
        print(f"訪問情報: {self.current_visit_info}")
        print(f"自由記述: {self.current_stream_text}")
        print("-" * 40)
        
        audio_cmd = self.get_audio_input()
        
        # 基本のFFmpegコマンド
        ffmpeg_cmd = [
            'ffmpeg',
            '-nostdin',
            '-thread_queue_size', '512',
            '-f', 'v4l2',
            '-framerate', '30',
            '-video_size', '1280x720',
            '-input_format', 'mjpeg',
            '-i', '/dev/video0',
            '-thread_queue_size', '512'
        ]
        ffmpeg_cmd.extend(audio_cmd)
        
        # フォントパス
        font_paths = [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        ]
        font_file = next((p for p in font_paths if os.path.exists(p)), font_paths[0])
        
        jp_font_paths = [
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/fonts-japanese-gothic.ttf',
        ]
        jp_font = next((p for p in jp_font_paths if os.path.exists(p)), None)
        
        if not jp_font:
            if DEBUG_MODE:
                logger.warning("日本語フォントが見つかりません")
            jp_font = font_file
        
        # filter_complexでビデオフィルタを設定
        filter_complex = []
        
        # 基本フィルタ（クロップと時刻表示）
        filter_complex.append("[0:v]crop=720:720:280:0")
        filter_complex.append(f"drawtext=fontfile={font_file}:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=5:x=10:y=10:text='%{{localtime}}'")
        
        # テキスト表示（特殊文字をエスケープ）
        if self.current_topic:
            escaped_topic = self.current_topic.replace("'", "\\'").replace(":", "\\:")
            filter_complex.append(f"drawtext=fontfile={jp_font}:text='{escaped_topic}':fontsize=24:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=5:x=10:y=45")
        
        if self.current_visit_info:
            escaped_visit = self.current_visit_info.replace("'", "\\'").replace(":", "\\:")
            filter_complex.append(f"drawtext=fontfile={jp_font}:text='{escaped_visit}':fontsize=24:fontcolor=yellow:box=1:boxcolor=black@0.7:boxborderw=5:x=10:y=80")
        
        if self.current_stream_text:
            escaped_stream = self.current_stream_text.replace("'", "\\'").replace(":", "\\:")
            filter_complex.append(f"drawtext=fontfile={jp_font}:text='{escaped_stream}':fontsize=24:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=5:x=10:y=115")
        
        filter_str = ",".join(filter_complex)
        
        if self.enable_udp:
            # splitフィルタを使わずに、teeマルチプレクサを使用
            ffmpeg_cmd.extend(['-filter_complex', filter_str + "[processed]"])
            ffmpeg_cmd.extend([
                '-map', '[processed]',
                '-map', '1:a?',
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-b:v', '1200k',
                '-maxrate', '1200k',
                '-bufsize', '2400k',
                '-g', '60',
                '-keyint_min', '60',
                '-sc_threshold', '0',
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-ar', '44100',
                '-ac', '2',
                '-f', 'tee',
                f'[f=flv]{self.stream_url}|[f=mpegts:select=v]udp://{self.udp_address}:{self.udp_port}?pkt_size=1316'
            ])
        else:
            ffmpeg_cmd.extend(['-vf', filter_str])
            ffmpeg_cmd.extend([
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-b:v', '1200k',
                '-maxrate', '1200k',
                '-bufsize', '2400k',
                '-g', '60',
                '-keyint_min', '60',
                '-sc_threshold', '0',
                '-pix_fmt', 'yuv420p',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-ar', '44100',
                '-ac', '2',
                '-f', 'flv',
                self.stream_url
            ])
        
        try:
            if DEBUG_MODE:
                logger.info(f"FFmpegコマンド: {' '.join(ffmpeg_cmd)}")
            
            self.ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if DEBUG_MODE else subprocess.DEVNULL,
                universal_newlines=True,
                encoding='utf-8',
                bufsize=1
            )
            
            self.session_start_time = time.time()
            time.sleep(5)
            
            # 起動確認
            if self.ffmpeg_process and self.ffmpeg_process.poll() is not None:
                print("エラー: 配信の開始に失敗しました")
                self.ffmpeg_process = None
                return False
            
            return True
            
        except Exception as e:
            print(f"エラー: {e}")
            return False
    
    def monitor_stream(self):
        """配信を監視"""
        error_count = 0
        last_status_time = time.time()
        last_text_check = time.time()
        
        while self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            try:
                current_time = time.time()
                
                # 5秒ごとにテキストファイルをチェック
                if current_time - last_text_check > 5:
                    # 修正点1: タイマーのリセットをブロックの最初に移動し、バグを修正
                    last_text_check = current_time
                    
                    # 3つのファイルすべての古い内容を保存
                    old_visit = self.current_visit_info
                    old_topic = self.current_topic
                    old_stream_text = self.current_stream_text
                    
                    self.read_text_files() # 3つのファイルをすべて読み込む
                    
                    # 修正点2: いずれかのファイルに変更があった場合に再起動するよう条件を拡張
                    if (old_visit != self.current_visit_info or 
                        old_topic != self.current_topic or 
                        old_stream_text != self.current_stream_text):
                        
                        print("テキストファイル更新のため再開します")
                        # どのファイルが更新されたか分かりやすく表示
                        if old_visit != self.current_visit_info:
                            print(f"  - 訪問情報: {self.current_visit_info}")
                        if old_topic != self.current_topic:
                            print(f"  - トピック: {self.current_topic}")
                        if old_stream_text != self.current_stream_text:
                            print(f"  - 自由記述: {self.current_stream_text}")

                        return "text_updated" # FFmpegを再起動してテキストを反映
                
                # セッションタイムアウトチェック
                if self.session_start_time and (current_time - self.session_start_time) > self.max_session_duration:
                    return "session_timeout"
                
                # ステータス表示（1分ごと）
                if current_time - last_status_time > 60:
                    elapsed = int(current_time - self.session_start_time)
                    print(f"配信中... ({elapsed//3600}時間{(elapsed%3600)//60}分経過)")
                    last_status_time = current_time
                
                # デバッグモードの場合のみFFmpeg出力を監視
                if DEBUG_MODE and self.ffmpeg_process.stderr:
                    import select
                    ready, _, _ = select.select([self.ffmpeg_process.stderr], [], [], 0.1)
                    if ready:
                        line = self.ffmpeg_process.stderr.readline()
                        if line:
                            line = line.strip()
                            if 'error' in line.lower():
                                error_count += 1
                                if error_count > 50:
                                    return "too_many_errors"
                
                # 終了時刻チェック
                if self.end_time and datetime.datetime.now() >= self.end_time:
                    return "end_time_reached"
                    
            except Exception as e:
                if DEBUG_MODE:
                    logger.warning(f"監視エラー: {e}")
            
            time.sleep(0.5)
        
        return "process_died"
    def stop_stream_session(self):
        """配信セッションを停止"""
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            print("配信を停止します...")
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
                self.ffmpeg_process.wait(timeout=5)
            self.ffmpeg_process = None
    
    def start_stream(self):
        """配信を開始（再接続ループ付き）"""
        reconnect_count = 0
        
        while True:
            if self.start_stream_session():
                reconnect_count = 0
                result = self.monitor_stream()
                
                self.stop_stream_session()
                
                if result == "session_timeout":
                    print("セッションタイムアウト。再接続します")
                    continue
                elif result == "end_time_reached":
                    print("終了時刻に達しました")
                    break
                elif result == "text_updated":
                    print("テキスト更新のため再開します")
                    continue
                else:
                    break
            else:
                reconnect_count += 1
                if reconnect_count > self.max_reconnect_attempts:
                    print("配信開始に失敗しました")
                    break
                print(f"再接続を試みます... ({reconnect_count}/{self.max_reconnect_attempts})")
                time.sleep(self.reconnect_delay)
    
    def schedule_stream(self, start_time_str=None, end_time_str=None):
        """スケジュール配信"""
        now = datetime.datetime.now()
        
        if not start_time_str:
            start_time_str = self.default_start_time
        if not end_time_str:
            end_time_str = self.default_end_time
        
        try:
            hour, minute = map(int, start_time_str.split(':'))
            self.start_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            hour, minute = map(int, end_time_str.split(':'))
            self.end_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            if self.end_time <= self.start_time:
                self.end_time += datetime.timedelta(days=1)
            
            if now >= self.end_time:
                self.start_time += datetime.timedelta(days=1)
                self.end_time += datetime.timedelta(days=1)
            elif now >= self.start_time:
                self.start_time = now
            
            print(f"配信スケジュール: {self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}")
            
            if self.start_time > now:
                wait_seconds = (self.start_time - now).total_seconds()
                print(f"開始時刻まで待機中... ({int(wait_seconds//60)}分)")
                time.sleep(wait_seconds)
            
            self.start_stream()
            
        except Exception as e:
            print(f"エラー: {e}")

def main():
    """メイン関数"""
    print("=== YouTube Live 配信プログラム ===")
    
    streamer = YouTubeStreamer()
    
    # オプション処理
    if '--no-audio' in sys.argv:
        streamer.use_audio = False
        print("音声: 無効")
    
    if '--no-udp' in sys.argv:
        print("UDP出力: 無効")
    elif streamer.enable_udp:
        print(f"UDP出力: 有効 (udp://{streamer.udp_address}:{streamer.udp_port})")
    
    # スケジュール設定
    args = [arg for arg in sys.argv[1:] if not arg.startswith('--')]
    start_time = args[0] if len(args) >= 1 else None
    end_time = args[1] if len(args) >= 2 else None
    
    try:
        streamer.schedule_stream(start_time, end_time)
    except KeyboardInterrupt:
        print("\n中断されました")
        if streamer.ffmpeg_process:
            streamer.stop_stream_session()
    finally:
        print("プログラムを終了しました")

if __name__ == "__main__":
    main()