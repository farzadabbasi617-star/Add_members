"""نتیجه‌ی کهنه‌ی تست اکانت نباید اکانت سالم را «خراب» نشان دهد.

باگ واقعی (۱.۹.۳): بعد از رفع باگ ذخیره‌ی سشن در ۱.۹.۲، اکانت
+989924237228 باز هم «خراب» نشان داده می‌شد با پیام «سشن در دیسک و
بکاپ نیست».

تایم‌لاین از لاگ سرور و دیتابیس:
    10:57:27  probe اجرا شد ⇒ «سشن نیست» ثبت شد  (آن لحظه درست بود)
    11:09:05  سشن ذخیره شد — ۲۸۶۷۲ بایت روی دیسک و در دیتابیس
    نتیجه‌ی کهنه هرگز پاک نشد ⇒ UI تا اجرای بعدی probe «خراب» می‌گفت

یعنی سشن سالم بود؛ فقط حکمِ منسوخ باقی مانده بود.
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOGIN = (ROOT / "account_login.py").read_text(encoding="utf-8")
DOCTOR = (ROOT / "account_doctor.py").read_text(encoding="utf-8")
WEB = (ROOT / "web_app.py").read_text(encoding="utf-8")


def _func(src, name):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"تابع {name} پیدا نشد")


def _no_comments(src):
    return "\n".join(l for l in src.split("\n") if not l.lstrip().startswith("#"))


PERSIST = _no_comments(_func(LOGIN, "_persist"))
ACCOUNTS = _no_comments(_func(WEB, "get_accounts_dict"))


# ------------------------------------------- پاک کردن نتیجه بعد از لاگین

def test_clear_probe_result_exists():
    assert "def clear_probe_result(" in DOCTOR, "تابع پاک‌کننده‌ی نتیجه وجود ندارد"


def test_clear_probe_result_persists_change():
    """پاک کردن باید در دیتابیس بنشیند، نه فقط در حافظه.

    درس تکرارشده: به‌روزرسانی حافظه بدون ثبت دائمی ⇒ باگ برمی‌گردد.
    """
    fn = _no_comments(_func(DOCTOR, "clear_probe_result"))
    assert "kv_set" in fn, "تغییر باید با kv_set ذخیره شود"
    i_pop = fn.index("pop")
    i_set = fn.index("kv_set")
    assert i_pop < i_set, "اول حذف، بعد ذخیره"


def test_login_clears_stale_probe_on_success():
    """⚠️ هسته‌ی باگ: لاگین موفق باید حکم قبلی را باطل کند."""
    assert "clear_probe_result" in PERSIST, (
        "بعد از لاگین موفق باید نتیجه‌ی تست قبلی پاک شود، وگرنه اکانت "
        "تازه «خراب» نشان داده می‌شود"
    )


def test_clear_happens_after_session_confirmed():
    """نتیجه فقط وقتی پاک شود که سشن واقعاً ذخیره شده باشد."""
    i_guard = PERSIST.index("if not os.path.exists(dst):")
    i_clear = PERSIST.index("clear_probe_result")
    assert i_guard < i_clear, (
        "پاک کردن نتیجه باید بعد از گاردِ وجود فایل باشد — وگرنه لاگین "
        "ناموفق هم حکم درستِ «خراب» را پاک می‌کند"
    )


def test_clear_failure_is_logged_not_silent():
    raw = _func(LOGIN, "_persist")
    m = re.search(r"clear_probe_result\(phone\).*?except Exception as e:(.*?)\n\n", raw, re.S)
    assert m, "خطای پاک کردن باید گرفته شود"
    assert "type(e).__name__" in m.group(1), "نوع استثنا باید لاگ شود"


def test_no_silent_except_around_clear():
    assert "except Exception: pass" not in PERSIST


# ------------------------------------------- گارد کهنگی در UI

def test_dead_verdict_checks_session_mtime():
    """اگر سشن جدیدتر از تست باشد، حکم «خراب» کهنه است."""
    assert "getmtime" in ACCOUNTS, (
        "برای تشخیص کهنگی باید زمان فایل سشن با زمان تست مقایسه شود"
    )


def test_stale_verdict_downgraded_not_shown_as_dead():
    """حکم کهنه باید به «تست‌نشده» تبدیل شود، نه «خراب»."""
    m = re.search(r'if _stale:(.*?)else:(.*?)reason = pr\.get\("error"\)', ACCOUNTS, re.S)
    assert m, "شاخه‌ی تشخیص کهنگی وجود ندارد"
    stale_branch = m.group(1)
    assert '"unchecked"' in stale_branch, (
        "اکانتی که سشنش بعد از تست ذخیره شده باید «تست‌نشده» باشد نه «خراب»"
    )
    assert "dead" not in stale_branch


def test_staleness_compares_against_probe_timestamp():
    """مقایسه باید با ts خود نتیجه باشد، نه یک عدد ثابت."""
    m = re.search(r"_pts = int\(pr\.get\(['\"]ts['\"]\).*?\)", ACCOUNTS)
    assert m, "زمان تست باید از pr['ts'] خوانده شود"
    assert "getmtime(_sp) > _pts" in ACCOUNTS, (
        "شرط کهنگی: فایل سشن جدیدتر از نتیجه‌ی تست"
    )


def test_staleness_requires_real_session_file():
    """فایل خالی یا ناموجود نباید حکم «خراب» را باطل کند."""
    assert "getsize(_sp) > 100" in ACCOUNTS, (
        "فقط سشن واقعی (نه فایل صفر-بایتی) می‌تواند حکم را باطل کند"
    )


def test_staleness_guard_defaults_to_dead_on_error():
    """اگر بررسی کهنگی خطا داد، حکم محافظه‌کارانه بماند."""
    m = re.search(r"except Exception:\s*\n\s*_stale = False", ACCOUNTS)
    assert m, (
        "در صورت خطا _stale باید False بماند تا اکانت واقعاً خراب "
        "به‌اشتباه سالم اعلام نشود"
    )


def test_healthy_and_limited_paths_untouched():
    """گارد نباید مسیرهای دیگر وضعیت را تغییر داده باشد."""
    assert 'status = "limited"' in ACCOUNTS
    assert 'status = "healthy"' in ACCOUNTS
    assert 'status = "busy"' in ACCOUNTS
