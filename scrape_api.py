# =================================================================
# 🔌 لایه‌ی سازگاری با MTProto خام (Pyrogram 2.0.106)
# =================================================================
"""چرا این فایل وجود دارد.

چند متد استخراج در attacker.py به هلپرهای سطح‌بالایی صدا می‌زدند که
**در Pyrogram 2.0.106 اصلاً وجود ندارند**:

    Client.get_message_reactions   ❌ وجود ندارد
    Client.get_poll_voters         ❌ وجود ندارد

و یکی هم به `pyrogram.raw.functions.types` اشاره می‌کرد که مسیر
غلطی است (تایپ‌ها زیر `pyrogram.raw.types` هستند، نه زیر `functions`).

نتیجه: AttributeError پرتاب می‌شد و دقیقاً یک لایه بالاتر توسط
`except: pass` یا `except Exception: continue` بلعیده می‌شد. یعنی متدها
هرگز کار نمی‌کردند ولی هیچ خطایی هم در لاگ دیده نمی‌شد — «موفقیت
خاموش». به همین دلیل ۹ ماه کسی متوجه نشد.

این ماژول همان قابلیت‌ها را مستقیم روی MTProto خام پیاده می‌کند و با
تست‌های واقعی امضا (signature) قفل شده تا ارتقای بعدی Pyrogram دوباره
بی‌صدا نشکند.
"""
import asyncio

from pyrogram.errors import FloodWait
from pyrogram.raw import functions, types

# چند نوع پاسخ رأی نظرسنجی در لایه‌های مختلف API وجود دارد
_VOTE_TYPES = tuple(
    t for t in (
        getattr(types, "MessageUserVote", None),
        getattr(types, "MessageUserVoteInputOption", None),
        getattr(types, "MessageUserVoteMultiple", None),
    ) if t is not None
)


def peer_user_id(peer):
    """استخراج user_id از هر شکلِ Peer.

    ری‌اکشن‌ها می‌توانند از طرف کانال هم باشند (PeerChannel) که user_id
    ندارد؛ در آن حالت None برمی‌گردد و باید رد شود.
    """
    if peer is None:
        return None
    uid = getattr(peer, "user_id", None)
    return int(uid) if uid else None


async def iter_message_reactors(app, peer, msg_id, reaction=None, limit=100,
                                max_total=1000, on_flood=None):
    """جایگزین درست `get_message_reactions` که در این نسخه وجود ندارد.

    از `messages.GetMessageReactionsList` استفاده می‌کند و با `next_offset`
    صفحه‌بندی می‌کند. کاربران کامل را از فیلد `users` پاسخ برمی‌گرداند —
    یعنی **بدون** نیاز به یک `get_users` جداگانه به ازای هر نفر، که در
    نسخه‌ی قبلی هزینه‌ی اصلی بود.
    """
    input_peer = await app.resolve_peer(peer)
    reaction_obj = types.ReactionEmoji(emoticon=reaction) if isinstance(reaction, str) else reaction
    offset = None
    seen = 0
    while True:
        try:
            res = await app.invoke(functions.messages.GetMessageReactionsList(
                peer=input_peer, id=msg_id, limit=limit,
                reaction=reaction_obj, offset=offset,
            ))
        except FloodWait as e:
            if on_flood:
                await on_flood(e)
            else:
                await asyncio.sleep(e.value)
            continue
        except Exception:
            return

        users = {u.id: u for u in getattr(res, "users", []) or []}
        reactions = getattr(res, "reactions", []) or []
        if not reactions:
            return
        for r in reactions:
            uid = peer_user_id(getattr(r, "peer_id", None))
            if uid is None:
                continue
            seen += 1
            yield uid, users.get(uid)
            if seen >= max_total:
                return
        offset = getattr(res, "next_offset", None)
        if not offset:
            return


