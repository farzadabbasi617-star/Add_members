"""
بودجه‌ی نرخ پیش‌فیلتر.

🚨 علت «با کمترین ادد لیمیت می‌خوریم» (۱.۶.۴):

لاگ زنده نشان داد اکانت‌ها با **صفر ادد** PEER_FLOOD می‌گرفتند:

    21:06:18  🚫 [+989359428854] PEER_FLOOD (بار 3) — تا اینجا 0 ادد
    21:06:22  🚫 [+989035171235] PEER_FLOOD (بار 3) — تا اینجا 0 ادد

و درست قبلش، ۲۵۲ خط از این:

    [prefilter] پیش‌فیلتر برای این دسته ناموفق بود (FloodWait)

تراکم: **۹۴ خطا در یک دقیقه**.

علت: پیش‌فیلتر روی کل صف (۹٬۶۰۰ نفر) اجرا می‌شد. هر دسته‌ی ۱۰۰تایی
یک `get_users` است ⇒ ۹۶ درخواست پشت سر هم. اکانت تمام بودجه‌ی نرخش را
**قبل از اولین ادد** می‌سوزاند.

بدتر: وقتی FloodWait می‌گرفت، حلقه ادامه می‌داد و باز درخواست می‌زد.
"""
import pathlib
import re

import pytest

import add_engine

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _FloodErr(Exception):
    pass


_FloodErr.__name__ = "FloodWait"


class _User:
    """کاربر سالم — تلگرام واقعی آبجکت برمی‌گرداند، نه لیست خالی."""

    def __init__(self, uid):
        self.id = uid
        self.is_bot = False
        self.is_deleted = False
        self.is_scam = False
        self.is_fake = False
        self.first_name = "u"
        self.username = None


class _App:
    """کلاینت ساختگی که مثل تلگرام rate-limit می‌دهد."""

    def __init__(self, fail_after=0):
        self.calls = 0
        self.fail_after = fail_after

    async def get_users(self, ids):
        self.calls += 1
        if self.fail_after and self.calls > self.fail_after:
            raise _FloodErr("FLOOD_WAIT_X")
        return [_User(i) for i in ids]


def _members(n):
    return [{"user_id": 100000 + i} for i in range(n)]


async def test_prefilter_stops_when_telegram_rate_limits(monkeypatch):
    """
    مهم‌ترین تست: با اولین FloodWait باید کلاً رها کند، نه اینکه
    ۹۶ بار پشت سر هم درخواست بزند.
    """
    monkeypatch.setattr(add_engine, "get_blocked_ids_cached", lambda: set())
    app = _App(fail_after=1)
    keep, stats = await add_engine.prefilter_unaddable(
        app, _members(5000), mark_blocked=False
    )
    assert app.calls <= 3, (
        f"{app.calls} درخواست بعد از FloodWait — باید فوراً رها می‌کرد"
    )
    assert stats.get("aborted") is True
    assert len(keep) == 5000, "هیچ کاربری نباید حذف شود وقتی بررسی نشده"


async def test_prefilter_gives_up_after_repeated_failures(monkeypatch):
    """خطاهای غیرFlood هم نباید بی‌نهایت تکرار شوند."""
    monkeypatch.setattr(add_engine, "get_blocked_ids_cached", lambda: set())

    class _Broken:
        def __init__(self):
            self.calls = 0

        async def get_users(self, ids):
            self.calls += 1
            raise ValueError("boom")

    app = _Broken()
    keep, stats = await add_engine.prefilter_unaddable(
        app, _members(5000), mark_blocked=False
    )
    assert app.calls <= 4, f"{app.calls} تلاش — باید زود رها می‌کرد"
    assert len(keep) == 5000


async def test_no_user_lost_when_prefilter_aborts(monkeypatch):
    """رها کردن پیش‌فیلتر نباید باعث گم شدن کاربر شود."""
    monkeypatch.setattr(add_engine, "get_blocked_ids_cached", lambda: set())
    app = _App(fail_after=1)
    keep, _ = await add_engine.prefilter_unaddable(
        app, _members(1234), mark_blocked=False
    )
    assert len(keep) == 1234


# ───────── دامنه‌ی پیش‌فیلتر در ورکر ─────────

def test_worker_scans_incrementally_not_whole_queue():
    """
    نباید کل صف را یکجا بررسی کند. از ۱.۸.۰ اسکن تطبیقی است:
    دسته‌دسته جلو می‌رود و به‌محض رسیدن به تعداد کافی کاربر سالم
    می‌ایستد — چون حالا پرایوسی‌بسته‌ها هم رد می‌شوند.
    """
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "_max_scan" in src, "سقف ایمنی اسکن لازم است"
    assert "len(_clean) < _need" in src, "باید تا رسیدن به هدف ادامه دهد"


def test_scan_has_hard_ceiling():
    """
    حتی اگر کاربر سالمی پیدا نشود، اسکن نباید بی‌نهایت ادامه یابد —
    خودِ پیش‌فیلتر سهمیه می‌سوزاند (درس ۱.۶.۴).
    """
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    m = re.search(r"_max_scan = min\(len\(members\), (\d+)\)", src)
    assert m, "سقف اسکن پیدا نشد"
    ceiling = int(m.group(1))
    assert ceiling <= 3000, f"سقف {ceiling} یعنی تا {ceiling // 100} درخواست"

    m2 = re.search(r"_need = max\((\d+), len\(accs\) \* (\d+)\)", src)
    assert m2, "هدف کاربر سالم پیدا نشد"
    assert int(m2.group(1)) <= 300 and int(m2.group(2)) <= 100


def test_queue_is_rebuilt_after_partial_prefilter():
    """بخش بررسی‌نشده نباید از صف حذف شود."""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "members = _clean + members[_checked:]" in src, (
        "صف باید از سالم‌های بررسی‌شده + بقیه بازسازی شود"
    )
