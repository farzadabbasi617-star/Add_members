"""دکوریترهای Pyrogram باید روی تابع درست نشسته باشند.

پس‌زمینه (کشف‌شده در لاگ زنده‌ی سرور):

    AttributeError: 'Message' object has no attribute 'data'
      File "bot.py", line 5853, in _cb_impl

علت: دکوریتور `@app.on_message(...)` اشتباهاً روی `_cb_impl` نشسته
بود — تابعی که **کال‌بک کوئری** می‌گیرد، نه پیام. بین دکوریتور و
`async def` یک خط خالی بود که خطا را از چشم پنهان می‌کرد:

    @app.on_message(...)

    async def _cb_impl(c, q):      ← اشتباه
        d = q.data                 ← Message.data وجود ندارد ⇒ کرش

پیامد: هر پیام متنی ادمین کرش می‌کرد، و هندلر واقعیِ مراحل —
`steps()` — چون هیچ دکوریتوری نداشت **هرگز ثبت نمی‌شد**. یعنی کل
جریان گفت‌وگوی ربات (ورود شماره، کد تأیید، رمز دومرحله‌ای، آپلود
سشن و ۲۳ مرحله‌ی دیگر) از کار افتاده بود.

⚠️ نکته‌ی مهم: هیچ‌کدام از ۵۲۹ تست قبلی این را نگرفتند، چون همه‌شان
کد را به‌صورت متن یا AST بررسی می‌کردند و فایل کاملاً معتبر بود.
باگ فقط در زمان اجرا و با پیام واقعی بروز می‌کرد.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)

FUNCS = {n.name: n for n in ast.walk(TREE)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _decorators(fn):
    out = []
    for d in fn.decorator_list:
        seg = ast.get_source_segment(SRC, d) or ""
        out.append(seg)
    return out


def _has(fn, kind):
    return any(kind in d for d in _decorators(fn))


# ------------------------------------------------ تطابق دکوریتور و امضا

def test_callback_impl_is_not_registered_as_a_message_handler():
    """⚠️ همان باگی که در پروداکشن می‌ترکید."""
    fn = FUNCS["_cb_impl"]
    assert not _has(fn, "on_message"), (
        "_cb_impl کال‌بک کوئری می‌گیرد؛ ثبتش به‌عنوان هندلر پیام یعنی "
        "کرش روی q.data برای هر پیام"
    )


def test_the_real_text_handler_is_actually_registered():
    """اگر ثبت نشود، کل جریان گفت‌وگوی ربات بی‌صدا می‌میرد."""
    fn = FUNCS["steps"]
    assert _has(fn, "on_message"), (
        "steps() هندلر واقعی پیام‌هاست و باید با @app.on_message ثبت شود"
    )


def test_text_handler_accepts_both_text_and_documents():
    """آپلود فایل سشن هم از همین مسیر می‌آید."""
    deco = " ".join(_decorators(FUNCS["steps"]))
    assert "filters.text" in deco and "filters.document" in deco
    assert "ADMIN_ID" in deco, "فقط ادمین باید بتواند مراحل را پیش ببرد"
    assert 'command("start")' in deco, "/start هندلر جدای خودش را دارد"


@pytest.mark.parametrize("name", ["_cb_impl"])
def test_callback_style_functions_use_query_param(name):
    """امضای (c, q) یعنی کال‌بک؛ نباید با on_message جفت شود."""
    fn = FUNCS[name]
    args = [a.arg for a in fn.args.args]
    assert args[:2] == ["c", "q"]


def test_message_handlers_never_touch_query_only_attributes():
    """هر هندلر on_message که به `.data` دست بزند همان باگ را دارد.

    `Message` فیلد `data` ندارد (فقط `date`)، پس این دقیقاً همان
    اشتباه است — چه با تایپو، چه با دکوریتور جابه‌جا.
    """
    offenders = []
    for name, fn in FUNCS.items():
        if not _has(fn, "on_message"):
            continue
        body = ast.get_source_segment(SRC, fn) or ""
        second = [a.arg for a in fn.args.args][1:2]
        if second and f"{second[0]}.data" in body:
            offenders.append(name)
    assert not offenders, (
        f"این هندلرهای پیام به .data دسترسی دارند: {offenders}"
    )


def test_no_decorator_is_separated_from_its_function_by_blank_lines():
    """خط خالی بین دکوریتور و تابع، خطا را از چشم پنهان می‌کند.

    پایتون آن را می‌پذیرد، ولی خواننده تصور می‌کند دکوریتور به تابعِ
    *قبلی* تعلق دارد. دقیقاً همین چیدمان باعث شد باگ ماه‌ها دیده نشود.
    """
    lines = SRC.splitlines()
    bad = []
    for name, fn in FUNCS.items():
        if not fn.decorator_list:
            continue
        last_deco_end = max(
            getattr(d, "end_lineno", d.lineno) for d in fn.decorator_list)
        # خطوط بین آخرین دکوریتور و خود تابع
        between = lines[last_deco_end:fn.lineno - 1]
        if any(not ln.strip() for ln in between):
            bad.append(f"{name} (خط {fn.lineno})")
    assert not bad, f"دکوریتور با خط خالی از تابعش جدا شده: {bad}"


def test_every_step_handler_is_reachable_through_the_router():
    """مراحل باید در StepRouter ثبت شده باشند وگرنه هرگز اجرا نمی‌شوند."""
    registered = sum(1 for fn in FUNCS.values()
                     if any("_STEPS.step" in d for d in _decorators(fn)))
    assert registered >= 10, (
        f"فقط {registered} مرحله ثبت شده — انتظار می‌رفت بیشتر باشد"
    )
