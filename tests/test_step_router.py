"""تست‌های مسیریاب مراحل گفت‌وگو.

نکتهٔ اصلی این تست‌ها **گاردها** هستند. سه مرحله در ``_steps_impl`` شرط اضافی
داشتند (``and m.document`` یا ``and m.text``) و رفتار درست این است که وقتی
گارد برقرار نیست، پیام به مرحلهٔ دیگری بیفتد — نه اینکه هندلر با پیام نامناسب
اجرا شود. اگر این جزئیات از بین برود، کاربری که به‌جای فایل متن می‌فرستد در
حالت آپلود گیر می‌کند و هیچ خطایی هم نمی‌بیند.
"""

import asyncio

import pytest

from step_router import StepRouter, has_document, has_text


class FakeMessage:
    def __init__(self, document=None, text=None):
        self.document = document
        self.text = text


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_plain_step_matches():
    r = StepRouter()

    @r.step("phone")
    async def handler(m):  # noqa: ARG001
        return "phone"

    assert _run(r.resolve("phone", FakeMessage())(None)) == "phone"


def test_unknown_step_returns_none():
    r = StepRouter()

    @r.step("phone")
    async def handler(m):  # noqa: ARG001
        return "phone"

    assert r.resolve("nope", FakeMessage()) is None


def test_empty_step_returns_none():
    """``if not step: return`` در کد اصلی — باید حفظ شود."""
    r = StepRouter()

    @r.step("phone")
    async def handler(m):  # noqa: ARG001
        return "phone"

    assert r.resolve("", FakeMessage()) is None
    assert r.resolve(None, FakeMessage()) is None


def test_document_guard_blocks_text_only_message():
    """مرحلهٔ آپلود سشن فقط با فایل اجرا می‌شود.

    در کد اصلی این ``step == "upload_session" and m.document`` بود؛ پیام متنی
    از کنارش رد می‌شد و به شاخه‌های بعدی می‌رسید.
    """
    r = StepRouter()

    @r.step("upload_session", guard=has_document)
    async def upload(m):  # noqa: ARG001
        return "upload"

    assert r.resolve("upload_session", FakeMessage(text="سلام")) is None
    assert _run(r.resolve("upload_session", FakeMessage(document=object()))(None)) == "upload"


def test_text_guard_blocks_document_only_message():
    r = StepRouter()

    @r.step("upload_session_phone", guard=has_text)
    async def handler(m):  # noqa: ARG001
        return "phone"

    assert r.resolve("upload_session_phone", FakeMessage(document=object())) is None
    assert _run(r.resolve("upload_session_phone", FakeMessage(text="0912"))(None)) == "phone"


def test_guarded_then_unguarded_falls_through():
    """اگر گارد رد شود، هندلر بعدیِ همان مرحله امتحان می‌شود.

    این همان معنای ``and`` در زنجیرهٔ اصلی است: شرط رد می‌شود و اجرا به شاخهٔ
    بعدی می‌رسد.
    """
    r = StepRouter()

    @r.step("x", guard=has_document)
    async def with_doc(m):  # noqa: ARG001
        return "with_doc"

    @r.step("x")
    async def fallback(m):  # noqa: ARG001
        return "fallback"

    assert _run(r.resolve("x", FakeMessage(document=object()))(None)) == "with_doc"
    assert _run(r.resolve("x", FakeMessage(text="hi"))(None)) == "fallback"


def test_two_unguarded_handlers_for_same_step_is_rejected():
    """دومین هندلر بدون گارد هرگز اجرا نمی‌شود — همان اشتباهی که در کد
    کال‌بک سه بار تکرار شده بود."""
    r = StepRouter()

    @r.step("x")
    async def first(m):  # noqa: ARG001
        return 1

    with pytest.raises(ValueError, match="هرگز اجرا نمی‌شود"):

        @r.step("x")
        async def second(m):  # noqa: ARG001
            return 2


def test_one_handler_for_several_steps():
    """``step in ["adder_target", "adder_target_manual"]`` در کد اصلی."""
    r = StepRouter()

    @r.step("adder_target", "adder_target_manual")
    async def handler(m):  # noqa: ARG001
        return "target"

    for s in ("adder_target", "adder_target_manual"):
        assert _run(r.resolve(s, FakeMessage())(None)) == "target"


def test_introspection():
    r = StepRouter()

    @r.step("a", "b")
    async def handler(m):  # noqa: ARG001
        return None

    assert r.steps == ["a", "b"]
    assert len(r) == 2
