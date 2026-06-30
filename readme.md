# Raspberry Pi YouTube Live Streamer

Raspberry Pi 5を使用した鳥の定点観測YouTube Live自動配信システム（第5版）

## 概要

コシアカツバメの巣をRaspberry Pi 5で定点観測し、YouTube Liveに自動配信するシステムです。YouTube Data APIによるBroadcast自動管理、Telegram Botによるスマホ操作、気象センサー連携、動体検知による訪問統計表示を備えています。

## システム構成

```
[cron 4:20]                 ← 毎朝4:20に自動起動
[Telegram Bot]              ← スマホから手動で配信開始/停止/ステータス確認
      ↓
[streamer.py]               ← 司令塔: YouTube APIでBroadcast作成、8h分割管理、DNS障害リトライ
      ↓                        異常時はTelegram通知 (notify.py)
[stream_ffmpeg.py]          ← FFmpeg配信エンジン: カメラ映像 → YouTube RTMP + UDP
      ↑ ZMQ                   RTMP瞬断時は同一配信枠で粘り強く再接続
[weather.py]                ← I2Cセンサー → ZMQで配信画面に気象情報表示（ZMQ自動再接続対応）
[bird_counter_lite.py]      ← UDP受信 → 動体検知 → visit_info.txt → 画面表示
[health_check.sh]           ← 5分ごとにCPU温度・メモリ・負荷を記録（ロガー）
```

### 各スクリプトの役割

| ファイル | 役割 |
|---|---|
| `streamer.py` | 配信制御の司令塔。YouTube APIでBroadcastを作成し、8時間ごとにセグメント分割。コアタイム（4:30-19:30）の自動管理。DNS/ネットワーク一時障害に耐えるリトライ機構搭載 |
| `stream_ffmpeg.py` | FFmpeg配信エンジン。カメラ映像にテキストオーバーレイを施し、YouTube RTMPとUDPに同時出力。ZMQによるリアルタイムテキスト更新。RTMP瞬断時は同一配信枠（URL）のまま最大3回再接続を試みる |
| `bird_counter_lite.py` | 動体検知プログラム。UDP受信したフレームを解析し、鳥の訪問を検出・記録。ローカル動画ファイルでのテスト機能付き |
| `telegram_bot.py` | Telegram Botによる配信制御。配信開始/停止、ステータス確認、ログ閲覧をスマホから操作 |
| `weather.py` | SHT30（温湿度）+ BMP180（気圧）センサーをI2Cで読み取り、ZMQでFFmpegの画面表示に送信。FFmpeg再起動時のZMQ自動再接続対応 |
| `notify.py` | Telegram通知ヘルパー。配信開始・異常終了・短時間終了3回連続などの重要イベントをスマホに通知 |
| `youtube_api.py` | YouTube Data API v3ヘルパー。認証、Broadcast作成/終了、ストリームキー取得、orphanクリーンアップ |
| `auth_setup.py` | Google OAuth初回認証スクリプト（1回だけ実行） |
| `health_check.sh` | システム状態ロガー。CPU温度・メモリ使用量・負荷・ffmpegプロセス数を5分ごとに記録 |

## 耐障害設計

### ネットワーク瞬断への対応（RTMP粘り腰再接続）

ネットワークの一時的な切断が発生した場合、従来は即座に配信枠（URL）を作り直していましたが、現在は以下のロジックで同一URLを維持します。

- **RTMP切断検知時**: 10秒待機後、同じYouTube配信枠のままFFmpegを再起動（最大3回）
- **Watchdogタイムアウト時**: 10秒クールダウン後に再起動（ALSAデバイスの解放時間を確保）
- **オーディオデバイス**: 初回検出結果をキャッシュし、再接続時のALSAプローブをスキップ（xrun緩和）
- **3回連続失敗時**: 復旧を断念し、親プロセスが新しい配信枠を作成して配信を継続

### DNS/API障害への対応

`streamer.py` はYouTube APIの全呼び出しを `with_retry()` でラップしており、一時的なDNS解決失敗やAPI障害に対して最大5回（30秒間隔）のリトライを行います。

### 異常通知

配信の開始・異常終了・短時間終了3回連続などの重要イベントは、`notify.py` 経由でTelegramに通知されます。

## 運用環境の構成

### プロセス管理

本システムでは、各プロセスを以下の方法で管理しています。

