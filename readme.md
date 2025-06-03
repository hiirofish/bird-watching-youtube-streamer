# Raspberry Pi YouTube Live Streamer
Raspberry Pi 5を使用した鳥の定点観測YouTube Live自動配信システム

## 概要
このプロジェクトは、指定した時間に自動的にYouTube Liveへの配信を開始・停止するPythonスクリプトです。22分問題を解決し、長時間（12時間以上）の安定した配信を実現しています。

## 主な特徴
* 🕐 **スケジュール配信**: 設定した時刻に自動で配信開始・終了
* 📹 **最適化された設定**: Raspberry Pi 5とLogitech C270に最適化
* 📊 **詳細なログ**: 配信状態とシステムリソースの監視
* ⏰ **22分問題の解決**: YouTube側の制限を回避する設定

## 必要な環境

### ハードウェア
* Raspberry Pi 5 (8GB推奨)
* Logitech C270 HD Webcam（または互換性のあるUSBカメラ）
* 安定したインターネット接続

### ソフトウェア
* Raspberry Pi OS (64-bit)
* Python 3.x
* FFmpeg
* 必要なPythonパッケージ：
   * psutil
   * その他標準ライブラリ

## インストール

1. システムの更新とFFmpegのインストール

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install ffmpeg python3-pip -y
```

2. Pythonパッケージのインストール

```bash
pip3 install psutil
```

3. スクリプトのダウンロード

```bash
git clone https://github.com/yourusername/raspberry-pi-youtube-streamer.git
cd raspberry-pi-youtube-streamer
```

## 設定

### 1. YouTube Studioでストリームキーを取得
1. YouTube Studioにアクセス
2. 左メニューから「ライブ配信」を選択
3. 「エンコーダ配信」タブを選択
4. ストリームキーをコピー

### 2. YouTube側の設定（重要）
**22分問題を回避するために以下の設定が必須です：**
* ✅ **DVRを無効にする**
* ✅ **遅延を「通常」に設定**（低遅延は使用しない）
* ✅ **360°動画をオフ**

### 3. ストリームキーの設定

#### 方法1: 環境変数で設定
```bash
export YOUTUBE_STREAM_KEY='abcd-efgh-ijkl-mnop-qrst'
```

永続化したい場合は`~/.bashrc`に追加：
```bash
echo "export YOUTUBE_STREAM_KEY='abcd-efgh-ijkl-mnop-qrst'" >> ~/.bashrc
source ~/.bashrc
```

#### 方法2: config.txtファイルで設定
プロジェクトディレクトリに`config.txt`を作成：
```
STREAM_KEY=abcd-efgh-ijkl-mnop-qrst
```

#### ストリームキーの形式について
* YouTubeのストリームキーは通常、ハイフンで区切られた英数字の文字列です
* 例: `abcd-efgh-ijkl-mnop-qrst`
* 実際のキーはYouTube Studioから取得してください

## 使用方法

### 基本的な使用方法（デフォルト: 5:00-20:00）
```bash
python3 youtube_streamer.py
```

### カスタム時間での実行
```bash
# 9:00から12:00まで配信
python3 youtube_streamer.py 9:00 12:00

# 開始時刻のみ指定（9:00-20:00）
python3 youtube_streamer.py 9:00
```

## 技術仕様

### FFmpegコマンドの詳細
主要な設定：
* **映像入力**: 1280x720 MJPEG形式
* **出力解像度**: 720x720（正方形に切り抜き）
* **ビットレート**: 1200kbps（CBR）
* **フレームレート**: 30fps
* **音声**: AAC 128kbps
* **エンコーダー**: libx264（ultrafast preset）

### なぜこの設定なのか

1. **CBR（固定ビットレート）**
   * YouTubeサーバーの負荷を一定に保つ
   * 長時間配信の安定性向上

2. **720x720への切り抜き**
   * 正方形フォーマットで見やすい
   * データ量の削減（約44%）

3. **30fpsでの配信**
   * C270カメラは720p/30fps対応
   * 滑らかな映像を実現

### YouTubeストリームURL
YouTubeライブ配信のストリームURLは共通です：
```
rtmp://a.rtmp.youtube.com/live2/
```
このURLは公開情報のため、READMEに記載しても問題ありません。

## トラブルシューティング

### 22分で配信が切れる場合
1. YouTube Studioで**DVRが無効**になっているか確認
2. **遅延設定が「通常」**になっているか確認
3. 上記を変更後、新しい配信で試す

### カメラが認識されない場合
```bash
# カメラデバイスの確認
ls -la /dev/video*
v4l2-ctl --list-devices
```

### 配信が開始されない場合
```bash
# ログの確認
tail -f stream_logs/daily_stream_*.log

# FFmpegプロセスの確認
ps aux | grep ffmpeg
```

## ログファイル
ログは`stream_logs/`ディレクトリに日付ごとに保存されます：
* `daily_stream_YYYYMMDD.log`

ログには以下の情報が記録されます：
* 配信の開始・停止時刻
* FFmpegの統計情報（ビットレート、FPS等）
* システムリソース使用状況（10分ごと）
* エラーや警告

## .gitignoreの設定
プロジェクトルートに`.gitignore`ファイルを作成してください。詳細は同梱の`.gitignore`ファイルを参照してください。

## ライセンス
MIT License

## 謝辞
このプロジェクトは、鳥の観察を愛する全ての人のために作られました。
