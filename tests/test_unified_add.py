"""ادد تک‌اکانتی و موازی باید یک موتور باشند.

پس‌زمینه
---------
تا پیش از این، «ادد تک‌اکانتی» پیاده‌سازی جداگانهٔ خودش را داشت
(``_execute_simple_add_inner``، ۳۲۳ خط). مقایسهٔ خط‌به‌خط با نسخهٔ موازی نشان
داد پنج محافظ در آن **وجود نداشت**:

    • مدیریت PeerFlood
    • محافظ شکست پیاپی (۲۵ شکست → توقف)
    • تأیید عضویت واقعی بعد از دعوت
    • توقف اضطراری از مینی‌اپ
    • شروع پلکانی

یعنی کاربری که تک‌اکانتی می‌زد با محافظت کمتری کار می‌کرد و اکانتش بیشتر در
معرض بن بود — برعکس چیزی که از یک حالت «آرام‌تر» انتظار می‌رود.

این‌ها ناشی از بدی کد نبود؛ ناشی از این بود که دو نسخه از یک منطق وجود داشت.
هر بهبودی که به یکی اضافه می‌شد، به دیگری نمی‌رسید. این تست‌ها جلوی برگشت آن
وضعیت را می‌گیرند.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    lines = BOT.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith(f"async def {name}"))
    last = start
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == "":
            continue
        if lines[i][0].isspace():
            last = i
        else:
            break
    return "\n".join(lines[start : last + 1])


def test_duplicate_single_account_engine_is_gone():
    """پیاده‌سازی موازیِ دوم نباید برگردد."""
    assert "async def _execute_simple_add_inner" not in BOT


def test_single_account_delegates_to_the_parallel_engine():
    """ادد تک باید همان موتور را با یک ورکر صدا بزند، نه منطق خودش را."""
    body = _function_body("_execute_simple_add")
    assert "_execute_parallel_add(" in body, "ادد تک دیگر به موتور اصلی وصل نیست"
    assert "{phone: info}" in body, "باید دیکشنری تک‌عضوی بسازد"


def test_single_account_path_stays_thin():
    """اگر کسی دوباره منطق ادد را اینجا بنویسد، همان انشعاب قبلی تکرار می‌شود."""
    body = _function_body("_execute_simple_add")
    code = [
        l
        for l in body.split("\n")
        if l.strip() and not l.strip().startswith("#") and '"""' not in l
    ]
    assert len(code) < 40, f"_execute_simple_add دوباره بزرگ شده: {len(code)} خط"


def test_add_engine_keeps_every_anti_ban_guard():
    """محافظ‌هایی که نسخهٔ تک‌اکانتی نداشت باید در موتور مشترک باشند.

    هرکدام از این‌ها بعد از یک حادثهٔ واقعی اضافه شده‌اند؛ حذف هر کدام یعنی
    برگشتن به همان حادثه.
    """
    engine = _function_body("_execute_parallel_add")
    required = {
        "PeerFlood": r"PeerFlood",
        "محافظ شکست پیاپی": r"_global_consecutive_fails",
        "تأیید عضویت واقعی": r"confirm_joined",
        "توقف اضطراری": r"stop_event",
        "شروع پلکانی": r"stagger_delay",
        "تأخیر انسانی": r"human_delay",
        "لیست ممنوعه": r"get_blocked_ids_cached",
        "throttle سراسری": r"global_throttle",
        "FloodWait": r"FloodWait",
    }
    missing = [name for name, pat in required.items() if not re.search(pat, engine)]
    assert not missing, f"محافظ حذف‌شده از موتور: {missing}"


def test_engine_makes_no_assumption_about_account_count():
    """موتور نباید فرض کند بیش از یک اکانت دارد.

    تنها بررسی مجاز «صفر اکانت» است؛ هر شرطی مثل ``num_accounts > 1`` یعنی
    مسیر تک‌اکانتی دوباره رفتار متفاوتی پیدا می‌کند.
    """
    engine = _function_body("_execute_parallel_add")
    for match in re.finditer(r"(num_accounts|len\(accs\))\s*([<>]=?|==)\s*(\d+)", engine):
        value = int(match.group(3))
        # فقط «خالی بودن» قابل قبول است: == 0 یا > 0.
        # هر آستانه‌ای بالاتر از صفر یعنی مسیر تک‌اکانتی رفتار متفاوتی می‌گیرد.
        assert value == 0, f"فرض دربارهٔ تعداد اکانت: {match.group(0)}"


def test_stagger_delay_is_actually_imported():
    """این تابع سال‌ها صدا زده می‌شد بدون اینکه import شده باشد.

    نتیجه: NameError در هر اجرا، یعنی «شروع پلکانی» — که جلوی الگوی هماهنگ و
    بن هم‌زمان اکانت‌ها را می‌گیرد — عملاً هرگز کار نکرد.
    """
    assert re.search(r"^from add_engine import \([^)]*stagger_delay", BOT, re.M | re.S)
    engine = _function_body("_execute_parallel_add")
    assert "_stag(" not in engine, "ارجاع به نام قدیمی و تعریف‌نشده"


def test_worker_index_reaches_the_inner_worker():
    """بدون این، شروع پلکانی حتی بعد از import هم کار نمی‌کند."""
    engine = _function_body("_execute_parallel_add")
    assert "_worker_account_inner(phone, info, _worker_index)" in engine
    assert "async def _worker_account_inner(phone, info, _worker_index=0):" in engine
