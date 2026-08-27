"""قفل‌کردن لایه‌ی MTProto خام در برابر «شکست خاموش».

پس‌زمینه: چهار متد از دوازده متد استخراج هرگز کار نمی‌کردند. علتش این
بود که به API‌هایی صدا می‌زدند که در Pyrogram 2.0.106 وجود ندارند:

    Client.get_message_reactions        ❌ وجود ندارد
    Client.get_poll_voters              ❌ وجود ندارد
    raw.functions.types.InputMessages…  ❌ مسیر غلط

هر سه AttributeError می‌دادند و دقیقاً یک لایه بالاتر با `except: pass`
بلعیده می‌شدند. متد «موفق» گزارش می‌شد ولی صفر کاربر برمی‌گرداند.

این تست‌ها امضای واقعی API را بررسی می‌کنند تا ارتقای بعدی Pyrogram
دوباره بی‌صدا نشکند — اگر مسیری عوض شود، اینجا قرمز می‌شود نه در
پروداکشن.
"""
import inspect

import pytest

import scrape_api


# ---------------------------------------- فرضیات درباره‌ی API خام

def test_raw_functions_have_no_types_attribute():
    """همان اشتباهی که scrape_global_search را کشته بود."""
    from pyrogram.raw import functions
    assert not hasattr(functions, "types"), (
        "اگر این روزی True شود یعنی ساختار Pyrogram عوض شده و باید "
        "دوباره بررسی شود"
    )


def test_highlevel_reaction_helpers_really_are_absent():
    """اثبات اینکه حذف آن فراخوانی‌ها لازم بود، نه سلیقه‌ای."""
    from pyrogram import Client
    assert not hasattr(Client, "get_message_reactions")
    assert not hasattr(Client, "get_poll_voters")


@pytest.mark.parametrize("mod,name,params", [
    ("messages", "GetMessageReactionsList", {"peer", "id", "limit", "reaction", "offset"}),
    ("messages", "GetPollVotes", {"peer", "id", "limit", "option", "offset"}),
    ("messages", "SearchGlobal", {"q", "filter", "min_date", "max_date",
                                  "offset_rate", "offset_peer", "offset_id", "limit"}),
    ("channels", "GetParticipants", {"channel", "filter", "offset", "limit", "hash"}),
])
def test_raw_signature_matches_our_call(mod, name, params):
    """اگر امضا عوض شود فراخوانی ما TypeError می‌دهد — اینجا بگیریمش."""
    import importlib
    m = importlib.import_module(f"pyrogram.raw.functions.{mod}")
    sig = inspect.signature(getattr(m, name).__init__)
    actual = set(sig.parameters) - {"self"}
    missing = params - actual
    assert not missing, f"{name} دیگر این پارامترها را ندارد: {missing}"


def test_required_raw_types_exist():
    from pyrogram.raw import types
    for n in ("InputMessagesFilterEmpty", "InputPeerEmpty", "ReactionEmoji",
              "ChannelParticipantsSearch", "InputChannel", "InputPeerChannel"):
        assert hasattr(types, n), f"raw.types.{n} وجود ندارد"


# ---------------------------------------- رفتار هلپرها

def test_peer_user_id_handles_channel_reactions():
    """ری‌اکشن از طرف کانال user_id ندارد و نباید کاربر جعلی بسازد."""
    from pyrogram.raw import types
    assert scrape_api.peer_user_id(types.PeerUser(user_id=77)) == 77
    assert scrape_api.peer_user_id(types.PeerChannel(channel_id=5)) is None
    assert scrape_api.peer_user_id(None) is None


