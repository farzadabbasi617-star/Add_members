"""
=================================================================
📲 افزودن اکانت از مینی‌اپ — فلوی لاگین سه مرحله‌ای
=================================================================

مالک می‌خواهد شماره‌های جدید را مستقیم از مینی‌اپ اضافه کند (برای ادد
موازی) و شماره‌های خراب را حذف کند.

فلو:
    ۱) start(phone)          → تلگرام کد تأیید می‌فرستد
    ۲) submit_code(code)     → ورود؛ اگر رمز دو مرحله‌ای لازم بود اعلام می‌کند
    ۳) submit_password(pwd)  → ورود نهایی و ذخیره سشن

ملاحظات امنیتی:
- کد تأیید و رمز فقط در حافظه می‌مانند و هرگز لاگ یا ذخیره نمی‌شوند.
- هر جلسه ۵ دقیقه اعتبار دارد و بعد خودکار پاک می‌شود.
- سشن بعد از موفقیت رمزنگاری‌شده در دیتابیس ذخیره می‌گردد (مثل بقیه).
- جلسه‌های ناتمام کلاینت خود را می‌بندند تا سشن نیمه‌کاره جا نماند.
"""
import asyncio
import os
import random
import re
import time

import db

logger = None
try:
    import logging
    logger = logging.getLogger("antiscraper.account_login")
except Exception:
    pass

_SESSION_TTL = 300          # ۵ دقیقه — همان مهلتی که تلگرام برای کد می‌دهد
_lock = asyncio.Lock()
_pending = {}               # phone -> {client, hash, ts, step, tmp_name}


def normalize_phone(raw):
    """
    یکسان‌سازی شماره ایران به فرمت بین‌المللی.

    می‌پذیرد: +989121234567 / 09121234567 / 9121234567 / ۰۹۱۲…
    """
    s = str(raw or "").strip()
    if not s:
        return None
    # ارقام فارسی/عربی → لاتین
    s = s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    s = re.sub(r"[\s\-()]", "", s)

    if s.startswith("+"):
        digits = re.sub(r"\D", "", s)
        return "+" + digits if len(digits) >= 10 else None

    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("98") and len(digits) >= 12:
        pass
    elif digits.startswith("0"):
        digits = "98" + digits[1:]
    elif len(digits) == 10 and digits.startswith("9"):
        digits = "98" + digits
    return "+" + digits if len(digits) >= 10 else None


def _purge_expired():
    now = time.time()
    for phone in [p for p, v in _pending.items() if now - v["ts"] > _SESSION_TTL]:
        entry = _pending.pop(phone, None)
        if entry:
            _close_quietly(entry)


def _close_quietly(entry):
    """بستن کلاینت نیمه‌کاره و پاک کردن فایل سشن موقت."""
    client = entry.get("client")
    if client:
        try:
            app = getattr(client, "app", client)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_disconnect(app))
        except Exception:
            pass
    tmp = entry.get("tmp_name")
    if tmp:
        for suffix in (".session", ".session-journal"):
            path = tmp + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


async def _disconnect(app):
    try:
        await app.disconnect()
    except Exception:
        pass


def pending_phones():
    """شماره‌هایی که وسط فرایند لاگین هستند (برای نمایش/دیباگ)."""
    _purge_expired()
    return {p: v.get("step") for p, v in _pending.items()}


