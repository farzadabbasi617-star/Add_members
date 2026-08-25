"""تست‌های یکپارچگی جدول اعزام کال‌بک.

این‌ها سه باگ واقعی را قفل می‌کنند که هنگام تبدیل زنجیرهٔ ۱۲۸ شرطیِ ``_cb_impl``
به مسیریاب پیدا شدند. هر سه از یک ریشه می‌آمدند: وقتی انتخاب هندلر با اسکن خطیِ
دستی انجام شود، «کدام شرط اول می‌آید» یک جزئیات پنهان اما تعیین‌کننده است و
هیچ ابزاری خطا نمی‌دهد وقتی اشتباه باشد.
"""

import re
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent / "bot.py"
SOURCE = BOT.read_text(encoding="utf-8")


def _registered_exact():
    keys = []
    for m in re.findall(r"^@_CB\.exact\(([^)]*)\)", SOURCE, re.M):
        keys += re.findall(r'"([^"]+)"', m)
    return keys


def _registered_prefix():
    keys = []
    for m in re.findall(r"^@_CB\.prefix\(([^)]*)\)", SOURCE, re.M):
        keys += re.findall(r'"([^"]+)"', m)
    return keys


def test_no_duplicate_exact_registrations():
    """ثبت دوتایی یعنی یکی از دو هندلر مرده است.

    کد قبلی سه مورد داشت: ``noop``، ``parallel_mode_safe`` و
    ``parallel_mode_fast``. نسخهٔ دوم هر سه هرگز اجرا نمی‌شد چون شرط هم‌نامِ
    بالاتر زودتر ``return`` می‌کرد.
    """
    keys = _registered_exact()
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"کلید تکراری: {sorted(dupes)}"


def test_no_duplicate_prefix_registrations():
    keys = _registered_prefix()
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"پیشوند تکراری: {sorted(dupes)}"


def test_specific_prefix_not_shadowed_by_general_one():
    """پیشوند خاص‌تر باید برندهٔ پیشوند عمومی‌تر باشد.

    ``show_list_source_`` زیر ``show_list_`` ثبت شده بود، پس دکمهٔ «کاربران»
    (که ``show_list_source_<chat_id>`` می‌فرستد) به هندلر اشتباه می‌رسید و آنجا
    ``int("source")`` صدا زده می‌شد -> ValueError. عملاً آن دکمه همیشه خراب بود.

    مسیریاب بلندترین پیشوند را انتخاب می‌کند، پس این حالت ساختاراً درست است؛
    این تست فقط تضمین می‌کند قاعده حفظ شود.
    """
    import sys

    sys.path.insert(0, str(BOT.parent))
    from callback_router import CallbackRouter

    router = CallbackRouter()

    @router.prefix("show_list_")
    async def general(q):  # noqa: ARG001
        return "general"

    @router.prefix("show_list_source_")
    async def specific(q):  # noqa: ARG001
        return "specific"

    assert router.resolve("show_list_source_123").__name__ == "specific"
    assert router.resolve("show_list_0").__name__ == "general"


