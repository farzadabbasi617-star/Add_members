"""اثبات عددی صرفه‌جویی بهینه‌سازی.

این تست‌ها ادعاهای عملکردی را می‌سنجند، نه اینکه صرفاً وجود کد را
بررسی کنند. اگر کسی روزی بهینه‌سازی را برگرداند، اینجا قرمز می‌شود.

دو گلوگاهی که رفع شد:

  ۱. شش روش هرکدام کل تاریچه را جدا می‌خواندند (۶ برابر دانلود)
  ۲. تأیید عضویت تک‌به‌تک بدون کش (N+1)
"""
import ast
import pathlib
import types

import pytest

from scrape_optimize import AdaptiveThrottle, MembershipOracle, MessageHarvester

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ============================================================ Harvester

def _msg(mid=1, **kw):
    m = types.SimpleNamespace(
        id=mid, from_user=None, forward_from=None, reply_to_message=None,
        new_chat_members=None, entities=None, reactions=None, poll=None,
    )
    for k, v in kw.items():
        setattr(m, k, v)
    return m


def _user(uid, **kw):
    u = types.SimpleNamespace(id=uid, is_bot=False, is_deleted=False,
                              first_name="U", last_name="", username="")
    for k, v in kw.items():
        setattr(u, k, v)
    return u


class _Sink:
    def __init__(self):
        self.calls = []

    async def __call__(self, user, source):
        self.calls.append((user.id, source))


async def test_single_pass_extracts_every_source_at_once():
    """⚠️ هستهٔ صرفه‌جویی: یک پیام، همهٔ منابع، یک بار.

    قبلاً هر منبع یک عبور جدا روی کل تاریخچه لازم داشت.
    """
    sink = _Sink()
    h = MessageHarvester(sink)
    ent = types.SimpleNamespace(user=_user(5), type=None)
    from pyrogram.enums import MessageEntityType
    ent.type = MessageEntityType.TEXT_MENTION

    await h.consume(_msg(
        1,
        from_user=_user(1),
        forward_from=_user(2),
        reply_to_message=_msg(9, from_user=_user(3), forward_from=_user(4)),
        entities=[ent],
        new_chat_members=[_user(6)],
    ))

    got = dict(sink.calls)
    assert got == {1: "msg", 2: "fwd", 3: "reply", 4: "reply_fwd",
                   5: "mention", 6: "join"}, (
        "همهٔ شش منبع باید در یک عبور استخراج شوند"
    )


async def test_join_events_no_longer_need_their_own_pass():
    """scrape_join_events یک عبور کامل می‌زد تا فقط یک فیلد را بخواند."""
    sink = _Sink()
    h = MessageHarvester(sink)
    await h.consume(_msg(1, new_chat_members=[_user(7), _user(8)]))
    assert {u for u, _ in sink.calls} == {7, 8}


async def test_bots_and_deleted_accounts_are_dropped():
    """ادد کردنشان غیرممکن است — نگه‌داشتنشان یعنی صف آلوده."""
    sink = _Sink()
    h = MessageHarvester(sink)
    await h.consume(_msg(1, from_user=_user(1, is_bot=True)))
    await h.consume(_msg(2, from_user=_user(2, is_deleted=True)))
    assert sink.calls == []


async def test_already_known_users_are_never_re_emitted():
    """هر کاربر تکراری یعنی نوشتن بی‌فایده در دیتابیس."""
    sink = _Sink()
    h = MessageHarvester(sink, existing_ids={1})
    await h.consume(_msg(1, from_user=_user(1)))
    await h.consume(_msg(2, from_user=_user(2)))
    await h.consume(_msg(3, from_user=_user(2)))  # تکراری
    assert sink.calls == [(2, "msg")]


async def test_reaction_and_poll_ids_are_collected_not_fetched():
    """⚡ کلید صرفه‌جویی دوم.

    ری‌اکشن و رأی درخواست جدا لازم دارند، ولی نباید تاریخچه را دوباره
    خواند تا بفهمیم کدام پیام‌ها دارندشان. شناسه در همان عبور اول جمع
    می‌شود.
    """
    h = MessageHarvester(_Sink())
    await h.consume(_msg(10, reactions=types.SimpleNamespace(reactions=[1])))
    await h.consume(_msg(11, poll=object()))
    await h.consume(_msg(12))  # نه ری‌اکشن نه نظرسنجی
    assert h.reaction_msg_ids == [10]
    assert h.poll_msg_ids == [11], "فقط پیام‌های واجد شرایط پرسیده شوند"


async def test_harvester_counts_messages_for_cap_detection():
    """استراتژی از این عدد می‌فهمد که آیا به سقف خورده‌ایم."""
    h = MessageHarvester(_Sink())
    for i in range(5):
        await h.consume(_msg(i))
    assert h.messages_seen == 5


# ============================================================ Oracle

class _App:
    def __init__(self, members=(), fail=()):
        self.members = set(members)
        self.fail = set(fail)
        self.calls = 0

    async def get_chat_member(self, chat, uid):
        self.calls += 1
        if uid in self.fail:
            raise RuntimeError("USER_NOT_PARTICIPANT")
        return object() if uid in self.members else None


