"""Telegram Bot v3 - ツバメ配信コントローラー"""
import os
import subprocess
import signal
import logging
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes


def _load_config(path='config.txt'):
    cfg = {}
    with open(path, 'r') as f:
        for line in f:
            if '=' in line and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                cfg[k.strip()] = v.strip()
    return cfg

_cfg = _load_config()
BOT_TOKEN = _cfg['TELEGRAM_BOT_TOKEN']
ALLOWED_CHAT_ID = int(_cfg['TELEGRAM_CHAT_ID'])
WORK_DIR = Path('/home/pi/bird-watching-youtube-streamer')
STATUS_FILE = WORK_DIR / 'stream_status.json'
PID_FILE = WORK_DIR / 'streamer.pid'
LOG_DIR = WORK_DIR / 'stream_logs'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

def get_status():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return 'running', pid
        except (OSError, ValueError):
            PID_FILE.unlink(missing_ok=True)
    r = subprocess.run(['pgrep', '-f', 'ffmpeg.*rtmp'], capture_output=True, text=True)
    if r.stdout.strip():
        return 'orphan', None
    return 'stopped', None
def get_status_text():
    status, _ = get_status()
    if status == 'running':
        try:
            import json
            s = json.loads(STATUS_FILE.read_text())
            split = s.get('segment_end') or '不明'
            end = s.get('final_end') or '手動停止まで'
            return f'🟢 配信中\n  次の分割予定: {split}\n  配信終了予定: {end}'
        except Exception:
            return '🟢 配信中'
    elif status == 'orphan':
        return '🟡 ffmpeg稼働中（Bot外）'
    return '⚫ 停止中'
def get_keyboard():
    status, _ = get_status()
    icons = {'running': '🟢 配信中', 'orphan': '🟡 ffmpeg稼働中', 'stopped': '⚫ 停止中'}
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📊 {icons[status]}", callback_data='status')],
        [InlineKeyboardButton("🚀 配信開始", callback_data='start'),
         InlineKeyboardButton("⏹ 停止", callback_data='stop')],
        [InlineKeyboardButton("⏰ 自動予約確認", callback_data='cron'),
         InlineKeyboardButton("📋 ログ", callback_data='log')],
    ])

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
#    await update.message.reply_text('🐦 ツバメ配信コントローラー', reply_markup=get_keyboard())
    await update.message.reply_text(f'🐦 ツバメ配信コントローラー\n\n{get_status_text()}', reply_markup=get_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.message.chat.id != ALLOWED_CHAT_ID:
        return
    await query.answer()

    try:
        if query.data == 'start':
            status, _ = get_status()
            if status != 'stopped':
                await query.edit_message_text('⚠️ 既に配信中です（自動/手動問わず）', reply_markup=get_keyboard())
                return
            LOG_DIR.mkdir(exist_ok=True)
            subprocess.Popen(
                ['nohup', 'python3', str(WORK_DIR / 'streamer.py'), '--now'],
                cwd=str(WORK_DIR),
                stdout=open(LOG_DIR / 'bot_launch.log', 'a'),
                stderr=subprocess.STDOUT,
                start_new_session=True
            )
            await query.edit_message_text('🚀 手動配信を開始しました\n10時間ごとに自動分割されます', reply_markup=get_keyboard())

        elif query.data == 'stop':
            status, pid = get_status()
            if status == 'stopped':
                await query.edit_message_text('⚫ 配信は動いていません', reply_markup=get_keyboard())
                return
            if pid:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            subprocess.run(['pkill', '-f', 'ffmpeg.*rtmp'])
            PID_FILE.unlink(missing_ok=True)
            try:
                import youtube_api
                yt = youtube_api.get_youtube_service()
                res = yt.liveBroadcasts().list(part='status', mine=True).execute()
                for b in res.get('items', []):
                    if b['status']['lifeCycleStatus'] in ('live', 'ready', 'testing'):
                        youtube_api.end_broadcast(yt, b['id'])
            except Exception as e:
                log.warning(f'Broadcast終了エラー: {e}')
            await query.edit_message_text('⏹ 配信を停止しました', reply_markup=get_keyboard())

        elif query.data == 'cron':
            result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
            cron_lines = [l for l in result.stdout.split('\n') if 'streamer' in l and not l.startswith('#')]
            if cron_lines:
                msg = '⏰ 自動予約:\n```\n' + '\n'.join(cron_lines) + '\n```'
            else:
                msg = '⏰ 自動予約は設定されていません'
            await query.edit_message_text(msg, parse_mode='Markdown', reply_markup=get_keyboard())

        elif query.data == 'log':
            log_files = sorted(LOG_DIR.glob('streamer_*.log'))
            if log_files:
                lines = log_files[-1].read_text(encoding='utf-8').strip().split('\n')
                tail = '\n'.join(lines[-10:])
                await query.edit_message_text(f'📋 ログ:\n```\n{tail}\n```',
                    parse_mode='Markdown', reply_markup=get_keyboard())
            else:
                await query.edit_message_text('📋 ログなし', reply_markup=get_keyboard())

        elif query.data == 'status':
       #     await query.edit_message_text('🐦 ツバメ配信コントローラー', reply_markup=get_keyboard())
            await query.edit_message_text(f'🐦 ツバメ配信コントローラー\n\n{get_status_text()}', reply_markup=get_keyboard())

    except Exception as e:
        log.error(f'エラー: {e}')

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))
    log.info('Bot起動中...')
    app.run_polling()

if __name__ == '__main__':
    main()