def test_every_emitted_callback_data_has_a_handler():
    """هر دکمه‌ای که ساخته می‌شود باید هندلری داشته باشد.

    دکمهٔ بدون هندلر یعنی کاربر کلیک می‌کند و هیچ اتفاقی نمی‌افتد — بدترین نوع
    خرابی، چون هیچ خطایی هم در لاگ نیست.
    """
    exact = set(_registered_exact())
    prefixes = _registered_prefix()

    # فقط callback_dataهای ثابت (بدون f-string) قابل بررسی خودکارند.
    literals = set(re.findall(r'callback_data="([a-z0-9_]+)"', SOURCE))

    # دکمه‌های شکسته‌ای که از قبل وجود داشتند و این تست کشفشان کرد.
    # عمداً اینجا فهرست شده‌اند تا (الف) تست سبز بماند و رگرسیون‌های *جدید* را
    # بگیرد، و (ب) بدهی فنی مخفی نشود. هر کدام یک دکمهٔ واقعی در رابط کاربری
    # است که کلیک روی آن هیچ کاری نمی‌کند:
    #
    #   parallel_start_add  — دکمهٔ «▶️ شروع» در منوی ادد موازی (خطوط ۵۰۶۴، ۵۰۸۴)
    #   ig_follow_menu      — دکمهٔ «🔙 منوی follow» اینستاگرام
    #   ig_follow_stats     — دکمهٔ «📊 آمار follow» اینستاگرام
    #
    # این‌ها پیش از ریفکتور هم بی‌اثر بودند؛ رفع‌شان نیازمند تصمیم محصولی است
    # (آیا این قابلیت‌ها اصلاً باید وجود داشته باشند؟) نه صرفاً تغییر ساختار.
    known_broken = {"parallel_start_add", "ig_follow_menu", "ig_follow_stats"}
    ignore = {"noop"} | known_broken

    missing = sorted(
        d
        for d in literals - ignore
        if d not in exact and not any(d.startswith(p) for p in prefixes)
    )
    assert not missing, f"دکمه بدون هندلر: {missing}"


def test_dispatcher_answers_unknown_callbacks():
    """کال‌بک ناشناخته باید حداقل اسپینر دکمه را ببندد.

    در نسخهٔ قبلی تابع بی‌صدا تا انتها می‌رفت و دکمه تا تایم‌اوت تلگرام
    می‌چرخید.
    """
    assert "handler = _CB.resolve(d)" in SOURCE
    assert "if handler is None:" in SOURCE
    body = SOURCE.split("if handler is None:", 1)[1][:400]
    assert "q.answer()" in body


def test_cb_impl_is_a_thin_dispatcher():
    """``_cb_impl`` باید کوتاه بماند.

    قبلاً ۳۲۳۳ خط بود. اگر کسی دوباره منطق را داخلش بنویسد، این تست می‌شکند.
    """
    lines = SOURCE.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("async def _cb_impl"))
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i] and not lines[i][0].isspace() and "def " in lines[i]
        ),
        len(lines),
    )
    assert end - start < 40, f"_cb_impl دوباره بزرگ شده: {end - start} خط"


def test_steps_impl_is_a_thin_dispatcher():
    """``_steps_impl`` هم مثل ``_cb_impl`` باید کوتاه بماند.

    قبلاً ۱۱۰۰ خط بود. دو شاخهٔ quick_step عمداً درون‌خطی مانده‌اند چون پیش از
    گاردِ «step خالی» اجرا می‌شوند و وضعیتشان در کلید دیگری نگه داشته می‌شود.
    """
    lines = SOURCE.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("async def _steps_impl"))
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i] and not lines[i][0].isspace() and ("def " in lines[i] or lines[i].startswith("@"))
        ),
        len(lines),
    )
    assert end - start < 70, f"_steps_impl دوباره بزرگ شده: {end - start} خط"


def test_show_account_picker_takes_q_explicitly():
    """این تابع قبلاً داخل ``_cb_impl`` تعریف شده بود و ``q`` را از closure
    می‌گرفت. بعد از انتقال به سطح ماژول، اگر ``q`` پارامتر نباشد در زمان اجرا
    NameError می‌دهد — چیزی که تست‌های واحد نمی‌گیرند چون این مسیر شبکه لازم
    دارد. pyflakes آن را گرفت؛ این تست جلوی برگشتش را می‌گیرد.
    """
    assert "async def show_account_picker(q, callback, back_cb, mode_label):" in SOURCE
    # هیچ فراخوانی‌ای نباید q را جا بیندازد
    calls = re.findall(r"await show_account_picker\(([^)]*)\)", SOURCE)
    for call in calls:
        assert call.split(",")[0].strip() == "q", f"فراخوانی بدون q: {call}"
