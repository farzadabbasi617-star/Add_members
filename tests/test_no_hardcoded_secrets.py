"""
نگهبان اسرار — جلوگیری از برگشتن مقادیر حساس به داخل کد.

تا نسخه ۱.۴.۰ توکن بات و رشته اتصال کامل دیتابیس (با رمز) به‌عنوان
مقدار پیش‌فرض داخل config.py و db.py هاردکد شده بودند و روی مخزن
عمومی منتشر می‌شدند. این تست‌ها نمی‌گذارند دوباره اتفاق بیفتد.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# فایل‌هایی که نمونه/مستند هستند و طبیعتاً مقدار جعلی دارند
ALLOWED = {".env.example", "README.md", "CHANGELOG.md"}

# توکن بات تلگرام: 8-10 رقم + ":AA" + حدود ۳۳ کاراکتر
BOT_TOKEN_RE = re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}")

# رشته اتصال با رمز واقعی داخلش
DB_URL_RE = re.compile(r"postgres(?:ql)?://[^\s\"']+:[^\s@\"']+@[^\s\"']+")

# مقادیر جعلی که در نمونه‌ها مجازند
PLACEHOLDER = re.compile(
    r"user:pass|invalid:invalid|u:p@|YOUR|REPLACE|example\.com|<[^>]+>|xxx|123456",
    re.IGNORECASE,
)


def _scan(pattern):
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name in ALLOWED:
            continue
        if path.suffix not in {".py", ".yaml", ".yml", ".toml", ".json", ".sh", ".md", ""}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for match in pattern.finditer(text):
            if PLACEHOLDER.search(match.group(0)):
                continue
            line = text[: match.start()].count("\n") + 1
            hits.append(f"{path.relative_to(ROOT)}:{line}")
    return hits


def test_no_hardcoded_bot_token():
    hits = _scan(BOT_TOKEN_RE)
    assert not hits, (
        f"توکن بات هاردکد شده در: {hits}. "
        "توکن باید فقط از متغیر محیطی BOT_TOKEN خوانده شود."
    )


def test_no_hardcoded_database_url():
    hits = _scan(DB_URL_RE)
    assert not hits, (
        f"رشته اتصال دیتابیس با رمز، هاردکد شده در: {hits}. "
        "فقط از متغیر محیطی DATABASE_URL استفاده کنید."
    )


def test_config_defaults_are_empty():
    """مقادیر حساس در config.py نباید پیش‌فرض واقعی داشته باشند."""
    import config

    assert config.BOT_TOKEN in ("", "test:token", None) or config.BOT_TOKEN.startswith(
        ("123", "test")
    ), "BOT_TOKEN نباید مقدار پیش‌فرض واقعی داشته باشد"

    src = (ROOT / "config.py").read_text(encoding="utf-8")
    assert 'os.environ.get("DATABASE_URL", "")' in src, (
        "DATABASE_URL باید پیش‌فرض خالی داشته باشد"
    )


def test_assert_env_reports_missing():
    """assert_env باید متغیرهای جا افتاده را گزارش کند."""
    import config

    missing = config.assert_env(strict=False)
    assert isinstance(missing, list)


def test_assert_env_raises_when_incomplete(monkeypatch):
    """در حالت strict باید با SystemExit و پیام واضح متوقف شود."""
    import config

    monkeypatch.setattr(config, "REQUIRED_ENV", {"BOT_TOKEN": "", "API_ID": 0})
    with pytest.raises(SystemExit) as err:
        config.assert_env(strict=True)
    assert "BOT_TOKEN" in str(err.value)