async def start(raw_phone):
    """مرحله ۱ — ارسال کد تأیید. برمی‌گرداند (ok, message, needs)."""
    phone = normalize_phone(raw_phone)
    if not phone:
        return False, "شماره نامعتبر است. نمونه درست: +989121234567", None

    existing = db.load_accounts() or {}
    if phone in existing:
        return False, f"اکانت {phone} از قبل در لیست هست.", None

    async with _lock:
        _purge_expired()
        if phone in _pending:
            return False, "برای این شماره قبلاً کد فرستاده شده. کد را وارد کن یا چند دقیقه صبر کن.", "code"

    try:
        from attacker import AdvancedScraper, DEVICE_FP, SESSIONS_DIR
        from config import API_ID, API_HASH

        if not API_ID or not API_HASH:
            return False, "API_ID و API_HASH تنظیم نشده‌اند.", None

        fp = random.choice(DEVICE_FP)
        tmp_name = os.path.join(
            SESSIONS_DIR, f"tmp_login_{int(time.time())}_{random.randint(1000, 9999)}"
        )
        client = AdvancedScraper(
            tmp_name, API_ID, API_HASH, phone=phone, device_fp=fp, force_fresh=True
        )
        await asyncio.wait_for(client.connect(), timeout=45)

        sent = await client.app.send_code(phone)
        if not sent or not getattr(sent, "phone_code_hash", None):
            await _disconnect(client.app)
            return False, "تلگرام کد را ارسال نکرد. چند دقیقه بعد دوباره امتحان کن.", None

        async with _lock:
            _pending[phone] = {
                "client": client,
                "hash": sent.phone_code_hash,
                "ts": time.time(),
                "step": "code",
                "tmp_name": tmp_name,
                "device_fp": fp,
            }

        return True, f"کد تأیید به {phone} ارسال شد. کد ۵ رقمی را وارد کن.", "code"

    except Exception as e:
        name = type(e).__name__
        if "FloodWait" in name:
            wait = getattr(e, "value", None) or getattr(e, "x", "?")
            return False, f"تلگرام محدودیت گذاشته. {wait} ثانیه صبر کن.", None
        if "PhoneNumberInvalid" in name:
            return False, "این شماره از نظر تلگرام معتبر نیست.", None
        if "PhoneNumberBanned" in name:
            return False, "این شماره توسط تلگرام مسدود شده.", None
        return False, f"خطا در ارسال کد: {name} — {str(e)[:150]}", None


async def submit_code(raw_phone, code):
    """مرحله ۲ — ورود با کد. برمی‌گرداند (ok, message, needs)."""
    phone = normalize_phone(raw_phone)
    code = re.sub(r"\D", "", str(code or ""))
    if not phone:
        return False, "شماره نامعتبر است.", None
    if not code:
        return False, "کد تأیید را وارد کن.", "code"

    async with _lock:
        _purge_expired()
        entry = _pending.get(phone)
    if not entry:
        return False, "جلسه منقضی شده. دوباره از ابتدا شروع کن.", None

    client = entry["client"]
    try:
        await client.app.sign_in(phone, entry["hash"], code)
    except Exception as e:
        name = type(e).__name__
        if "SessionPasswordNeeded" in name:
            async with _lock:
                entry["step"] = "password"
                entry["ts"] = time.time()
            return True, "این اکانت رمز دو مرحله‌ای دارد. رمز را وارد کن.", "password"
        if "PhoneCodeInvalid" in name:
            return False, "کد اشتباه است. دوباره وارد کن.", "code"
        if "PhoneCodeExpired" in name:
            await _finish(phone, success=False)
            return False, "کد منقضی شد. از ابتدا شروع کن.", None
        await _finish(phone, success=False)
        return False, f"خطا در ورود: {name} — {str(e)[:150]}", None

    return await _persist(phone)


async def submit_password(raw_phone, password):
    """مرحله ۳ — رمز دو مرحله‌ای."""
    phone = normalize_phone(raw_phone)
    if not phone:
        return False, "شماره نامعتبر است.", None
    if not password:
        return False, "رمز دو مرحله‌ای را وارد کن.", "password"

    async with _lock:
        _purge_expired()
        entry = _pending.get(phone)
    if not entry:
        return False, "جلسه منقضی شده. دوباره از ابتدا شروع کن.", None

    try:
        await entry["client"].app.check_password(password)
    except Exception as e:
        name = type(e).__name__
        if "PasswordHashInvalid" in name:
            return False, "رمز اشتباه است. دوباره وارد کن.", "password"
        await _finish(phone, success=False)
        return False, f"خطا در تأیید رمز: {name} — {str(e)[:150]}", None

    return await _persist(phone)


