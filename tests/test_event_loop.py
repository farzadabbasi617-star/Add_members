"""
تست زمان‌بندی کار پس‌زمینه.

ریشه باگ «ادد اجرا نمی‌شود» (نسخه ۱.۵.۱):

`bot.py` هنگام import یک event loop می‌ساخت و همان را به‌عنوان
`main_event_loop` ثبت می‌کرد. ولی `app.run()` پایروگرام حلقه خودش را
می‌سازد و اجرا می‌کند — آن loop اولیه هرگز `run` نمی‌شد.

در نتیجه `_schedule_coro`:
  ۱) `main_event_loop.is_running()` → False
  ۲) `asyncio.get_running_loop()` → RuntimeError (داخل ترد aiohttp)
  ۳) یک loop جدید می‌ساخت و `create_task` می‌زد — ولی هیچ‌کس آن loop را
     اجرا نمی‌کرد، پس کوروتین **بی‌صدا دور ریخته می‌شد**

نتیجه: دکمه ادد پیام موفقیت می‌داد، ولی هیچ عملیاتی شروع نمی‌شد.
"""
import asyncio
import re
import pathlib
import threading
import time

import pytest

import web_app

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def live_loop():
    """یک event loop واقعاً در حال اجرا در ترد جدا."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(
        target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()),
        daemon=True,
    )
    t.start()
    for _ in range(50):
        if loop.is_running():
            break
        time.sleep(0.02)
    yield loop
    loop.call_soon_threadsafe(loop.stop)


@pytest.fixture(autouse=True)
def _reset():
    saved = (web_app.main_event_loop, web_app.bot_app)
    yield
    web_app.main_event_loop, web_app.bot_app = saved


def _wait(flag, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if flag:
            return True
        time.sleep(0.05)
    return bool(flag)


def test_coroutine_runs_on_registered_live_loop(live_loop):
    web_app.set_main_event_loop(live_loop)
    web_app.bot_app = None
    done = []

    async def job():
        done.append(1)

    web_app._schedule_coro(job())
    assert _wait(done), "کوروتین روی حلقه ثبت‌شده اجرا نشد"


def test_falls_back_to_pyrogram_loop_when_registered_one_is_dead(live_loop):
    """
    دقیقاً سناریوی باگ: حلقه ثبت‌شده مرده است ولی کلاینت پایروگرام
    روی حلقه دیگری زنده است.
    """
    dead = asyncio.new_event_loop()          # ساخته شده ولی هرگز run نشده
    web_app.set_main_event_loop(dead)

    class _Client:
        loop = live_loop

    web_app.bot_app = _Client()

    done = []

    async def job():
        done.append(1)

    web_app._schedule_coro(job())
    assert _wait(done), (
        "وقتی حلقه ثبت‌شده مرده است باید از حلقه کلاینت پایروگرام استفاده شود"
    )


def test_never_silently_drops_work():
    """
    اگر هیچ حلقه زنده‌ای نباشد، کار باید روی ترد اختصاصی اجرا شود —
    نه اینکه در loopیی که کسی اجرایش نمی‌کند گم شود.
    """
    web_app.main_event_loop = None
    web_app.bot_app = None
    done = []

    async def job():
        done.append(1)

    assert web_app._schedule_coro(job()) is True
    assert _wait(done, 4.0), "کار بی‌صدا دور ریخته شد"


def test_schedule_reports_success():
    """فراخوان باید بداند کار زمان‌بندی شد یا نه."""
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    m = re.search(r"def _schedule_coro\(coro\):(.*?)(?=\n\nclass |\ndef )", src, re.S)
    assert m and "return True" in m.group(1), "_schedule_coro باید نتیجه را برگرداند"


def test_no_orphan_loop_creation():
    """
    الگوی خطرناک: ساختن loop و create_task بدون اجرا کردن آن.
    کوروتین هرگز اجرا نمی‌شود و هیچ خطایی هم دیده نمی‌شود.
    """
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    m = re.search(r"def _schedule_coro\(coro\):(.*?)(?=\n\nclass |\ndef )", src, re.S)
    body = m.group(1)

    if "new_event_loop()" in body:
        assert "run_until_complete" in body or "run_forever" in body, (
            "حلقه جدید ساخته می‌شود ولی اجرا نمی‌شود — کار گم می‌شود"
        )


# ───────────────── آمار زنده ─────────────────

def test_final_stats_survive_after_run_ends():
    """
    قبلاً به‌محض پایان عملیات همه اعداد صفر می‌شدند و کاربر هرگز
    نمی‌فهمید چند نفر واقعاً اضافه شدند.
    """
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    assert '"finished"' in src, "API باید پایان عملیات را اعلام کند"
    assert "live_elapsed_final" in src, "زمان نهایی باید نگه داشته شود"


def test_both_add_paths_record_final_elapsed():
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    assert src.count('atk_state_ref["live_elapsed_final"]') >= 2, (
        "هر دو مسیر تک‌اکانت و موازی باید زمان نهایی را ثبت کنند"
    )


def test_ui_renders_finished_summary():
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    assert "fin.finished" in src, "UI باید خلاصه پایان را نمایش دهد"
    assert "آخرین عملیات" in src


def test_live_bot_module_loop_wins_over_stale_reference(live_loop):
    """
    باگ دوم (۱.۵.۲): بعد از رفع باگ اول، ادد همچنان اجرا نمی‌شد.

    عیب‌یابی زنده روی سرویس نشان داد `bot_app.loop` و حلقه واقعی
    پایروگرام دو شیء متفاوت‌اند:

        bot_module_loop = ...940304   ← پایروگرام واقعاً اینجاست
        bot_app.loop    = ...982384   ← کهنه، ولی انتخاب می‌شد

    کوروتین روی حلقه‌ای می‌رفت که کلاینت روی آن نبود.
    ماژول زنده `bot` باید اولویت داشته باشد.
    """
    import sys
    import types

    stale = asyncio.new_event_loop()
    stale_thread = threading.Thread(
        target=lambda: (asyncio.set_event_loop(stale), stale.run_forever()),
        daemon=True,
    )
    stale_thread.start()
    for _ in range(50):
        if stale.is_running():
            break
        time.sleep(0.02)

    class _Stale:
        loop = stale

    web_app.bot_app = _Stale()
    web_app.main_event_loop = stale

    fake_bot = sys.modules.get("bot")
    created = False
    if fake_bot is None:
        fake_bot = types.ModuleType("bot")
        sys.modules["bot"] = fake_bot
        created = True

    saved_app = getattr(fake_bot, "app", None)

    class _LiveClient:
        loop = live_loop

    fake_bot.app = _LiveClient()
    try:
        assert web_app._resolve_bot_loop() is live_loop, (
            "حلقه ماژول زنده bot باید بر ارجاع کهنه اولویت داشته باشد"
        )
    finally:
        if created:
            sys.modules.pop("bot", None)
        elif saved_app is not None:
            fake_bot.app = saved_app
        stale.call_soon_threadsafe(stale.stop)


def test_resolver_checks_bot_module_first():
    """ترتیب اولویت باید در کد صریح باشد، نه تصادفی."""
    src = (ROOT / "web_app.py").read_text(encoding="utf-8")
    m = re.search(r"def _resolve_bot_loop\(\):(.*?)(?=\ndef _schedule_coro)", src, re.S)
    body = m.group(1)
    module_pos = body.index('sys.modules.get("bot")')
    stale_pos = body.index('getattr(bot_app, "loop"')
    assert module_pos < stale_pos, (
        "ماژول زنده bot باید قبل از ارجاع bot_app بررسی شود"
    )
