"""配信制御スクリプト v3 - 統合モード + 状態ファイル"""
import os
import sys
import subprocess
import time
import json
import signal
import logging
from datetime import datetime, timedelta
from pathlib import Path
import youtube_api

WORK_DIR = Path(__file__).parent
PID_FILE = WORK_DIR / 'streamer.pid'
STATUS_FILE = WORK_DIR / 'stream_status.json'
LOG_DIR = WORK_DIR / 'stream_logs'
STREAM_SCRIPT = WORK_DIR / 'stream_ffmpeg.py'
CONFIG_FILE = WORK_DIR / 'broadcast_config.json'

# Broadcast time settings
CORE_START = '5:00'
CORE_END = '19:00'
MAX_SEGMENT_HOURS = 8  # Max 8 hours per segment to avoid 12h limit

logging.getLogger('googleapiclient').setLevel(logging.ERROR)
logging.getLogger('google').setLevel(logging.ERROR)
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"streamer_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO, format='%(asctime)s %(message)s',
    handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

def is_already_running():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            pass
    PID_FILE.write_text(str(os.getpid()))
    return False

def write_status(segment_end, final_end, broadcast_id, mode):
    STATUS_FILE.write_text(json.dumps({
        'segment_end': segment_end.strftime('%H:%M') if segment_end else None,
        'final_end': final_end.strftime('%H:%M') if final_end else None,
        'broadcast_id': broadcast_id,
        'mode': mode,
        'updated': datetime.now().strftime('%H:%M:%S'),
    }))

def core_window(now):
    """Return (core_start_dt, core_end_dt) for the day of `now`."""
    sh, sm = map(int, CORE_START.split(':'))
    eh, em = map(int, CORE_END.split(':'))
    cs = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    ce = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return cs, ce

def run_segment(seg_end, mode):
    """Run ONE broadcast segment until seg_end. Returns actual run duration (sec).

    The caller (stream_session) decides whether another segment follows; this
    function just owns the lifecycle of a single YouTube broadcast.
    """
    now = datetime.now()
    yt = youtube_api.get_youtube_service()
    stream = youtube_api.get_or_create_stream(yt)
    _, stream_key = youtube_api.get_stream_key(stream)
    bc = youtube_api.create_broadcast(yt, config_file=str(CONFIG_FILE))
    broadcast_id = bc['id']
    youtube_api.bind_broadcast_to_stream(yt, broadcast_id, stream['id'])
    log.info(f'Broadcast開始: {broadcast_id} (終了予定 {seg_end.strftime("%H:%M")})')

    write_status(seg_end, None, broadcast_id, mode)

    env = os.environ.copy()
    env['YOUTUBE_STREAM_KEY'] = stream_key
    started = time.time()
    try:
        process = subprocess.Popen(
            ['python3', str(STREAM_SCRIPT), now.strftime('%H:%M'), seg_end.strftime('%H:%M')],
            env=env, cwd=str(WORK_DIR)
        )
        process.wait()
    except Exception as e:
        log.error(f'ffmpegエラー: {e}')
    finally:
        # Always close the broadcast we opened so it cannot become an orphan
        youtube_api.end_broadcast(yt, broadcast_id)
        log.info(f'Broadcast終了: {broadcast_id}')
    return time.time() - started

def stream_session(mode):
    """Run segments according to core-time rules. Each segment <= MAX_SEGMENT_HOURS.

    Rules:
      - In core hours (5:00-19:00): split at 8h, keep going until 19:00.
      - Before 5:00: run up to 8h; once it rolls into core hours it keeps
        going until 19:00 (re-evaluated each segment by current time).
      - After 19:00 (manual test): one 8h block, then stop.
    """
    short_fail = 0
    while True:
        now = datetime.now()
        _, ce = core_window(now)
        cap = now + timedelta(hours=MAX_SEGMENT_HOURS)
        after_core = now >= ce

        # Never exceed 8h; in/before core also never run past today's 19:00
        seg_end = cap if after_core else min(cap, ce)

        log.info(f'セグメント: {now.strftime("%H:%M")} → {seg_end.strftime("%H:%M")} (mode={mode})')
        dur = run_segment(seg_end, mode)

        # Early-failure guard: if ffmpeg never really ran (died within 2 min),
        # do not churn out dozens of dead broadcasts like the zmq-broken morning.
        if dur < 120:
            short_fail += 1
            log.warning(f'セグメントが約{int(dur)}秒で終了（失敗の可能性 {short_fail}/3）')
            if short_fail >= 3:
                log.error('短時間終了が3回連続。配信を中止します（原因を確認してください）')
                break
            time.sleep(10)
            continue
        short_fail = 0

        if after_core:
            break  # single 8h block (manual, outside core hours)
        if datetime.now() >= ce - timedelta(minutes=3):
            break  # reached today's 19:00
        log.info('8時間分割のため次のセグメントを作成...')
        time.sleep(5)

def _safe_cleanup(reason=''):
    """Best-effort orphan sweep; never raises."""
    try:
        yt = youtube_api.get_youtube_service()
        youtube_api.cleanup_orphans(yt)
    except Exception as e:
        log.warning(f'クリーンアップ失敗{(" ("+reason+")") if reason else ""}: {e}')

def _on_sigterm(*_):
    # Telegram stop sends SIGTERM; turn it into SystemExit so `finally` runs
    # and the in-flight broadcast gets cleaned up instead of orphaned.
    raise SystemExit(0)

def main():
    if is_already_running():
        print('既に配信プロセスが実行中です')
        sys.exit(1)

    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        # Free the stream key from any leftover broadcast BEFORE we start.
        # This is what prevents an old orphan from swallowing the whole day.
        _safe_cleanup('startup')

        now = datetime.now()
        cs, ce = core_window(now)

        if '--now' in sys.argv:
            if now >= ce:
                log.info('=== 手動配信開始（コアタイム外: 8時間で自動停止）===')
            elif now < cs:
                log.info('=== 手動配信開始（5:00前: 8時間→コアタイムに合流し19:00まで）===')
            else:
                log.info(f'=== 手動配信開始（コアタイム内: {CORE_END}まで継続）===')
            stream_session('manual')
        else:
            if now >= ce:
                log.info('終了時刻を過ぎています')
                return
            if now < cs:
                wait = (cs - now).total_seconds()
                log.info(f'開始時刻まで待機 ({int(wait // 60)}分)')
                time.sleep(wait)
            log.info(f'=== 自動配信開始 ({CORE_START}-{CORE_END}) ===')
            stream_session('auto')
    except (KeyboardInterrupt, SystemExit):
        log.info('中断されました')
    finally:
        # Close anything we may have left open (e.g. on SIGTERM mid-segment)
        _safe_cleanup('shutdown')
        PID_FILE.unlink(missing_ok=True)
        STATUS_FILE.unlink(missing_ok=True)
        log.info('配信制御終了')

if __name__ == '__main__':
    main()