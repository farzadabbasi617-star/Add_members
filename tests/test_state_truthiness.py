"""
تله falsy بودن dict خالی.

🚨 ریشه باگ «همه آمار صفر و ماکت است» و «ادد اجرا نمی‌شود» (۱.۵.۳):

`bot.py` استیت را این‌طور می‌سازد:

    atk_state = {}

و همان را به مینی‌اپ پاس می‌دهد. ولی در پایتون **dict خالی falsy است**:

    bool({}) == False

پس هر گاردی به شکل `if atk_state_ref:` رد می‌شد و هیچ‌کدام از نوشتن‌های
آمار زنده اجرا نمی‌شدند:

    - add_in_progress هرگز True نمی‌شد  → is_adding همیشه False
    - live_added / live_failed / live_skipped هرگز نوشته نمی‌شدند
    - live_status_text خالی می‌ماند → کاربر هیچ خطایی نمی‌دید

کاربر می‌دید همه کارت‌ها صفرند و «مثل ماکت» است — دقیقاً همین بود.

گارد درست: `if atk_state_ref is not None:`
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ("web_app.py", "bot.py")


def test_empty_dict_is_falsy_sanity():
    """مستندسازی خود تله — پایه‌ی همه تست‌های این فایل."""
    assert not bool({}), "اگر این بشکند یعنی فرض بنیادی عوض شده"
    assert {} is not None


@pytest.mark.parametrize("fname", FILES)
def test_no_truthiness_guard_on_state_ref(fname):
    """
    هیچ‌جا نباید استیت را با truthiness چک کرد — چون درست در لحظه‌ای
    که هنوز خالی است (شروع عملیات) گارد رد می‌شود.
    """
    src = (ROOT / fname).read_text(encoding="utf-8")
    bad = []
    for i, line in enumerate(src.split("\n"), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"if atk_state_ref\s*:", stripped):
            bad.append(f"خط {i}: {stripped[:70]}")
        if re.search(r"\batk_state_ref\s+and\b", stripped):
            bad.append(f"خط {i}: {stripped[:70]}")

    assert not bad, (
        f"{fname} استیت را با truthiness چک می‌کند. dict خالی falsy است، "
        f"پس آمار زنده هرگز نوشته نمی‌شود:\n  " + "\n  ".join(bad[:8])
    )


@pytest.mark.parametrize("fname", FILES)
def test_state_guards_use_is_not_none(fname):
    """گارد درست باید واقعاً استفاده شده باشد، نه اینکه گارد حذف شود."""
    src = (ROOT / fname).read_text(encoding="utf-8")
    assert "atk_state_ref is not None" in src, (
        f"{fname} باید از `atk_state_ref is not None` استفاده کند"
    )


def test_add_in_progress_set_before_scheduling():
    """
    پرچم باید همگام و قبل از زمان‌بندی کار پس‌زمینه ست شود، وگرنه
    اولین پول داشبورد False می‌بیند و UI فکر می‌کند چیزی شروع نشده.
    """
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    for fn in ("trigger_parallel_add", "trigger_single_add"):
        m = re.search(rf"def {fn}\(.*?(?=\ndef |\nMINI_APP_HTML)", src, re.S)
        assert m, f"{fn} پیدا نشد"
        body = m.group(0)
        flag = body.index('atk_state_ref["add_in_progress"] = True')
        sched = body.index("_schedule_coro(")
        assert flag < sched, (
            f"{fn} باید add_in_progress را قبل از _schedule_coro ست کند"
        )


def test_live_counters_are_initialized():
    """همه شمارنده‌های UI باید در شروع مقداردهی شوند تا None نمانند."""
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    m = re.search(r"def trigger_parallel_add\(.*?(?=\ndef |\nMINI_APP_HTML)", src, re.S)
    body = m.group(0)
    for key in ("live_added", "live_failed", "live_skipped", "live_total", "live_start_time"):
        assert f'atk_state_ref["{key}"]' in body, f"{key} مقداردهی اولیه نشده"
