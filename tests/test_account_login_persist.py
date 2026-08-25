"""ذخیره‌سازی سشن بعد از لاگین موفق.

باگ واقعی (۱.۹.۲): `_persist` دستی دنبال `entry["tmp_name"] + ".session"`
می‌گشت، ولی `AdvancedScraper(force_fresh=True)` نام فایل را خودش عوض
می‌کند (`_newtmp_<phone>_<ts>_<rand>`) و `tmp_name` را نادیده می‌گیرد.
نتیجه: `os.path.exists(src)` همیشه False بود، سشن هرگز منتقل نمی‌شد،
`save_session_blob` اجرا نمی‌شد، ولی تابع پیام **موفقیت** برمی‌گرداند.
کاربر «با موفقیت اضافه شد» می‌دید و بعد اکانت «خراب» بود.
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOGIN = (ROOT / "account_login.py").read_text(encoding="utf-8")
ATTACKER = (ROOT / "attacker.py").read_text(encoding="utf-8")
TREE = ast.parse(LOGIN)


def _func(name, tree=None):
    for node in ast.walk(tree or TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"تابع {name} پیدا نشد")


PERSIST_RAW = ast.get_source_segment(LOGIN, _func("_persist"))


def _strip_comments(src):
    """حذف کامنت‌ها.

    ⚠️ لازم است: کامنتِ توضیح باگ عیناً `entry["tmp_name"]` و
    `save_session_blob` را نقل می‌کند و باعث مثبت/منفی کاذب در
    تست‌های مبتنی بر ترتیب می‌شد.
    """
    out = []
    for line in src.split("\n"):
        st = line.lstrip()
        if st.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


PERSIST = _strip_comments(PERSIST_RAW)


# ------------------------------------------------- بازتولید خود باگ

def test_force_fresh_ignores_passed_session_name():
    """ریشه‌ی باگ: نام سشن پاس‌شده وقتی force_fresh است نادیده گرفته می‌شود."""
    init = ast.get_source_segment(ATTACKER, _func("__init__", ast.parse(ATTACKER)))
    assert "_newtmp_" in init, "الگوی نام‌گذاری موقت تغییر کرده"
    m = re.search(r"if phone and force_fresh:(.*?)elif", init, re.S)
    assert m, "شاخه‌ی force_fresh پیدا نشد"
    branch = m.group(1)
    assert "session_path = os.path.join(SESSIONS_DIR, tmp_fname)" in branch, (
        "force_fresh باید مسیر خودش را بسازد — اگر این عوض شد، فرض تست باطل است"
    )


def test_login_never_trusts_tmp_name_alone():
    """tmp_name نباید تنها منبع نام فایل باشد."""
    # اگر tmp_name استفاده می‌شود، باید فقط به‌عنوان گزینه‌ی پشتیبان باشد
    if 'entry["tmp_name"]' in PERSIST:
        assert "candidates" in PERSIST, (
            "tmp_name فقط به‌عنوان یکی از چند گزینه مجاز است، نه منبع یکتا"
        )
        i_real = PERSIST.index('getattr(getattr(client, "app", None), "name", None)')
        i_tmp = PERSIST.index('entry["tmp_name"]')
        assert i_real < i_tmp, "نام واقعی کلاینت باید قبل از tmp_name امتحان شود"


# ------------------------------------------------- مسیر درست

def test_uses_persist_to_permanent():
    """باید از متد خود کلاینت استفاده شود که نام واقعی را می‌داند."""
    assert "persist_to_permanent()" in PERSIST, (
        "persist_to_permanent باید صدا زده شود — این متد .wal/.shm را هم منتقل می‌کند"
    )


def test_persist_to_permanent_still_exists_in_attacker():
    """نگهبان یکپارچگی: متدی که به آن تکیه می‌کنیم باید موجود باشد."""
    assert "async def persist_to_permanent" in ATTACKER


def test_persist_to_permanent_called_before_disconnect():
    """این متد خودش storage را می‌بندد؛ بعد از disconnect بی‌فایده است."""
    i_persist = PERSIST.index("persist_to_permanent()")
    i_disc = PERSIST.index("_disconnect(client.app)")
    assert i_persist < i_disc, (
        "persist_to_permanent باید قبل از _disconnect صدا زده شود"
    )


def test_fallback_reads_real_client_name():
    """مسیر جایگزین باید نام واقعی فایل را از کلاینت بخواند."""
    assert 'getattr(getattr(client, "app", None), "name", None)' in PERSIST, (
        "نام واقعی سشن باید از client.app.name خوانده شود نه از tmp_name"
    )


# ------------------------------------------------- عدم موفقیت کاذب

def test_missing_session_returns_failure():
    """⚠️ مهم‌ترین تست: نبود فایل سشن نباید «موفق» گزارش شود."""
    m = re.search(r"if not os\.path\.exists\(dst\):(.*?)\n        try:", PERSIST, re.S)
    assert m, "گارد «سشن ذخیره نشد» وجود ندارد"
    guard = m.group(1)
    assert "return False" in guard, (
        "وقتی سشن ذخیره نشده باید False برگردد — قبلاً پیام موفقیت می‌داد "
        "و کاربر بعداً اکانت را «خراب» می‌دید"
    )


def test_failed_persist_removes_half_saved_account():
    """اکانت بدون سشن نباید در دیتابیس بماند."""
    m = re.search(r"if not os\.path\.exists\(dst\):(.*?)\n        try:", PERSIST, re.S)
    guard = m.group(1)
    assert "delete_account" in guard, (
        "اکانت نیمه‌ذخیره باید حذف شود وگرنه در فهرست «خراب» می‌ماند"
    )


def test_delete_account_exists_in_db():
    """نگهبان یکپارچگی برای تابعی که در مسیر خطا صدا می‌زنیم."""
    dbsrc = (ROOT / "db.py").read_text(encoding="utf-8")
    assert "def delete_account(" in dbsrc


def test_save_blob_only_after_file_confirmed():
    """بکاپ دیتابیس باید بعد از تأیید وجود فایل باشد."""
    i_guard = PERSIST.index("if not os.path.exists(dst):")
    i_blob = PERSIST.index("save_session_blob")
    assert i_guard < i_blob, "گارد وجود فایل باید قبل از save_session_blob باشد"


def test_success_message_reports_session_size():
    """لاگ موفقیت باید اندازه‌ی سشن را بدهد تا فایل صفر-بایتی معلوم شود."""
    assert "getsize(dst)" in PERSIST, (
        "اندازه‌ی فایل در لاگ موفقیت کمک می‌کند سشن خالی زود تشخیص داده شود"
    )


def test_persist_errors_are_logged_not_swallowed():
    """درس تکرارشده: except خالی علت را ماه‌ها پنهان می‌کند."""
    m = re.search(r"persist_to_permanent\(\).*?except Exception as e:(.*?)\n\n", PERSIST, re.S)
    assert m, "خطای persist_to_permanent باید گرفته و لاگ شود"
    assert "type(e).__name__" in m.group(1), "نوع استثنا باید لاگ شود"


@pytest.mark.parametrize("bad", ["except Exception: pass", "except: pass"])
def test_no_silent_except_in_persist(bad):
    assert bad not in PERSIST, f"استثنای بی‌صدا در _persist: {bad}"
