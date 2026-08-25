"""
سیاست نرخ ادد: **هیچ محدودیت اختراعی از طرف ما**.

مالک صریحاً گفت:
    «خودت محدودیت ایجاد نکن، بذار اکانت‌ها تا حداکثر ظرفیت خودشون ادد
     بزنن و فقط وقتی که تلگرام خودش محدودشون کرد صبر کنیم تا آزاد بشن.»

تاریخچه‌ی اشتباه ما:
- ۱.۵.۵ جریمه‌ی ۲۴ ساعته را هاردکد کرد (تلگرام هیچ مدتی نمی‌دهد).
- ۱.۵.۸ یک warm-up اختراع کرد که اکانت‌ها را به ۱۲ ادد محدود می‌کرد،
  و حالت safe را خودسرانه دو برابر کند کرد.

هر دو برداشته شدند. این فایل نگهبان است که دوباره برنگردند.

تنها انتظار باقی‌مانده، عقب‌نشینی کوتاه بعد از PEER_FLOOD است — چون
تلگرام مدت اعلام نمی‌کند و تنها راه فهمیدن، تست دوباره است.
"""
import pathlib

import config
from add_engine import (
    peer_flood_cooldown,
    describe_cooldown,
    warmup_cap,
    stagger_delay,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _handler(code):
    """بلوک هندلر یک خطای مشخص در ورکر موازی."""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    for marker in (f'if "{code}" in err:', f'if "{code}" in err '):
        i = src.find(marker)
        if i != -1:
            break
    assert i != -1, f"هندلر {code} پیدا نشد"
    # تا انتهای همین هندلر: خط بعدیِ هم‌سطح یا کم‌عمق‌تر
    lines = src[i:].split("\n")
    indent = len(lines[0]) - len(lines[0].lstrip())
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        out.append(ln)
    return "\n".join(out)


# ───────────── بدون سقف مصنوعی ─────────────

def test_no_artificial_per_account_cap():
    """اکانت باید تا ظرفیت واقعی کار کند، نه سقف اختراعی ما."""
    assert config.MAX_ADD_PER_ACCOUNT >= 500, (
        f"سقف {config.MAX_ADD_PER_ACCOUNT} محدودیت ماست، نه تلگرام"
    )


def test_no_artificial_daily_cap():
    for mode, cap in config.MODE_DAILY_CAP.items():
        assert cap >= 500, f"سقف روزانه {mode}={cap} محدودیت ماست"


def test_warmup_is_off_by_default():
    """
    warm-up اکانت‌های سالم را بیکار نگه می‌داشت. باید خاموش باشد و
    فقط با env روشن شود.
    """
    assert config.WARMUP_ENABLED is False


def test_warmup_cap_does_not_limit_when_disabled():
    """با warm-up خاموش، سابقه‌ی اکانت نباید سقف بیاورد."""
    for history in (0, 1, 7, 50, 300):
        assert warmup_cap(history, 1000) == 1000, (
            f"اکانت با سابقه {history} نباید محدود شود"
        )


def test_collect_ready_has_no_hardcoded_100_cap():
    src = (ROOT / "account_doctor.py").read_text(encoding="utf-8")
    i = src.index("def collect_ready_accounts")
    body = src[i:i + 2000]
    assert ">= 100" not in body, "سقف هاردکد ۱۰۰ باید حذف شده باشد"
    assert "MAX_ADD_PER_ACCOUNT" in body


def test_limited_account_is_retried_after_deadline():
    """
    اکانتی که مهلت محدودیتش گذشته باید دوباره وارد کار شود، نه اینکه
    برای همیشه کنار گذاشته شود.
    """
    src = (ROOT / "account_doctor.py").read_text(encoding="typing" and "utf-8")
    i = src.index("def collect_ready_accounts")
    body = src[i:i + 2000]
    assert "remaining_seconds" in body, (
        "باید بر اساس مهلت باقی‌مانده تصمیم بگیرد، نه فقط برچسب limited"
    )


def test_speeds_are_reasonable_for_parallel_use():
    """
    ⚠️ بازنگری ۱.۷.۰: این تست قبلاً اعداد دقیق را قفل می‌کرد، ولی آن
    اعداد با ۶ اکانت موازی باعث PEER_FLOOD می‌شدند. حالا معیار،
    «فاصله‌ی مؤثر روی گروه» است، نه یک عدد ثابت.
    """
    for mode in ("ultra", "fast", "safe"):
        lo, hi = config.DELAY_RANGES[mode]
        assert lo > 0 and hi > lo, f"{mode}: بازه نامعتبر"

    # safe باید با ۶ اکانت هم واقعاً امن بماند
    lo, hi = config.DELAY_RANGES["safe"]
    assert ((lo + hi) / 2) / 6 >= 14


# ───────────── واکنش به محدودیت واقعی تلگرام ─────────────

def test_peer_flood_backoff_is_short():
    """
    تلگرام مدت نمی‌دهد، پس صبر باید کوتاه باشد و مکرر تست شود.
    ۲۴ ساعت حدسِ ما بود و اکانت سالم را یک روز بیکار کرد.
    """
    assert peer_flood_cooldown(1) <= 5 * 60, "اولین صبر باید چند دقیقه باشد"
    assert peer_flood_cooldown(99) <= 60 * 60, "هرگز نباید ساعت‌ها صبر کنیم"


def test_backoff_progresses_but_stays_bounded():
    vals = [peer_flood_cooldown(n) for n in range(1, 7)]
    assert vals == sorted(vals)
    assert max(vals) <= 60 * 60


def test_no_hardcoded_day_penalty():
    window = _handler("PEER_FLOOD")
    assert "24 * 3600" not in window
    assert "peer_flood_cooldown" in window


def test_flooded_account_retries_instead_of_quitting():
    """
    مهم‌ترین تست این فایل: اکانت بعد از PEER_FLOOD نباید تا پایان کل
    عملیات کنار برود — باید صبر کند و دوباره تست کند.
    """
    window = _handler("PEER_FLOOD")
    assert "continue" in window, (
        "اکانت باید بعد از استراحت دوباره تلاش کند، نه اینکه خارج شود"
    )
    assert "stop_event.wait()" in window, "صبر باید با دکمه توقف قابل لغو باشد"


def test_successful_add_resets_strikes():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "_peer_flood_strikes.pop(phone, None)" in src


def test_floodwait_respects_telegram_value():
    """
    برخلاف PEER_FLOOD، تلگرام برای FloodWait مقدار دقیق می‌دهد —
    باید دقیقاً همان رعایت شود، نه عدد خودمان.
    """
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "fw.value" in src


def test_user_told_account_is_not_banned():
    window = _handler("PEER_FLOOD")
    assert "بن نشده" in window


# ───────────── تنها هماهنگی باقی‌مانده ─────────────

def test_stagger_is_minimal():
    """
    فاصله‌ی شروع فقط برای جلوگیری از هجوم هم‌زمان است — نباید به
    کندسازی واقعی تبدیل شود.
    """
    delays = [stagger_delay(i, "safe") for i in range(8)]
    assert max(delays) < 90, f"فاصله شروع {max(delays):.0f}s کندسازی است"


def test_describe_cooldown_is_readable():
    assert "دقیقه" in describe_cooldown(180)
    assert "ساعت" in describe_cooldown(7200)
