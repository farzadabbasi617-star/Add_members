"""
=================================================================
🔒 Account State — ردیاب «اشغال بودن» اکانت‌ها
=================================================================
مشکل واقعی: وقتی دو عملیات همزمان از یک سشن استفاده می‌کنند
(مثلاً اسکن خودکار + حمله دستی با همان اکانت)، تلگرام AUTH_KEY_DUPLICATED
می‌دهد و معمولاً سشن را می‌سوزاند → اکانت «سالم» ولی بی‌استخراج!

این ماژول جلوی استفاده همزمان را می‌گیرد + TTL خودکار دارد
که اگر فراموش شد release شود، اکانت برای همیشه قفل نماند.
"""
import time
import threading

_lock = threading.Lock()
_busy = {}          # phone -> {"label": str, "ts": float}
_last_used = {}     # phone -> unix ts
_last_error = {}    # phone -> str
_DEFAULT_TTL = 45 * 60   # ثانیه (سقف امن — عملیات‌ها معمولاً خیلی زودتر تمام می‌شوند)


def _expired(entry, now):
    return (now - entry["ts"]) > _DEFAULT_TTL


def mark_busy(phone, label="عملیات", ttl=None):
    """اشغال کردن اکانت. برمی‌گرداند (True, None) یا (False, label_owner)"""
    phone = str(phone)
    now = time.time()
    with _lock:
        if phone in _busy and not _expired(_busy[phone], now):
            return False, _busy[phone]["label"]
        _busy[phone] = {"label": label, "ts": now}
        return True, None


def release(phone):
    """آزاد کردن اکانت (در پایان هر عملیات صدا زده می‌شود)"""
    if phone is None:
        return
    with _lock:
        _busy.pop(str(phone), None)


def busy_label(phone):
    """اگر اکانت مشغول است، برچسب عملیات را برمی‌گرداند؛ وگرنه None"""
    phone = str(phone)
    now = time.time()
    with _lock:
        entry = _busy.get(phone)
        if entry and not _expired(entry, now):
            return entry["label"]
        if entry:
            _busy.pop(phone, None)   # منقضی شده — خودکار آزاد
        return None


def all_busy():
    """نقشه اکانت‌های مشغول (برای نمایش/دیباگ)"""
    now = time.time()
    with _lock:
        for phone in [p for p, e in _busy.items() if _expired(e, now)]:
            _busy.pop(phone, None)
        return {p: e["label"] for p, e in _busy.items()}


def mark_used(phone):
    """ثبت آخرین زمان استفاده (برای چرخش عادلانه بین اکانت‌ها)"""
    if phone is None:
        return
    with _lock:
        _last_used[str(phone)] = time.time()


def last_used(phone):
    with _lock:
        return _last_used.get(str(phone), 0)


def set_last_error(phone, msg):
    if phone is None:
        return
    with _lock:
        if msg:
            _last_error[str(phone)] = str(msg)[:240]
        else:
            _last_error.pop(str(phone), None)


def get_last_error(phone):
    with _lock:
        return _last_error.get(str(phone), "")


def reset_for_tests():
    with _lock:
        _busy.clear()
        _last_used.clear()
        _last_error.clear()