class _FakeApp:
    """اپ ساختگی که پاسخ‌های صفحه‌بندی‌شده را برمی‌گرداند."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    async def resolve_peer(self, peer):
        return peer

    async def invoke(self, query):
        self.calls.append(query)
        return self.pages.pop(0) if self.pages else None


def _reaction_page(uids, next_offset):
    from pyrogram.raw import types
    from pyrogram.raw.types.messages import MessageReactionsList
    return MessageReactionsList(
        count=len(uids),
        reactions=[types.MessagePeerReaction(
            peer_id=types.PeerUser(user_id=u), date=0,
            reaction=types.ReactionEmoji(emoticon="👍"),
        ) for u in uids],
        chats=[], users=[], next_offset=next_offset,
    )


async def test_reactors_follow_next_offset():
    """⚠️ بدون دنبال‌کردن next_offset فقط صفحه‌ی اول می‌آمد.

    یعنی از پستی با ۵۰۰۰ ری‌اکشن، ۱۰۰ نفر استخراج می‌شد.
    """
    app = _FakeApp([_reaction_page([1, 2], "cur"), _reaction_page([3], None)])
    got = [uid async for uid, _ in
           scrape_api.iter_message_reactors(app, "g", 1, limit=2)]
    assert got == [1, 2, 3]
    assert app.calls[1].offset == "cur", "offset صفحه‌ی بعد ارسال نشد"


async def test_reactors_respect_max_total():
    """محافظ در برابر پست ویروسی با میلیون‌ها ری‌اکشن."""
    app = _FakeApp([_reaction_page([1, 2, 3, 4], "x")])
    got = [uid async for uid, _ in
           scrape_api.iter_message_reactors(app, "g", 1, max_total=2)]
    assert got == [1, 2]


async def test_reactors_return_user_objects_from_payload():
    """صرفه‌جویی کلیدی: کاربر از خود پاسخ می‌آید، نه get_users جداگانه.

    نسخه‌ی قبلی به ازای هر ری‌اکت‌دهنده یک get_users می‌زد؛ روی پستی با
    ۱۰۰۰ ری‌اکشن یعنی ۱۰۰۰ درخواست اضافه و فلود قطعی.
    """
    from pyrogram.raw import types
    from pyrogram.raw.types.messages import MessageReactionsList
    page = MessageReactionsList(
        count=1,
        reactions=[types.MessagePeerReaction(
            peer_id=types.PeerUser(user_id=9), date=0,
            reaction=types.ReactionEmoji(emoticon="🔥"))],
        chats=[],
        users=[types.User(id=9, first_name="Ali")],
        next_offset=None,
    )
    app = _FakeApp([page])
    out = [(uid, u) async for uid, u in
           scrape_api.iter_message_reactors(app, "g", 1)]
    assert out[0][0] == 9
    assert out[0][1].first_name == "Ali", "کاربر باید از payload بیاید"


async def test_reaction_string_becomes_reaction_object():
    """رشته‌ی ایموجی باید به ReactionEmoji تبدیل شود وگرنه سرور رد می‌کند."""
    from pyrogram.raw import types
    app = _FakeApp([_reaction_page([1], None)])
    _ = [x async for x in
         scrape_api.iter_message_reactors(app, "g", 1, reaction="😂")]
    assert isinstance(app.calls[0].reaction, types.ReactionEmoji)
    assert app.calls[0].reaction.emoticon == "😂"


async def test_poll_voters_paginate_and_carry_users():
    from pyrogram.raw import types
    from pyrogram.raw.types.messages import VotesList
    page = VotesList(
        count=2,
        votes=[types.MessageUserVote(user_id=4, option=b"a", date=0)],
        users=[types.User(id=4, first_name="Sara")],
        next_offset=None,
    )
    app = _FakeApp([page])
    out = [(uid, u) async for uid, u in scrape_api.iter_poll_voters(app, "g", 1)]
    assert out == [(4, out[0][1])] and out[0][1].first_name == "Sara"


async def test_search_global_builds_valid_filter():
    """این دقیقاً همان فراخوانی است که قبلاً AttributeError می‌داد."""
    from pyrogram.raw import types
    app = _FakeApp([object()])
    await scrape_api.search_global(app, "سلام", limit=10)
    q = app.calls[0]
    assert isinstance(q.filter, types.InputMessagesFilterEmpty)
    assert isinstance(q.offset_peer, types.InputPeerEmpty)
    assert q.q == "سلام"


async def test_participants_search_stops_on_short_page():
    """صفحه‌ی ناقص یعنی پایان — وگرنه حلقه‌ی بی‌نهایت.

    ⚠️ سرور روی صفحه‌ی آخر لیست کوتاه‌تر می‌دهد و بعد لیست خالی. اگر فقط
    به «خالی» تکیه کنیم یک درخواست اضافه می‌دهیم؛ اگر به هیچ‌کدام تکیه
    نکنیم تا ابد می‌چرخیم.
    """
    from pyrogram.raw import types
    from pyrogram.raw.types.channels import ChannelParticipants

    def page(n):
        return ChannelParticipants(
            count=n, participants=[],
            chats=[], users=[types.User(id=i) for i in range(n)])

    app = _FakeApp([page(200), page(3), page(200)])
    app.resolve_peer = _mk_channel_resolver()
    got = [u async for u in
           scrape_api.iter_participants_search(app, "g", limit=200)]
    assert len(got) == 203, "باید بعد از صفحه‌ی ناقص متوقف شود"


async def test_participants_search_advances_offset():
    """بدون افزایش offset، همان صفحه‌ی اول بی‌نهایت بار می‌آمد."""
    from pyrogram.raw import types
    from pyrogram.raw.types.channels import ChannelParticipants
    app = _FakeApp([
        ChannelParticipants(count=2, participants=[], chats=[],
                            users=[types.User(id=1), types.User(id=2)]),
        ChannelParticipants(count=0, participants=[], chats=[], users=[]),
    ])
    app.resolve_peer = _mk_channel_resolver()
    _ = [u async for u in
         scrape_api.iter_participants_search(app, "g", limit=2)]
    assert [c.offset for c in app.calls] == [0, 2]


async def test_basic_group_is_skipped_not_crashed():
    """گروه ساده کانال نیست؛ GetParticipants رویش خطا می‌دهد."""
    from pyrogram.raw import types
    app = _FakeApp([])

    async def resolve(peer):
        return types.InputPeerChat(chat_id=5)

    app.resolve_peer = resolve
    got = [u async for u in scrape_api.iter_participants_search(app, "g")]
    assert got == []


def _mk_channel_resolver():
    from pyrogram.raw import types

    async def resolve(peer):
        return types.InputPeerChannel(channel_id=1, access_hash=2)

    return resolve