| プロセス | 管理方法 | 備考 |
|---|---|---|
| `streamer.py` | cron（毎朝4:20） | 配信時間中のみ稼働。終了後は自動終了 |
| `telegram_bot.py` | cron `@reboot` | 起動時に自動開始、常駐 |
| `weather.py` | systemd service | 常駐デーモン、障害時は自動再起動 |
| `health_check.sh` | cron `*/5` | 5分ごとにシステム状態を記録 |
| `bird_counter_lite.py` | 手動 | 配信中に別ターミナルで実行 |

### crontab の設定

```cron
# 毎日4:20に起動 → 4:30-19:30自動配信
20 4 * * * cd /home/pi/bird-watching-youtube-streamer && python3 -u streamer.py >> stream_logs/cron.log 2>&1

# 5分ごとにシステム状態を記録
*/5 * * * * /home/pi/health_check.sh

# 起動時にTelegram Botを自動開始
@reboot cd /home/pi/bird-watching-youtube-streamer && python3 -u telegram_bot.py >> stream_logs/telegram_bot.log 2>&1
```

### systemd サービス（weather.py）

`/etc/systemd/system/weather.service`:

```ini
[Unit]
Description=Weather sensor (SHT30/BMP180) to FFmpeg ZMQ
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/bird-watching-youtube-streamer
ExecStart=/usr/bin/python3 /home/pi/bird-watching-youtube-streamer/weather.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# サービスの有効化（初回のみ）
sudo systemctl enable weather.service
sudo systemctl start weather.service

# 状態確認
sudo systemctl status weather.service
sudo journalctl -u weather.service --since "1 hour ago"
```

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
- OpenCV (cv2)、numpy
- google-api-python-client、google-auth（YouTube API用）
- python-telegram-bot（Telegram Bot用）
- smbus2、pyzmq（センサー・ZMQ連携用）

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

```
STREAM_KEY=your-youtube-stream-key
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
TELEGRAM_CHAT_ID=your-telegram-chat-id
```

### 2. テキストファイルの準備

```bash
cp stream.txt.example stream.txt
cp topic.txt.example topic.txt
```

### 3. YouTube API認証（初回のみ）

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. YouTube Data API v3 を有効化
3. OAuth 2.0 クライアントIDを作成し、`credentials/client_secret.json` として保存
4. 初回認証を実行：

```bash
python3 auth_setup.py
```

### 4. broadcast_config.json の作成

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

### 5. 配信画面のテキスト表示

```
┌──────────────────────────────────────────┐
│ [トピック] topic.txt     [登録] stream.txt│
│ [訪問情報] visit_info.txt（自動更新）     │
│                                          │
│            [カメラ映像エリア]              │
│                                          │
│ [気象] weather.py→ZMQ       [時刻] 自動  │
└──────────────────────────────────────────┘
```

- `topic.txt` — 配信のメインタイトル（手動編集）
- `stream.txt` — 視聴者向けメッセージ（手動編集）
- `visit_info.txt` — 動体検知結果（自動生成）
- 気象情報 — weather.py が ZMQ 経由で直接更新（ファイル不要）

## 通常運用

### 自動配信（推奨）

cronで毎朝4:20に `streamer.py` が起動し、4:30〜19:30の配信を自動管理します。8時間ごとにセグメント分割され、19:30に自動終了します。異常が発生した場合はTelegramに通知が届きます。

### 手動配信（Telegram Bot経由）

1. Telegramで Bot に `/start` を送信
2. コントロールパネルが表示される
3. 「🚀 配信開始」ボタンで即座に配信開始
4. 「⏹ 停止」ボタンで停止

コアタイム内なら19:30まで自動継続、コアタイム外なら8時間で自動停止します。

### 手動配信（ターミナル）

```bash
# 即座に配信開始
python3 streamer.py --now
```

### VNCリモートデスクトップでの運用

各プログラムをフォアグラウンドの専用ターミナルで実行します。ログが直接見えるため、障害時の調査・対応が容易です。

```
[ターミナル1] 作業用（ログ確認、設定変更など）
[ターミナル2] python3 bird_counter_lite.py ← 動体検知（配信中）
[ターミナル3] tail -f stream_logs/streamer_$(date +%Y%m%d).log ← ログ監視
```

※ `telegram_bot.py` は `@reboot` cron、`weather.py` は systemd で自動起動するため、手動でターミナルを開く必要はありません。

## 動体検知

### 基本操作

