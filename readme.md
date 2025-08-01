# Raspberry Pi YouTube Live Streamer

Raspberry Pi 5を使用した鳥の定点観測YouTube Live自動配信システム（第3版）

## 概要
指定した時間に自動的にYouTube Liveへの配信を開始・停止するPythonスクリプトです。長時間配信の安定性を向上させ、YouTube側の制限に対応した自動再接続機能を実装しています。

## 🎉 第4版の新機能
* **詳細な診断ログ**: システムハングアップの原因特定のための拡張ログ機能
* **プロセス管理の強化**: 子プロセスとリソースの完全な追跡
* **クリーンアップ処理**: 終了時の詳細な状態記録とリソース解放
* **カスタムテキスト表示**: 配信画面に任意のテキストを表示可能

## 主な特徴
* 🕐 **スケジュール配信**: 設定した時刻に自動で配信開始・終了
* 📹 **最適化された設定**: Raspberry Pi 5とLogitech C270に最適化
* 🔄 **自動再接続**: 8時間ごとまたは切断時に自動的に再接続
* ⏰ **時刻表示**: 配信画面に現在時刻とカスタムテキストを表示
* 🔍 **診断機能**: システムハングアップの原因特定支援

## 必要な環境

### ハードウェア
* Raspberry Pi 5 (8GB推奨)
* Logitech C270 HD Webcam（または互換性のあるUSBカメラ）
* 安定したインターネット接続

### ソフトウェア
* Raspberry Pi OS (64-bit)
* Python 3.x
* FFmpeg 4.x以上
* psutil (Pythonパッケージ)

## インストール

```bash
# システムの更新とFFmpegのインストール
sudo apt update
sudo apt upgrade -y
sudo apt install ffmpeg python3-pip fonts-dejavu-core fonts-noto-cjk -y

# Pythonパッケージのインストール
pip3 install psutil

# スクリプトのダウンロード
git clone https://github.com/yourusername/raspberry-pi-youtube-streamer.git
cd raspberry-pi-youtube-streamer
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

### 3. カスタムテキストの設定（オプション）

配信画面に表示するカスタムテキストを変更したい場合は、`youtube_streamer.py`の以下の部分を編集：

```python
# カスタムテキスト設定（52行目付近）
self.custom_text = "２回目の産卵。 ８月２２日巣立ち予定！"
self.custom_text_size = 24  # フォントサイズ
self.custom_text_y_offset = 35  # 時刻からの縦の距離
```

## 使用方法

```bash
# 基本（デフォルト: 4:00-20:00）
python3 youtube_streamer.py

# カスタム時間
python3 youtube_streamer.py 9:00 17:00

# オプション
python3 youtube_streamer.py --no-audio              # 音声なし
python3 youtube_streamer.py --session-hours 6       # セッション時間変更
```

## 診断ログ

ログは`stream_logs/daily_stream_YYYYMMDD.log`に保存されます。

第3版では以下の詳細情報が記録されます：
* プロセスの開始・終了の詳細
* メモリとCPUの使用状況
* ファイルディスクリプタの状態
* 子プロセスの情報

## トラブルシューティング

### 配信終了後のシステムハングアップ

最新のログを確認：
```bash
tail -n 200 stream_logs/daily_stream_$(date +%Y%m%d).log
```

残存プロセスの確認：
```bash
ps aux | grep -E "(python|ffmpeg)" | grep -v grep
```

強制終了が必要な場合：
```bash
sudo pkill -9 -f youtube_streamer
sudo pkill -9 ffmpeg
```

### その他の問題

* **カメラが認識されない**: `/dev/video0`の存在を確認
* **音声が入力されない**: `--no-audio`オプションを使用
* **時刻が表示されない**: DejaVuフォントのインストールを確認

## 技術仕様

* **映像**: 1280x720入力 → 720x720出力、30fps、1200kbps
* **音声**: AAC 128kbps
* **エンコーダー**: libx264 (ultrafast preset)
* **自動再接続**: 8時間ごと、最大5回再試行
* **テキスト表示**: 時刻（24pt）+ カスタムテキスト（設定可能）

## バージョン履歴

- **v3.1.0** (2025-08-01) - カスタムテキスト表示機能追加
- **v3.0.0** (2025-06-19) - 診断機能追加、プロセス管理強化
- **v2.0.0** (2024-06-13) - 自動再接続、実時刻表示
- **v1.0.0** - 基本機能

## ライセンス
MIT License