async def iter_poll_voters(app, peer, msg_id, limit=100, max_total=1000,
                           on_flood=None):
    """جایگزین درست `get_poll_voters` که در این نسخه وجود ندارد."""
    input_peer = await app.resolve_peer(peer)
    offset = None
    seen = 0
    while True:
        try:
            res = await app.invoke(functions.messages.GetPollVotes(
                peer=input_peer, id=msg_id, limit=limit, offset=offset,
            ))
        except FloodWait as e:
            if on_flood:
                await on_flood(e)
            else:
                await asyncio.sleep(e.value)
            continue
        except Exception:
            return

        users = {u.id: u for u in getattr(res, "users", []) or []}
        votes = getattr(res, "votes", []) or []
        if not votes:
            return
        for v in votes:
            uid = getattr(v, "user_id", None) or peer_user_id(getattr(v, "peer", None))
            if not uid:
                continue
            seen += 1
            yield int(uid), users.get(int(uid))
            if seen >= max_total:
                return
        offset = getattr(res, "next_offset", None)
        if not offset:
            return


async def search_global(app, query, limit=50, on_flood=None):
    """جستجوی سراسری پیام‌ها.

    نسخه‌ی قبلی `raw_fns.types.InputMessagesFilterEmpty()` را صدا می‌زد که
    مسیرش وجود ندارد ⇒ AttributeError در همان اولین تکرار حلقه.
    """
    try:
        return await app.invoke(functions.messages.SearchGlobal(
            q=query,
            filter=types.InputMessagesFilterEmpty(),
            min_date=0, max_date=0, offset_rate=0,
            offset_peer=types.InputPeerEmpty(),
            offset_id=0, limit=limit,
        ))
    except FloodWait as e:
        if on_flood:
            await on_flood(e)
        return None
    except Exception:
        return None


async def as_input_channel(app, peer):
    """تبدیل هر شناسه به InputChannel.

    `channels.GetParticipants` فقط InputChannel قبول می‌کند، نه InputPeer.
    گروه‌های ساده (basic group) کانال نیستند و None برمی‌گردانند.
    """
    p = await app.resolve_peer(peer)
    if isinstance(p, types.InputPeerChannel):
        return types.InputChannel(channel_id=p.channel_id, access_hash=p.access_hash)
    if isinstance(p, types.InputChannel):
        return p
    return None


async def iter_participants_search(app, channel, query="", limit=200,
                                   max_total=10000, on_flood=None):
    """صفحه‌بندی مستقیم اعضا با `ChannelParticipantsSearch`.

    ⚡ چرا این روش خیلی بهتر از نسخه‌ی قبلی است:

    نسخه‌ی قبلی برای هر پیشوند، `contacts.Search` سراسری می‌زد و بعد برای
    **تک‌تک** نتایج یک `get_chat_member` می‌فرستاد تا ببیند عضو هست یا نه.
    یعنی N+1 درخواست، و نتایج جستجوی سراسری اصلاً ربطی به گروه هدف
    نداشتند، پس نرخ اصابت نزدیک صفر بود.

    این نسخه پرس‌وجو را به خود سرور تلگرام می‌سپارد: جستجو مستقیماً روی
    لیست اعضای همان کانال انجام می‌شود، پس هر نتیجه **قطعاً** عضو است و
    هیچ درخواست تأییدی لازم نیست. ضمناً سقف ۱۰٬۰۰۰ نفریِ لیست معمولی را
    هم دور می‌زند، چون هر پیشوند سهمیه‌ی جداگانه دارد.
    """
    input_channel = await as_input_channel(app, channel)
    if input_channel is None:
        return
    offset = 0
    seen = 0
    while offset < max_total:
        try:
            res = await app.invoke(functions.channels.GetParticipants(
                channel=input_channel,
                filter=types.ChannelParticipantsSearch(q=query),
                offset=offset, limit=limit, hash=0,
            ))
        except FloodWait as e:
            if on_flood:
                await on_flood(e)
            else:
                await asyncio.sleep(e.value)
            continue
        except Exception:
            return

        users = getattr(res, "users", None)
        if not users:
            return
        for u in users:
            seen += 1
            yield u
        if len(users) < limit:
            return
        offset += len(users)