```bash
# UDP受信で動体検知（配信中に別ターミナルで実行）
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
| セグメント分割 | 8時間ごと（YouTube 12h制限対策） |
| テキスト更新 | ZMQ + textfile reload（映像中断なし） |

### UDP出力

| 項目 | 値 |
|---|---|
| プロトコル | UDP/TS over IP |
| ポート | 1234 |
| アドレス | localhost |
| パケットサイズ | 1316バイト |

### 気象センサー

| センサー | I2Cアドレス | 測定項目 |
|---|---|---|
| SHT30 | 0x44 | 温度・湿度 |
| BMP180 | 0x77 | 温度・気圧 |

更新間隔10秒、ZMQ (tcp://127.0.0.1:5555) 経由でFFmpegの画面表示に反映。ZMQ送信が5回連続失敗すると自動的にソケットを再作成して再接続。

## トラブルシューティング

### 配信が始まらない

```bash
# ステータス確認
cat stream_status.json

# ログ確認
tail -20 stream_logs/streamer_$(date +%Y%m%d).log

# orphan broadcastのクリーンアップ
python3 -c "import youtube_api; yt=youtube_api.get_youtube_service(); youtube_api.cleanup_orphans(yt)"
```

### 気象データが表示されない（`--°C --%` のまま）

weather.py の ZMQ 接続が古いFFmpegプロセスに向いている可能性があります（FFmpeg再起動後に発生）。通常は自動再接続されますが、即座に解消するには：

```bash
# weather.py を再起動
sudo systemctl restart weather.service

# 確認
sudo journalctl -u weather.service --since "1 min ago"
```

### 動体検知が反応しない

```bash
# ローカルファイルでテスト
python3 bird_counter_lite.py --file test.mp4 --show --debug

# ROI・感度調整
python3 bird_counter_lite.py --file test.mp4 --roi 30,620,580,70 --threshold 3
```

### システム状態の確認

```bash
# CPU温度・負荷の履歴
tail -20 stream_logs/health_$(date +%Y%m).log

# 全プロセスの状態
ps aux | grep -E "streamer|ffmpeg|telegram|weather" | grep -v grep

# weather.py のステータス
sudo systemctl status weather.service
```

### プロセスの停止

```bash
# Telegram Bot から停止（推奨）
# → Bot の「⏹ 停止」ボタン

# ターミナルから停止
kill $(cat streamer.pid)
pkill -f 'ffmpeg.*rtmp'
```

## ログファイル

| ログ | 場所 | 内容 |
|---|---|---|
| 親プロセス | `stream_logs/streamer_YYYYMMDD.log` | Broadcast作成/終了、セグメント管理 |
| FFmpeg stderr | `stream_logs/ffmpeg_stderr_YYYYMMDD_HHMMSS.log` | FFmpegのエンコード状況、RTMP/ALSAエラー |
| cron出力 | `stream_logs/cron.log` | cronからの起動ログ |
| システム状態 | `stream_logs/health_YYYYMM.log` | CPU温度、メモリ、負荷（5分間隔） |
| weather.py | `journalctl -u weather.service` | 気象データ読み取り、ZMQ送信状況 |

## ファイル構成

```
bird-watching-youtube-streamer/
├── streamer.py              # 配信制御（司令塔）v4
├── stream_ffmpeg.py         # FFmpeg配信エンジン（RTMP粘り腰再接続対応）
├── bird_counter_lite.py     # 動体検知
├── telegram_bot.py          # Telegram Bot
├── weather.py               # 気象センサー（ZMQ自動再接続対応）
├── notify.py                # Telegram通知ヘルパー
├── youtube_api.py           # YouTube API ヘルパー
├── auth_setup.py            # OAuth初回認証
├── stream.txt.example       # 表示テキストのサンプル
├── topic.txt.example        # トピックテキストのサンプル
├── readme.md
├── broadcast_config.json    # 配信設定（※.gitignore）
├── config.txt               # 秘密情報（※.gitignore）
├── credentials/             # OAuth認証情報（※.gitignore）
└── stream_logs/             # ログディレクトリ（※.gitignore）
```

## バージョン履歴

- **v5.1.0** (2026-07) - RTMP瞬断時の粘り腰再接続、watchdogクールダウン、weather.py ZMQ自動再接続、Telegram異常通知(notify.py)
- **v5.0.0** (2026-06) - YouTube API自動管理、Telegram Bot、気象センサー連携、セキュリティ整備
- **v4.1.0** - テキスト更新時の映像スキップ解決、動体検知精度向上
- **v4.0.0** - UDP出力・動体検知連携機能追加
- **v3.1.0** - カスタムテキスト表示機能追加
- **v3.0.0** - 診断機能追加、プロセス管理強化
- **v2.0.0** - 自動再接続、実時刻表示
- **v1.0.0** - 基本機能

## ライセンス

MIT License