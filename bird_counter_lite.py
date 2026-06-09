#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鳥の巣訪問カウンター（UDP受信版 + ローカルファイル対応）
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
        # 全履歴を保存するログファイルを追加
        self.visit_history_file = "visit_history.log"
        
        # 訪問情報の初期化
        self.visit_data = self.load_visit_info()
        
    def load_visit_info(self):
        """訪問情報を読み込む"""
        # visitsリストをデフォルトから削除
        default_data = {
            'count': 0,
            'last_duration': 0,
            'last_visit_time': '',
            'total_duration': 0,
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
                # visitsキーをチェックしないように変更
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
            # visitsキーをデータから除外して保存
            summary_data = {k: v for k, v in data.items() if k != 'visits'}
            with open(self.visit_info_file, 'w', encoding='utf-8') as f:
                json.dump(summary_data, f, ensure_ascii=False, indent=2)
            
            with open(self.count_file, 'w', encoding='utf-8') as f:
                f.write(str(data['count']))
                
        except Exception as e:
            if DEBUG_MODE:
                print(f"保存エラー: {e}")
    
    def record_visit_start(self, video_time=None):
        """訪問開始を記録"""
        self.visit_data['count'] += 1
        current_time = datetime.now()
        
        if video_time is not None:
            hours = int(video_time // 3600)
            minutes = int((video_time % 3600) // 60)
            seconds = int(video_time % 60)
            print(f"[{hours:02d}:{minutes:02d}:{seconds:02d}] 訪問 {self.visit_data['count']}: 開始")
        else:
            print(f"[{current_time.strftime('%H:%M:%S')}] 訪問 {self.visit_data['count']}: 開始")
        self.save_visit_info()
    
    def record_visit_end(self, duration, video_time=None):
        """訪問終了を記録"""
        current_time = datetime.now()
        time_str = current_time.strftime('%H時%M分')
        
        self.visit_data['last_duration'] = round(duration, 1)
        self.visit_data['last_visit_time'] = time_str
        self.visit_data['total_duration'] += duration
        
        visit_record = {
            'time': current_time.isoformat(),
            'duration': round(duration, 1),
            'count': self.visit_data['count']
        }
        
        # 全履歴を別ファイルに追記
        try:
            with open(self.visit_history_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(visit_record, ensure_ascii=False) + '\n')
        except Exception as e:
            if DEBUG_MODE:
                print(f"履歴保存エラー: {e}")

        if video_time is not None:
            hours = int(video_time // 3600)
            minutes = int((video_time % 3600) // 60)
            seconds = int(video_time % 60)
            print(f"[{hours:02d}:{minutes:02d}:{seconds:02d}] 訪問 {self.visit_data['count']}: 終了 ({duration:.1f}秒)")
        else:
            print(f"[{current_time.strftime('%H:%M:%S')}] 訪問 {self.visit_data['count']}: 終了 ({duration:.1f}秒)")
        self.save_visit_info()
    
    def process_frame(self, frame, timestamp, is_local_file=False):
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
        
        self.detect_visit(timestamp, large_objects, is_local_file)
        self.prev_frame = blur_roi
        return self.is_visiting
    
    def detect_visit(self, timestamp, large_objects, is_local_file=False):
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
        
        if DEBUG_MODE:
            if parent_score >= self.score_threshold - 1:
                video_time = timestamp if is_local_file else None
                if video_time is not None:
                    hours = int(video_time // 3600)
                    minutes = int((video_time % 3600) // 60)
                    seconds = int(video_time % 60)
                    time_str = f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"
                else:
                    time_str = f"[{timestamp:.1f}s]"
                print(f"{time_str} Score: {parent_score}/{self.score_threshold} | Motion: {recent_motion:.3f} | Area: {recent_area:.3f} | Dark: {recent_darkness:.3f} | Objects: {large_objects}")
        
        if parent_score >= self.score_threshold:
            if not self.is_visiting:
                if timestamp - self.last_visit_time > self.min_visit_interval:
                    self.is_visiting = True
                    self.current_visit_start = timestamp
                    video_time = timestamp if is_local_file else None
                    self.record_visit_start(video_time)
        else:
            if self.is_visiting:
                self.is_visiting = False
                duration = timestamp - self.current_visit_start
                video_time = timestamp if is_local_file else None
                self.record_visit_end(duration, video_time)
                self.last_visit_time = timestamp

def process_local_file(filepath, roi=None, fps_target=2, show_video=False, save_debug=False, save_log=False):
    """ローカルファイルを処理"""
    if roi is None:
        roi = (26, 618, 590, 66)
    
    if not os.path.exists(filepath):
        print(f"エラー: ファイルが見つかりません: {filepath}")
        return
    
    print("=== 鳥の巣監視システム（ローカルファイルモード） ===")
    print(f"入力ファイル: {filepath}")
    if DEBUG_MODE:
        print(f"ROI: x={roi[0]}, y={roi[1]}, w={roi[2]}, h={roi[3]}")
        print(f"処理FPS: {fps_target}")
    print("-" * 40)
    
    # カウンター初期化
    counter = BirdVisitCounter(nest_roi=roi)
    
    # ログファイルの準備
    log_file = None
    if save_log:
        log_filename = filepath.rsplit('.', 1)[0] + '_detection.log'
        log_file = open(log_filename, 'w', encoding='utf-8')
        log_file.write(f"=== 検出ログ: {filepath} ===\n")
        log_file.write(f"ROI: x={roi[0]}, y={roi[1]}, w={roi[2]}, h={roi[3]}\n")
        log_file.write(f"閾値: {counter.score_threshold}\n")
        log_file.write("-" * 40 + "\n")
        print(f"ログファイル: {log_filename}")
    
    # ビデオキャプチャを開く
    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        print(f"エラー: ビデオファイルを開けません: {filepath}")
        if log_file:
            log_file.close()
        return
    
    # ビデオ情報を取得
    original_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / original_fps if original_fps > 0 else 0
    
    print(f"ビデオ情報: {original_fps:.1f}fps, {total_frames}フレーム, {duration:.1f}秒")
    
    # フレームスキップの計算
    frame_skip = max(1, int(original_fps / fps_target))
    
    # デバッグ動画の保存設定（検出部分のみ）
    debug_writer = None
    debug_segments = []
    current_visit_start_frame = None
    
    if save_debug:
        debug_filename = filepath.rsplit('.', 1)[0] + '_detections.mp4'
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        # Note: Use a standard frame size like (720, 720) for the debug writer
        debug_writer = cv2.VideoWriter(debug_filename, fourcc, fps_target, (720, 720))
        print(f"検出部分を保存: {debug_filename}")
    
    start_time = time.time()
    frame_count = 0
    processed_count = 0
    last_stats_time = start_time
    
    # 訪問検出履歴
    detection_history = []
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            frame_count += 1
            
            if frame_count % frame_skip != 0:
                continue
            
            if frame.shape[:2] != (720, 720):
                frame = cv2.resize(frame, (720, 720))
            
            timestamp = processed_count * (1.0 / fps_target)
            is_visiting = counter.process_frame(frame, timestamp, is_local_file=True)
            processed_count += 1
            
            display_frame = None
            if show_video or (save_debug and is_visiting):
                display_frame = frame.copy()
                x, y, w, h = roi
                color = (0, 255, 0) if is_visiting else (0, 0, 255)
                cv2.rectangle(display_frame, (x, y), (x + w, y + h), color, 2)
                
                hours = int(timestamp // 3600)
                minutes = int((timestamp % 3600) // 60)
                seconds = int(timestamp % 60)
                time_text = f"Time: {hours:02d}:{minutes:02d}:{seconds:02d}"
                cv2.putText(display_frame, time_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                
                info_text = f"Visit: {counter.visit_data['count']} | {'VISITING' if is_visiting else 'WAITING'}"
                cv2.putText(display_frame, info_text, (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
                
                progress = int((frame_count / total_frames) * 100) if total_frames > 0 else 0
                cv2.putText(display_frame, f"Progress: {progress}%", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            if save_debug and is_visiting and debug_writer and display_frame is not None:
                debug_writer.write(display_frame)

            if show_video and display_frame is not None:
                cv2.imshow('Bird Nest Monitor', display_frame)
                key = cv2.waitKey(1)
                if key == ord('q'):
                    break
                elif key == ord(' '):
                    cv2.waitKey(0)
            
            current_time = time.time()
            if current_time - last_stats_time >= 30:
                progress = int((frame_count / total_frames) * 100) if total_frames > 0 else 0
                print(f"[進捗] {progress}% | 訪問数: {counter.visit_data['count']} | 総滞在: {counter.visit_data['total_duration']:.0f}秒")
                last_stats_time = current_time
                
    except KeyboardInterrupt:
        print("\n処理を中断しました")
    except Exception as e:
        print(f"エラー: {e}")
    finally:
        cap.release()
        if debug_writer:
            debug_writer.release()
        if show_video:
            cv2.destroyAllWindows()
        
        if save_debug:
             print(f"検出動画を保存しました: {debug_filename}")

        if log_file:
            log_file.close()
        
        print("\n" + "=" * 40)
        print("処理完了:")
        print(f"  処理フレーム数: {processed_count}/{total_frames}")
        print(f"  総訪問回数: {counter.visit_data['count']}回")
        print(f"  総滞在時間: {counter.visit_data['total_duration']:.1f}秒")
        if counter.visit_data['count'] > 0:
            avg_duration = counter.visit_data['total_duration'] / counter.visit_data['count']
            print(f"  平均滞在時間: {avg_duration:.1f}秒")
        
        # NOTE: This part had a bug, it's now removed as history is in a separate file.

def process_udp_stream(udp_port=1234, roi=None, fps_target=2):
    """UDP受信と処理（元のコード）"""
    if roi is None:
        roi = (26, 618, 590, 66)
    
    print("=== 鳥の巣監視システム ===")
    print(f"UDPポート: {udp_port}")
    if DEBUG_MODE:
        print(f"ROI: x={roi[0]}, y={roi[1]}, w={roi[2]}, h={roi[3]}")
        print(f"処理FPS: {fps_target}")
    print("-" * 40)
    
    counter = BirdVisitCounter(nest_roi=roi)
    
    ffmpeg_cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error' if not DEBUG_MODE else 'warning',
        '-fflags', 'nobuffer+genpts', '-flags', 'low_delay',
        '-analyzeduration', '1000000', '-probesize', '1000000',
        '-i', f'udp://127.0.0.1:{udp_port}?overrun_nonfatal=1&fifo_size=50000000',
        '-vf', 'scale=720:720,format=bgr24', '-f', 'rawvideo',
        '-pix_fmt', 'bgr24', '-r', str(fps_target), '-'
    ]
    
    print("UDP受信を開始します...")
    print("配信プログラムが起動していることを確認してください")
    
    try:
        process = subprocess.Popen(
            ffmpeg_cmd, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if DEBUG_MODE else subprocess.DEVNULL,
            bufsize=10**8
        )
    except Exception as e:
        print(f"エラー: FFmpegの起動に失敗しました: {e}")
        return
    
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
            raw_frame = process.stdout.read(frame_size)
            
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
            
            if not connection_established:
                print("\n✓ ストリーム受信開始")
                print("-" * 40)
                connection_established = True
            
            no_data_count = 0
            
            try:
                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((height, width, 3))
            except Exception as e:
                if DEBUG_MODE:
                    print(f"フレーム変換エラー: {e}")
                continue
            
            frame_count += 1
            current_time = time.time()
            
            if current_time - last_process_time >= (1.0 / fps_target):
                timestamp = current_time - start_time
                counter.process_frame(frame, timestamp)
                processed_count += 1
                last_process_time = current_time
            
            if current_time - last_stats_time >= 30:
                elapsed = current_time - start_time
                if DEBUG_MODE:
                    actual_fps = processed_count / elapsed if elapsed > 0 else 0
                    print(f"[統計] 経過: {elapsed:.0f}秒 | 処理FPS: {actual_fps:.2f} | 訪問数: {counter.visit_data['count']}")
                else:
                    print(f"訪問数: {counter.visit_data['count']} | 総滞在: {counter.visit_data['total_duration']:.0f}秒")
                last_stats_time = current_time
                
            if process.poll() is not None:
                if DEBUG_MODE:
                    print("FFmpegプロセスが終了しました")
                break
                
    except KeyboardInterrupt:
        print("\n終了します...")
    except Exception as e:
        print(f"エラー: {e}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except:
                process.kill()
        
        print("\n" + "=" * 40)
        print("最終統計:")
        print(f"  総訪問回数: {counter.visit_data['count']}回")
        print(f"  総滞在時間: {counter.visit_data['total_duration']:.1f}秒")
        if counter.visit_data['count'] > 0:
            avg_duration = counter.visit_data['total_duration'] / counter.visit_data['count']
            print(f"  平均滞在時間: {avg_duration:.1f}秒")

def main():
    """メイン関数"""
    show_help = '-h' in sys.argv or '--help' in sys.argv
    
    if show_help:
        print("使用方法: python3 bird_counter_lite.py [オプション]")
        print("\nオプション:")
        print("  --file FILE       ローカルファイルを処理")
        print("  --port PORT       UDPポート番号 (デフォルト: 1234)")
        print("  --fps FPS         処理FPS (デフォルト: 2)")
        print("  --roi X,Y,W,H     ROI領域を指定 (デフォルト: 26,618,590,66)")
        print("  --threshold N     検出閾値 (デフォルト: 4)")
        print("  --show            ビデオを表示（ローカルファイルのみ）")
        print("  --no-debug-video  検証動画を保存しない（ローカルファイル時）")
        print("  --no-log          ログを保存しない（ローカルファイル時）")
        print("  --reset           訪問情報をリセット")
        print("  --debug           デバッグモード")
        print("  -h, --help        このヘルプを表示")
        print("\n注意: ローカルファイル処理時は自動的に検証動画とログが保存されます")
        return
    
    fps = 2
    if '--fps' in sys.argv:
        try:
            fps_index = sys.argv.index('--fps') + 1
            fps = float(sys.argv[fps_index])
        except (ValueError, IndexError):
            pass

    roi = (26, 618, 590, 66)
    if '--roi' in sys.argv:
        try:
            roi_index = sys.argv.index('--roi') + 1
            parts = sys.argv[roi_index].split(',')
            if len(parts) == 4:
                roi = tuple(map(int, parts))
        except (ValueError, IndexError):
            pass

    threshold = 4
    if '--threshold' in sys.argv:
        try:
            thr_index = sys.argv.index('--threshold') + 1
            threshold = int(sys.argv[thr_index])
        except (ValueError, IndexError):
            pass
    
    # This needs to be set before creating the counter instance
    BirdVisitCounter.__init__.__defaults__ = (roi, threshold)

    if '--reset' in sys.argv:
        if os.path.exists("visit_info.txt"): 
            os.remove("visit_info.txt")
        if os.path.exists("count.txt"): 
            os.remove("count.txt")
        if os.path.exists("visit_history.log"): 
            os.remove("visit_history.log")
        print("訪問情報をリセットしました")
        return
    
    if '--file' in sys.argv:
        try:
            file_index = sys.argv.index('--file') + 1
            filepath = sys.argv[file_index]
            show_video = '--show' in sys.argv
            # ローカルファイル時はデフォルトで有効
            save_debug = not ('--no-debug-video' in sys.argv)
            save_log = not ('--no-log' in sys.argv)
            process_local_file(filepath, roi=roi, fps_target=fps, 
                             show_video=show_video, save_debug=save_debug,
                             save_log=save_log)
            return
        except (ValueError, IndexError):
            print("エラー: --file オプションにはファイルパスが必要です")
            return

    port = 1234
    if '--port' in sys.argv:
        try:
            port_index = sys.argv.index('--port') + 1
            port = int(sys.argv[port_index])
        except (ValueError, IndexError):
            port = 1234
    
    process_udp_stream(udp_port=port, roi=roi, fps_target=fps)

if __name__ == "__main__":
    main()
