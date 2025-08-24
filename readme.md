# Raspberry Pi YouTube Live Streamer

Raspberry Pi 5を使用した鳥の定点観測YouTube Live自動配信システム（第4版）

## 概要
指定した時間に自動的にYouTube Liveへの配信を開始・停止するPythonスクリプトです。長時間配信の安定性を向上させ、YouTube側の制限に対応した自動再接続機能を実装。さらに、UDP出力による動体検知連携機能を追加しました。

## 🎉 第4版の新機能
* **UDP出力連携**: 配信映像をUDPで同時出力し、動体検知プログラムと連携
* **外部テキストファイル対応**: 配信画面のテキストを外部ファイルで管理・動的更新
* **自動動体検知**: 鳥の巣への訪問を自動検知し、統計情報を配信画面に表示
* **リアルタイム情報更新**: テキストファイル変更時の自動反映（5秒間隔）

## 主な特徴
* 🕐 **スケジュール配信**: 設定した時刻に自動で配信開始・終了
* 📹 **最適化された設定**: Raspberry Pi 5とLogitech C270に最適化
* 🔄 **自動再接続**: 8時間ごとまたは切断時に自動的に再接続
* ⏰ **時刻表示**: 配信画面に現在時刻とカスタムテキストを表示
* 🔍 **動体検知連携**: UDP出力により同時に動体検知を実行
* 📊 **訪問統計表示**: 鳥の巣への訪問回数・滞在時間をリアルタイム表示

## 必要な環境

### ハードウェア
* Raspberry Pi 5 (8GB推奨)
* Logitech C270 HD Webcam（または互換性のあるUSBカメラ）
* 安定したインターネット接続

### ソフトウェア
* Raspberry Pi OS (64-bit)
* Python 3.x
* FFmpeg 4.x以上
* OpenCV (cv2) - 動体検知用
* numpy - 画像処理用

## インストール

```bash
# システムの更新とFFmpegのインストール
sudo apt update
sudo apt upgrade -y
sudo apt install ffmpeg python3-pip fonts-dejavu-core fonts-noto-cjk -y

# Pythonパッケージのインストール
pip3 install opencv-python numpy

# スクリプトのダウンロード
git clone https://github.com/hiirofish/bird-watching-youtube-streamer.git
cd bird-watching-youtube-streamer
```

## 設定

### 1. YouTube Studioでストリームキーを取得
1. YouTube Studioの「ライブ配信」→「エンコーダ配信」
2. ストリームキーをコピー

### 2. ストリームキーの設定

#### 方法1: 環境変数
```bash
export YOUTUBE_STREAM_KEY='abcd-efgh-ijkl-mnop-qrst'
```

#### 方法2: config.txt
```
STREAM_KEY=abcd-efgh-ijkl-mnop-qrst
```

### 3. 表示テキストの設定

配信画面に表示される4つのテキスト情報を外部ファイルで管理できます：

#### 配信画面のテキスト表示位置
```
┌─────────────────────────────────┐
│ [時刻] 2024-08-24 14:30:15     │ ← 自動生成（変更不可）
│ [トピック] topic.txt の内容     │ ← 1行目
│ [訪問情報] visit_info.txt の内容│ ← 2行目（自動更新）
│ [自由記述] stream.txt の内容    │ ← 3行目
│                                │
│        [カメラ映像エリア]        │
│                                │
└─────────────────────────────────┘
```

#### ファイル詳細

**topic.txt**（トピック・タイトル情報）
```
２回目の産卵! 8月22日巣立ち予定!!
```
- 配信のメインタイトルや現在の状況
- 巣の状態、卵の数、予定日など重要な情報

**stream.txt**（視聴者向けメッセージ）
```
Youtube登録お願いします
```
- 視聴者へのお礼やお願い
- チャンネル登録の呼びかけなど

**visit_info.txt**（動体検知結果 - 自動生成）
```json
{
  "count": 5,
  "last_duration": 12.3,
  "last_visit_time": "14時30分",
  "total_duration": 145.6,
  "visits": [...]
}
```
- 動体検知プログラムが自動で更新
- 訪問回数、滞在時間、最終訪問時刻を記録
- **手動で編集する必要なし**

**count.txt**（簡易カウント - 自動生成）
```
5
```
- 訪問回数のみの簡易ファイル
- 他のプログラムからの参照用

## 使用方法

### 基本的な配信（UDP出力あり）

```bash
# デフォルト設定で配信開始（5:10-19:20、UDP出力有効）
python3 udp_youtube_steamer.py

# カスタム時間指定
python3 udp_youtube_steamer.py 9:00 17:00

# デバッグモード（詳細ログ表示）
python3 udp_youtube_steamer.py --debug

# UDP出力無効（動体検知なし）
python3 udp_youtube_steamer.py --no-udp

# 音声無効
python3 udp_youtube_steamer.py --no-audio
```

