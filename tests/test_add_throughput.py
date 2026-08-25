"""بودجه‌ی درخواست تلگرام در مسیر ادد موازی.

پس‌زمینه (۱.۹.۴): مالک گزارش داد ادد «کمترین بازدهی، طولانی‌ترین زمان و
سریع‌ترین لیمیت خوردن» را دارد و قبلاً بهتر بود. داده تأییدش کرد:

    ۱۰ آگوست : ۷۰۵ ادد موفق در یک روز
    ۱۶ آگوست :  ۵۱ ادد موفق

علت: سه لایه‌ی محافظتی که جداگانه اضافه شده بودند روی هم جمع شدند و
هزینه‌ی هر کاربر را از ۱ درخواست به ۴ درخواست رساندند:

    get_users      (فیلتر درون‌خطی)  = ۱
    InviteToChannel                  = ۱
    get_chat_member × ۲ (confirm)    = ۲

بودجه‌ی نرخ تلگرام ثابت است، پس ۴ برابر شدن هزینه یعنی ۴ برابر کمتر ادد
و رسیدن بسیار سریع‌تر به PEER_FLOOD.

این تست‌ها بودجه را قفل می‌کنند تا دوباره بی‌سروصدا بالا نرود.
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
ENGINE = (ROOT / "add_engine.py").read_text(encoding="utf-8")


def _worker_body():
    """بدنه‌ی ورکر واقعی ادد موازی.

    ⚠️ توابع تودرتو: _execute_parallel_add → worker_account →
    _worker_account_inner. بدنه‌ی واقعی در آخری است.
    """
    for node in ast.walk(ast.parse(BOT)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name == "_worker_account_inner":
            return ast.get_source_segment(BOT, node)
    raise AssertionError("_worker_account_inner پیدا نشد")


WORKER = _worker_body()
WORKER_CODE = "\n".join(
    l for l in WORKER.split("\n") if not l.lstrip().startswith("#")
)


# ------------------------------------------------- بودجه درخواست

def test_no_confirm_joined_in_parallel_worker():
    """⚠️ گران‌ترین لایه: تا ۲ get_chat_member اضافه به ازای هر دعوت."""
    assert "confirm_joined" not in WORKER_CODE, (
        "confirm_joined از ورکر موازی حذف شده — missing_invitees که خود "
        "تلگرام در پاسخ دعوت برمی‌گرداند رایگان است و کافی"
    )


def test_invite_result_still_checked():
    """حذف confirm_joined نباید آمار را دوباره دروغین کند."""
    assert "invite_did_not_join(invite_res, uid)" in WORKER_CODE, (
        "پاسخ دعوت باید همچنان بررسی شود وگرنه آمار ادد غیرواقعی می‌شود"
    )


def test_no_get_users_per_member():
    """🔄 به‌روزشده (۱.۹.۵): فیلتر درون‌خطی کلاً حذف شد.

    قبلاً «دقیقاً ۱ بار» الزام بود. حالا صفر است چون فیلتر بر اساس
    last-seen حدس می‌زد و ۹۴٪ صف را دور می‌ریخت.
    """
    n = len(re.findall(r"await client\.get_users\(", WORKER_CODE))
    assert n == 0, f"انتظار ۰ فراخوانی get_users، یافت شد {n}"


def test_single_invite_call_per_member():
    n = len(re.findall(r"InviteToChannel\(", WORKER_CODE))
    assert n == 1, f"انتظار ۱ فراخوانی InviteToChannel، یافت شد {n}"


def test_no_add_contact():
    """AddContact در ۱.۷.۰ حذف شد — برنگردد."""
    assert "AddContact(" not in WORKER_CODE


def test_total_budget_per_addable_user_is_one():
    """قرارداد صریح: کاربر قابل‌ادد حداکثر ۲ درخواست."""
    budget = (
        len(re.findall(r"await client\.get_users\(", WORKER_CODE))
        + len(re.findall(r"InviteToChannel\(", WORKER_CODE))
        + len(re.findall(r"await confirm_joined\(", WORKER_CODE))
        + len(re.findall(r"AddContact\(", WORKER_CODE))
    )
    assert budget <= 1, (
        f"بودجه‌ی هر کاربر {budget} درخواست است. سقف ۱ است — همان چیزی که "
        "در ۱۰ آگوست ۷۰۵ ادد داد. هر افزایشی مستقیماً نرخ ادد را پایین و "
        "احتمال PEER_FLOOD را بالا می‌برد."
    )


# ------------------------------------------------- تأخیرهای هدررفته

def test_no_fixed_delay_after_privacy_skip():
    """⚠️ ۹۷٪ صف پرایوسی‌بسته است.

    تأخیر ۳ ثانیه‌ای بعد از هر رد ⇒ ۹۷۰۰ × ۳ ≈ ۸ ساعت خواب محض بدون
    حتی یک ادد. رد کردن نباید تأخیر داشته باشد: get_users سبک است و
    PEER_FLOOD مخصوص دعوت است نه خواندن پروفایل.
    """
    assert "timeout=3.0" not in WORKER_CODE, (
        "تأخیر ثابت بعد از رد پرایوسی حذف شده است"
    )


def test_stop_button_still_works():
    """🔄 به‌روزشده (۱.۹.۵): مسیر رد پرایوسی دیگر وجود ندارد.

    ولی دکمه‌ی توقف باید در مسیرهای باقی‌مانده محترم بماند.
    """
    assert "stop_event" in WORKER_CODE, "ورکر باید به رویداد توقف گوش بدهد"
    assert re.search(r"wait_for\(\s*stop_event\.wait\(\)", WORKER_CODE), (
        "توقف باید حین انتظار هم اثر کند"
    )


def test_successful_add_still_throttled():
    """مسیر مصرف‌کننده‌ی بودجه باید همچنان تأخیر داشته باشد.

    درس ۱.۷.۱: تأخیر باید به «درخواست مصرف‌شده» گره بخورد.
    """
    assert "_spent_request" in WORKER_CODE
    assert "human_delay(add_mode)" in WORKER_CODE


def test_failed_invite_path_still_throttled():
    """«دعوت شد ولی عضو نشد» هم یک درخواست کامل است."""
    # ⚠️ لنگر باید تنگ باشد. با `.*?` روی کل بدنه، اولین _spent_request
    # بعدی (متعلق به مسیر ادد موفق) هم تطابق می‌داد و تست حتی وقتی
    # throttle این مسیر حذف شده بود سبز می‌ماند.
    i = WORKER_CODE.index("invited but not a member")
    j = WORKER_CODE.index("continue", i)
    segment = WORKER_CODE[i:j]
    assert "_spent_request = True" in segment, (
        "مسیر «عضو نشد» باید _spent_request را قبل از continue ست کند — "
        "این خودِ باگی بود که سه نسخه طول کشید تا پیدا شود"
    )


# ------------------------------------------------- پیش‌فیلتر دسته‌ای

def test_batch_prefilter_disabled_by_default():
    """هم‌پوشانی کامل با فیلتر درون‌خطی ⇒ دو برابر درخواست برای یک نتیجه."""
    assert 'os.environ.get("PREFILTER_ENABLED", "0")' in BOT, (
        "پیش‌فیلتر دسته‌ای باید پیش‌فرض خاموش باشد"
    )
    m = re.search(r"_prefilter_on = .*?\n\s*if _prefilter_on and members", BOT, re.S)
    assert m, "شرط اجرای پیش‌فیلتر باید به پرچم گره خورده باشد"


def test_batch_prefilter_can_be_re_enabled():
    """خاموش‌کردن نباید حذف‌کردن باشد."""
    assert "prefilter_unaddable" in BOT, "کد پیش‌فیلتر باید باقی بماند"


# ------------------------------------------------- محافظ‌های حیاتی

def test_never_add_again_still_recorded():
    """درس: هر «دیگر تلاش نکن» باید در DB بنشیند نه فقط حافظه."""
    assert "never_add_again(uid" in WORKER_CODE


def test_peer_flood_handling_intact():
    assert "PEER_FLOOD" in WORKER_CODE
    assert "peer_flood_cooldown" in WORKER_CODE


def test_abort_after_repeated_failures_intact():
    """محافظ «۲۵ دعوت پیاپی بی‌نتیجه» نباید حذف شده باشد."""
    assert "_ABORT_AFTER_FAILS" in BOT


def test_no_silent_except_swallowing_add_errors():
    """درس تکرارشده: except خالی علت را چند نسخه پنهان کرد."""
    assert "except Exception: pass" not in WORKER_CODE


def test_confirm_joined_still_available_for_other_paths():
    """حذف از ورکر موازی نباید تابع را از بین ببرد."""
    assert "async def confirm_joined" in ENGINE