async def _persist(phone):
    """ذخیره اکانت و سشن بعد از ورود موفق."""
    async with _lock:
        entry = _pending.get(phone)
    if not entry:
        return False, "جلسه پیدا نشد.", None

    client = entry["client"]
    try:
        me = await client.app.get_me()
        name = (getattr(me, "first_name", "") or "").strip() or phone
        username = (getattr(me, "username", "") or "").strip()

        db.save_account(phone, name, username, entry.get("device_fp") or {})

        # 💾 انتقال سشن به نام دائمی.
        #
        # ⚠️ اینجا قبلاً باگ داشت: کد دستی دنبال `entry["tmp_name"] + ".session"`
        # می‌گشت، ولی AdvancedScraper با force_fresh=True نام فایل را خودش
        # عوض می‌کند (`_newtmp_<phone>_<ts>_<rand>`) و tmp_name را نادیده
        # می‌گیرد. پس os.path.exists(src) همیشه False بود:
        #   • فایل سشن هرگز به acc_<phone>.session منتقل نمی‌شد
        #   • save_session_blob هرگز اجرا نمی‌شد ⇒ سشن در دیتابیس نبود
        #   • اکانت ذخیره می‌شد ولی بدون سشن ⇒ در مینی‌اپ «خراب» دیده می‌شد
        # حالا از persist_to_permanent() خود کلاینت استفاده می‌کنیم که نام
        # واقعی فایل را می‌داند و .wal/.shm را هم منتقل می‌کند.
        from attacker import SESSIONS_DIR, safe_phone_filename
        final_base = os.path.join(SESSIONS_DIR, f"acc_{safe_phone_filename(phone)}")
        dst = final_base + ".session"

        moved = False
        try:
            if hasattr(client, "persist_to_permanent") and getattr(client, "_perm_session_path", None):
                await client.persist_to_permanent()
                moved = os.path.exists(dst)
        except Exception as e:
            print(f"⚠️ persist_to_permanent {phone}: {type(e).__name__}: {e}", flush=True)

        await _disconnect(client.app)
        await asyncio.sleep(0.4)   # مهلت به SQLite برای بستن فایل

        # مسیر جایگزین: اگر persist_to_permanent در دسترس نبود یا کار نکرد،
        # نام واقعی فایل را از خود کلاینت بخوان (نه از tmp_name).
        if not moved:
            candidates = []
            real_name = getattr(getattr(client, "app", None), "name", None)
            if real_name:
                candidates.append(real_name + ".session")
            candidates.append(entry["tmp_name"] + ".session")
            for src in candidates:
                if src and os.path.exists(src):
                    try:
                        if os.path.exists(dst):
                            os.remove(dst)
                        os.replace(src, dst)
                        moved = True
                        break
                    except Exception as e:
                        print(f"⚠️ انتقال سشن {phone}: {e}", flush=True)

        if not os.path.exists(dst):
            # ⚠️ بی‌صدا موفق اعلام نکن. قبلاً همین باعث می‌شد کاربر پیام
            # «با موفقیت اضافه شد» ببیند و بعد اکانت «خراب» باشد.
            await _finish(phone, success=False)
            try:
                db.delete_account(phone)
            except Exception:
                pass
            print(f"❌ سشن {phone} ذخیره نشد — اکانت اضافه نشد", flush=True)
            return False, (
                "ورود به تلگرام موفق بود ولی فایل سشن ذخیره نشد. "
                "لطفاً دوباره تلاش کن."
            ), None

        try:
            with open(dst, "rb") as fh:
                db.save_session_blob(phone, fh.read())
        except Exception as e:
            print(f"⚠️ بکاپ سشن {phone}: {e}", flush=True)

        # 🧹 نتیجه‌ی تستِ قبل از لاگین را پاک کن.
        #
        # ⚠️ بدون این، اکانتِ تازه و سالم «خراب» نشان داده می‌شود.
        # get_accounts_dict وضعیت را از همین نتیجه‌ی ذخیره‌شده می‌خواند و
        # اگر probe قبلاً (وقتی هنوز سشنی نبود) شکست خورده باشد، آن خطا
        # تا اجرای بعدیِ probe باقی می‌ماند.
        try:
            from account_doctor import clear_probe_result
            clear_probe_result(phone)
        except Exception as e:
            print(f"⚠️ پاک کردن نتیجه‌ی تست {phone}: {type(e).__name__}: {e}", flush=True)

        async with _lock:
            _pending.pop(phone, None)

        print(f"✅ اکانت {phone} ({name}) از مینی‌اپ اضافه شد "
              f"(سشن: {os.path.getsize(dst)} بایت)", flush=True)
        return True, f"اکانت «{name}» ({phone}) با موفقیت اضافه شد.", None

    except Exception as e:
        await _finish(phone, success=False)
        return False, f"ورود موفق بود ولی ذخیره نشد: {type(e).__name__} — {str(e)[:150]}", None


async def _finish(phone, success):
    async with _lock:
        entry = _pending.pop(phone, None)
    if not entry:
        return
    client = entry.get("client")
    if client:
        await _disconnect(getattr(client, "app", client))
    if not success:
        tmp = entry.get("tmp_name")
        if tmp:
            for suffix in (".session", ".session-journal"):
                path = tmp + suffix
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass


async def cancel(raw_phone):
    """لغو دستی یک جلسه ناتمام."""
    phone = normalize_phone(raw_phone)
    if not phone:
        return False, "شماره نامعتبر است."
    await _finish(phone, success=False)
    return True, "جلسه لغو شد."


def reset_for_tests():
    _pending.clear()
