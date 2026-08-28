"""
Group Manager PRO — کامل‌ترین ماژول مدیریت گروه تلگرام
================================================================
استاندارد بات‌های روز دنیا (Rose / Combot / GroupHelp / Shieldy):

🤖 CAS (Combot Anti-Spam) — چک بانک جهانی اسپمرها هنگام عضویت
🧩 CAPTCHA — دکمه تأیید انسان‌بودن، کیک خودکار غیرپاسخ‌دهنده‌ها
🛡️ ضدحمله (Raid Protection) — قفل خودکار گروه در حمله جوین انبوه
⏱️ بن/میوت زمان‌دار — پارس زمان انسانی (30s 10m 2h 3d 1w)
🧹 purge / del — پاک‌سازی انبوه با ریپلای
🔒 قفل‌های ریز — لینک/فوروارد/استیکر/گیف/ویس/ویدیو/عکس/فایل/کانتکت/نظرسنجی/کپس/ایموجی
📋 پنل اینلاین — همه تنظیمات با دکمه (به‌جای دستور متنی)
📢 گزارش به ادمین‌ها — /report یا ریپلای @admin
💾 ذخیره‌سازی دائمی — تنظیمات و وارن‌ها در PostgreSQL
⭐ promote / demote — ارتقا و تنزل با بات
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import random
import string
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from urllib import request as _ureq

from pyrogram import Client, filters
from pyrogram.errors import (
    UserNotParticipant, ChatAdminRequired, RightForbidden,
    RPCError, UserAdminInvalid, PeerIdInvalid, FloodWait
)
from pyrogram.enums import ChatMembersFilter
from pyrogram.types import (
    Message, CallbackQuery, ChatPermissions,
    InlineKeyboardMarkup, InlineKeyboardButton
)

import db

logger = logging.getLogger("antiscraper.grouppro")

CAS_API = "https://api.cas.chat"          # Combot Anti-Spam
CAPTCHA_TTL = 180                          # ثانیه فرصت حل کپچا
ADMIN_CACHE_TTL = 300                      # کش ادمین‌بودن (ثانیه)

# ══════════════════════════════════════════════
# دیتابیس — جدول‌های ماژول
# ══════════════════════════════════════════════

def _init_tables():
    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mod_settings_tbl (
            chat_id BIGINT PRIMARY KEY,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at BIGINT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mod_warns_tbl (
            chat_id BIGINT,
            user_id BIGINT,
            count INTEGER DEFAULT 0,
            updated_at BIGINT,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    conn.commit()
    cur.close()


_tables_ok = False


def _ensure_tables():
    """ساخت جدول‌ها با اولین استفاده — موقع import پول دیتابیس هنوز آماده نیست"""
    global _tables_ok
    if _tables_ok:
        return
    try:
        _init_tables()
        _tables_ok = True
        logger.info("mod tables ready")
    except Exception as e:
        logger.error(f"mod tables init err: {e}")


def _db_load_settings(chat_id: int) -> dict | None:
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute("SELECT settings FROM mod_settings_tbl WHERE chat_id = %s", (chat_id,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"load settings err: {e}")
        return None


def _db_save_settings(chat_id: int, settings: dict):
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mod_settings_tbl (chat_id, settings, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET settings = EXCLUDED.settings,
                                                   updated_at = EXCLUDED.updated_at
        """, (chat_id, Json_wrap(settings), int(time.time())))
        cur.close()
    except Exception as e:
        logger.error(f"save settings err: {e}")


def Json_wrap(d):
    from psycopg2.extras import Json
    return Json(d)


def _db_warns(chat_id: int, user_id: int) -> int:
    _ensure_tables()
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute("SELECT count FROM mod_warns_tbl WHERE chat_id=%s AND user_id=%s",
                    (chat_id, user_id))
        row = cur.fetchone()
        cur.close()
        return row[0] or 0
    except Exception:
        return 0


