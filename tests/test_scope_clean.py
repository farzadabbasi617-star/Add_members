"""
تست‌های نگهبان دامنه پروژه.

این ربات فقط برای «اسکرپ و ادد ممبر» است. این تست‌ها جلوی برگشتن
ماژول‌های بی‌ربط (دادگاه/داوری، چت‌بات Flexa، کریپتو، دانلودر) را می‌گیرند
و باگ تداخل هندلرها را دوباره پیدا می‌کنند.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ماژول‌هایی که عمداً حذف شده‌اند و نباید برگردند
REMOVED_MODULES = [
    "hunter.py",              # crypto-treasure-hunter — ریپوی جدا
    "project_finder.py",      # اسکنر پروژه گیت‌هاب — بی‌ربط
    "downloader.py",          # دانلودر مدیا — بی‌ربط
    "simple_flow.py",         # کد مرده
    "patch.py",               # کد مرده
    "patch_callbacks.py",     # کد مرده
    "ultimate_accounts.py",   # کد مرده
    "ai_chat.py",             # چت‌بات Flexa
    "flexa_integration.py",   # چت‌بات Flexa
    "flexa_auto_respond.py",  # چت‌بات Flexa
    "chat_history.py",        # حافظه چت Flexa
]


def test_removed_modules_stay_removed():
    """ماژول‌های خارج از دامنه نباید دوباره اضافه شوند."""
    present = [m for m in REMOVED_MODULES if (ROOT / m).exists()]
    assert not present, f"ماژول‌های خارج از دامنه برگشته‌اند: {present}"


def test_no_dangling_imports_of_removed_modules():
    """هیچ فایلی نباید ماژول حذف‌شده را import کند."""
    stems = [m[:-3] for m in REMOVED_MODULES]
    pattern = re.compile(
        r"^\s*(?:from\s+(" + "|".join(stems) + r")\b|import\s+(" + "|".join(stems) + r")\b)",
        re.MULTILINE,
    )
    offenders = []
    for py in ROOT.glob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            offenders.append(py.name)
    assert not offenders, f"import معلق به ماژول حذف‌شده در: {offenders}"


def test_no_court_or_tribunal_code():
    """کد بات «دادگاه/داوری» نباید وارد این ریپو شود."""
    # این‌ها نشانگرهای یکتای بات haghbakie در ریپوی Flexa_app هستند
    markers = ["SUBMIT_STORY", "SUBMIT_SIDE_A", "SUBMIT_SIDE_B", "pending_stories"]
    offenders = []
    for py in ROOT.glob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for marker in markers:
            if marker in text:
                offenders.append(f"{py.name}:{marker}")
    assert not offenders, f"کد دادگاه پیدا شد: {offenders}"


def test_single_framework_only():
    """فقط Pyrogram — نباید python-telegram-bot قاطی شود."""
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pyrogram" in reqs, "pyrogram باید در requirements باشد"
    assert "python-telegram-bot" not in reqs, (
        "python-telegram-bot با pyrogram قاطی شده — این ریپو فقط Pyrogram است"
    )


def test_catchall_group_text_handlers_are_separated():
    """
    چند هندلر روی «هر پیام متنی گروه» باید group= متفاوت داشته باشند.

    در Pyrogram اگر دو هندلر در یک group ثبت شوند، فقط اولی اجرا می‌شود
    و بقیه بی‌صدا خاموش می‌مانند. این دقیقاً باگی بود که defender.monitor_message
    را از کار انداخته بود.
    """
    text = (ROOT / "bot.py").read_text(encoding="utf-8")
    catchall = re.compile(
        r"^@app\.on_message\(\s*(?:filters\.text\s*&\s*filters\.group"
        r"|filters\.group\s*&\s*filters\.text)([^)]*)\)",
        re.MULTILINE,
    )
    groups = []
    for match in catchall.finditer(text):
        tail = match.group(1)
        found = re.search(r"group\s*=\s*(\d+)", tail)
        groups.append(int(found.group(1)) if found else 0)

    assert len(groups) == len(set(groups)), (
        f"هندلرهای catch-all روی متنِ گروه، group تکراری دارند: {groups}. "
        "هر کدام باید group= یکتا داشته باشد وگرنه فقط اولی اجرا می‌شود."
    )
