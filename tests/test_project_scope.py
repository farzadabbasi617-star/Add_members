"""مرزهای دامنهٔ پروژه.

نسخهٔ قبلی این ربات به‌مرور ماژول‌های بی‌ربط جمع کرد — اینستاگرام، گنج‌یاب،
اکانت‌یاب، گروه‌یاب، دسته‌بندی هوشمند، اسکن خودکار — تا جایی که منوی اصلی
هفت بخش داشت و پیدا کردن دکمهٔ ادد وقت می‌گرفت.

این تست‌ها آن مرز را قفل می‌کنند. اگر کسی دوباره چنین ماژولی اضافه کند، اینجا
می‌شکند و لااقل تصمیم آگاهانه گرفته می‌شود.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
WEB = (ROOT / "web_app.py").read_text(encoding="utf-8")

#: ماژول‌هایی که عمداً در این پروژه نیستند.
OUT_OF_SCOPE = (
    "instagram_scraper",
    "lead_finder",
    "group_finder",
    "chat_analyzer",
    "channel_adder",
    "bg_scraper",
)


def test_removed_modules_are_not_present():
    for name in OUT_OF_SCOPE:
        assert not (ROOT / f"{name}.py").exists(), f"{name}.py نباید وجود داشته باشد"


def test_no_code_imports_a_removed_module():
    for name in OUT_OF_SCOPE:
        for source, label in ((BOT, "bot.py"), (WEB, "web_app.py")):
            assert f"import {name}" not in source, f"{label} هنوز {name} را import می‌کند"
            assert f"from {name}" not in source, f"{label} هنوز از {name} import می‌کند"


def test_every_button_has_a_handler():
    """دکمهٔ بدون هندلر یعنی کاربر کلیک می‌کند و هیچ اتفاقی نمی‌افتد.

    بدترین نوع خرابی، چون هیچ خطایی هم در لاگ نیست. حذف یک قابلیت باید
    دکمه‌هایش را هم با خودش ببرد.
    """
    exact = {
        key
        for group in re.findall(r"^@_CB\.exact\(([^)]*)\)", BOT, re.M)
        for key in re.findall(r'"([^"]+)"', group)
    }
    prefixes = [
        key
        for group in re.findall(r"^@_CB\.prefix\(([^)]*)\)", BOT, re.M)
        for key in re.findall(r'"([^"]+)"', group)
    ]
    literals = set(re.findall(r'callback_data="([a-z0-9_]+)"', BOT))

    orphans = sorted(
        d
        for d in literals - {"noop"}
        if d not in exact and not any(d.startswith(p) for p in prefixes)
    )
    assert not orphans, f"دکمه بدون هندلر: {orphans}"


def test_only_two_add_modes_in_ui():
    """رابط کاربری فقط «آهسته و امن» و «اولترا فست» دارد."""
    import config

    assert config.UI_ADD_MODES == ("safe", "ultra")
    for dead in ("speed-max", "speed-fast", "single-speed-max", "single-speed-fast"):
        assert f'id="{dead}"' not in WEB, f"دکمهٔ حذف‌شده هنوز هست: {dead}"


def test_legacy_mode_names_still_resolve():
    """نام‌های قدیمی نباید KeyError بدهند.

    اگر عملیاتی با add_mode="fast" در دیتابیس ثبت شده و نیمه‌کاره مانده،
    بعد از این تغییر هم باید بتواند ادامه پیدا کند.
    """
    import config

    for table in (config.DELAY_RANGES, config.BREAK_RANGES, config.STAGGER_START):
        for legacy in ("fast", "max"):
            assert legacy in table, f"نام قدیمی {legacy} از {table} حذف شده"


def test_session_helpers_survived_the_cleanup():
    """اینها داخل bg_scraper بودند ولی ربطی به اسکن خودکار ندارند.

    رندر دیسک را بین دیپلوی‌ها پاک می‌کند؛ بدون بکاپ سشن در دیتابیس، هر
    دیپلوی یعنی همهٔ اکانت‌ها باید دوباره لاگین کنند.
    """
    assert (ROOT / "session_store.py").exists()
    from session_store import backup_session, ensure_session  # noqa: F401


def test_miniapp_has_only_the_four_intended_tabs():
    """مینی‌اپ باید فقط داشبورد، ادد، استخراج و اکانت‌ها را نشان دهد.

    تب‌های «شکارچی لید» و «CRM» حذف شدند: قیف فروش به استخراج و ادد ممبر
    ربطی نداشت و فقط نوار پایین را شلوغ می‌کرد.
    """
    tabs = set(re.findall(r'id="tab-([a-z]+)"', WEB))
    assert tabs == {"dashboard", "attack", "scrape", "accounts"}, f"تب‌ها: {sorted(tabs)}"

    navs = set(re.findall(r'id="nav-([a-z]+)"', WEB))
    assert navs == tabs, f"نوار پایین با تب‌ها نمی‌خواند: {sorted(navs)}"


def test_no_dead_javascript_from_removed_tabs():
    """تابع جاوااسکریپتِ بی‌صاحب یعنی خطای runtime موقع کلیک."""
    for fn in ("runLeadSearch", "setLeadPreset", "loadCrmLeads", "filterCrmStatus",
               "updateLeadStatus", "copyInviteMsg"):
        assert fn not in WEB, f"تابع مربوط به قابلیت حذف‌شده باقی مانده: {fn}"


def test_no_dead_lead_api_routes():
    """endpointای که هندلرش حذف شده، موقع صدا زدن ۵۰۰ می‌دهد."""
    assert "/api/leads/" not in WEB
    assert "get_leads_stats_dict" not in WEB
    assert "get_leads_list_dict" not in WEB
