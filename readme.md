# Raspberry Pi YouTube Live Streamer

Raspberry Pi 5を使用した鳥の定点観測YouTube Live自動配信システム（第5版）

## 概要

コシアカツバメの巣をRaspberry Pi 5で24時間定点観測し、YouTube Liveに自動配信するシステムです。YouTube Data APIによるBroadcast自動管理、Telegram Botによるスマホ操作、気象センサー連携、動体検知による訪問統計表示など、運用の自動化を重視した構成になっています。

## システム構成

```
[Telegram Bot]          ← スマホから配信開始/停止/ステータス確認
      ↓
[streamer.py]           ← 司令塔: YouTube APIでBroadcast作成、8h分割管理
      ↓
[stream_ffmpeg.py]      ← FFmpeg配信エンジン: カメラ映像 → YouTube RTMP + UDP
      ↓                        ↑
[bird_counter_lite.py]  ← UDP受信 → 動体検知 → visit_info.txt 更新
[weather.py]            ← I2Cセンサー → ZMQで配信画面に気象情報表示
```

### 各スクリプトの役割

| ファイル | 役割 |
|---|---|
| `streamer.py` | 配信制御の司令塔。YouTube APIでBroadcastを作成し、8時間ごとにセグメント分割。コアタイム（5:00-19:00）の自動管理 |
| `stream_ffmpeg.py` | FFmpeg配信エンジン。カメラ映像にテキストオーバーレイを施し、YouTube RTMPとUDPに同時出力。ZMQによるリアルタイムテキスト更新対応 |
| `bird_counter_lite.py` | 動体検知プログラム。UDP受信したフレームを解析し、鳥の訪問を検出・記録。ローカル動画ファイルでのテスト機能付き |
| `telegram_bot.py` | Telegram Botによる配信制御。配信開始/停止、ステータス確認、ログ閲覧、cron確認をスマホから操作 |
| `weather.py` | SHT30（温湿度）+ BMP180（気圧）センサーをI2Cで読み取り、ZMQでFFmpegの画面表示に送信 |
| `youtube_api.py` | YouTube Data API v3ヘルパー。認証、Broadcast作成/終了、ストリームキー取得、orphanクリーンアップ |
| `auth_setup.py` | Google OAuth初回認証スクリプト（1回だけ実行） |

## 主な特徴

- **YouTube API自動管理**: Broadcastの作成・紐付け・終了をAPIで自動化。orphanブロードキャストの自動クリーンアップ
- **8時間セグメント分割**: YouTubeの12時間制限に対応。8時間ごとに新しいBroadcastを自動作成
- **Telegram Bot操作**: スマホから配信開始/停止/ステータス確認が可能
- **気象情報表示**: 温度・湿度・気圧をリアルタイムで配信画面に表示（ZMQ経由、映像中断なし）
- **動体検知**: UDP出力映像をフレーム差分解析し、鳥の訪問を自動カウント
- **スムーズなテキスト更新**: FFmpegのtextfile reload機能とZMQで映像を途切れさせずに画面更新
- **Watchdog**: FFmpegハング検知による自動復旧
- **ローカルテスト**: 録画ファイルで動体検知パラメータを事前テスト可能

## 必要な環境

### ハードウェア

- Raspberry Pi 5 (8GB推奨)
- Logitech C270 HD Webcam（または互換性のあるUSBカメラ）
- SHT30 温湿度センサー（I2C）
- BMP180 気圧センサー（I2C）
- 安定したインターネット接続

### ソフトウェア

- Raspberry Pi OS (64-bit)
- Python 3.x
- FFmpeg 4.2以上（textfile reload + ZMQ対応）
- OpenCV (cv2)
- numpy
- google-api-python-client, google-auth（YouTube API用）
- python-telegram-bot（Telegram Bot用）
- smbus2, pyzmq（センサー・ZMQ連携用）

## インストール

```bash
# System packages
sudo apt update && sudo apt upgrade -y
sudo apt install ffmpeg python3-pip fonts-dejavu-core fonts-noto-cjk -y

# FFmpeg version check (4.2+ required)
ffmpeg -version

# Python packages
pip3 install opencv-python numpy
pip3 install google-api-python-client google-auth google-auth-oauthlib
pip3 install python-telegram-bot
pip3 install smbus2 pyzmq

# Clone repository
git clone https://github.com/hiirofish/bird-watching-youtube-streamer.git
cd bird-watching-youtube-streamer
```

## 設定

### 1. config.txt の作成

秘密情報はすべて `config.txt` で管理します（.gitignore対象）。

```bash
cp stream.txt.example stream.txt
cp topic.txt.example topic.txt
```

```
STREAM_KEY=your-youtube-stream-key
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-telegram-chat-id
```

### 2. YouTube API認証（初回のみ）

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. YouTube Data API v3 を有効化
3. OAuth 2.0 クライアントIDを作成し、`credentials/client_secret.json` として保存
4. 初回認証を実行：

```bash
python3 auth_setup.py
```

### 3. broadcast_config.json の作成

配信タイトルや説明文を設定します（.gitignore対象）。

```json
{
  "title": "【LIVE】配信タイトル",
  "description": "配信の説明文",
  "category_id": "15",
  "privacy": "public",
  "language": "ja"
}
```

### 4. 表示テキストの設定

配信画面のテキスト表示：

```
┌──────────────────────────────────────┐
│ [トピック] topic.txt        [登録お願い] stream.txt │
│ [訪問情報] visit_info.txt（自動更新）               │
│                                                      │
│             [カメラ映像エリア]                         │
│                                                      │
│ [気象情報] weather.py→ZMQ     [時刻] 自動生成        │
└──────────────────────────────────────┘
```

