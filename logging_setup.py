"""
=================================================================
📜 Logging Setup — لاگینگ استاندارد و حرفه‌ای
=================================================================
- خروجی کنسول (برای Render) + فایل چرخشی (برای دیباگ محلی)
- شکار خودکار print های قدیمی کد و تبدیل آن‌ها به رکورد لاگ
- سطح‌بندی هوشمند بر اساس ایموجی‌های موجود در پیام (✅/⚠️/❌)
"""
import os
import sys
import logging
import logging.handlers

import config

_installed = False
_installed_print = False
_original_print = None

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level=None):
    """پیکربندی ریشه‌ای لاگر — یک‌بار در شروع برنامه صدا زده می‌شود"""
    global _installed
    if _installed:
        return
    lvl = getattr(logging, str(level or config.LOG_LEVEL), logging.INFO)

    root = logging.getLogger()
    root.setLevel(lvl)

    # فرمت یکسان
    fmt = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    # ۱) کنسول
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(lvl)
    root.addHandler(console)

    # ۲) فایل چرخشی (فقط محلی — روی Render در مسیر کاری نوشته می‌شود)
    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(config.LOG_DIR, "bot.log"),
            maxBytes=config.LOG_FILE_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        fh.setLevel(lvl)
        root.addHandler(fh)
    except Exception as e:
        logging.getLogger("logging_setup").warning("Could not init file logging: %s", e)

    # آرام کردن کتابخانه‌های پر سر و صدا
    for noisy in ("pyrogram", "aiohttp", "instaloader", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _installed = True
    logging.getLogger("logging_setup").info("✅ Logging initialized (level=%s, dir=%s)", lvl, config.LOG_DIR)


def _print_to_log(*args, **kwargs):
    """شکار print های قدیمی: هم روی stdout چاپ می‌شود، هم وارد لاگ فایل می‌شود"""
    _original_print(*args, **kwargs)
    try:
        text = " ".join(str(a) for a in args)
        if not text.strip():
            return
        log = logging.getLogger("bot.print")
        if any(c in text for c in ("❌", "🚫", "Error", "Traceback", "CRASH")):
            log.error(text)
        elif "⚠️" in text or "Warn" in text:
            log.warning(text)
        else:
            log.info(text)
    except Exception:
        pass


def install_print_logging():
    """نصب شکارکننده print — فقط یک‌بار"""
    global _original_print
    if _original_print is not None:
        return
    _original_print = __builtins__["print"] if isinstance(__builtins__, dict) else __builtins__.print
    try:
        import builtins
        builtins.print = _print_to_log
    except Exception:
        pass


def get_logger(name):
    """لاگر نام‌گذاری‌شده برای ماژول‌ها"""
    return logging.getLogger(f"antiscraper.{name}")
