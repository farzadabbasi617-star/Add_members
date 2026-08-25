"""تست‌های مسیریاب کال‌بک.

نکتهٔ مهم: این تست‌ها رفتار *انتخاب هندلر* را قفل می‌کنند، چون همین انتخاب بود
که در نسخهٔ قبلی به ترتیب دستیِ ۱۲۸ شرط ``if`` وابسته بود. اگر ترتیب اشتباه
شود، یک دکمه بی‌صدا کار نمی‌کند و هیچ خطایی هم بالا نمی‌آید.
"""

import asyncio

import pytest

from callback_router import CallbackRouter


def _router():
    r = CallbackRouter()

    @r.exact("home")
    async def home(q):  # noqa: ARG001
        return "home"

    @r.exact("bg_menu")
    async def bg_menu(q):  # noqa: ARG001
        return "bg_menu"

    @r.prefix("bg_acc_")
    async def bg_acc(q):  # noqa: ARG001
        return "bg_acc"

    @r.prefix("atk_target_")
    async def atk_target(q):  # noqa: ARG001
        return "atk_target"

    @r.prefix("atk_target_manual")
    async def atk_target_manual(q):  # noqa: ARG001
        return "atk_target_manual"

    return r


def _call(handler):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(handler(None))


def test_exact_match_wins():
    r = _router()
    assert _call(r.resolve("home")) == "home"
    assert _call(r.resolve("bg_menu")) == "bg_menu"


def test_prefix_match():
    r = _router()
    assert _call(r.resolve("bg_acc_09123456789")) == "bg_acc"


def test_longest_prefix_wins():
    """``atk_target_manual`` نباید توسط ``atk_target_`` بلعیده شود.

    در کد قبلی این با شرط دستیِ ``and not d.startswith("atk_target_manual")``
    تضمین می‌شد — یعنی هر پیشوند جدید نیاز به یادآوری دستی داشت.
    """
    r = _router()
    assert _call(r.resolve("atk_target_manual")) == "atk_target_manual"
    assert _call(r.resolve("atk_target_12345")) == "atk_target"


def test_exact_beats_prefix():
    r = CallbackRouter()

    @r.prefix("bg_")
    async def generic(q):  # noqa: ARG001
        return "generic"

    @r.exact("bg_menu")
    async def specific(q):  # noqa: ARG001
        return "specific"

    assert _call(r.resolve("bg_menu")) == "specific"
    assert _call(r.resolve("bg_other")) == "generic"


def test_unknown_callback_returns_none():
    r = _router()
    assert r.resolve("nothing_here") is None
    assert r.resolve("") is None
    assert r.resolve(None) is None


def test_duplicate_registration_is_rejected():
    """ثبت دوتایی یک کلید یعنی یکی از دو هندلر هرگز اجرا نمی‌شود.

    در فایل ۳۲۳۳ خطی این خطا کاملاً نامرئی بود؛ اینجا بلافاصله می‌شکند.
    """
    r = CallbackRouter()

    @r.exact("dup")
    async def first(q):  # noqa: ARG001
        return 1

    with pytest.raises(ValueError, match="تکراری"):

        @r.exact("dup")
        async def second(q):  # noqa: ARG001
            return 2


def test_duplicate_prefix_is_rejected():
    r = CallbackRouter()

    @r.prefix("x_")
    async def first(q):  # noqa: ARG001
        return 1

    with pytest.raises(ValueError, match="تکراری"):

        @r.prefix("x_")
        async def second(q):  # noqa: ARG001
            return 2


def test_multiple_keys_on_one_handler():
    r = CallbackRouter()

    @r.exact("a", "b", "c")
    async def shared(q):  # noqa: ARG001
        return "shared"

    for key in ("a", "b", "c"):
        assert _call(r.resolve(key)) == "shared"


def test_len_and_introspection():
    r = _router()
    assert len(r) == 5
    assert "home" in r.exact_keys
    # پیشوندها باید از بلند به کوتاه مرتب باشند
    assert r.prefixes[0] == "atk_target_manual"
