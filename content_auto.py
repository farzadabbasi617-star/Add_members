# -*- coding: utf-8 -*-
"""
ماژول «تولید محتوا و انتشار خودکار» برای بات Add_members
=========================================================
۱. تولید متن فارسی گیمینگ با هوش مصنوعی (Groq)
۲. پیش‌نمایش + تولید دوباره
۳. انتشار دستی یا خودکار (زمان‌بندی‌شده) به کانال/گروه تلگرام

تنظیمات در دیتابیس (kv) ذخیره می‌شوند و بین ری‌استارت‌ها باثبات‌اند.
همهٔ تعاملات «ادمین» (ADMIN_ID) هستند.
"""

from __future__ import annotations

import asyncio
import os
import random
import time

import requests

import db as _db
from config import ADMIN_ID, BOT_TOKEN  # noqa: F401

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_KV_TARGET = "content_target_chat"
_KV_ENABLED = "content_autopost_enabled"
_KV_INTERVAL = "content_autopost_interval_hours"
_KV_LAST_POST = "content_last_post_ts"
_KV_BRAND = "content_brand"
_KV_STEP = "content_step"
_KV_TOPIC = "content_last_topic"
_KV_TEXT = "content_last_text"

DEFAULT_BRAND = "Gament"

_TOPIC_POOL = [
    "تورنومنت کلش رویال با جایزه نقدی",
    "فروش ویژه جم و CP کلش رویال",
    "مسابقهٔ آنلاین Call of Duty Mobile",
    "تورنومنت فورتنایت رایگان",
    "افتتاح تورنومنت و جوایز هیجان‌انگیز",
    "خرید ارزان V-Bucks و فورتنایت",
    "تیم‌سازی و رقابت گروهی در کلش رویال",
    "لدربرد هفتگی و ویژه‌ها",
]

_SYSTEM_PROMPT = (
    "تو یک نویسندهٔ حرفه‌ای محتوای فارسی برای یک جامعهٔ گیمینگ (پلتفرم تورنومنت و فروشگاه)"
    " هستی. لحن گرم، صمیمی و هیجان‌انگیز، مناسب کانال تلگرام. متن را کوتاه (۲ تا ۵ خط)،"
    " بدون ایموجیِ اضافه و بدون تگ هشتگ، فقط متن نهایی را برگردان. زبان خروجی: فارسی."
)


# ═══════════════════════════ تولید محتوا ═══════════════════════════
def _call_groq(topic: str, brand: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY تنظیم نشده است")
    user = f"موضوع: {topic}\nنام برند: {brand}\nلطفاً یک پست جذاب بنویس."
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "max_tokens": 300,
            "temperature": 0.9,
        },
        timeout=25,
    )
    resp.raise_for_status()
    return (resp.json()["choices"][0]["message"]["content"] or "").strip()


def _fallback_post(topic: str, brand: str) -> str:
    return (
        f"🏆 {topic} آغاز شد!\n\n"
        f"به جمع گیمرهای {brand} بپیوندید و شانس برنده‌شدن جایزه را داشته باشید.\n"
        f"برای ثبت‌نام و اطلاعات بیشتر روی دکمهٔ زیر بزنید."
    )


def generate_content(topic: str = "") -> dict:
    """تولید یک پست فارسی. اگر خطا بود قالب آفلاین برمی‌گرداند تا هیچ‌وقت کلاً از کار نیفتد."""
    brand = _db.kv_get(_KV_BRAND, DEFAULT_BRAND) or DEFAULT_BRAND
    topic = (topic or "").strip() or random.choice(_TOPIC_POOL)
    try:
        text = _call_groq(topic, brand)
        if not text:
            raise RuntimeError("خالی")
        source = "groq"
    except Exception as e:  # noqa: BLE001
        text = _fallback_post(topic, brand)
        source = f"fallback: {type(e).__name__}"
    return {"topic": topic, "text": text, "source": source, "brand": brand}


# ═══════════════════════════ انتشار ═══════════════════════════
def _target() -> str:
    return (_db.kv_get(_KV_TARGET, "") or "").strip()


async def send_to_target(client, text: str) -> tuple[bool, str]:
    target = _target()
    if not target:
        return False, "کانال/گروه مقصد تنظیم نشده. از منوی ⚙️ تنظیمات، کانال را وارد کن."
    try:
        await client.send_message(target, text, disable_web_page_preview=True)
        _db.kv_set(_KV_LAST_POST, time.time())
        return True, f"✅ منتشر شد به <b>{target}</b>"
    except Exception as e:  # noqa: BLE001
        return False, f"❌ خطا در انتشار: <code>{type(e).__name__}: {str(e)[:180]}</code>"


def _interval_hours() -> float:
    try:
        return float(_db.kv_get(_KV_INTERVAL, 6) or 6)
    except (TypeError, ValueError):
        return 6.0


# ═══════════════════════════ حلقهٔ انتشار خودکار ═══════════════════════════
_LAST_SENT = 0.0
_TASK = None


