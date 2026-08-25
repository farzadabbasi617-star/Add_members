"""نگهداری سشن اکانت‌ها روی دیسک و بکاپ آن در دیتابیس.

چرا جدا شد
-----------
این دو تابع قبلاً داخل ``bg_scraper.py`` بودند — ماژول «اسکن خودکار پس‌زمینه»
که در این نسخه حذف شد. ولی خودشان ربطی به اسکن خودکار ندارند: هر جای برنامه
که بخواهد با یک اکانت ذخیره‌شده وصل شود به آن‌ها نیاز دارد.

مسئله‌ای که حل می‌کنند: رندر دیسک را بین دیپلوی‌ها **پاک می‌کند**. اگر سشن فقط
روی دیسک باشد، بعد از هر دیپلوی همهٔ اکانت‌ها خارج می‌شوند و باید دوباره با کد
تأیید لاگین کنند — که سریع‌ترین راه بن‌شدنشان است. پس نسخهٔ دوم در دیتابیس
نگه داشته می‌شود و موقع نیاز روی دیسک بازگردانده می‌شود.
"""

from __future__ import annotations

import os

from attacker import SESSIONS_DIR, _enable_wal_on_session, safe_phone_filename
from db import load_session_blob, save_session_blob

#: فایل سشن کوچک‌تر از این یعنی ناقص یا خراب است، نه یک سشن واقعی.
_MIN_SESSION_BYTES = 100


def session_path(phone: str) -> str:
    """مسیر فایل سشن این شماره روی دیسک."""
    return os.path.join(SESSIONS_DIR, f"acc_{safe_phone_filename(phone)}.session")


async def ensure_session(phone: str):
    """اطمینان از وجود فایل سشن؛ در صورت نبود، بازگردانی از بکاپ دیتابیس.

    خروجی مسیر فایل است، یا ``None`` اگر نه روی دیسک باشد و نه بکاپی وجود
    داشته باشد (یعنی این اکانت واقعاً لاگین نشده).
    """
    path = session_path(phone)

    if os.path.exists(path) and os.path.getsize(path) > _MIN_SESSION_BYTES:
        # WAL را روی سشن موجود هم فعال کن: بدون آن، نوشتن هم‌زمان چند ورکر
        # روی یک فایل SQLite به «database is locked» می‌خورد.
        _enable_wal_on_session(path[: -len(".session")])
        return path

    blob = load_session_blob(phone)
    if blob:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(blob)
        _enable_wal_on_session(path[: -len(".session")])
        return path

    return None


def backup_session(phone: str) -> bool:
    """کپی فایل سشن به دیتابیس. خروجی: آیا بکاپ گرفته شد.

    خطا را بالا نمی‌فرستد — این کار همیشه جانبی است و نباید جریان اصلی (که
    معمولاً وسط یک عملیات ادد است) را بشکند.
    """
    path = session_path(phone)
    try:
        if not os.path.exists(path):
            return False
        with open(path, "rb") as handle:
            save_session_blob(phone, handle.read())
        return True
    except Exception as error:  # noqa: BLE001 - عمداً وسیع؛ توضیح بالا
        print(f"session backup err ({phone}): {error}", flush=True)
        return False
