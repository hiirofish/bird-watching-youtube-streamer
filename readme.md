# Raspberry Pi YouTube Live Streamer

Raspberry Pi 5を使用した鳥の定点観測YouTube Live自動配信システム（第2版）

## 概要
このプロジェクトは、指定した時間に自動的にYouTube Liveへの配信を開始・停止するPythonスクリプトです。長時間配信の安定性を向上させ、YouTube側の制限に対応した自動再接続機能を実装しています。

## 🎉 第2版の新機能
* **実時刻表示**: 配信画面に現在時刻を表示（localtime使用）
* **自動再接続**: 8時間ごとまたは切断時に自動的に再接続
* **長時間配信対応**: 12時間以上の連続配信を実現
* **改善されたスケジュール機能**: 終了時刻の確実な制御

## 主な特徴
* 🕐 **スケジュール配信**: 設定した時刻に自動で配信開始・終了
* 📹 **最適化された設定**: Raspberry Pi 5とLogitech C270に最適化
* 📊 **詳細なログ**: 配信状態とシステムリソースの監視
* 🔄 **自動再接続**: YouTube側の制限を回避する設計
* ⏰ **時刻表示**: 配信画面に現在時刻を表示

## 必要な環境

### ハードウェア
* Raspberry Pi 5 (8GB推奨)
* Logitech C270 HD Webcam（または互換性のあるUSBカメラ）
* 安定したインターネット接続

### ソフトウェア
* Raspberry Pi OS (64-bit)
* Python 3.x
* FFmpeg 4.x以上
* 必要なPythonパッケージ：
  * psutil

## インストール

1. システムの更新とFFmpegのインストール
```bash
sudo apt update
sudo apt upgrade -y
sudo apt install ffmpeg python3-pip fonts-dejavu-core -y
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

### 2. ストリームキーの設定

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

## 使用方法

### 基本的な使用方法（デフォルト: 4:00-20:00）
```bash
python3 youtube_streamer_reconnect.py
```
※ 現在時刻が配信時間外の場合は、次の開始時刻まで待機します

### カスタム時間での実行
```bash
# 9:00から12:00まで配信
python3 youtube_streamer_reconnect.py 9:00 12:00

# 開始時刻のみ指定（9:00-20:00）
python3 youtube_streamer_reconnect.py 9:00
```

### オプション
```bash
# 音声なしで配信
python3 youtube_streamer_reconnect.py --no-audio

# セッション時間を変更（デフォルト8時間）
python3 youtube_streamer_reconnect.py --session-hours 6

# 組み合わせ例
python3 youtube_streamer_reconnect.py 9:00 17:00 --session-hours 4
```

## 技術仕様

### FFmpegコマンドの詳細
主要な設定：
* **映像入力**: 1280x720 MJPEG形式
* **出力解像度**: 720x720（正方形に切り抜き）
* **ビットレート**: 1200kbps
* **フレームレート**: 30fps
* **音声**: AAC 128kbps
* **エンコーダー**: libx264（ultrafast preset）
* **時刻表示**: DejaVuSansフォント使用

### 自動再接続の仕組み
1. **セッションタイムアウト管理**
   * デフォルト8時間ごとに自動的に再接続
   * YouTube側の長時間配信制限を回避

2. **エラー検出と再接続**
   * "Broken pipe"エラーを検出して自動再接続
   * 最大5回まで再接続を試行

3. **時刻表示の実装**
   * FFmpegのdrawtextフィルタで実時刻を表示
   * localtime形式が使用できない場合はUTC時刻にフォールバック

## トラブルシューティング

### 配信が終了時刻を過ぎても続く場合
1. ログで終了時刻が正しく設定されているか確認
2. プロセスが正常に動作しているか確認：
   ```bash
   ps aux | grep youtube_streamer
   ```
3. 必要に応じて手動で停止：
   ```bash
   # Ctrl+C または
   pkill -f youtube_streamer
   ```

### 時刻が表示されない場合
1. フォントがインストールされているか確認：
   ```bash
   ls -la /usr/share/fonts/truetype/dejavu/
   ```
2. FFmpegが時刻表示をサポートしているか確認：
   ```bash
   ffmpeg -filters | grep drawtext
   ```

### カメラが認識されない場合
```bash
# カメラデバイスの確認
ls -la /dev/video*
v4l2-ctl --list-devices

# 権限の確認
groups $USER  # video グループに所属しているか確認
```

### 音声が入力されない場合
```bash
# 音声デバイスの確認
arecord -l
# 音声なしで配信する場合
python3 youtube_streamer_reconnect.py --no-audio
```

## ログファイル
ログは`stream_logs/`ディレクトリに日付ごとに保存されます：
* `daily_stream_YYYYMMDD.log`

ログには以下の情報が記録されます：
* 配信の開始・停止時刻
* セッション時間と総配信時間
* FFmpegの進行状況（1分ごと）
* システムリソース使用状況（10分ごと）
* エラーや警告、再接続イベント

## ベストプラクティス

### YouTube Studio側の設定
1. **DVRを無効化** - 長時間配信の安定性向上
2. **低遅延モードを無効化** - バッファリングの削減
3. **配信の説明欄に配信時間を記載** - 視聴者への案内

### システム側の設定
1. **十分なストレージ容量を確保** - ログファイル用
2. **定期的な再起動をcronで設定** - 週1回程度
3. **温度監視** - 特に夏場は放熱対策を

## .gitignoreの設定
```
# ログファイル
stream_logs/
*.log

# 設定ファイル（秘密情報を含む）
config.txt
.env

# Pythonキャッシュ
__pycache__/
*.py[cod]
*$py.class

# システムファイル
.DS_Store
Thumbs.db
```

## バージョン履歴
- **v2.0.0** (2024-06-13)
  - 自動再接続機能の実装
  - 実時刻表示機能の追加
  - 長時間配信の安定性向上
  - スケジュール機能の改善

- **v1.0.0** (初版)
  - 基本的な配信機能
  - スケジュール配信
  - ログ記録

## ライセンス
MIT License

## 謝辞
このプロジェクトは、鳥の観察を愛する全ての人のために作られました。特に、長時間の観察を可能にする安定した配信システムの実現に貢献してくださった全ての方に感謝します。