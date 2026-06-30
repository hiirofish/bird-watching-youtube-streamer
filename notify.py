"""notify.py - lightweight one-shot Telegram sender (no polling).

Used by streamer.py to push alerts (start / abnormal exit / give-up) to the
phone. Intentionally dependency-light and failure-proof: a notification must
NEVER crash or delay the broadcast controller.
"""
import logging
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_FILE = Path(__file__).parent / 'config.txt'


def _load_config(path=CONFIG_FILE):
    cfg = {}
    try:
        with open(path) as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    cfg[k.strip()] = v.strip()
    except Exception as e:
        log.warning(f'notify: config load failed: {e}')
    return cfg


def send(text):
    """Send a Telegram message. Returns True on success, False otherwise.

    Uses urllib (stdlib) so there is no extra dependency. Times out fast so a
    dead network can never stall the streamer.
    """
    try:
        c = _load_config()
        token = c.get('TELEGRAM_BOT_TOKEN')
        chat_id = c.get('TELEGRAM_CHAT_ID')
        if not token or not chat_id:
            log.warning('notify: token/chat_id missing in config.txt')
            return False
        url = f'https://api.telegram.org/bot{token}/sendMessage'
        data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text}).encode()
        with urllib.request.urlopen(url, data=data, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        # Never propagate: notification failure must not affect streaming.
        log.warning(f'notify: send failed: {e}')
        return False


if __name__ == '__main__':
    # Manual test: python3 notify.py
    ok = send('🔔 notify.py test message')
    print('sent OK' if ok else 'send failed')
