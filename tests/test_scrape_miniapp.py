"""
استخراج ممبر از گروه، مستقیم از مینی‌اپ.

مالک: «اگه قراره گروه اسکریپت کنم قابلیتشم به مینی اپ اضافه کن»

قبلاً اسکرپ فقط از مسیر «گروه‌های کشف‌شده» در دسترس بود — جایی برای
وارد کردن دستی لینک گروه وجود نداشت.

نکات حساسی که در این فایل قفل می‌شوند:
- ورودی کاربر در هر فرمتی (لینک/یوزرنیم/آیدی) درست نرمال شود
- callbackهای پیشرفت **async** باشند (در attacker با await صدا
  زده می‌شوند؛ نسخه‌ی sync بی‌صدا استثنا می‌داد)
- خطاها با راهنمای فارسی به کاربر برسند
"""
import pathlib
import re

import pytest

from web_app import normalize_group_ref

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = (ROOT / "web_app.py").read_text(encoding="utf-8")


# ───────── نرمال‌سازی ورودی ─────────

@pytest.mark.parametrize("raw,expected", [
    ("https://t.me/gament_super_gp", "@gament_super_gp"),
    ("http://t.me/abc_def", "@abc_def"),
    ("t.me/abc_def", "@abc_def"),
    ("telegram.me/abc_def", "@abc_def"),
    ("@mygroup", "@mygroup"),
    ("mygroup", "@mygroup"),
    ("https://t.me/group/123", "@group"),
    ("https://t.me/name?start=x", "@name"),
])
def test_public_refs_normalize(raw, expected):
    assert normalize_group_ref(raw) == expected


def test_numeric_id_kept_as_int():
    assert normalize_group_ref("-1004316603248") == -1004316603248


def test_persian_digits_supported():
    assert normalize_group_ref("۱۲۳۴۵۶۷۸۹۰۱") == 12345678901


@pytest.mark.parametrize("raw", [
    "https://t.me/+AbCdEf123",
    "https://t.me/joinchat/XYZ",
])
def test_private_invite_links_preserved(raw):
    """لینک دعوت خصوصی نباید به @ تبدیل شود — پایروگرام خودش می‌فهمد."""
    out = normalize_group_ref(raw)
    assert out.startswith("https://t.me/")
    assert "+" in out or "joinchat" in out


@pytest.mark.parametrize("raw", ["", None, "ab", "!!!", "خطا"])
def test_invalid_refs_rejected(raw):
    assert normalize_group_ref(raw) is None


def test_whitespace_tolerated():
    assert normalize_group_ref("  @group  ") == "@group"


# ───────── اعتبارسنجی و گاردها ─────────

def test_invalid_input_returns_friendly_error():
    src = WEB
    i = src.index("def trigger_scrape_group")
    window = src[i:i + 1200]
    assert "normalize_group_ref" in window
    assert "نامعتبر" in window, "پیام خطای فارسی لازم است"


def test_scrape_blocked_while_another_job_runs():
    """
    اسکرپ و ادد هر دو از اکانت‌ها استفاده می‌کنند — اجرای هم‌زمان
    یعنی تداخل سشن و سوختن اکانت.
    """
    src = WEB
    i = src.index("def trigger_scrape_group")
    window = src[i:i + 1400]
    assert "add_in_progress" in window
    assert "در حال اجراست" in window


# ───────── پیشرفت زنده ─────────

def test_progress_callbacks_are_async():
    """
    🚨 در attacker.py هر دو با `await` صدا زده می‌شوند:
        await self._progress_cb(text_out)
        await self._incremental_save_cb(...)

    نسخه‌ی sync استثنا می‌دهد و در except بی‌صدا بلعیده می‌شود —
    همان تله‌ای که قبلاً فیلتر پرایوسی را ساعت‌ها بی‌اثر کرد.
    """
    import re
    # روی خطِ تعریف لنگر بینداز، نه هر جای فایل (کامنت هم شامل می‌شد)
    for fn in ("_on_progress", "_on_save"):
        assert re.search(rf"^\s*async def {fn}\(", WEB, re.M), (
            f"{fn} باید async باشد — در attacker با await صدا زده می‌شود"
        )
        assert not re.search(rf"^\s*def {fn}\(", WEB, re.M), (
            f"نسخه sync از {fn} وجود دارد"
        )


def test_callbacks_are_passed_to_scraper():
    i = WEB.index("run_full_scrape(")
    window = WEB[i:i + 400]
    assert "progress_cb=" in window
    assert "incremental_save_cb=" in window


def test_live_total_set_so_finished_flag_works():
    """
    پرچم `finished` در داشبورد به `live_total` وابسته است. اگر صفر
    بماند، خلاصه‌ی پایان اسکرپ هرگز نمایش داده نمی‌شود.
    """
    # لنگر: خط live_mode حالا شرطی است (اسکرپ عمیق / اسکرپ گروه)
    i = WEB.index('atk_state_ref["live_mode"] =')
    window = WEB[i:i + 400]
    assert 'atk_state_ref["live_total"]' in window


def test_final_elapsed_recorded():
    i = WEB.index("async def run_scrape_job")
    j = WEB.index("_schedule_coro(run_scrape_job())")
    assert "live_elapsed_final" in WEB[i:j]


def test_busy_account_released_in_finally():
    """اگر اکانت آزاد نشود، برای همیشه «مشغول» می‌ماند."""
    i = WEB.index("async def run_scrape_job")
    j = WEB.index("_schedule_coro(run_scrape_job())")
    body = WEB[i:j]
    assert "finally:" in body
    assert "account_state.release(phone)" in body


# ───────── پیام خطا ─────────

@pytest.mark.parametrize("needle", [
    "وجود ندارد",      # username not occupied
    "خصوصی است",       # private group
    "محدودیت",         # flood
])
def test_error_hints_are_actionable(needle):
    i = WEB.index("Scrape job error")
    window = WEB[i:i + 1200]
    assert needle in window, f"راهنمای «{needle}» برای کاربر لازم است"


# ───────── رابط کاربری ─────────

def test_ui_elements_exist():
    for el in ("scrape-input", "btn-scrape", "scrape-msg",
               "scrape-live", "scrape-count", "scrape-stage"):
        assert f'id="{el}"' in WEB, f"عنصر {el} در UI نیست"


def test_ui_functions_defined():
    for fn in ("startGroupScrape", "updateScrapeProgress", "scrapeMsg"):
        assert f"function {fn}" in WEB, f"تابع {fn} تعریف نشده"


def test_progress_hooked_into_dashboard_poll():
    """بدون این، نوار پیشرفت هرگز به‌روز نمی‌شود."""
    assert "updateScrapeProgress(m);" in WEB


def test_scrape_input_is_ltr():
    """آدرس انگلیسی در فیلد راست‌به‌چپ ناخوانا می‌شود."""
    assert re.search(r'id="scrape-input"[^>]*dir="ltr"', WEB)


def test_no_raw_newline_in_new_js():
    """
    ⚠️ تله‌ی تکراری: `\\n` در سورس پایتون به newline واقعی تبدیل
    می‌شود و رشته‌ی جاوااسکریپت را می‌شکند — یک بار کل مینی‌اپ را
    فریز کرد.
    """
    i = WEB.index("async function startGroupScrape")
    j = WEB.index("async function scrapeDiscoveredGroup")
    block = WEB[i:j]
    for line in block.split("\n"):
        no_esc = line.replace("\\'", "").replace('\\"', "")
        assert no_esc.count("'") % 2 == 0 or "//" in no_esc, (
            f"رشته بسته‌نشده: {line.strip()[:60]}"
        )
