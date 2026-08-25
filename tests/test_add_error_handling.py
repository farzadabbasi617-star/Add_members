"""
مدیریت خطاهای بحرانی تلگرام حین ادد.

اجرای واقعی روی سرویس زنده (۱۶ اکانت‌دقیقه) این آمار را داد:

    CHAT_WRITE_FORBIDDEN     32   ← دسترسی افزودن عضو نیست
    PEER_FLOOD               30   ← اکانت محدود شده
    invited but not a member 30   ← درست: در «رد شده» رفت
    USER_CHANNELS_TOO_MUCH    2
    Invited (موفق)            4

هیچ‌کدام از دو خطای اول مدیریت نمی‌شدند: ورکر در حلقه می‌ماند و
هزاران بار همان خطا را می‌گرفت. اکانت‌ها بی‌فایده می‌سوختند و
نرخ ادد به‌شدت پایین می‌آمد.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOT_SRC = (ROOT / "bot.py").read_text(encoding="utf-8")


def _parallel_worker_body():
    """
    بدنه _execute_parallel_add را بر اساس تورفتگی جدا می‌کند.

    regex ساده کار نمی‌کند چون این تابع خودش شامل
    `async def worker_account(...)` تودرتو است.
    """
    lines = BOT_SRC.split("\n")
    start = next(
        i for i, l in enumerate(lines)
        if l.startswith("async def _execute_parallel_add(")
    )
    out = [lines[start]]
    for line in lines[start + 1:]:
        if line and not line[0].isspace():   # به تعریف سطح‌بالای بعدی رسیدیم
            break
        out.append(line)
    return "\n".join(out)


def _handler(code):
    """
    بلوک هندلر همان خطا را برمی‌گرداند.

    نمی‌توان از اولین occurrence استفاده کرد — کامنت‌های توضیحی هم شامل
    همین رشته‌ها هستند. مبنا خودِ شرط `if "CODE" in err:` است.
    """
    body = _parallel_worker_body()
    # هندلر ممکن است شرط مرکب داشته باشد:
    #   if "CHAT_WRITE_FORBIDDEN" in err or "CHAT_ADMIN_REQUIRED" in err:
    for marker in (f'if "{code}" in err:', f'if "{code}" in err '):
        i = body.find(marker)
        if i != -1:
            break
    assert i != -1, f"هندلر {code} پیدا نشد"
    lines = body[i:].split(chr(10))
    indent = len(lines[0]) - len(lines[0].lstrip())
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        out.append(ln)
    return chr(10).join(out)


@pytest.mark.parametrize("code", ["PEER_FLOOD", "CHAT_WRITE_FORBIDDEN", "USER_CHANNELS_TOO_MUCH"])
def test_critical_errors_are_handled(code):
    assert code in _parallel_worker_body(), (
        f"{code} مدیریت نمی‌شود — ورکر در حلقه می‌ماند و اکانت را می‌سوزاند"
    )


def test_peer_flood_pauses_then_retries():
    """
    ⚠️ تصحیح: قبلاً ورکر برای همیشه خارج می‌شد. حالا کوتاه صبر می‌کند
    و دوباره تست می‌کند — تلگرام مدت آزادی را اعلام نمی‌کند، پس تنها
    راه فهمیدن، تلاش دوباره است.
    """
    window = _handler("PEER_FLOOD")
    assert "set_adder_limit" in window, "اکانت باید به‌عنوان محدودشده ثبت شود"
    assert "stop_event.wait()" in window, "صبر باید با دکمه توقف قابل لغو باشد"


def test_peer_flood_cooldown_is_progressive_not_fixed():
    """
    ⚠️ تصحیح ۱.۵.۸: قبلاً این تست ۲۴ ساعت ثابت را الزام می‌کرد — که
    خودش باعث شد اکانتی با ۱ ادد یک روز از دست برود. حالا باید از
    بک‌آف تدریجی استفاده شود.
    """
    window = _handler("PEER_FLOOD")
    assert "peer_flood_cooldown" in window, "باید از بک‌آف کوتاه استفاده کند"
    assert "24 * 3600" not in window, "جریمه ثابت ۲۴ ساعته نباید هاردکد باشد"
    assert "continue" in window, "اکانت باید دوباره تلاش کند، نه اینکه خارج شود"


def test_write_forbidden_stops_and_explains():
    """این خطای پیکربندی است — کاربر باید بفهمد چه کار کند."""
    body = _parallel_worker_body()
    i = body.index("CHAT_WRITE_FORBIDDEN")
    # تا انتهای همین هندلر — طول ثابت با تغییر کد می‌شکند
    window = body[i:body.index("break", i) + len("break")]
    assert "live_status_text" in window, "کاربر باید علت را در مینی‌اپ ببیند"
    assert "بقیه اکانت‌ها ادامه" in window, (
        "این خطا مختص یک اکانت است — نباید کل عملیات را متوقف نشان دهد"
    )


@pytest.mark.parametrize("code", ["PEER_FLOOD", "CHAT_WRITE_FORBIDDEN"])
def test_interrupted_member_is_requeued(code):
    """
    وقتی ورکر به‌خاطر خطای اکانت متوقف می‌شود، کاربری که در دست داشت
    نباید هدر برود — ورکر دیگری باید بتواند اضافه‌اش کند.
    """
    window = _handler(code)
    assert "put_nowait(member)" in window, (
        f"بعد از {code} کاربر باید به صف برگردد"
    )


def test_too_many_channels_counts_as_skipped_not_failed():
    """تقصیر ما نیست — نباید جزو خطا شمرده شود."""
    body = _parallel_worker_body()
    i = body.index("USER_CHANNELS_TOO_MUCH")
    # فقط تا انتهای همین بلوک — نه هندلر بعدی
    window = body[i:body.index("continue", i) + len("continue")]
    assert "total_skipped += 1" in window
    assert "total_failed" not in window, "این خطا تقصیر ما نیست، نباید failed شمرده شود"


def test_success_still_requires_membership_confirmation():
    """
    مهم‌ترین تضمین: «۱۰۰ نوشت ولی ۱۰ تا شد» نباید برگردد.
    فقط بعد از confirm_joined باید موفق شمرده شود.
    """
    # ⚠️ لنگر عوض شد (۱.۹.۴): confirm_joined از ورکر موازی حذف شد چون
    # تا ۲ get_chat_member اضافه به ازای هر دعوت می‌زد و بازدهی را
    # چهار برابر پایین آورد. تضمین سر جایش است ولی حالا از
    # missing_invitees خوانده می‌شود که خود تلگرام رایگان برمی‌گرداند.
    # پنجره‌ی ۳۰۰ کاراکتری هم شکننده بود؛ به لنگر نحوی تغییر کرد.
    body = _parallel_worker_body()
    code = "\n".join(
        l for l in body.split("\n") if not l.lstrip().startswith("#")
    )
    checked = code.index("invite_did_not_join(invite_res, uid)")
    added = code.index("total_added += 1")
    assert checked < added, "نتیجه دعوت باید قبل از شمردن موفقیت بررسی شود"

    block = code[checked:code.index("continue", checked)]
    assert "total_skipped += 1" in block, (
        "کاربر تأییدنشده باید در «رد شده» برود، نه «موفق»"
    )
    assert "never_add_again" in block, (
        "کاربری که عضو نشد باید دائمی ثبت شود تا دوباره دعوت نشود"
    )


def test_write_forbidden_is_per_account_not_global():
    """
    🔍 یافته از داده واقعی (۱۶ اکانت‌دقیقه روی سرویس زنده):

        +989302206873 → 2 ادد موفق
        +989913928426 → 2 ادد موفق
        +989038511300 → 2 ادد موفق
        +989377649452 → 2 ادد موفق
        +989359428854 → 1 ادد موفق
        +989020212998 → 1 ادد موفق
        +989034694783 → 59 خطای CHAT_WRITE_FORBIDDEN ← فقط این یکی

    گروه در تمام این مدت can_send_messages=False داشت. پس بستن ارسال
    پیام مانع ادد نیست — تحلیل اولیه غلط بود. مشکل مختص یک اکانت است
    (عضو گروه نبودن یا محدود شدن).

    این تست جلوی برگشتن آن تحلیل غلط را می‌گیرد.
    """
    window = _handler("CHAT_WRITE_FORBIDDEN")

    # نباید کاربر را به تنظیمات گروه بفرستد — گمراه‌کننده است
    assert "Add Members را برای اعضا فعال" not in window, (
        "این خطا مختص یک اکانت است، نه تنظیمات گروه"
    )
    assert "بقیه اکانت‌ها ادامه" in window


def test_diagnose_does_not_blame_send_messages_permission():
    """
    can_send_messages=False نباید «آماده نبودن برای ادد» تلقی شود —
    داده واقعی خلافش را ثابت کرد.
    """
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    assert '"ok_for_adding": bool(can_inv)' in src, (
        "ok_for_adding فقط باید به can_invite_users وابسته باشد"
    )
    assert "تا وقتی ارسال پیام" not in src, "راهنمای غلط قبلی باید حذف شده باشد"
