"""
تست پیش‌فیلتر کاربران غیرقابل‌ادد.

هدف: قبل از شروع حلقه ادد، کاربرانی که قطعاً اضافه نمی‌شوند را کنار
بگذاریم. هر تلاش ناموفق ادد بودجه نرخ اکانت را می‌سوزاند؛ با ۲۵ هزار
ممبر این یعنی هزاران درخواست بیهوده و رسیدن سریع به FloodWait.

اصل طراحی: **محافظه‌کار باش**. اگر بررسی شکست خورد، کاربر را نگه دار.
یک تلاش اضافه خیلی بهتر از حذف کاربر سالم است.
"""
import pytest

import add_engine
from add_engine import (
    _is_unaddable_user,
    format_prefilter_report,
    prefilter_unaddable,
)


class _User:
    def __init__(self, uid, is_deleted=False, is_bot=False, is_self=False):
        self.id = uid
        self.is_deleted = is_deleted
        self.is_bot = is_bot
        self.is_self = is_self


class _App:
    """اپ ساختگی: فقط شناسه‌های شناخته‌شده را برمی‌گرداند."""

    def __init__(self, known, fail=False):
        self.known = {u.id: u for u in known}
        self.fail = fail
        self.batches = 0

    async def get_users(self, ids):
        self.batches += 1
        if self.fail:
            raise RuntimeError("FLOOD_WAIT_5")
        return [self.known[i] for i in ids if i in self.known]


def _members(*ids):
    return [{"user_id": i, "username": f"u{i}"} for i in ids]


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """جلوگیری از نوشتن در دیتابیس واقعی حین تست."""
    monkeypatch.setattr(add_engine, "never_add_again", lambda *a, **kw: None)
    monkeypatch.setattr(add_engine, "get_blocked_ids_cached", lambda: set())


# ─────────────────── تشخیص تک‌کاربر ───────────────────

def test_deleted_account_is_unaddable():
    bad, why = _is_unaddable_user(_User(1, is_deleted=True))
    assert bad and "حذف" in why


def test_bot_is_unaddable():
    bad, why = _is_unaddable_user(_User(2, is_bot=True))
    assert bad and "ربات" in why


def test_self_is_unaddable():
    bad, _ = _is_unaddable_user(_User(3, is_self=True))
    assert bad


def test_missing_user_is_unaddable():
    bad, _ = _is_unaddable_user(None)
    assert bad


def test_normal_user_is_addable():
    bad, why = _is_unaddable_user(_User(4))
    assert not bad and why == ""


# ─────────────────── فیلتر دسته‌ای ───────────────────

async def test_removes_deleted_and_bots():
    app = _App([
        _User(1),
        _User(2, is_deleted=True),
        _User(3, is_bot=True),
        _User(4),
    ])
    keep, stats = await prefilter_unaddable(app, _members(1, 2, 3, 4))

    kept_ids = {m["user_id"] for m in keep}
    assert kept_ids == {1, 4}
    assert stats["removed"] == 2
    assert stats["kept"] == 2


async def test_removes_users_telegram_does_not_know():
    """اگر تلگرام کاربر را برنگرداند، وجود ندارد."""
    app = _App([_User(1)])
    keep, stats = await prefilter_unaddable(app, _members(1, 777))

    assert {m["user_id"] for m in keep} == {1}
    assert stats["reasons"].get("پیدا نشد") == 1


async def test_already_blocked_skipped_without_network(monkeypatch):
    """کاربران لیست ممنوعه نباید حتی یک درخواست شبکه ایجاد کنند."""
    monkeypatch.setattr(add_engine, "get_blocked_ids_cached", lambda: {2, 3})
    app = _App([_User(1)])

    keep, stats = await prefilter_unaddable(app, _members(1, 2, 3))

    assert {m["user_id"] for m in keep} == {1}
    assert stats["reasons"].get("در لیست ممنوعه") == 2


async def test_conservative_on_api_failure():
    """
    مهم‌ترین تست: اگر بررسی شکست خورد، هیچ‌کس نباید حذف شود.

    بهتر است چند تلاش اضافه انجام شود تا اینکه کاربران سالم به‌خاطر
    یک خطای موقت شبکه برای همیشه از دیتابیس پاک شوند.
    """
    app = _App([_User(1), _User(2)], fail=True)
    keep, stats = await prefilter_unaddable(app, _members(1, 2, 3))

    assert len(keep) == 3, "هنگام خطای API نباید کسی حذف شود"
    assert stats["errors"] == 1


async def test_empty_input():
    app = _App([])
    keep, stats = await prefilter_unaddable(app, [])
    assert keep == []
    assert stats["removed"] == 0


async def test_batches_large_lists():
    """لیست بزرگ باید به چند درخواست تقسیم شود، نه یکی."""
    users = [_User(i) for i in range(1, 251)]
    app = _App(users)
    keep, _ = await prefilter_unaddable(app, _members(*range(1, 251)))

    assert len(keep) == 250
    assert app.batches == 3, f"باید ۳ دسته باشد (۱۰۰+۱۰۰+۵۰)، شد {app.batches}"


async def test_marks_removed_users_as_blocked(monkeypatch):
    """کاربران حذف‌شده باید در لیست «هرگز دوباره» ثبت شوند تا دفعه بعد اسکن نشوند."""
    marked = []
    monkeypatch.setattr(add_engine, "never_add_again", lambda uid, r="": marked.append((uid, r)))

    app = _App([_User(1), _User(2, is_deleted=True)])
    await prefilter_unaddable(app, _members(1, 2), mark_blocked=True)

    assert 2 in [uid for uid, _ in marked]
    assert 1 not in [uid for uid, _ in marked]


async def test_respects_mark_blocked_false(monkeypatch):
    marked = []
    monkeypatch.setattr(add_engine, "never_add_again", lambda uid, r="": marked.append(uid))

    app = _App([_User(2, is_deleted=True)])
    await prefilter_unaddable(app, _members(2), mark_blocked=False)

    assert marked == []


# ─────────────────── گزارش ───────────────────

def test_report_empty_when_nothing_removed():
    assert format_prefilter_report({"removed": 0}) == ""
    assert format_prefilter_report(None) == ""


def test_report_lists_reasons():
    out = format_prefilter_report({
        "removed": 5, "kept": 20,
        "reasons": {"ربات": 3, "حساب حذف‌شده": 2},
    })
    assert "5" in out and "ربات" in out and "20" in out
    assert "سهمیه" in out, "گزارش باید توضیح دهد که سهمیه مصرف نشده"
