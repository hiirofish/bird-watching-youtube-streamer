"""配信制御スクリプト v4 - 統合モード + 状態ファイル + リトライ + Telegram通知

v4の変更点:
  - ネットワーク/DNS一時障害に耐えるリトライ (with_retry)
  - 異常終了・配信開始をTelegramに通知 (notify.py)
  これにより 'Temporary failure in name resolution' のような一時障害で
  1日分の配信を失う事故を防ぐ。
"""
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
import notify

WORK_DIR = Path(__file__).parent
PID_FILE = WORK_DIR / 'streamer.pid'
STATUS_FILE = WORK_DIR / 'stream_status.json'
LOG_DIR = WORK_DIR / 'stream_logs'
STREAM_SCRIPT = WORK_DIR / 'stream_ffmpeg.py'
CONFIG_FILE = WORK_DIR / 'broadcast_config.json'

# Broadcast time settings
CORE_START = '4:30'
CORE_END = '19:30'
MAX_SEGMENT_HOURS = 8  # Max 8 hours per segment to avoid 12h limit

# Retry settings for transient network/DNS failures
RETRY_ATTEMPTS = 5
RETRY_WAIT_SEC = 30

logging.getLogger('googleapiclient').setLevel(logging.ERROR)
logging.getLogger('google').setLevel(logging.ERROR)
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"streamer_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO, format='%(asctime)s %(message)s',
    handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)


def with_retry(fn, attempts=RETRY_ATTEMPTS, wait=RETRY_WAIT_SEC, label=''):
    """Call fn() with retries to survive transient DNS/network failures.

    Retries on ANY exception (DNS errors surface as gaierror/ServerNotFoundError
    deep inside httplib2, so we cannot narrow the type cleanly). Raises
    RuntimeError only after all attempts are exhausted.
    """
    last_err = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            log.warning(f'{label} 失敗 (試行 {i}/{attempts}): {e}')
            if i < attempts:
                time.sleep(wait)
    raise RuntimeError(f'{label} が {attempts} 回の試行後に失敗: {last_err}')


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

    All YouTube API setup calls are wrapped in with_retry so a transient DNS
    blip at segment boundary (the v3 failure mode) no longer kills the day.
    """
    now = datetime.now()
    # --- API setup with retry (this block previously died on the first DNS error) ---
    yt = with_retry(youtube_api.get_youtube_service, label='get_youtube_service')
    stream = with_retry(lambda: youtube_api.get_or_create_stream(yt), label='get_or_create_stream')
    _, stream_key = youtube_api.get_stream_key(stream)
    bc = with_retry(lambda: youtube_api.create_broadcast(yt, config_file=str(CONFIG_FILE)),
                    label='create_broadcast')
    broadcast_id = bc['id']
    with_retry(lambda: youtube_api.bind_broadcast_to_stream(yt, broadcast_id, stream['id']),
               label='bind_broadcast_to_stream')
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
        # Always close the broadcast we opened so it cannot become an orphan.
        # end_broadcast already swallows its own errors, but wrap in retry too
        # so a DNS blip here does not leave an orphan holding the stream key.
        try:
            with_retry(lambda: youtube_api.end_broadcast(yt, broadcast_id),
                       attempts=3, label='end_broadcast')
        except Exception as e:
            log.error(f'Broadcast終了に失敗（孤立の可能性）: {e}')
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

        # run_segment can now raise (retries exhausted). Treat as a short-fail
        # rather than crashing the whole session, so we keep trying / notify.
        try:
            dur = run_segment(seg_end, mode)
        except Exception as e:
            log.error(f'セグメント開始に失敗（リトライ枯渇）: {e}')
            notify.send(f'⚠️ 配信セグメント開始に失敗（API/ネット障害の可能性）\n{e}')
            dur = 0

        # Early-failure guard: if ffmpeg never really ran (died within 2 min),
        # do not churn out dozens of dead broadcasts like the zmq-broken morning.
        if dur < 120:
            short_fail += 1
            log.warning(f'セグメントが約{int(dur)}秒で終了（失敗の可能性 {short_fail}/3）')
            if short_fail >= 3:
                log.error('短時間終了が3回連続。配信を中止します（原因を確認してください）')
                notify.send('🔴 配信を中止しました\n短時間終了が3回連続。原因の確認が必要です。')
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


def _kill_ffmpeg():
    """Kill any lingering ffmpeg processes to prevent zombie accumulation."""
    try:
        subprocess.run(['pkill', '-f', 'ffmpeg.*rtmp'], timeout=5)
    except Exception:
        pass

def _safe_cleanup(reason=''):
    """Best-effort orphan sweep; never raises."""
    _kill_ffmpeg()
    try:
        yt = with_retry(youtube_api.get_youtube_service, attempts=3, label='cleanup get_service')
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
            notify.send('🟢 手動配信を開始しました')
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
            notify.send(f'🟢 自動配信を開始しました ({CORE_START}-{CORE_END})')
            stream_session('auto')
    except (KeyboardInterrupt, SystemExit):
        log.info('中断されました')
    except Exception as e:
        # Catch-all so an unexpected crash still notifies the phone.
        log.error(f'予期しないエラーで終了: {e}')
        notify.send(f'🔴 配信制御が予期しないエラーで終了しました\n{e}')
    finally:
        # Close anything we may have left open (e.g. on SIGTERM mid-segment)
        _safe_cleanup('shutdown')
        PID_FILE.unlink(missing_ok=True)
        STATUS_FILE.unlink(missing_ok=True)
        log.info('配信制御終了')


if __name__ == '__main__':
    main()