async def _autopost_loop(client):
    from pyrogram.errors import FloodWait  # noqa: BLE001
    global _LAST_SENT
    while True:
        try:
            enabled = bool(_db.kv_get(_KV_ENABLED, False))
            target = _target()
            interval = _interval_hours()
            if enabled and target and (time.time() - _LAST_SENT) >= (interval * 3600):
                content = generate_content()
                ok, msg = await send_to_target(client, content["text"])
                if ok:
                    _LAST_SENT = time.time()
                    print(f"[content_auto] autopost sent: {content['topic']}", flush=True)
                else:
                    print(f"[content_auto] autopost err: {msg}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[content_auto] loop err: {e}", flush=True)
        await asyncio.sleep(300)


def start_in_background(app_bot):
    """شروع حلقهٔ انتشار خودکار با الگوی bg_scraper (امن در سطح ماژول/زمان اجرا)."""
    global _TASK
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            _TASK = asyncio.create_task(_autopost_loop(app_bot))
            return _TASK
    except Exception:  # noqa: BLE001
        pass
    _TASK = asyncio.ensure_future(_autopost_loop(app_bot))
    return _TASK


# ═══════════════════════════ ثبت هندلرها ═══════════════════════════
def register(app, _CB):
    """هندلرها را روی app (pyrogram) و _CB (CallbackRouter) ثبت می‌کند.

    فراخوانی: بعد از ساخت app و _CB در bot.py.
    callback_data ها (پیشوند content_):
        content_menu, content_back, content_gen, content_gen_custom,
        content_regenerate, content_publish, content_settings,
        content_set_target, content_auto_toggle, content_interval_<n>,
        content_topic_<i>
    """
    from pyrogram import filters
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    def _b(txt, data):
        return InlineKeyboardButton(txt, callback_data=data)

    def _menu_rows():
        return [
            [_b("🎲 تولید محتوای تصادفی", "content_gen")],
            [_b("✍️ موضوع دلخواه", "content_gen_custom"), _b("🔁 تولید دوباره", "content_regenerate")],
            [_b("📤 انتشار به کانال", "content_publish")],
            [_b("⚙️ تنظیمات", "content_settings")],
            [_b("🏠 منوی اصلی", "home")],
        ]

    def _menu():
        return InlineKeyboardMarkup(_menu_rows())

    async def _status_text() -> str:
        return "\n".join([
            "🤖 <b>تولید محتوا و انتشار خودکار</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"🏷️ برند: <b>{_db.kv_get(_KV_BRAND, DEFAULT_BRAND)}</b>",
            f"📤 کانال مقصد: <b>{_target() or 'تنظیم نشده'}</b>",
            f"⏰ فاصلهٔ انتشار: <b>{_interval_hours():g} ساعت</b>",
            f"🔁 انتشار خودکار: <b>{'فعال ✅' if _db.kv_get(_KV_ENABLED, False) else 'غیرفعال ⛔'}</b>",
            "━━━━━━━━━━━━━━━━━━",
            "تولید محتوا با هوش مصنوعی (Groq) انجام می‌شود.",
        ])

    def _edit_kb():
        return InlineKeyboardMarkup([
            [_b("🔁 تولید دوباره", "content_regenerate"), _b("📤 انتشار", "content_publish")],
            [_b("🔙 بازگشت", "content_menu")],
        ])

    # ── دستور /content ──
    @app.on_message(filters.command("content") & filters.private & filters.user(ADMIN_ID))
    async def content_cmd(c, m):
        await m.reply_text(await _status_text(), reply_markup=_menu(), disable_web_page_preview=True)

    # ── کال‌بک‌ها ──
    @_CB.exact("content_menu")
    async def _menu_home(c, q):
        await q.message.edit_text(await _status_text(), reply_markup=_menu(), disable_web_page_preview=True)

    @_CB.exact("content_gen")
    async def _gen(c, q):
        content = generate_content()
        _db.kv_set(_KV_TOPIC, content["topic"])
        _db.kv_set(_KV_TEXT, content["text"])
        txt = (f"✍️ <b>موضوع:</b> {content['topic']}\n"
               f"━━━━━━━━━━━━━━━━━━\n{content['text']}\n")
        await q.message.edit_text(txt, reply_markup=_edit_kb(), disable_web_page_preview=True)

    @_CB.prefix("content_topic_")
    async def _gen_topic(c, q):
        idx = q.data.split("_")[-1]
        try:
            topic = _TOPIC_POOL[int(idx)]
        except (ValueError, IndexError):
            topic = _TOPIC_POOL[0]
        content = generate_content(topic)
        _db.kv_set(_KV_TOPIC, content["topic"])
        _db.kv_set(_KV_TEXT, content["text"])
        txt = (f"✍️ <b>موضوع:</b> {content['topic']}\n"
               f"━━━━━━━━━━━━━━━━━━\n{content['text']}\n")
        await q.message.edit_text(txt, reply_markup=_edit_kb(), disable_web_page_preview=True)

    @_CB.exact("content_gen_custom")
    async def _gen_custom(c, q):
        _db.kv_set(_KV_STEP, "topic")
        await q.answer("موضوع را بفرست", show_alert=False)
        await q.message.edit_text(
            "✍️ <b>موضوع دلخواه</b>\n\nلطفاً موضوع را به‌صورت پیام بفرست.",
            reply_markup=InlineKeyboardMarkup([[_b("🔙 بازگشت", "content_menu")]]),
            disable_web_page_preview=True,
        )

    @_CB.exact("content_regenerate")
    async def _regenerate(c, q):
        topic = _db.kv_get(_KV_TOPIC, "") or ""
        content = generate_content(topic)
        _db.kv_set(_KV_TOPIC, content["topic"])
        _db.kv_set(_KV_TEXT, content["text"])
        txt = (f"✍️ <b>موضوع:</b> {content['topic']}\n"
               f"━━━━━━━━━━━━━━━━━━\n{content['text']}\n")
        try:
            await q.message.edit_text(txt, reply_markup=_edit_kb(), disable_web_page_preview=True)
        except Exception:  # noqa: BLE001
            await q.message.reply_text(txt, reply_markup=_edit_kb(), disable_web_page_preview=True)

    @_CB.exact("content_publish")
    async def _publish(c, q):
        text = _db.kv_get(_KV_TEXT, "") or ""
        if not text:
            await q.answer("اول محتوا را تولید کن.", show_alert=True)
            return
        ok, msg = await send_to_target(c, text)
        await q.answer(msg, show_alert=not ok)

    @_CB.exact("content_settings")
    async def _settings(c, q):
        kb = InlineKeyboardMarkup([
            [_b("📤 تنظیم کانال مقصد", "content_set_target")],
            [_b("🔁 انتشار خودکار: خاموش/روشن", "content_auto_toggle")],
            [_b("فاصلهٔ ۲ ساعت", "content_interval_2"),
             _b("فاصلهٔ ۶ ساعت", "content_interval_6")],
            [_b("فاصلهٔ ۱۲", "content_interval_12"),
             _b("فاصلهٔ ۲۴", "content_interval_24")],
            [_b("🔙 بازگشت", "content_menu")],
        ])
        await q.message.edit_text(await _status_text(), reply_markup=kb, disable_web_page_preview=True)

    @_CB.exact("content_set_target")
    async def _set_target(c, q):
        _db.kv_set(_KV_STEP, "target")
        await q.answer()
        await q.message.edit_text(
            "📤 <b>کانال/گروه مقصد</b>\n\nآیدی عددی یا نام کاربری را بفرست:\n"
            "<code>@my_channel</code>  یا  <code>-1001234567890</code>\n\n"
            "⚠️ ربات باید ادمینِ آن باشد.",
            reply_markup=InlineKeyboardMarkup([[_b("🔙 بازگشت", "content_menu")]]),
            disable_web_page_preview=True,
        )

    @_CB.exact("content_auto_toggle")
    async def _auto_toggle(c, q):
        _db.kv_set(_KV_ENABLED, not bool(_db.kv_get(_KV_ENABLED, False)))
        await _settings(c, q)

    @_CB.prefix("content_interval_")
    async def _interval(c, q):
        v = q.data.split("_")[-1]
        try:
            _db.kv_set(_KV_INTERVAL, float(v))
        except ValueError:
            pass
        await _settings(c, q)

    # ── ورودی متنی ادمین (فقط وقتی در مرحلهٔ ماژول باشیم) ──
    @app.on_message(filters.private & filters.user(ADMIN_ID) & filters.text
                    & ~filters.command(["start", "content"]))
    async def content_step(c, m):
        state = _db.kv_get(_KV_STEP, "") or ""
        if not state:
            return  # به هندلرهای دیگر بات دست نزن
        if state == "target":
            _db.kv_set(_KV_TARGET, m.text.strip())
            _db.kv_set(_KV_STEP, "")
            await m.reply_text(
                f"✅ کانال/گروه مقصد تنظیم شد: <b>{_target()}</b>",
                reply_markup=_menu(), disable_web_page_preview=True,
            )
        elif state == "topic":
            _db.kv_set(_KV_STEP, "")
            content = generate_content(m.text)
            _db.kv_set(_KV_TOPIC, content["topic"])
            _db.kv_set(_KV_TEXT, content["text"])
            txt = (f"✍️ <b>موضوع:</b> {content['topic']}\n"
                   f"━━━━━━━━━━━━━━━━━━\n{content['text']}\n")
            await m.reply_text(txt, reply_markup=_edit_kb(), disable_web_page_preview=True)

    # شروع حلقهٔ انتشار خودکار در بلوک __main__ (نه این‌جا)
    # این‌جا فقط هندلرها ثبت می‌شوند؛ حلقه با start_in_background() اجرا می‌شود.
    return None
