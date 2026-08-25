"""حالت «حداکثری» و حذف فیلترهای حدسی.

پس‌زمینه (۱.۹.۵): مالک گزارش داد در ۱ ساعت با ۸ اکانت فقط ۹ ادد شد.
لاگ همان اجرا:

    ۱۳۹ × «رد شد: پرایوسی بسته (last-seen مخفی)»
      ۹ × ادد موفق

یعنی فیلتر ۹۴٪ صف را دور می‌ریخت.

⚠️ چرا فیلتر اساساً غلط بود: بر اساس `user.status` قضاوت می‌کرد، یعنی
«آخرین بازدید مخفی است». ولی در تلگرام «Last Seen & Online» و
«Who can add me to groups» دو تنظیم کاملاً جدا هستند. کاربری که آخرین
بازدیدش را مخفی کرده ممکن است به‌راحتی ادد شود. بدتر: با
never_add_again(uid,"privacy") آن‌ها *برای همیشه* از صف حذف می‌شدند
(۳۵۵ نفر تا لحظه‌ی کشف).

مالک: «هرچی فیلتر، هرچی اضافه‌کاری، هرچی محدودیت هست رو بردار.»
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
CFG = (ROOT / "config.py").read_text(encoding="utf-8")
WEB = (ROOT / "web_app.py").read_text(encoding="utf-8")


def _worker():
    for n in ast.walk(ast.parse(BOT)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                n.name == "_worker_account_inner":
            return ast.get_source_segment(BOT, n)
    raise AssertionError("_worker_account_inner پیدا نشد")


WORKER = "\n".join(
    l for l in _worker().split("\n") if not l.lstrip().startswith("#")
)


# ------------------------------------------------ حذف فیلتر حدسی

def test_no_inline_privacy_filter():
    """⚠️ هسته‌ی مشکل: ۱۳۹ رد در برابر ۹ ادد."""
    assert "_is_unaddable_user" not in WORKER, (
        "فیلتر پرایوسی درون‌خطی باید حذف شده باشد — بر اساس last-seen "
        "حدس می‌زد و ۹۴٪ صف را دور می‌ریخت"
    )


def test_no_get_users_call_in_worker():
    """هر get_users یک درخواست است که به ادد تبدیل نمی‌شود."""
    n = len(re.findall(r"await client\.get_users\(", WORKER))
    assert n == 0, f"{n} فراخوانی get_users باقی مانده"


def test_no_never_add_again_for_privacy_guess():
    """حدس نباید کاربر را برای همیشه از صف بیرون بیندازد."""
    assert 'never_add_again(uid, reason)' not in WORKER, (
        "ثبت دائمی بر اساس حدسِ پرایوسی حذف شده — ۳۵۵ نفر اشتباهاً "
        "برای همیشه رد شده بودند"
    )


def test_budget_is_one_request_per_user():
    """قرارداد: دقیقاً یک درخواست به ازای هر کاربر، مثل ۱۰ آگوست (۷۰۵ ادد)."""
    budget = (
        len(re.findall(r"await client\.get_users\(", WORKER))
        + len(re.findall(r"InviteToChannel\(", WORKER))
        + len(re.findall(r"await confirm_joined\(", WORKER))
        + len(re.findall(r"AddContact\(", WORKER))
    )
    assert budget == 1, f"بودجه {budget} درخواست است، باید ۱ باشد"


def test_telegram_remains_the_only_judge():
    """داور «قابل ادد بودن» فقط پاسخ خود تلگرام است."""
    assert "UserPrivacyRestricted" in BOT, (
        "پاسخ قطعی تلگرام باید مدیریت شود — این جایگزین حدس ماست"
    )


# ------------------------------------------------ کلیدهای حالت max

@pytest.mark.parametrize("table", [
    "DELAY_RANGES", "BREAK_RANGES", "MODE_DAILY_CAP", "STAGGER_START",
])
def test_max_key_present_everywhere(table):
    """⚠️ کلید جامانده = سقوط بی‌صدا به پیش‌فرض.

    MODE_DAILY_CAP.get(add_mode, 100) یعنی نبودِ کلید «max» حالت
    حداکثری را روی ۱۰۰ ادد قفل می‌کرد — دقیقاً برعکس هدف.
    """
    import config
    d = getattr(config, table)
    assert "max" in d, f"کلید 'max' در {table} نیست ⇒ سقوط به پیش‌فرض"


def test_max_cap_is_effectively_unlimited():
    import config
    assert config.MODE_DAILY_CAP["max"] >= 100000





def test_no_jitter_in_max_mode():
    """jitter می‌توانست تأخیر را ۲.۸ برابر کند."""
    from add_engine import human_delay
    import config
    lo, hi = config.DELAY_RANGES["max"]
    vals = [human_delay("max") for _ in range(2000)]
    assert max(vals) <= hi, (
        f"در حالت max نباید jitter اعمال شود — بیشینه {max(vals):.2f} > {hi}"
    )


def test_jitter_still_applies_to_other_modes():
    """حذف jitter نباید سراسری باشد."""
    from add_engine import human_delay
    import config
    _, hi = config.DELAY_RANGES["ultra"]
    vals = [human_delay("ultra") for _ in range(2000)]
    assert max(vals) > hi, "حالت‌های دیگر باید jitter داشته باشند"


def test_no_periodic_break_in_max():
    import config
    assert "max" in config.NO_PERIODIC_BREAK_MODES
    assert "_periodic_break_on" in BOT, "شاخه‌ی استراحت دوره‌ای باید مشروط باشد"


def test_periodic_break_uses_bound_module_reference():
    """⚠️ bot.py ماژول config را import نکرده — فقط نام‌های جدا.

    استفاده از `config.X` آنجا NameError زمان‌اجرا می‌دهد که py_compile
    هرگز نمی‌گیرد.
    """
    m = re.search(r"_periodic_break_on = add_mode not in getattr\(\s*(\w+),", BOT)
    assert m, "شرط استراحت دوره‌ای پیدا نشد"
    ref = m.group(1)
    assert f"import config as {ref}" in BOT, (
        f"'{ref}' باید صریحاً import شده باشد وگرنه NameError می‌دهد"
    )


# ------------------------------------------------ UI

def test_only_two_speed_buttons_are_offered():
    """رابط کاربری فقط دو حالت دارد: آهسته و امن، اولترا فست.

    نسخهٔ قبلی چهار دکمه داشت (max/ultra/fast/safe) که تفاوتشان برای کاربر
    روشن نبود.
    """
    assert 'id="speed-safe"' in WEB
    assert 'id="speed-ultra"' in WEB
    assert 'id="speed-max"' not in WEB
    assert 'id="speed-fast"' not in WEB
    assert 'id="single-speed-safe"' in WEB
    assert 'id="single-speed-ultra"' in WEB
    assert 'id="single-speed-max"' not in WEB
    assert 'id="single-speed-fast"' not in WEB


def test_safe_is_the_default_speed():
    """پیش‌فرض باید محافظه‌کارانه باشد.

    قبلاً پیش‌فرض max بود؛ یعنی کاربری که هیچ دکمه‌ای نمی‌زد، تهاجمی‌ترین
    حالت را می‌گرفت و اکانتش را می‌سوزاند.
    """
    assert "let selectedParallelSpeed = 'safe';" in WEB
    assert "let selectedSingleSpeed = 'safe';" in WEB


def test_speed_buttons_guard_missing_element():
    """اگر دکمه‌ای در DOM نباشد، تابع نباید با null بشکند."""
    m = re.search(r"function setParallelSpeed\(speed\)\s*\{(.*?)\n        \}", WEB, re.S)
    assert m, "setParallelSpeed پیدا نشد"
    assert "if (!btn) return;" in m.group(1)


# ------------------------------------------------ محافظ‌های واقعی

def test_telegram_limits_still_respected():
    """«محدودیت نساز» یعنی محدودیتِ خودمان، نه نادیده گرفتن تلگرام.

    ⚠️ صرفِ وجود نام کافی نیست: باید نتیجه‌اش واقعاً برای صبر کردن
    استفاده شود. جهش‌آزمایی نشان داد جایگزینی فراخوانی با عدد صفر از
    نسخه‌ی قبلی این تست رد می‌شد.
    """
    assert "PEER_FLOOD" in WORKER
    assert "FloodWait" in BOT

    m = re.search(r"cooldown\s*=\s*peer_flood_cooldown\(\s*strikes\s*\)", WORKER)
    assert m, "مقدار cooldown باید از peer_flood_cooldown(strikes) بیاید"

    # و همان cooldown باید واقعاً صبر ایجاد کند
    assert re.search(r"wait_for\(\s*stop_event\.wait\(\)\s*,\s*timeout=cooldown", WORKER), (
        "cooldown باید به انتظار واقعی وصل باشد، نه فقط محاسبه شود"
    )


def test_real_stats_preserved():
    """آمار باید واقعی بماند."""
    assert "invite_did_not_join(invite_res, uid)" in WORKER


def test_duplicate_protection_preserved():
    """ادد تکراری = درخواست هدررفته."""
    assert "blocked_ids" in WORKER


def test_throttle_still_tied_to_spent_request():
    """درس ۱.۷.۱: تأخیر باید به «درخواست مصرف‌شده» گره بخورد."""
    assert "_spent_request" in WORKER
    assert "human_delay(add_mode)" in WORKER