- `topic.txt` — 配信のメインタイトル（手動編集）
- `stream.txt` — 視聴者向けメッセージ（手動編集）
- `visit_info.txt` — 動体検知結果（自動生成）
- 気象情報 — weather.py が ZMQ 経由で直接更新（ファイル不要）

## 使用方法

### 自動配信（cron推奨）

```bash
# crontab -e に追加（毎日4:55に起動、5:00-19:00配信）
55 4 * * * cd /home/pi/bird-watching-youtube-streamer && python3 streamer.py >> stream_logs/cron.log 2>&1
```

### 手動配信

```bash
# 即座に配信開始（コアタイム内なら19:00まで、外なら8時間）
python3 streamer.py --now
```

### Telegram Bot

```bash
# Bot起動（常駐）
python3 telegram_bot.py
```

Botコマンド: `/start` でコントロールパネル表示。配信開始/停止/ステータス/ログをボタン操作。

### 動体検知（別ターミナル）

```bash
# UDP受信で動体検知
python3 bird_counter_lite.py

# デバッグモード
python3 bird_counter_lite.py --debug

# ローカル動画でテスト
python3 bird_counter_lite.py --file recording.mp4 --show --debug

# ROI・感度調整
python3 bird_counter_lite.py --roi 30,620,580,70 --threshold 3

# 訪問情報リセット
python3 bird_counter_lite.py --reset
```

### 気象センサー（別ターミナル）

```bash
python3 weather.py
```

## 動体検知の詳細

### 検知方式

フレーム差分 + 連結成分解析による4要素スコアリング：

| 要素 | スコア | 閾値 |
|---|---|---|
| 動き検出 | +1 | 5%以上の変化 |
| 面積変化 | +2 | 8%以上の変化 |
| 暗さ検出 | +2 | 30%以上が暗い |
| 大きな物体 | +2 | 300px以上 |

合計スコアが閾値（デフォルト4）以上で「訪問」と判定。

### 出力ファイル

- `visit_info.txt` — 最新の統計情報（JSON、配信画面に表示）
- `visit_history.log` — 全訪問履歴（JSONL形式、後から分析可能）
- `count.txt` — 訪問回数のみ（外部参照用）

## 技術仕様

### 配信設定

| 項目 | 値 |
|---|---|
| 映像入力 | 1280x720 → 720x720クロップ |
| フレームレート | 30fps |
| ビットレート | 1200kbps |
| エンコーダー | libx264 (ultrafast) |
| 音声 | AAC 128kbps |
| セグメント分割 | 8時間ごと |
| テキスト更新 | ZMQ + textfile reload |

### UDP出力

| 項目 | 値 |
|---|---|
| プロトコル | UDP/TS over IP |
| ポート | 1234 |
| アドレス | localhost |
| パケットサイズ | 1316バイト |

### 気象センサー

| センサー | 接続 | 測定項目 |
|---|---|---|
| SHT30 (0x44) | I2C | 温度・湿度 |
| BMP180 (0x77) | I2C | 温度・気圧 |
| 更新間隔 | 10秒 | ZMQ経由で配信画面に表示 |

## トラブルシューティング

### 配信が始まらない

```bash
# streamer.pyのステータス確認
cat stream_status.json

# ログ確認
tail -20 stream_logs/streamer_$(date +%Y%m%d).log

# orphan broadcastのクリーンアップ
python3 -c "import youtube_api; yt=youtube_api.get_youtube_service(); youtube_api.cleanup_orphans(yt)"
```

### 動体検知が反応しない

```bash
# ローカルファイルでテスト
python3 bird_counter_lite.py --file test.mp4 --show --debug

# ROI・感度調整
python3 bird_counter_lite.py --file test.mp4 --roi 30,620,580,70 --threshold 3
```

### 気象データが表示されない

```bash
# I2Cデバイス確認
i2cdetect -y 1

# ZMQポート確認
ss -tulpn | grep 5555
```

## ファイル構成

```
bird-watching-youtube-streamer/
├── streamer.py              # 配信制御（司令塔）
├── stream_ffmpeg.py         # FFmpeg配信エンジン
├── bird_counter_lite.py     # 動体検知
├── telegram_bot.py          # Telegram Bot
├── weather.py               # 気象センサー
├── youtube_api.py           # YouTube API ヘルパー
├── auth_setup.py            # OAuth初回認証
├── pkill.sh                 # プロセス停止スクリプト
├── broadcast_config.json    # 配信設定（.gitignore）
├── config.txt               # 秘密情報（.gitignore）
├── credentials/             # OAuth認証情報（.gitignore）
├── stream.txt.example       # 表示テキストのサンプル
├── topic.txt.example        # トピックテキストのサンプル
├── stream_logs/             # ログディレクトリ（.gitignore）
└── readme.md
```

## バージョン履歴

- **v5.0.0** (2025-09) - YouTube API自動管理、Telegram Bot、気象センサー連携、セキュリティ整備
- **v4.1.0** (2024-08) - テキスト更新時の映像スキップ解決、動体検知精度向上
- **v4.0.0** (2024-08) - UDP出力・動体検知連携機能追加
- **v3.1.0** (2025-08) - カスタムテキスト表示機能追加
- **v3.0.0** (2025-06) - 診断機能追加、プロセス管理強化
- **v2.0.0** (2024-06) - 自動再接続、実時刻表示
- **v1.0.0** - 基本機能

## ライセンス

MIT License