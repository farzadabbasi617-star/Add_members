# -*- coding: utf-8 -*-
"""
بازبینی خبرهای سایت گیمنت در ربات Add_members (@NewAdd_members_bot)
=====================================================================
سایت گیمنت خبر را تولید می‌کند، آن را در دیتابیس خودش ذخیره می‌کند و پیامِ
بازبینی (با تصویر/متن و دکمه‌ها) را با همین ربات به ادمین می‌فرستد. این ماژول
دکمه‌های تأیید/اصلاح/رد و Reply ← متن اصلاح‌شده را مدیریت می‌کند و برای انجام
کار (انتشار در کانال / ذخیره متن اصلاح‌شده / رد) به سایت POST می‌کند.

چرا اینجا؟ چون فقط @NewAdd_members_bot ادمینِ کانال @Flexa_games است؛ پس
انتشار باید از طریق همین ربات انجام شود. سایتِ گیمنت از همین توکن برای فرستادن
پیام بازبینی و انتشار کانال استفاده می‌کند (GAMING_NEWS_REVIEW_BOT_TOKEN).

callback_data ها (پیشوند news:):
    news:approve:<honorId>   → انتشار در کانال
    news:edit:<honorId>      → از ادمین می‌خواهد Reply کند
    news:reject:<honorId>    → رد (کانال لمس نمی‌شود)
"""

from __future__ import annotations

import os
import re

import requests

from config import ADMIN_ID  # noqa: F401

REVIEW_URL = os.environ.get(
    "GAMING_NEWS_REVIEW_URL", "https://www.gament1.ir/api/honors/review"
).strip().rstrip("/")
REVIEW_SECRET = os.environ.get("GAMING_NEWS_REVIEW_SECRET", "").strip()

_HONOR_RE = re.compile(r"^news:(approve|edit|reject):([0-9a-f-]{36})$")


def _honor_from_message(msg):
    """honorId را از دکمه‌های پیامِ بازبینی درمی‌آورد (یا None)."""
    try:
        kb = getattr(msg, "reply_markup", None)
        if not kb:
            return None
        for row in kb.inline_keyboard:
            for btn in row:
                cd = getattr(btn, "callback_data", None) or ""
                m = _HONOR_RE.match(cd)
                if m:
                    return m.group(2)
    except Exception:  # noqa: BLE001
        return None
    return None


def _post(action, honor_id, text=None):
    try:
        r = requests.post(
            REVIEW_URL,
            json={"action": action, "honorId": honor_id, "text": text},
            headers={"Authorization": f"Bearer {REVIEW_SECRET}"},
            timeout=30,
        )
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


async def _finalize(q, text):
    """پیام بازبینی (عکس یا متن) را به حالت نهایی می‌برد و دکمه‌ها را می‌بندد."""
    try:
        await q.message.edit_text(text, reply_markup=None, disable_web_page_preview=True)
    except Exception:  # noqa: BLE001
        try:
            await q.message.edit_caption(text, reply_markup=None)
        except Exception:  # noqa: BLE001
            pass


def register(app, _CB):
    """هندلرها را روی app (pyrogram) و _CB (CallbackRouter) ثبت می‌کند."""
    from pyrogram import filters

    @_CB.prefix("news:approve:")
    async def _approve(c, q, d):  # noqa: ANN001
        m = _HONOR_RE.match(d or "")
        if not m:
            await q.answer("دکمه نامعتبر", show_alert=True)
            return
        honor_id = m.group(2)
        res = _post("approve", honor_id)
        if res.get("ok") and res.get("published"):
            await _finalize(q, "✅ <b>در کانال منتشر شد.</b>")
            await q.answer("✅ در کانال منتشر شد.")
        else:
            await q.answer("انتشار در کانال ناموفق بود. دوباره تلاش کن.", show_alert=True)

    @_CB.prefix("news:edit:")
    async def _edit(c, q, d):  # noqa: ANN001
        await q.answer(
            "همین پیام را Reply کن و متن اصلاح‌شده را بفرست، سپس «انتشار در کانال» را بزن.",
            show_alert=True,
        )

    @_CB.prefix("news:reject:")
    async def _reject(c, q, d):  # noqa: ANN001
        m = _HONOR_RE.match(d or "")
        if not m:
            await q.answer("دکمه نامعتبر", show_alert=True)
            return
        honor_id = m.group(2)
        _post("reject", honor_id)
        await _finalize(q, "🚫 <b>رد شد — در کانال منتشر نشد.</b>")
        await q.answer("خبر رد شد.")

    # Reply به پیامِ بازبینی = متن اصلاح‌شده
    @app.on_message(filters.private & filters.user(ADMIN_ID) & filters.text & filters.reply)
    async def _correct(c, m):  # noqa: ANN001
        honor_id = _honor_from_message(m.reply_to_message)
        if not honor_id:
            return  # به پیام‌های عادی ادمین دست نزن
        res = _post("correct", honor_id, m.text)
        if res.get("ok"):
            await m.reply_text(
                "✏️ <b>متن اصلاح‌شده ذخیره شد.</b>\n\n"
                "حالا دکمه «✅ انتشار در کانال» را بزن.",
                disable_web_page_preview=True,
            )
        else:
            await m.reply_text(
                "⚠️ ذخیره متن اصلاح‌شده ناموفق بود. دوباره تلاش کن.",
                disable_web_page_preview=True,
            )

    return None