def _db_warn_add(chat_id: int, user_id: int, delta: int = 1) -> int:
    _ensure_tables()
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO mod_warns_tbl (chat_id, user_id, count, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (chat_id, user_id)
            DO UPDATE SET count = mod_warns_tbl.count + %s, updated_at = EXCLUDED.updated_at
            RETURNING count
        """, (chat_id, user_id, max(delta, 0), int(time.time()), delta))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        return row[0] if row else delta
    except Exception as e:
        logger.error(f"warn add err: {e}")
        return 0


def _db_warn_reset(chat_id: int, user_id: int):
    try:
        conn = db.get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM mod_warns_tbl WHERE chat_id=%s AND user_id=%s",
                    (chat_id, user_id))
        conn.commit()
        cur.close()
    except Exception:
        pass

# ══════════════════════════════════════════════
# تنظیمات پیش‌فرض + دسترسی
# ══════════════════════════════════════════════

DEFAULTS = {
    "enabled": True,             # کلید اصلی مدیریت این گروه
    "welcome": True,
    "welcome_text": "",
    "goodbye": False,
    # قفل‌ها
    "lock_links": True,
    "lock_fwd": False,
    "lock_sticker": False,
    "lock_gif": False,
    "lock_voice": False,
    "lock_video_note": False,
    "lock_video": False,
    "lock_photo": False,
    "lock_document": False,
    "lock_contact": False,
    "lock_poll": False,
    "lock_channel": True,        # پیامِ ارسالی از کانال (بوت‌پست)
    "lock_caps": True,           # حروف بزرگ
    "caps_percent": 70,
    "caps_min_len": 12,
    "lock_emoji_flood": False,
    "emoji_max": 15,
    # ضد اسپم
    "flood": True,
    "flood_limit": 6,            # پیام
    "flood_time": 6,             # در N ثانیه
    "flood_action": "mute",      # mute | kick
    "flood_mute_min": 10,        # دقیقه
    # فحش
    "lock_profanity": True,
    "profanity_action": "warn",  # warn | mute | ban | delete
    # وارن
    "warn_limit": 3,
    "warn_action": "mute",       # mute | kick | ban
    "warn_mute_hours": 12,
    # عضویت
    "captcha": False,
    "cas_check": True,
    "cas_action": "ban",
    "raid_protection": True,
    "raid_joins": 6,             # N جوین
    "raid_window": 30,           # در M ثانیه
    "raid_lock_minutes": 10,
    # لاگ
    "log_chat": 0,               # کانال/گروه گزارش مودریشن (0 = خاموش)
    # کلمات ممنوع (لیست؛ regex ساده مجاز)
    "blacklist": [],
    "whitelist_links": ["t.me/", "telegram.me"],   # همیشه مجاز وقتی lock_links خاموش نیست
}

_settings_cache: dict[int, dict] = {}
_admin_cache: dict[tuple, tuple] = {}    # {(chat,user): (is_admin, ts)}
_user_msg_log = defaultdict(lambda: defaultdict(deque))   # {(chat,user): deque[ts]}
_join_log = defaultdict(deque)                            # {chat: deque[(ts,user)]}
_raid_lock_until: dict[int, float] = {}
_pending_captcha: dict[tuple, dict] = {}                  # {(chat,user): {...}}


def get_group_settings(chat_id: int) -> dict:
    _ensure_tables()
    s = _settings_cache.get(chat_id)
    if s is None:
        s = dict(DEFAULTS)
        saved = _db_load_settings(chat_id)
        if saved:
            s.update(saved)
        _settings_cache[chat_id] = s
    return s


def save_group_settings(chat_id: int):
    _db_save_settings(chat_id, _settings_cache.get(chat_id, {}))


def set_setting(chat_id: int, key: str, value):
    s = get_group_settings(chat_id)
    s[key] = value
    save_group_settings(chat_id)

# ══════════════════════════════════════════════
# ابزارها
# ══════════════════════════════════════════════

_DUR_RE = re.compile(r"(\d+)\s*(s|ث|m|د|h|س|d|روز|w|ه)")
_DUR_MAP = {"s": 1, "ث": 1, "m": 60, "د": 60, "h": 3600, "س": 3600,
            "d": 86400, "روز": 86400, "w": 604800, "ه": 604800}


def parse_duration(text: str) -> int | None:
    """'2h30m' یا '۱ ساعت' یا '90m' → ثانیه؛ نامعتبر → None"""
    if not text:
        return None
    total = 0
    found = False
    for num, unit in _DUR_RE.findall(text.lower()):
        total += int(num) * _DUR_MAP.get(unit, 0)
        found = True
    if not found:
        try:
            total = int(text)
            found = True
        except Exception:
            return None
    return total if found and total > 0 else None


def fmt_duration(sec: int) -> str:
    d, r = divmod(sec, 86400); h, r = divmod(r, 3600); m, s = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}روز")
    if h: parts.append(f"{h}ساعت")
    if m: parts.append(f"{m}دقیقه")
    if s and not d: parts.append(f"{s}ثانیه")
    return " ".join(parts) or "همیشه"


def human(u) -> str:
    if u is None:
        return "؟"
    name = (getattr(u, "first_name", "") or "") + " " + (getattr(u, "last_name", "") or "")
    name = name.strip() or (u.username or str(u.id))
    return f"<a href='tg://user?id={u.id}'>{name}</a>"


def log_mod(c: Client, chat_id: int, text: str):
    """ارسال رویداد مودریشن به کانال لاگ (اگر تنظیم باشد)"""
    s = get_group_settings(chat_id)
    if s.get("log_chat"):
        try:
            asyncio.get_event_loop().create_task(
                c.send_message(s["log_chat"], text))
        except Exception:
            pass


async def is_admin(c: Client, chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    cached = _admin_cache.get(key)
    if cached and time.time() - cached[1] < ADMIN_CACHE_TTL:
        return cached[0]
    try:
        m = await c.get_chat_member(chat_id, user_id)
        ok = getattr(m, "status", "") in ("administrator", "creator")
    except Exception:
        ok = False
    _admin_cache[key] = (ok, time.time())
    return ok


async def bot_can_restrict(c: Client, chat_id: int) -> bool:
    try:
        me = await c.get_chat_member(chat_id, "me")
        st = getattr(me, "status", "")
        if st not in ("administrator", "creator"):
            return False
        if st == "creator":
            return True
        perms = getattr(me, "privileges", None)
        return bool(perms and getattr(perms, "can_restrict_members", False))
    except Exception:
        return False


async def safe_delete(m: Message):
    try:
        await m.delete()
    except Exception:
        pass


def cas_check_sync(user_id: int) -> bool:
    """True = در بانک اسپمرهای جهانی است (requests-free با urllib)"""
    try:
        with _ureq.urlopen(f"{CAS_API}/check?user_id={user_id}", timeout=6) as r:
            data = json.loads(r.read().decode())
            return bool(data.get("ok"))
    except Exception:
        return False


async def cas_check(user_id: int) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, cas_check_sync, user_id)

# ══════════════════════════════════════════════
# مجوزهای چت
# ══════════════════════════════════════════════

PERM_ALL = ChatPermissions(
    can_send_messages=True, can_send_media_messages=True,
    can_send_other_messages=True, can_send_polls=True,
    can_add_web_page_previews=True)
PERM_NONE = ChatPermissions(can_send_messages=False)
PERM_NO_MEDIA = ChatPermissions(
    can_send_messages=True, can_send_media_messages=False,
    can_send_other_messages=False, can_send_polls=False,
    can_add_web_page_previews=True)

# ══════════════════════════════════════════════
# CAPTCHA
# ══════════════════════════════════════════════

async def _kick_later(c: Client, chat_id: int, user, msg: Message):
    await asyncio.sleep(CAPTCHA_TTL)
    key = (chat_id, user.id)
    if key in _pending_captcha:
        _pending_captcha.pop(key, None)
        try:
            await c.ban_chat_member(chat_id, user.id, until_date=datetime.now() + timedelta(minutes=2))
            await c.unban_chat_member(chat_id, user.id)
        except Exception:
            pass
        try:
            await msg.edit_text(f"⏱ {human(user)} وقتش تموم شد — کیک شد. دوباره جوین کن!")
        except Exception:
            pass
        log_mod(c, chat_id, f"🧩⏱ کیک نشدنِ کپچا: {human(user)}")


async def handle_new_member(c: Client, m: Message):
    chat_id = m.chat.id
    s = get_group_settings(chat_id)

    for u in (m.new_chat_members or []):
        if u.id in (c.me.id,):
            continue

        # ── ضدحمله ──
        if s.get("raid_protection"):
            now = time.time()
            dq = _join_log[chat_id]
            dq.append((now, u.id))
            while dq and now - dq[0][0] > s.get("raid_window", 30):
                dq.popleft()
            until = _raid_lock_until.get(chat_id, 0)
            if not until and len(dq) >= s.get("raid_joins", 6):
                _raid_lock_until[chat_id] = now + s.get("raid_lock_minutes", 10) * 60
                try:
                    await c.set_chat_permissions(chat_id, PERM_NONE)
                    await m.reply_text(
                        f"🛡️ <b>حمله عضویت شناسایی شد!</b>\n"
                        f"{len(dq)} نفر در {s.get('raid_window', 30)} ثانیه جوین شدن.\n"
                        f"گروه {s.get('raid_lock_minutes', 10)} دقیقه قفل شد.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔓 بازکردن گروه", callback_data="gpro_unlock")
                        ]]))
                    log_mod(c, chat_id, f"🛡️ ضدحمله فعال شد — {len(dq)} جوین سریع")
                except Exception:
                    pass
                return

        # ── CAS ──
        if s.get("cas_check"):
            try:
                if await cas_check(u.id):
                    act = s.get("cas_action", "ban")
                    try:
                        if act == "ban":
                            await c.ban_chat_member(chat_id, u.id)
                        else:
                            await c.kick_chat_member(chat_id, u.id)
                    except Exception:
                        pass
                    await m.reply_text(
                        f"🤖 <b>{human(u)}</b> در بانک جهانی اسپمرها (CAS) ثبت شده — {act} شد.")
                    log_mod(c, chat_id, f"🤖 CAS: {human(u)} → {act}")
                    continue
            except Exception:
                pass

        # ── کپچا ──
        if s.get("captcha"):
            try:
                await c.restrict_chat_member(chat_id, u.id, PERM_NONE,
                                             until_date=datetime.now() + timedelta(seconds=CAPTCHA_TTL + 60))
            except Exception:
                pass
            token = "".join(random.choices(string.ascii_letters + string.digits, k=8))
            _pending_captcha[(chat_id, u.id)] = {"token": token, "at": time.time()}
            btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ من انسانم", callback_data=f"gcap_{u.id}_{token}")
            ]])
            try:
                cmsg = await m.reply_text(
                    f"🧩 <b>خوش آمدی {human(u)}!</b>\n\n"
                    f"برای اثبات انسان‌بودن، تا {CAPTCHA_TTL // 60} دقیقه دیگر روی دکمه بزن،\n"
                    "وگرنه خودکار کیک می‌شوی.",
                    reply_markup=btn)
                asyncio.get_event_loop().create_task(
                    _kick_later(c, chat_id, u, cmsg))
            except Exception:
                pass
            continue

        # ── خوش‌آمد ساده ──
        if s.get("welcome"):
            txt = s.get("welcome_text") or \
                  f"👋 <b>خوش آمدی {human(u)}!</b>\nقوانین را رعایت کن 🌹"
            try:
                await m.reply_text(txt)
            except Exception:
                pass


async def handle_leave(c: Client, m: Message):
    s = get_group_settings(m.chat.id)
    _pending_captcha.pop((m.chat.id, m.left_chat_member.id), None)
    if s.get("goodbye") and m.left_chat_member and \
       m.left_chat_member.id != c.me.id:
        try:
            await m.reply_text(f"😢 <b>{human(m.left_chat_member)}</b> رفت...")
        except Exception:
            pass

# ══════════════════════════════════════════════
# وارن
# ══════════════════════════════════════════════

async def add_warn(c: Client, m: Message, target, reason: str = ""):
    chat_id = m.chat.id
    s = get_group_settings(chat_id)
    count = _db_warn_add(chat_id, target.id, 1)
    limit = s.get("warn_limit", 3)
    if count >= limit:
        _db_warn_reset(chat_id, target.id)
        act = s.get("warn_action", "mute")
        try:
            if act == "mute":
                await c.restrict_chat_member(
                    chat_id, target.id, PERM_NONE,
                    until_date=datetime.now() + timedelta(hours=s.get("warn_mute_hours", 12)))
            elif act == "kick":
                await c.ban_chat_member(chat_id, target.id,
                                        until_date=datetime.now() + timedelta(seconds=60))
                await c.unban_chat_member(chat_id, target.id)
            elif act == "ban":
                await c.ban_chat_member(chat_id, target.id)
        except Exception:
            pass
        await m.reply_text(
            f"🚫 <b>{human(target)}</b> به سقف {limit} اخطار رسید — {act} شد!")
        log_mod(c, chat_id, f"⚠️ سقف وارن: {human(target)} → {act}")
    else:
        await m.reply_text(
            f"⚠️ <b>{human(target)}</b> اخطار گرفتی ({count}/{limit})"
            + (f"\n reason: <i>{reason}</i>" if reason else ""))

# ══════════════════════════════════════════════
# هندلر پیام — همه چک‌های محتوایی
# ══════════════════════════════════════════════

LINK_RE = re.compile(r"(https?://|t\.me/|telegram\.me/|joinchat|@\w+)", re.I)
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002700-\U000027BF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "]+", re.U)


async def punish(c: Client, m: Message, action: str, dur_sec: int = 600, label: str = ""):
    try:
        if action == "delete":
            await safe_delete(m)
        elif action == "mute":
            await c.restrict_chat_member(
                m.chat.id, m.from_user.id, PERM_NONE,
                until_date=datetime.now() + timedelta(seconds=dur_sec))
            await safe_delete(m)
            await m.reply_text(f"🔇 {human(m.from_user)} {label} — سکوت {fmt_duration(dur_sec)}")
        elif action == "warn":
            await safe_delete(m)
            await add_warn(c, m, m.from_user, label)
        elif action == "ban":
            await c.ban_chat_member(m.chat.id, m.from_user.id)
            await safe_delete(m)
            await m.reply_text(f"🚫 {human(m.from_user)} {label} — بن شد!")
    except UserAdminInvalid:
        pass
    except Exception as e:
        logger.debug(f"punish err: {e}")


def _extract_text(m: Message) -> str:
    return (m.text or m.caption or "")


def _violations(s: dict, m: Message) -> tuple[str, str]:
    """برمی‌گرداند (نوع تخلف، توضیح) یا ("","")"""
    text = _extract_text(m)

    # پیام کانال/بوت‌پست
    if s.get("lock_channel") and getattr(m, "sender_chat", None):
        try:
            if m.sender_chat.id != m.chat.id:   # ادمین بی‌نام = خودِ چت، مجاز
                return ("channel", "پست کانال ممنوع")
        except Exception:
            pass

    if s.get("lock_links") and text:
        wl = s.get("whitelist_links", [])
        for match in LINK_RE.finditer(text):
            frag = match.group(0).lower()
            if not any(w.lower() in frag for w in wl):
                return ("links", "لینک ممنوع")

    if s.get("lock_fwd") and (m.forward_from or m.forward_from_chat or
                              getattr(m, "forward_sender_name", None)):
        if not getattr(m, "is_automatic_forward", False):
            return ("fwd", "فوروارد ممنوع")

    media_locks = {
        "lock_sticker": (m.sticker, "استیکر ممنوع"),
        "lock_gif": (m.animation, "گیف ممنوع"),
        "lock_voice": (m.voice, "ویس ممنوع"),
        "lock_video_note": (m.video_note, "ویدیو-نوت ممنوع"),
        "lock_video": (m.video, "ویدیو ممنوع"),
        "lock_photo": (m.photo, "عکس ممنوع"),
        "lock_document": (m.document, "فایل ممنوع"),
        "lock_contact": (m.contact, "مخاطب ممنوع"),
        "lock_poll": (m.poll, "نظرسنجی ممنوع"),
    }
    for key, (attr, label) in media_locks.items():
        if s.get(key) and attr:
            return (key.replace("lock_", ""), label)

    if text:
        # حروف بزرگ
        if s.get("lock_caps"):
            letters = [ch for ch in text if ch.isalpha()]
            if len(letters) >= s.get("caps_min_len", 12):
                caps = sum(1 for ch in letters if ch.isupper())
                if caps * 100 // len(letters) >= s.get("caps_percent", 70):
                    return ("caps", "حروف بزرگ زیاد")
        # ایموجی فلود
        if s.get("lock_emoji_flood"):
            n = len(EMOJI_RE.findall(text))
            if n >= s.get("emoji_max", 15):
                return ("emoji", f"{'≥'+str(s.get('emoji_max', 15))} ایموجی")
        # لیست سیاه
        bl = s.get("blacklist") or []
        low = text.lower()
        for word in bl:
            w = word.lower()
            if not w:
                continue
            if w.startswith("re:"):
                if re.search(w[3:], text, re.I):
                    return ("blacklist", "کلمه ممنوع")
            elif w in low:
                return ("blacklist", "کلمه ممنوع")

    # فلود
    if s.get("flood"):
        now = time.time()
        dq = _user_msg_log[m.chat.id][m.from_user.id]
        dq.append(now)
        win = s.get("flood_time", 6)
        while dq and now - dq[0] > win:
            dq.popleft()
        if len(dq) >= s.get("flood_limit", 6):
            dq.clear()
            return ("flood", f"فلود ({s.get('flood_limit', 6)} پیام/{win}s)")

    return ("", "")


def _action_for(s: dict, kind: str) -> tuple[str, int]:
    if kind == "flood":
        return (s.get("flood_action", "mute"), s.get("flood_mute_min", 10) * 60)
    if kind in ("links", "channel", "fwd", "caps", "emoji"):
        return ("delete", 0)
    if kind == "blacklist":
        return (s.get("profanity_action", "warn"), 3600)
    return ("delete", 0)

# ══════════════════════════════════════════════
# پنل اینلاین
# ══════════════════════════════════════════════

TOGGLES = [
    ("enabled", "🛡️ مدیریت"),
    ("welcome", "👋 خوش‌آمد"),
    ("captcha", "🧩 کپچا"),
    ("cas_check", "🤖 CAS"),
    ("raid_protection", "🛡 ضدحمله"),
    ("lock_links", "🔗 لینک"),
    ("lock_fwd", "↪️ فوروارد"),
    ("lock_channel", "📡 پست‌کانال"),
    ("lock_sticker", "🎨 استیکر"),
    ("lock_gif", "🎞 گیف"),
    ("lock_voice", "🎙 ویس"),
    ("lock_video_note", "📹 ویدنوت"),
    ("lock_video", "🎬 ویدیو"),
    ("lock_photo", "🖼 عکس"),
    ("lock_document", "📁 فایل"),
    ("lock_contact", "📇 مخاطب"),
    ("lock_poll", "📊 نظرسنجی"),
    ("lock_caps", "🔠 کپس"),
    ("lock_emoji_flood", "🤯 ایموجی"),
    ("flood", "🌊 فلود"),
    ("lock_profanity", "🤬 فحش"),
]

NUMS = [
    ("warn_limit", "⚠️ سقف وارن", 1, 10),
    ("flood_limit", "🌊 فلود N پیام", 3, 20),
    ("flood_time", "⏱ در N ثانیه", 3, 30),
    ("caps_percent", "🔠 درصد کپس", 50, 100),
    ("emoji_max", "🤯 سقف ایموجی", 5, 50),
    ("raid_joins", "🛡 حساسیت ضدحمله", 3, 20),
]


def _toggle_val(s: dict, key: str) -> str:
    return "✅" if s.get(key) else "❌"


def build_panel(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    s = get_group_settings(chat_id)
    txt = (
        "🛡️ <b>پنل مدیریت گروه</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"وضعیت: {'🟢 فعال' if s.get('enabled') else '🔴 خاموش'}\n"
        f"⚠️ سقف وارن: {s.get('warn_limit',3)} → {s.get('warn_action','mute')}\n"
        f"🌊 فلود: {s.get('flood_limit',6)} پیام / {s.get('flood_time',6)}s → {s.get('flood_action','mute')}\n"
        f"🛡 ضدحمله: {s.get('raid_joins',6)} جوین / {s.get('raid_window',30)}s\n"
        f"📢 کانال لاگ: {'تنظیم شده' if s.get('log_chat') else 'خاموش'}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>روی هر گزینه بزن تا خاموش/روشن شود:</i>"
    )
    rows = []
    for i in range(0, len(TOGGLES), 3):
        chunk = TOGGLES[i:i + 3]
        rows.append([InlineKeyboardButton(
            f"{_toggle_val(s, k)} {label}", callback_data=f"gp_t_{k}")
            for k, label in chunk])
    nav = [
        [InlineKeyboardButton("📢 کانال لاگ", callback_data="gp_setlog"),
         InlineKeyboardButton("💬 متن خوش‌آمد", callback_data="gp_setwelcome")],
        [InlineKeyboardButton("⛔ کلمات ممنوع", callback_data="gp_setbl"),
         InlineKeyboardButton("♻️ ریست تنظیمات", callback_data="gp_reset")],
        [InlineKeyboardButton("🔄 بروزرسانی", callback_data="gp_refresh")],
    ]
    rows.extend(nav)
    return txt, InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════
# ثبت هندلرها
# ══════════════════════════════════════════════

def register_group_handlers(app: Client, admin_id: int):
    """همه هندلرهای مدیریت گروه — از bot.py یک بار صدا زده می‌شود"""

    # ─────────── عضویت / خروج ───────────
    @app.on_message(filters.new_chat_members & filters.group)
    async def _on_join(c: Client, m: Message):
        try:
            await handle_new_member(c, m)
        except Exception as e:
            logger.error(f"join err: {e}")

    @app.on_message(filters.left_chat_member & filters.group)
    async def _on_left(c: Client, m: Message):
        try:
            await handle_leave(c, m)
        except Exception:
            pass

    # ─────────── کپچا ───────────
    @app.on_callback_query(filters.regex(r"^gcap_\d+_\w+$"))
    async def _on_captcha(c: Client, q: CallbackQuery):
        parts = q.data.split("_")
        uid, token = int(parts[1]), "_".join(parts[2:])
        key = (q.message.chat.id, uid)
        pend = _pending_captcha.get(key)
        if not pend or pend["token"] != token:
            await q.answer("مهلت تمام شده یا اعتبار ندارد", show_alert=True)
            return
        if q.from_user.id != uid:
            await q.answer("این دکمه برای خودت نیست!", show_alert=True)
            return
        _pending_captcha.pop(key, None)
        try:
            await c.restrict_chat_member(q.message.chat.id, uid, PERM_ALL)
        except Exception:
            pass
        try:
            await q.message.edit_text(f"✅ <b>{q.from_user.mention()}</b> تأیید شد — خوش اومدی! 🎉")
        except Exception:
            pass
        log_mod(c, q.message.chat.id, f"🧩 کپچا حل شد: {q.from_user.mention()}")
        await q.answer("تأیید شد ✅")

    # ─────────── پیام‌ها ───────────
    @app.on_message(filters.group & ~filters.service, group=4)
    async def _on_msg(c: Client, m: Message):
        if not m.from_user:
            return
        chat_id = m.chat.id
        s = get_group_settings(chat_id)
        if not s.get("enabled"):
            return
        if await is_admin(c, chat_id, m.from_user.id):
            return
        if getattr(m, "sender_chat", None) and m.sender_chat.id == chat_id:
            return   # ادمین بی‌نام

        try:
            # در وضعیت حمله قفل‌شده: همه‌چیز (به‌جز ادمین) پاک
            if _raid_lock_until.get(chat_id, 0) > time.time():
                await safe_delete(m)
                return

            # کپچا معلق: هر پیامی پاک می‌شود
            if s.get("captcha") and (chat_id, m.from_user.id) in _pending_captcha:
                await safe_delete(m)
                return

            kind, label = _violations(s, m)
            if kind:
                act, dur = _action_for(s, kind)
                if s.get("lock_profanity") and kind == "blacklist":
                    act, dur = _action_for(s, kind)
                await punish(c, m, act, dur, label)
                log_mod(c, chat_id,
                        f"🧹 {kind}: {human(m.from_user)} → {act} | {label}")
        except Exception as e:
            logger.debug(f"msg mod err: {e}")

    # ─────────── گزارش به ادمین‌ها ───────────
    @app.on_message(filters.command(["report", "admin"], prefixes=["/", "!"]) & filters.group)
    async def _on_report(c: Client, m: Message):
        if not m.reply_to_message:
            await m.reply_text("روی پیام متخلف ریپلای کن: /report", delete_after=8)
            return
        admins = []
        try:
            async for a in c.get_chat_members(m.chat.id, filter=ChatMembersFilter.ADMINISTRATORS):
                if not a.user.is_bot:
                    admins.append(a.user.mention())
        except Exception:
            pass
        if admins:
            await m.reply_to_message.reply_text(
                "🚨 <b>گزارش به ادمین‌ها:</b>\n" + " ".join(admins[:15]),
                delete_after=120)
        await safe_delete(m)

    # ─────────── دستورات مودریشن ───────────
    async def _target(c: Client, m: Message):
        if m.reply_to_message and m.reply_to_message.from_user:
            return m.reply_to_message.from_user
        if len(m.command) > 1:
            arg = m.command[1]
            try:
                if arg.lstrip("-").isdigit():
                    return await c.get_users(int(arg))
                return await c.get_users(arg.replace("@", ""))
            except Exception:
                return None
        return None

    def _parse_time_arg(m: Message, idx: int = 2) -> int | None:
        if len(m.command) > idx:
            return parse_duration(m.command[idx])
        return None

    @app.on_message(filters.command("ban") & filters.group)
    async def _ban(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        u = await _target(c, m)
        if not u:
            await m.reply_text("ریپلای کن یا آی‌دی بده: <code>/ban @user 2d</code>", delete_after=10)
            return
        if await is_admin(c, m.chat.id, u.id):
            await m.reply_text("ادمین رو نمی‌تونم بن کنم!", delete_after=8)
            return
        dur = _parse_time_arg(m)
        try:
            until = datetime.now() + timedelta(seconds=dur) if dur else None
            await c.ban_chat_member(m.chat.id, u.id, until_date=until)
            t = f"⏱ {fmt_duration(dur)}" if dur else "دائمی"
            await m.reply_text(f"🚫 {human(u)} بن شد ({t})")
            log_mod(c, m.chat.id, f"🚫 بن {t}: {human(u)} توسط {m.from_user.mention()}")
        except Exception as e:
            await m.reply_text(f"خطا: <code>{str(e)[:80]}</code>", delete_after=10)

    @app.on_message(filters.command("unban") & filters.group)
    async def _unban(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        u = await _target(c, m)
        if not u:
            return
        try:
            await c.unban_chat_member(m.chat.id, u.id)
            await m.reply_text(f"✅ {human(u)} آنبن شد")
        except Exception as e:
            await m.reply_text(f"خطا: <code>{str(e)[:80]}</code>", delete_after=10)

    @app.on_message(filters.command("kick") & filters.group)
    async def _kick(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        u = await _target(c, m)
        if not u:
            return
        try:
            await c.ban_chat_member(m.chat.id, u.id,
                                    until_date=datetime.now() + timedelta(seconds=45))
            await c.unban_chat_member(m.chat.id, u.id)
            await m.reply_text(f"👢 {human(u)} کیک شد")
        except Exception as e:
            await m.reply_text(f"خطا: <code>{str(e)[:80]}</code>", delete_after=10)

    @app.on_message(filters.command("mute") & filters.group)
    async def _mute(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        u = await _target(c, m)
        if not u:
            await m.reply_text("ریپلای کن: <code>/mute @user 1h</code>", delete_after=10)
            return
        dur = _parse_time_arg(m) or 3600
        try:
            await c.restrict_chat_member(m.chat.id, u.id, PERM_NONE,
                                         until_date=datetime.now() + timedelta(seconds=dur))
            await m.reply_text(f"🔇 {human(u)} ساکت شد ({fmt_duration(dur)})")
            log_mod(c, m.chat.id, f"🔇 میوت {fmt_duration(dur)}: {human(u)}")
        except Exception as e:
            await m.reply_text(f"خطا: <code>{str(e)[:80]}</code>", delete_after=10)

    @app.on_message(filters.command("unmute") & filters.group)
    async def _unmute(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        u = await _target(c, m)
        if not u:
            return
        try:
            await c.restrict_chat_member(m.chat.id, u.id, PERM_ALL)
            await m.reply_text(f"🔊 {human(u)} آزاد شد")
        except Exception as e:
            await m.reply_text(f"خطا: <code>{str(e)[:80]}</code>", delete_after=10)

    @app.on_message(filters.command("warn") & filters.group)
    async def _warn(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        u = await _target(c, m)
        if not u:
            return
        reason = " ".join(m.command[2:]) if len(m.command) > 2 else ""
        await add_warn(c, m, u, reason)
        await safe_delete(m)

    @app.on_message(filters.command("warns") & filters.group)
    async def _warns(c: Client, m: Message):
        u = await _target(c, m) or m.from_user
        n = _db_warns(m.chat.id, u.id)
        s = get_group_settings(m.chat.id)
        await m.reply_text(f"⚠️ {human(u)} — اخطارها: <b>{n}/{s.get('warn_limit', 3)}</b>",
                           delete_after=30)

    @app.on_message(filters.command("resetwarn") & filters.group)
    async def _resetwarn(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        u = await _target(c, m)
        if not u:
            return
        _db_warn_reset(m.chat.id, u.id)
        await m.reply_text(f"♻️ اخطارهای {human(u)} ریست شد", delete_after=15)

    @app.on_message(filters.command("purge") & filters.group)
    async def _purge(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        if not m.reply_to_message:
            await m.reply_text("روی اولین پیامی که می‌خوای پاک بشه ریپلای کن", delete_after=10)
            return
        start = m.reply_to_message.id
        end = m.id
        ids = list(range(start, end + 1))[:200]
        try:
            await c.delete_messages(m.chat.id, ids)
            st = await m.reply_text(f"🧹 {len(ids)} پیام پاک شد", delete_after=6)
        except Exception as e:
            await m.reply_text(f"خطا: <code>{str(e)[:80]}</code>", delete_after=10)

    @app.on_message(filters.command("del") & filters.group)
    async def _del(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        if m.reply_to_message:
            await safe_delete(m.reply_to_message)
            await safe_delete(m)

    @app.on_message(filters.command("promote") & filters.group)
    async def _promote(c: Client, m: Message):
        if m.from_user.id != admin_id:
            return
        u = await _target(c, m)
        if not u:
            await m.reply_text("ریپلای کن یا آی‌دی بده", delete_after=10)
            return
        from pyrogram.types import ChatPrivileges
        try:
            await c.promote_chat_member(
                m.chat.id, u.id,
                privileges=ChatPrivileges(
                    can_delete_messages=True, can_restrict_members=True,
                    can_invite_users=True, can_pin_messages=True))
            await m.reply_text(f"⭐ {human(u)} ادمین شد")
        except Exception as e:
            await m.reply_text(f"خطا: <code>{str(e)[:80]}</code>", delete_after=10)

    @app.on_message(filters.command("demote") & filters.group)
    async def _demote(c: Client, m: Message):
        if m.from_user.id != admin_id:
            return
        u = await _target(c, m)
        if not u:
            return
        from pyrogram.types import ChatPrivileges
        try:
            await c.promote_chat_member(
                m.chat.id, u.id,
                privileges=ChatPrivileges())
            await m.reply_text(f"📉 {human(u)} از ادمینی درآمد")
        except Exception as e:
            await m.reply_text(f"خطا: <code>{str(e)[:80]}</code>", delete_after=10)

    # ─────────── قفل متنی (سازگار با قبل) ───────────
    LOCK_ALIAS = {
        "link": "lock_links", "links": "lock_links", "لینک": "lock_links",
        "fwd": "lock_fwd", "فوروارد": "lock_fwd",
        "sticker": "lock_sticker", "استیکر": "lock_sticker",
        "gif": "lock_gif", "گیف": "lock_gif",
        "voice": "lock_voice", "ویس": "lock_voice",
        "video": "lock_video", "ویدیو": "lock_video",
        "photo": "lock_photo", "عکس": "lock_photo",
        "document": "lock_document", "فایل": "lock_document",
        "contact": "lock_contact", "مخاطب": "lock_contact",
        "poll": "lock_poll", "نظرسنجی": "lock_poll",
        "caps": "lock_caps", "کپس": "lock_caps",
        "channel": "lock_channel", "کانال": "lock_channel",
    }

    @app.on_message(filters.command("lock") & filters.group)
    async def _lock(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        if len(m.command) < 2:
            await m.reply_text("چی رو قفل کنم؟\n" + "، ".join(LOCK_ALIAS.keys()), delete_after=15)
            return
        key = LOCK_ALIAS.get(m.command[1].lower())
        if not key:
            await m.reply_text("نوعش رو نمی‌شناسم", delete_after=10)
            return
        set_setting(m.chat.id, key, True)
        await m.reply_text(f"🔒 قفل شد: {m.command[1]}", delete_after=10)

    @app.on_message(filters.command("unlock") & filters.group)
    async def _unlock(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        if len(m.command) < 2:
            return
        key = LOCK_ALIAS.get(m.command[1].lower())
        if not key:
            return
        set_setting(m.chat.id, key, False)
        await m.reply_text(f"🔓 باز شد: {m.command[1]}", delete_after=10)

    @app.on_message(filters.command("lockdown") & filters.group)
    async def _lockdown(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        try:
            await c.set_chat_permissions(m.chat.id, PERM_NONE)
            await m.reply_text("🔒 گروه کامل قفل شد — با /open باز کن",
                               reply_markup=InlineKeyboardMarkup([[
                                   InlineKeyboardButton("🔓 بازکردن", callback_data="gpro_unlock")
                               ]]))
        except Exception as e:
            await m.reply_text(f"خطا (بات ادمین با دسترسی محدودسازی هست؟): <code>{str(e)[:80]}</code>")

    @app.on_callback_query(filters.regex("^gpro_unlock$"))
    async def _cb_unlock(c: Client, q: CallbackQuery):
        if not await is_admin(c, q.message.chat.id, q.from_user.id):
            await q.answer("فقط ادمین!", show_alert=True)
            return
        try:
            await c.set_chat_permissions(q.message.chat.id, PERM_ALL)
            _raid_lock_until.pop(q.message.chat.id, None)
            await q.answer("🔓 گروه باز شد")
            try:
                await q.message.edit_text("🔓 گروه باز شد")
            except Exception:
                pass
        except Exception as e:
            await q.answer(f"خطا: {str(e)[:60]}", show_alert=True)

    # ─────────── تنظیمات (پنل اینلاین) ───────────
    @app.on_message(filters.command("settings") & filters.group)
    async def _settings(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("فقط ادمین‌ها!", delete_after=6)
            return
        txt, kb = build_panel(m.chat.id)
        await m.reply_text(txt, reply_markup=kb)

    @app.on_callback_query(filters.regex("^gp_t_"))
    async def _cb_toggle(c: Client, q: CallbackQuery):
        chat_id = q.message.chat.id
        if not await is_admin(c, chat_id, q.from_user.id):
            await q.answer("فقط ادمین!", show_alert=True)
            return
        key = q.data[5:]
        s = get_group_settings(chat_id)
        if key not in s:
            await q.answer("نامشخص", show_alert=True)
            return
        set_setting(chat_id, key, not s.get(key))
        await q.answer("روشن شد ✅" if s.get(key) else "خاموش شد ❌")
        txt, kb = build_panel(chat_id)
        try:
            await q.message.edit_text(txt, reply_markup=kb)
        except Exception:
            pass

    @app.on_callback_query(filters.regex("^gp_refresh$"))
    async def _cb_refresh(c: Client, q: CallbackQuery):
        txt, kb = build_panel(q.message.chat.id)
        try:
            await q.message.edit_text(txt, reply_markup=kb)
        except Exception:
            pass
        await q.answer()

    @app.on_callback_query(filters.regex("^gp_reset$"))
    async def _cb_reset(c: Client, q: CallbackQuery):
        chat_id = q.message.chat.id
        if not await is_admin(c, chat_id, q.from_user.id):
            await q.answer("فقط ادمین!", show_alert=True)
            return
        keep_log = get_group_settings(chat_id).get("log_chat", 0)
        _settings_cache[chat_id] = dict(DEFAULTS)
        _settings_cache[chat_id]["log_chat"] = keep_log
        save_group_settings(chat_id)
        txt, kb = build_panel(chat_id)
        await q.answer("♻️ ریست شد")
        try:
            await q.message.edit_text(txt, reply_markup=kb)
        except Exception:
            pass

    @app.on_callback_query(filters.regex("^gp_setlog$"))
    async def _cb_setlog(c: Client, q: CallbackQuery):
        chat_id = q.message.chat.id
        if not await is_admin(c, chat_id, q.from_user.id):
            await q.answer("فقط ادمین!", show_alert=True)
            return
        _pending_captcha[(chat_id, "setlog")] = {"token": "log", "at": time.time(), "by": q.from_user.id}
        await q.message.edit_text(
            "📢 <b>کانال لاگ</b>\nآی‌دی عددی کانال/گروهی که می‌خوای گزارش‌ها اونجا بره رو بفرست.\n"
            "(بات باید توش ادمین باشه) — لغو: /cancellog")
        set_setting(chat_id, "_awaiting", "log")

    @app.on_callback_query(filters.regex("^gp_setwelcome$"))
    async def _cb_setwelcome(c: Client, q: CallbackQuery):
        chat_id = q.message.chat.id
        if not await is_admin(c, chat_id, q.from_user.id):
            await q.answer("فقط ادمین!", show_alert=True)
            return
        await q.message.edit_text(
            "💬 <b>متن خوش‌آمد</b>\nمتن جدید رو بفرست. از {name} هم می‌تونی استفاده کنی.\n"
            "لغو: /cancelwelcome")
        set_setting(chat_id, "_awaiting", "welcome")

    @app.on_callback_query(filters.regex("^gp_setbl$"))
    async def _cb_setbl(c: Client, q: CallbackQuery):
        chat_id = q.message.chat.id
        if not await is_admin(c, chat_id, q.from_user.id):
            await q.answer("فقط ادمین!", show_alert=True)
            return
        bl = get_group_settings(chat_id).get("blacklist", [])
        await q.message.edit_text(
            "⛔ <b>کلمات ممنوع</b>\n"
            "هر خط یک کلمه. پشتیبانی regex با پیشوند <code>re:</code>\n"
            "مثال:\n<code>کلمه_بد\nre:(س|x)+ک</code>\n\n"
            "حالا لیست جدید رو بفرست (خالی = پاک شدن همه):\nلغو: /cancelbl")
        set_setting(chat_id, "_awaiting", "bl")

    @app.on_message(filters.command(["cancellog", "cancelwelcome", "cancelbl"]) & filters.group)
    async def _cancel_await(c: Client, m: Message):
        set_setting(m.chat.id, "_awaiting", "")
        await m.reply_text("لغو شد", delete_after=6)

    # دریافت ورودی‌های متنی پنل
    @app.on_message(filters.group & filters.text & ~filters.command(
        ["settings", "ban", "unban", "kick", "mute", "unmute", "warn", "warns",
         "resetwarn", "purge", "del", "promote", "demote", "lock", "unlock",
         "lockdown", "report", "admin", "help", "cancellog", "cancelwelcome", "cancelbl"], prefixes="/!"), group=5)
    async def _on_panel_input(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            return
        s = get_group_settings(m.chat.id)
        awaiting = s.get("_awaiting", "")
        if not awaiting:
            return
        if m.text.startswith("/"):
            return
        if awaiting == "log":
            arg = m.text.strip()
            if arg.lower() in ("off", "خاموش", "none"):
                set_setting(m.chat.id, "log_chat", 0)
                set_setting(m.chat.id, "_awaiting", "")
                await m.reply_text("📢 کانال لاگ خاموش شد", delete_after=10)
                return
            try:
                cid = int(arg)
                await c.send_message(cid, "✅ این کانال به‌عنوان لاگ مودریشن تنظیم شد")
                set_setting(m.chat.id, "log_chat", cid)
                set_setting(m.chat.id, "_awaiting", "")
                await m.reply_text("✅ ثبت شد — از این به بعد گزارش‌ها اونجا می‌ره", delete_after=10)
            except Exception as e:
                await m.reply_text(f"نتونستم اونجا پیام بدم: <code>{str(e)[:80]}</code>\n"
                                   "بات باید ادمین کانال باشه", delete_after=12)
        elif awaiting == "welcome":
            set_setting(m.chat.id, "welcome_text", m.text[:800])
            set_setting(m.chat.id, "_awaiting", "")
            await m.reply_text("✅ متن خوش‌آمد ذخیره شد (از {name} پشتیبانی می‌شود)", delete_after=10)
        elif awaiting == "bl":
            words = [w.strip() for w in m.text.splitlines() if w.strip()]
            set_setting(m.chat.id, "blacklist", words[:100])
            set_setting(m.chat.id, "_awaiting", "")
            await m.reply_text(f"⛔ {len(words)} الگو در لیست سیاه ثبت شد", delete_after=10)

    # ─────────── راهنما ───────────
    @app.on_message(filters.command("help") & filters.group)
    async def _help(c: Client, m: Message):
        txt = (
            "🛡️ <b>راهنمای مدیریت گروه</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<b>مودریشن:</b>\n"
            "/ban @user [2h|3d] — بن (زمان‌دار هم می‌شه)\n"
            "/unban · /kick · /mute [1h] · /unmute\n"
            "/warn [دلیل] · /warns · /resetwarn\n"
            "/purge (ریپلای) · /del (ریپلای)\n"
            "/report (ریپلای) — گزارش به ادمین‌ها\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<b>قفل‌ها:</b>\n"
            "/lock لینک | فوروارد | استیکر | گیف | ویس | عکس | ویدیو | فایل | مخاطب | نظرسنجی | کپس | کانال\n"
            "/unlock همان‌طور · /lockdown · /settings (پنل)\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<b>حفاظت:</b>\n"
            "🧩 کپچا عضویت · 🤖 CAS بانک اسپمر · 🛡 ضدحمله جوین انبوه\n"
            "همه از /settings قابل تنظیم‌اند (فقط ادمین)"
        )
        await m.reply_text(txt, disable_web_page_preview=True)

    logger.info("Group Manager PRO registered — CAS, CAPTCHA, Raid, Panel, Temp-bans, Purge")