async def test_membership_is_asked_only_once_per_user():
    """⚠️ همان کاربر در چند روش مختلف پیدا می‌شود.

    بدون کش، هر بار یک درخواست جدا مصرف می‌شد.
    """
    app = _App(members={1})
    o = MembershipOracle(app, "g")
    for _ in range(5):
        assert await o.is_member(1) is True
    assert app.calls == 1, f"باید ۱ درخواست باشد، {app.calls} بود"
    assert o.stats["saved"] == 4


async def test_negative_results_are_cached_too():
    """غیرعضوها هم نباید دوباره پرسیده شوند."""
    app = _App(members=set())
    o = MembershipOracle(app, "g")
    for _ in range(4):
        assert await o.is_member(99) is False
    assert app.calls == 1


async def test_guaranteed_members_cost_zero_requests():
    """⚡ بزرگ‌ترین صرفه‌جویی کش.

    نتایج get_chat_members و GetParticipants ذاتاً عضو هستند؛ پرسیدن
    دوباره‌شان اتلاف محض است.
    """
    app = _App()
    o = MembershipOracle(app, "g")
    o.mark_members(range(1000))
    for uid in range(1000):
        assert await o.is_member(uid) is True
    assert app.calls == 0, "هیچ درخواستی نباید فرستاده شود"
    assert o.stats["saved_pct"] == 100.0


async def test_api_errors_are_cached_as_outsider():
    """خطا هم نتیجه است — تکرارش فقط درخواست هدر می‌دهد."""
    app = _App(fail={7})
    o = MembershipOracle(app, "g")
    assert await o.is_member(7) is False
    assert await o.is_member(7) is False
    assert app.calls == 1


async def test_is_settled_lets_callers_skip_pending_work():
    o = MembershipOracle(_App(), "g")
    o.mark_member(1)
    assert o.is_settled(1) and not o.is_settled(2)


async def test_flood_is_delegated_not_swallowed():
    """FloodWait باید به هندلر برسد تا throttle تنظیم شود."""
    from pyrogram.errors import FloodWait

    class _F:
        calls = 0

        async def get_chat_member(self, c, u):
            _F.calls += 1
            raise FloodWait(value=1)

    seen = []

    async def on_flood(e):
        seen.append(e.value)

    o = MembershipOracle(_F(), "g", on_flood=on_flood)
    await o.is_member(1)
    assert seen == [1]


# ============================================================ Throttle

def test_throttle_starts_at_full_speed():
    """⚠️ تأخیر ثابت یعنی کندی بی‌دلیل وقتی تلگرام راحت است."""
    assert AdaptiveThrottle().delay == 0.0


def test_throttle_backs_off_after_flood():
    t = AdaptiveThrottle()
    t.on_flood(10)
    first = t.delay
    assert first > 0
    t.on_flood(10)
    assert t.delay > first, "هر فلود باید عقب‌نشینی را بیشتر کند"


def test_throttle_never_exceeds_ceiling():
    """محافظ در برابر کند شدن بی‌نهایت."""
    t = AdaptiveThrottle(ceiling=1.0)
    for _ in range(50):
        t.on_flood(10)
    assert t.delay <= 1.0


def test_throttle_recovers_after_quiet_period():
    """بعد از آرامش باید دوباره سرعت بگیرد، وگرنه یک فلود کل عملیات را کند می‌کند."""
    t = AdaptiveThrottle()
    t.on_flood(5)
    peak = t.delay
    t._last_flood -= 31  # شبیه‌سازی ۳۱ ثانیه آرامش
    t.on_success()
    assert t.delay < peak


# ============================================================ یکپارچگی

SRC = (ROOT / "attacker.py").read_text(encoding="utf-8")


def test_no_raw_membership_calls_remain_in_attacker():
    """هر get_chat_member مستقیم یعنی دور زدن کش."""
    assert "self.app.get_chat_member(" not in SRC, (
        "همهٔ تأییدهای عضویت باید از MembershipOracle عبور کنند"
    )


def test_history_is_read_by_exactly_one_method():
    """⚠️ هستهٔ صرفه‌جویی اول: شش عبور → یک عبور."""
    tree = ast.parse(SRC)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "AdvancedScraper")
    readers = [n.name for n in cls.body
               if isinstance(n, ast.AsyncFunctionDef)
               and "get_chat_history" in (ast.get_source_segment(SRC, n) or "")]
    # scrape_deep_history تنها استثناست: فقط وقتی اجرا می‌شود که عبور
    # اول به سقف خورده باشد، و برای رسیدن به پیام‌های قدیمی‌تر ناچار
    # است دوباره بخواند — ولی از همان MessageHarvester استفاده می‌کند.
    assert set(readers) == {"scrape_unified_history", "scrape_deep_history"}, (
        f"فقط این دو مجازند تاریخچه بخوانند، ولی: {readers}"
    )


def test_deep_history_reuses_the_harvester_instead_of_duplicating_logic():
    tree = ast.parse(SRC)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "AdvancedScraper")
    body = ast.get_source_segment(
        SRC, next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "scrape_deep_history"))
    assert "MessageHarvester" in body
    assert "add_user(msg.from_user" not in body, "منطق نباید تکرار شود"


def test_adaptive_throttle_replaced_fixed_sleeps_in_hot_loops():
    """تأخیر ثابت per-message روی ۱۰٬۰۰۰ پیام ≈ ۹ دقیقه اتلاف."""
    tree = ast.parse(SRC)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "AdvancedScraper")
    body = ast.get_source_segment(
        SRC, next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "scrape_unified_history"))
    assert "self._throttle.wait()" in body
    assert "human_sleep" not in body