### 動体検知プログラム（別ターミナルで実行）

```bash
# 基本実行（UDPポート1234で受信）
python3 bird_counter_lite.py

# デバッグモード（詳細情報表示）
python3 bird_counter_lite.py --debug

# カスタムポート指定
python3 bird_counter_lite.py --port 5000

# 処理FPS変更（デフォルト2fps）
python3 bird_counter_lite.py --fps 1

# 訪問情報リセット
python3 bird_counter_lite.py --reset

# ヘルプ表示
python3 bird_counter_lite.py --help
```

## 動体検知の詳細設定

### ROI（関心領域）の調整

`bird_counter_lite.py`の以下の部分で検知エリアを変更できます：

```python
# BirdVisitCounterクラスの初期化部分（37行目付近）
def __init__(self, nest_roi=(26, 618, 590, 66), score_threshold=4):
    self.nest_roi = nest_roi  # (x, y, width, height)
```

**座標の意味：**
- `x=26`: 検知エリアの左端位置
- `y=618`: 検知エリアの上端位置  
- `width=590`: 検知エリアの幅
- `height=66`: 検知エリアの高さ

### 検知感度の調整

```python
# 検知スコアの閾値（136行目付近）
score_threshold=4  # デフォルト値、小さくすると高感度

# 各検知要素のスコア配分（detect_visit関数内）
if recent_motion > 0.05:     # 動き検出: 0.05 = 5%の変化
    parent_score += 1
if recent_area > 0.08:       # 面積変化: 0.08 = 8%の変化  
    parent_score += 2
if recent_darkness > 0.3:    # 暗さ検出: 0.3 = 30%が暗い
    parent_score += 2
if large_objects > 0:        # 大きな物体検出
    parent_score += 2
```

**調整例：**
- **感度を上げたい**: `score_threshold=3`、各閾値を小さくする
- **誤検知を減らしたい**: `score_threshold=5`、各閾値を大きくする
- **特定の要素を重視**: スコア配分を変更（例：`parent_score += 3`）

### 訪問判定の調整

```python
# 最小訪問間隔（44行目付近）
self.min_visit_interval = 3  # 3秒間隔、短くすると細かく検知

# 履歴の保持数（42-43行目付近）
self.motion_history = deque(maxlen=5)    # 動き履歴
self.area_history = deque(maxlen=5)      # 面積変化履歴
self.darkness_history = deque(maxlen=5)  # 暗さ履歴
```

## 動作の流れ

```
1. udp_youtube_steamer.py 起動
   ↓
2. YouTube Live配信開始 + UDP出力開始（localhost:1234）
   ↓
3. bird_counter_lite.py 起動（別ターミナル）
   ↓
4. UDP受信 → フレーム解析 → 動体検知
   ↓
5. 訪問検知時 → visit_info.txt 更新
   ↓
6. 配信プログラムが5秒ごとにファイル変更チェック
   ↓
7. 変更検出 → FFmpeg再起動 → 画面テキスト更新
```

## ログとトラブルシューティング

### ログファイル
- `stream_logs/daily_stream_YYYYMMDD.log` - 配信ログ
- `monitor_logs/` - その他の監視ログ

### よくある問題

**UDP接続できない**
```bash
# ポート使用状況確認
sudo netstat -tulpn | grep 1234

# ファイアウォール確認（必要に応じて）
sudo ufw status
```

**動体検知が反応しない**
```bash
# デバッグモードで詳細確認
python3 bird_counter_lite.py --debug

# ROI位置の確認が必要な場合は座標を調整
```

**テキストが更新されない**
```bash
# ファイルの文字エンコーディング確認
file topic.txt stream.txt

# ファイル権限確認
ls -la *.txt
```

## 技術仕様

### 配信設定
* **映像**: 1280x720入力 → 720x720出力、30fps、1200kbps
* **音声**: AAC 128kbps
* **エンコーダー**: libx264 (ultrafast preset)
* **自動再接続**: 8時間ごと、最大5回再試行

### UDP出力設定
* **プロトコル**: UDP/TS over IP
* **デフォルトポート**: 1234
* **アドレス**: localhost (127.0.0.1)
* **パケットサイズ**: 1316バイト

### 動体検知仕様
* **処理解像度**: 720x720
* **処理FPS**: 2fps（デフォルト）
* **検知方式**: フレーム差分 + 連結成分解析
* **判定要素**: 動き量・面積変化・明度変化・物体サイズ

## バージョン履歴

- **v4.0.0** (2024-08-24) - UDP出力・動体検知連携機能追加
- **v3.1.0** (2025-08-01) - カスタムテキスト表示機能追加
- **v3.0.0** (2025-06-19) - 診断機能追加、プロセス管理強化
- **v2.0.0** (2024-06-13) - 自動再接続、実時刻表示
- **v1.0.0** - 基本機能

## ライセンス
MIT License