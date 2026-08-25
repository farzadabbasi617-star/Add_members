"""
تست‌های افزودن اکانت از مینی‌اپ.

مالک می‌خواهد شماره‌های جدید را مستقیم از مینی‌اپ اضافه کند (برای ادد
موازی) و شماره‌های خراب را حذف کند.

نکات حساس این فلو:
- کد تأیید و رمز دو مرحله‌ای نباید هرگز لاگ یا ذخیره شوند
- جلسه ناتمام نباید سشن نیمه‌کاره روی دیسک جا بگذارد
- شماره ایرانی در هر فرمتی باید یکسان‌سازی شود
"""
import pathlib
import re

import pytest

import account_login
from account_login import normalize_phone

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean():
    account_login.reset_for_tests()
    yield
    account_login.reset_for_tests()


# ───────────────────── یکسان‌سازی شماره ─────────────────────

@pytest.mark.parametrize("raw", [
    "+989121234567",
    "09121234567",
    "9121234567",
    "989121234567",
    "0912 123 4567",
    "+98 912-123-4567",
    "۰۹۱۲۱۲۳۴۵۶۷",          # ارقام فارسی
    "٠٩١٢١٢٣٤٥٦٧",          # ارقام عربی
    "(0912) 123-4567",
])
def test_iranian_numbers_normalize_to_one_form(raw):
    assert normalize_phone(raw) == "+989121234567"


@pytest.mark.parametrize("raw", ["", None, "abc", "12", "۰۹"])
def test_invalid_numbers_rejected(raw):
    assert normalize_phone(raw) is None


def test_foreign_number_preserved():
    """فقط شماره ایران پیش‌شماره می‌گیرد؛ بقیه دست‌نخورده می‌مانند."""
    assert normalize_phone("+14155552671") == "+14155552671"


# ───────────────────── اعتبارسنجی ورودی ─────────────────────

async def test_start_rejects_invalid_phone():
    ok, msg, needs = await account_login.start("abc")
    assert not ok and "نامعتبر" in msg and needs is None


async def test_start_rejects_duplicate(monkeypatch):
    monkeypatch.setattr(account_login.db, "load_accounts", lambda: {"+989121234567": {}})
    ok, msg, _ = await account_login.start("09121234567")
    assert not ok and "قبل" in msg


async def test_code_without_session_is_rejected():
    ok, msg, _ = await account_login.submit_code("+989121234567", "12345")
    assert not ok and "منقضی" in msg


async def test_password_without_session_is_rejected():
    ok, msg, _ = await account_login.submit_password("+989121234567", "secret")
    assert not ok and "منقضی" in msg


async def test_empty_code_is_rejected(monkeypatch):
    account_login._pending["+989121234567"] = {
        "client": None, "hash": "h", "ts": 9e18, "step": "code", "tmp_name": "/tmp/x",
    }
    ok, msg, needs = await account_login.submit_code("+989121234567", "")
    assert not ok and needs == "code"


async def test_code_strips_non_digits(monkeypatch):
    """کاربر ممکن است «۱۲ ۳۴۵» بفرستد."""
    seen = {}

    class _App:
        async def sign_in(self, phone, hash_, code):
            seen["code"] = code

    class _Client:
        app = _App()

    account_login._pending["+989121234567"] = {
        "client": _Client(), "hash": "h", "ts": 9e18, "step": "code",
        "tmp_name": "/tmp/x", "device_fp": {},
    }
    monkeypatch.setattr(account_login, "_persist", lambda p: _ok())
    await account_login.submit_code("+989121234567", "1 2-3 4 5")
    assert seen["code"] == "12345"


async def _ok():
    return True, "ok", None


# ───────────────────── انقضای جلسه ─────────────────────

def test_expired_sessions_are_purged():
    account_login._pending["+989121234567"] = {
        "client": None, "hash": "h", "ts": 0, "step": "code", "tmp_name": None,
    }
    account_login._purge_expired()
    assert "+989121234567" not in account_login._pending


def test_pending_phones_hides_expired():
    account_login._pending["+989121234567"] = {
        "client": None, "hash": "h", "ts": 0, "step": "code", "tmp_name": None,
    }
    assert account_login.pending_phones() == {}


# ───────────────────── امنیت ─────────────────────

def test_secrets_are_never_logged():
    """
    کد تأیید و رمز دو مرحله‌ای نباید در print/log ظاهر شوند — لاگ Render
    ماندگار است و هرکس دسترسی داشته باشد می‌تواند اکانت را بدزدد.
    """
    src = (ROOT / "account_login.py").read_text(encoding="utf-8")
    for line in src.split("\n"):
        stripped = line.strip()
        if not (stripped.startswith("print(") or ".info(" in stripped or ".error(" in stripped):
            continue
        assert "password" not in stripped.lower(), f"رمز در لاگ: {stripped[:80]}"
        assert not re.search(r"\bcode\b", stripped), f"کد تأیید در لاگ: {stripped[:80]}"


def test_secrets_are_not_persisted():
    """کد و رمز فقط در حافظه‌اند و هرگز در دیتابیس نمی‌روند."""
    src = (ROOT / "account_login.py").read_text(encoding="utf-8")
    for call in re.findall(r"db\.\w+\([^)]*\)", src):
        assert "password" not in call.lower()
        assert "code" not in call.lower()


def test_session_ttl_is_short():
    """جلسه نباید بی‌نهایت باز بماند."""
    assert 60 <= account_login._SESSION_TTL <= 900


# ───────────────────── اتصال به وب‌اپ ─────────────────────

def test_all_three_endpoints_registered():
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    for route in ("/api/accounts/add", "/api/accounts/add/code", "/api/accounts/add/cancel"):
        assert f"'{route}'" in src, f"مسیر {route} ثبت نشده"


def test_ui_has_all_three_steps():
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    for element in ("add-step-phone", "add-step-code", "add-step-pass"):
        assert element in src, f"مرحله {element} در UI نیست"
    for fn in ("addAccountStart", "addAccountCode", "addAccountPassword", "addAccountCancel"):
        assert f"function {fn}" in src, f"تابع {fn} در فرانت‌اند نیست"


def test_password_field_is_masked():
    """رمز نباید روی صفحه قابل خواندن باشد."""
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    assert re.search(r'id="add-pass"[^>]*type="password"', src), (
        "فیلد رمز باید type=password باشد"
    )
