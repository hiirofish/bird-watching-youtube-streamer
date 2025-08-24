#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鳥の巣訪問カウンター（UDP受信版）
シンプルで安定した動作を重視
"""

import cv2
import subprocess
import numpy as np
import time
import os
import json
from collections import deque
from datetime import datetime
import sys

# デバッグモード確認
DEBUG_MODE = '--debug' in sys.argv

class BirdVisitCounter:
    def __init__(self, nest_roi=(26, 618, 590, 66), score_threshold=4):
        self.nest_roi = nest_roi
        self.score_threshold = score_threshold
        self.prev_frame = None
        self.motion_history = deque(maxlen=5)
        self.last_visit_time = -999
        self.min_visit_interval = 3
        
        # 訪問状態
        self.is_visiting = False
        self.current_visit_start = None
        
        # 履歴
        self.area_history = deque(maxlen=5)
        self.darkness_history = deque(maxlen=5)
        
        # ファイル設定
        self.visit_info_file = "visit_info.txt"
        self.count_file = "count.txt"
        
        # 訪問情報の初期化
        self.visit_data = self.load_visit_info()
        
    def load_visit_info(self):
        """訪問情報を読み込む"""
        default_data = {
            'count': 0,
            'last_duration': 0,
            'last_visit_time': '',
            'total_duration': 0,
            'visits': []
        }
        
        try:
            if not os.path.exists(self.visit_info_file):
                self.save_visit_info(default_data)
                return default_data
            
            with open(self.visit_info_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    self.save_visit_info(default_data)
                    return default_data
                
                data = json.loads(content)
                for key in default_data.keys():
                    if key not in data:
                        data[key] = default_data[key]
                
                if not DEBUG_MODE:
                    print(f"訪問情報: {data['count']}回訪問済み")
                return data
                
        except Exception as e:
            if DEBUG_MODE:
                print(f"読み込みエラー: {e}")
            return default_data
    
    def save_visit_info(self, data=None):
        """訪問情報を保存"""
        if data is None:
            data = self.visit_data
        
        try:
            with open(self.visit_info_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            with open(self.count_file, 'w', encoding='utf-8') as f:
                f.write(str(data['count']))
                
        except Exception as e:
            if DEBUG_MODE:
                print(f"保存エラー: {e}")
    
    def record_visit_start(self):
        """訪問開始を記録"""
        self.visit_data['count'] += 1
        current_time = datetime.now()
        print(f"[{current_time.strftime('%H:%M:%S')}] 訪問 {self.visit_data['count']}: 開始")
        self.save_visit_info()
    
    def record_visit_end(self, duration):
        """訪問終了を記録"""
        current_time = datetime.now()
        time_str = current_time.strftime('%H時%M分')
        
        self.visit_data['last_duration'] = duration
        self.visit_data['last_visit_time'] = time_str
        self.visit_data['total_duration'] += duration
        
        visit_record = {
            'time': current_time.isoformat(),
            'duration': duration,
            'count': self.visit_data['count']
        }
        self.visit_data['visits'].append(visit_record)
        if len(self.visit_data['visits']) > 10:
            self.visit_data['visits'] = self.visit_data['visits'][-10:]
        
        print(f"[{current_time.strftime('%H:%M:%S')}] 訪問 {self.visit_data['count']}: 終了 ({duration:.1f}秒)")
        self.save_visit_info()
    
    def process_frame(self, frame, timestamp):
        """フレームを処理"""
        x, y, w, h = self.nest_roi
        
        if y+h > frame.shape[0] or x+w > frame.shape[1]:
            return False
        
        roi = frame[y:y+h, x:x+w]
        gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blur_roi = cv2.GaussianBlur(gray_roi, (3, 3), 0)
        
        if self.prev_frame is None:
            self.prev_frame = blur_roi
            return False
        
        diff = cv2.absdiff(self.prev_frame, blur_roi)
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        
        motion_pixels = np.sum(thresh > 0)
        motion_ratio = motion_pixels / (w * h)
        
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(thresh, connectivity=8)
        large_objects = 0
        total_area = 0
        
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            total_area += area
            if area > 300:
                large_objects += 1
        
        dark_pixels = np.sum(gray_roi < 80)
        darkness_ratio = dark_pixels / (w * h)
        
        self.motion_history.append(motion_ratio)
        self.area_history.append(total_area / (w * h))
        self.darkness_history.append(darkness_ratio)
        
        self.detect_visit(timestamp, large_objects)
        self.prev_frame = blur_roi
        return self.is_visiting
    
    def detect_visit(self, timestamp, large_objects):
        """訪問を検出"""
        if len(self.motion_history) < 3:
            return
        
        recent_motion = np.mean(list(self.motion_history))
        recent_area = np.mean(list(self.area_history))
        recent_darkness = np.mean(list(self.darkness_history))
        
        parent_score = 0
        
        if recent_motion > 0.05:
            parent_score += 1
        if recent_area > 0.08:
            parent_score += 2
        if recent_darkness > 0.3:
            parent_score += 2
        if large_objects > 0:
            parent_score += 2
        
        if parent_score >= self.score_threshold:
            if not self.is_visiting:
                if timestamp - self.last_visit_time > self.min_visit_interval:
                    self.is_visiting = True
                    self.current_visit_start = timestamp
                    self.record_visit_start()
        else:
            if self.is_visiting:
                self.is_visiting = False
                duration = timestamp - self.current_visit_start
                self.record_visit_end(duration)
                self.last_visit_time = timestamp

def process_udp_stream(udp_port=1234, roi=None, fps_target=2):
    """UDP受信と処理"""
    if roi is None:
        roi = (26, 618, 590, 66)
    
    print("=== 鳥の巣監視システム ===")
    print(f"UDPポート: {udp_port}")
    if DEBUG_MODE:
        print(f"ROI: x={roi[0]}, y={roi[1]}, w={roi[2]}, h={roi[3]}")
        print(f"処理FPS: {fps_target}")
    print("-" * 40)
    
    # カウンター初期化
    counter = BirdVisitCounter(nest_roi=roi)
    
    # FFmpegコマンド（シンプル版）
    ffmpeg_cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'error' if not DEBUG_MODE else 'warning',
        '-fflags', 'nobuffer+genpts',
        '-flags', 'low_delay',
        '-analyzeduration', '1000000',
        '-probesize', '1000000',
        '-i', f'udp://127.0.0.1:{udp_port}?overrun_nonfatal=1&fifo_size=50000000',
        '-vf', 'scale=720:720,format=bgr24',
        '-f', 'rawvideo',
        '-pix_fmt', 'bgr24',
        '-r', str(fps_target),
        '-'
    ]
    
    print("UDP受信を開始します...")
    print("配信プログラムが起動していることを確認してください")
    
    # FFmpegプロセスを起動
    try:
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if DEBUG_MODE else subprocess.DEVNULL,
            bufsize=10**8
        )
    except Exception as e:
        print(f"エラー: FFmpegの起動に失敗しました: {e}")
        return
    
    # フレームサイズ
    width, height = 720, 720
    frame_size = width * height * 3
    
    start_time = time.time()
    last_process_time = 0
    frame_count = 0
    processed_count = 0
    last_stats_time = start_time
    no_data_count = 0
    connection_established = False
    
    try:
        while True:
            # FFmpegからフレームを読み込み
            try:
                raw_frame = process.stdout.read(frame_size)
            except Exception as e:
                if DEBUG_MODE:
                    print(f"読み込みエラー: {e}")
                break
            
            if len(raw_frame) != frame_size:
                no_data_count += 1
                
                if not connection_established and no_data_count % 10 == 0:
                    print(".", end="", flush=True)
                
                if no_data_count > 100:
                    if not connection_established:
                        print("\nデータが受信できません。配信プログラムを確認してください")
                    break
                    
                time.sleep(0.1)
                continue
            
            # 接続確立
            if not connection_established:
                print("\n✓ ストリーム受信開始")
                print("-" * 40)
                connection_established = True
            
            no_data_count = 0
            
            # NumPy配列に変換
            try:
                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((height, width, 3))
            except Exception as e:
                if DEBUG_MODE:
                    print(f"フレーム変換エラー: {e}")
                continue
            
            frame_count += 1
            current_time = time.time()
            
            # 指定間隔で処理
            if current_time - last_process_time >= (1.0 / fps_target):
                timestamp = current_time - start_time
                counter.process_frame(frame, timestamp)
                processed_count += 1
                last_process_time = current_time
            
            # 統計表示（30秒ごと）
            if current_time - last_stats_time >= 30:
                elapsed = current_time - start_time
                if DEBUG_MODE:
                    actual_fps = processed_count / elapsed if elapsed > 0 else 0
                    print(f"[統計] 経過: {elapsed:.0f}秒 | 処理FPS: {actual_fps:.2f} | 訪問数: {counter.visit_data['count']}")
                else:
                    print(f"訪問数: {counter.visit_data['count']} | 総滞在: {counter.visit_data['total_duration']:.0f}秒")
                last_stats_time = current_time
                
            # FFmpegプロセスの状態確認
            if process.poll() is not None:
                if DEBUG_MODE:
                    print("FFmpegプロセスが終了しました")
                break
                
    except KeyboardInterrupt:
        print("\n終了します...")
    except Exception as e:
        print(f"エラー: {e}")
    finally:
        # プロセスをクリーンアップ
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except:
                process.kill()
        
        # 最終統計
        print("\n" + "=" * 40)
        print("最終統計:")
        print(f"  総訪問回数: {counter.visit_data['count']}回")
        print(f"  総滞在時間: {counter.visit_data['total_duration']:.1f}秒")
        if counter.visit_data['count'] > 0:
            avg_duration = counter.visit_data['total_duration'] / counter.visit_data['count']
            print(f"  平均滞在時間: {avg_duration:.1f}秒")

def main():
    """メイン関数"""
    # オプション確認
    show_help = '-h' in sys.argv or '--help' in sys.argv
    
    if show_help:
        print("使用方法:")
        print("  python3 bird_counter_udp.py [オプション]")
        print("\nオプション:")
        print("  --port PORT    UDPポート番号 (デフォルト: 1234)")
        print("  --fps FPS      処理FPS (デフォルト: 2)")
        print("  --reset        訪問情報をリセット")
        print("  --debug        デバッグモード")
        print("  -h, --help     このヘルプを表示")
        return
    
    # FPS設定
    fps = 2
    for i, arg in enumerate(sys.argv):
        if arg == '--fps' and i+1 < len(sys.argv):
            try:
                fps = float(sys.argv[i+1])
            except:
                fps = 2
    
    # ポート設定
    port = 1234
    for i, arg in enumerate(sys.argv):
        if arg == '--port' and i+1 < len(sys.argv):
            try:
                port = int(sys.argv[i+1])
            except:
                port = 1234
    
    # リセット処理
    if '--reset' in sys.argv:
        if os.path.exists("visit_info.txt"):
            os.remove("visit_info.txt")
        if os.path.exists("count.txt"):
            os.remove("count.txt")
        print("訪問情報をリセットしました")
        return
    
    # UDP受信開始
    process_udp_stream(udp_port=port, fps_target=fps)

if __name__ == "__main__":
    main()