"""
🤖 ماژول مدیریت گروه
قابلیت‌ها:
- خوشامدگویی
- مدیریت اعضا (/ban /mute /warn /kick)
- ضد اسپم و تبلیغ
- ضد فلود
"""

import time
import re
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import ChatPermissions, Message
from pyrogram.errors import UserAdminInvalid, ChatAdminRequired

# ══════════════════════════════════════════════
# تنظیمات
# ══════════════════════════════════════════════

# پیام خوشامد
WELCOME_MESSAGES = [
    "سلام {mention} عزیز! 👋\nبه {chat_title} خوش اومدی! 🎉",
    "خوش اومدی {mention}! 🌟\nامیدواریم لحظات خوبی داشته باشی در {chat_title}",
    "🎊 {mention} به جمع ما پیوست!\nخوش اومدی به {chat_title}!",
]

# پیام خداحافظی
GOODBYE_MESSAGES = [
    "😢 {mention} گروه رو ترک کرد...",
    "👋 {mention} خداحافظ!",
]

# کلمات ممنوعه (فارسی)
FORBIDDEN_WORDS = [
    # فحش‌های فارسی
    "کیری", "کسکش", "کس ننه", "مادرت", "کون", "کونن", "کونی",
    "کسخل", "گایدی", "گاید", "عن", "گوه", "خره", "اسبی",
    # فحش‌های انگلیسی
    "fuck", "shit", "bitch", "ass", "dick", "pussy", "cock",
    "whore", "slut", "bastard", "cunt", "nigger", "faggot",
]

# الگوهای لینک
LINK_PATTERNS = [
    r'https?://\S+',
    r'telegram\.me/\S+',
    r't\.me/\S+',
    r'@\w{5,}',  # یوزرنیم‌ها
]

# ══════════════════════════════════════════════
# State Management
# ══════════════════════════════════════════════

# تنظیمات هر گروه
group_settings = {}

# سیستم هشدار
user_warnings = defaultdict(lambda: defaultdict(int))  # {chat_id: {user_id: count}}

# سیستم ضد فلود
user_messages = defaultdict(lambda: defaultdict(list))  # {chat_id: {user_id: [timestamps]}}


def get_group_settings(chat_id):
    """دریافت تنظیمات گروه"""
    if chat_id not in group_settings:
        group_settings[chat_id] = {
            "welcome_enabled": True,
            "goodbye_enabled": True,
            "anti_link": True,
            "anti_fwd": True,
            "anti_flood": True,
            "anti_profanity": True,
            "warn_limit": 3,
            "flood_limit": 5,  # پیام در 5 ثانیه
            "flood_time": 5,
        }
    return group_settings[chat_id]


# ══════════════════════════════════════════════
# Handler Registration
# ══════════════════════════════════════════════

def register_group_handlers(app: Client, admin_id: int):
    """ثبت هندلرهای مدیریت گروه"""
    
    # ═══════════════ 👋 خوشامدگویی ═══════════════
    @app.on_message(filters.new_chat_members & filters.group)
    async def on_new_member(c: Client, m: Message):
        settings = get_group_settings(m.chat.id)
        if not settings.get("welcome_enabled"):
            return
        
        # چک کن ربات ادمین هست
        try:
            bot_member = await c.get_chat_member(m.chat.id, "me")
            if bot_member.status not in ["administrator", "creator"]:
                return
        except:
            return
        
        for user in m.new_chat_members:
            if user.is_bot or user.is_self:
                continue
            
            mention = user.mention(user.first_name or "کاربر")
            chat_title = m.chat.title
            import random
            msg = random.choice(WELCOME_MESSAGES).format(
                mention=mention, 
                chat_title=chat_title
            )
            await m.reply_text(msg)
    
    # ═══════════════ 👋 خداحافظی ═══════════════
    @app.on_message(filters.left_chat_member & filters.group)
    async def on_left_member(c: Client, m: Message):
        settings = get_group_settings(m.chat.id)
        if not settings.get("goodbye_enabled"):
            return
        
        user = m.left_chat_member
        if user.is_bot or user.is_self:
            return
        
        mention = user.mention(user.first_name or "کاربر")
        import random
        msg = random.choice(GOODBYE_MESSAGES).format(mention=mention)
        await m.reply_text(msg)
    
    # ═══════════════ 🛡️ دستورات مدیریتی ═══════════════
    
    async def is_admin(c: Client, chat_id: int, user_id: int) -> bool:
        """چک کردن ادمین بودن"""
        try:
            member = await c.get_chat_member(chat_id, user_id)
            return member.status in ["administrator", "creator"]
        except:
            return False
    
    async def get_target_user(c: Client, m: Message):
        """گرفتن یوزر هدف از ریپلای یا منشن"""
        user = None
        if m.reply_to_message:
            user = m.reply_to_message.from_user
        elif m.entities:
            for entity in m.entities:
                if entity.type == "mention":
                    username = m.text[entity.offset:entity.offset+entity.length].lstrip("@")
                    try:
                        user = await c.get_users(username)
                    except:
                        pass
                    break
                elif entity.type == "text_mention":
                    user = entity.user
                    break
        return user
    
    # /ban
    @app.on_message(filters.command("ban") & filters.group)
    async def cmd_ban(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها میتونن بن کنن!")
            return
        
        user = await get_target_user(c, m)
        if not user:
            await m.reply_text("❌ کاربر رو مشخص کن! (ریپلای یا @username)")
            return
        
        try:
            await c.ban_chat_member(m.chat.id, user.id)
            await m.reply_text(f"🚫 {user.mention()} بن شد!")
        except Exception as e:
            await m.reply_text(f"❌ خطا: {e}")
    
    # /unban
    @app.on_message(filters.command("unban") & filters.group)
    async def cmd_unban(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        user = await get_target_user(c, m)
        if not user:
            await m.reply_text("❌ کاربر رو مشخص کن!")
            return
        
        try:
            await c.unban_chat_member(m.chat.id, user.id)
            await m.reply_text(f"✅ {user.mention()} آنبن شد!")
        except Exception as e:
            await m.reply_text(f"❌ خطا: {e}")
    
    # /kick
    @app.on_message(filters.command("kick") & filters.group)
    async def cmd_kick(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        user = await get_target_user(c, m)
        if not user:
            await m.reply_text("❌ کاربر رو مشخص کن!")
            return
        
        try:
            await c.ban_chat_member(m.chat.id, user.id)
            await asyncio.sleep(1)
            await c.unban_chat_member(m.chat.id, user.id)
            await m.reply_text(f"👢 {user.mention()} اخراج شد!")
        except Exception as e:
            await m.reply_text(f"❌ خطا: {e}")
    
    # /mute
    @app.on_message(filters.command("mute") & filters.group)
    async def cmd_mute(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        user = await get_target_user(c, m)
        if not user:
            await m.reply_text("❌ کاربر رو مشخص کن!")
            return
        
        try:
            perms = ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_invite_users=False,
                can_pin_messages=False,
                can_change_info=False,
            )
            await c.restrict_chat_member(m.chat.id, user.id, perms)
            await m.reply_text(f"🔇 {user.mention()} میوت شد!")
        except Exception as e:
            await m.reply_text(f"❌ خطا: {e}")
    
    # /unmute
    @app.on_message(filters.command("unmute") & filters.group)
    async def cmd_unmute(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        user = await get_target_user(c, m)
        if not user:
            await m.reply_text("❌ کاربر رو مشخص کن!")
            return
        
        try:
            perms = ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_change_info=True,
            )
            await c.restrict_chat_member(m.chat.id, user.id, perms)
            await m.reply_text(f"🔊 {user.mention()} آنمیوت شد!")
        except Exception as e:
            await m.reply_text(f"❌ خطا: {e}")
    
    # /warn
    @app.on_message(filters.command("warn") & filters.group)
    async def cmd_warn(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        user = await get_target_user(c, m)
        if not user:
            await m.reply_text("❌ کاربر رو مشخص کن!")
            return
        
        settings = get_group_settings(m.chat.id)
        warn_limit = settings.get("warn_limit", 3)
        
        user_warnings[m.chat.id][user.id] += 1
        warns = user_warnings[m.chat.id][user.id]
        
        if warns >= warn_limit:
            try:
                await c.ban_chat_member(m.chat.id, user.id)
                await m.reply_text(f"🚫 {user.mention()} به حداکثر هشدار رسید و بن شد!\n({warns}/{warn_limit})")
                user_warnings[m.chat.id][user.id] = 0
            except Exception as e:
                await m.reply_text(f"❌ خطا: {e}")
        else:
            await m.reply_text(f"⚠️ {user.mention()} هشدار {warns}/{warn_limit}\nاگه ادامه بدی بن میشی!")
    
    # /resetwarn
    @app.on_message(filters.command("resetwarn") & filters.group)
    async def cmd_resetwarn(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        user = await get_target_user(c, m)
        if not user:
            await m.reply_text("❌ کاربر رو مشخص کن!")
            return
        
        user_warnings[m.chat.id][user.id] = 0
        await m.reply_text(f"✅ هشدارهای {user.mention()} ریست شد!")
    
    # /pin
    @app.on_message(filters.command("pin") & filters.group)
    async def cmd_pin(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        if not m.reply_to_message:
            await m.reply_text("❌ روی پیام ریپلای کن!")
            return
        
        try:
            await m.reply_to_message.pin(disable_notification=False)
            await m.reply_text("📌 پیام پین شد!")
        except Exception as e:
            await m.reply_text(f"❌ خطا: {e}")
    
    # /unpin
    @app.on_message(filters.command("unpin") & filters.group)
    async def cmd_unpin(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        try:
            await c.unpin_chat_message(m.chat.id)
            await m.reply_text("📌 پیام آنپین شد!")
        except Exception as e:
            await m.reply_text(f"❌ خطا: {e}")
    
    # ═══════════════ 🚫 ضد اسپم ═══════════════
    
    @app.on_message(filters.group & ~filters.service)
    async def anti_spam_handler(c: Client, m: Message):
        """هندلر اصلی ضد اسپم"""
        if not m.from_user:
            return
        
        # ادمین‌ها معاف
        if await is_admin(c, m.chat.id, m.from_user.id):
            return
        
        settings = get_group_settings(m.chat.id)
        text = m.text or m.caption or ""
        deleted = False
        
        # ضد لینک
        if settings.get("anti_link"):
            for pattern in LINK_PATTERNS:
                if re.search(pattern, text, re.IGNORECASE):
                    try:
                        await m.delete()
                        await m.reply_text(f"🚫 {m.from_user.mention()} لینک ممنوعه!", delete_after=5)
                    except: pass
                    deleted = True
                    break
        
        if deleted:
            return
        
        # ضد فوروارد
        if settings.get("anti_fwd") and m.forward_from_chat:
            try:
                await m.delete()
                await m.reply_text(f"🚫 {m.from_user.mention()} فوروارد ممنوعه!", delete_after=5)
            except: pass
            return
        
        # ضد فحش
        if settings.get("anti_profanity"):
            text_lower = text.lower()
            for word in FORBIDDEN_WORDS:
                if word in text_lower:
                    try:
                        await m.delete()
                        # هشدار خودکار
                        user_warnings[m.chat.id][m.from_user.id] += 1
                        warns = user_warnings[m.chat.id][m.from_user.id]
                        warn_limit = settings.get("warn_limit", 3)
                        if warns >= warn_limit:
                            try:
                                await c.ban_chat_member(m.chat.id, m.from_user.id)
                                await m.reply_text(f"🚫 {m.from_user.mention()} بخاطر استفاده از کلمات نامناسب بن شد!")
                                user_warnings[m.chat.id][m.from_user.id] = 0
                            except: pass
                        else:
                            await m.reply_text(f"⚠️ {m.from_user.mention()} کلمات نامناسب ممنوع! ({warns}/{warn_limit})", delete_after=5)
                    except: pass
                    return
        
        # ضد فلود
        if settings.get("anti_flood"):
            chat_id = m.chat.id
            user_id = m.from_user.id
            flood_time = settings.get("flood_time", 5)
            flood_limit = settings.get("flood_limit", 5)
            
            now = time.time()
            user_messages[chat_id][user_id].append(now)
            
            # پاک کردن پیام‌های قدیمی
            user_messages[chat_id][user_id] = [
                t for t in user_messages[chat_id][user_id] 
                if now - t < flood_time
            ]
            
            if len(user_messages[chat_id][user_id]) > flood_limit:
                try:
                    # میوت موقت (5 دقیقه)
                    perms = ChatPermissions(
                        can_send_messages=False,
                        can_send_media_messages=False,
                        can_send_other_messages=False,
                        can_add_web_page_previews=False,
                    )
                    until_date = int(time.time()) + 300  # 5 دقیقه
                    await c.restrict_chat_member(chat_id, user_id, perms, until_date=until_date)
                    await m.reply_text(
                        f"🔇 {m.from_user.mention()} بخاطر فلود ۵ دقیقه میوت شد!",
                        delete_after=10
                    )
                except: pass
                user_messages[chat_id][user_id] = []
    
    # ═══════════════ ⚙️ تنظیمات ═══════════════
    
    # /settings
    @app.on_message(filters.command("settings") & filters.group)
    async def cmd_settings(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        settings = get_group_settings(m.chat.id)
        
        text = f"⚙️ <b>تنظیمات گروه</b>\n{'━'*20}\n"
        text += f"👋 خوشامدگویی: {'✅' if settings['welcome_enabled'] else '❌'}\n"
        text += f"👋 خداحافظی: {'✅' if settings['goodbye_enabled'] else '❌'}\n"
        text += f"🔗 ضد لینک: {'✅' if settings['anti_link'] else '❌'}\n"
        text += f"🔄 ضد فوروارد: {'✅' if settings['anti_fwd'] else '❌'}\n"
        text += f"🌊 ضد فلود: {'✅' if settings['anti_flood'] else '❌'}\n"
        text += f"🤬 ضد فحش: {'✅' if settings['anti_profanity'] else '❌'}\n"
        text += f"⚠️ حد هشدار: {settings['warn_limit']}\n"
        text += f"{'━'*20}\n\n"
        text += "<b>دستورات:</b>\n"
        text += "/ban - بن کاربر\n"
        text += "/unban - آنبن\n"
        text += "/kick - اخراج\n"
        text += "/mute - میوت\n"
        text += "/unmute - آنمیوت\n"
        text += "/warn - هشدار\n"
        text += "/resetwarn - ریست هشدار\n"
        text += "/pin - پین پیام\n"
        text += "/unpin - آنپین\n"
        
        await m.reply_text(text)
    
    # /toggle
    @app.on_message(filters.command("toggle") & filters.group)
    async def cmd_toggle(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        args = m.text.split()
        if len(args) < 2:
            await m.reply_text("❌ مثال: /toggle anti_link\nگزینه‌ها: welcome, goodbye, anti_link, anti_fwd, anti_flood, anti_profanity")
            return
        
        settings = get_group_settings(m.chat.id)
        key_map = {
            "welcome": "welcome_enabled",
            "goodbye": "goodbye_enabled",
            "anti_link": "anti_link",
            "link": "anti_link",
            "anti_fwd": "anti_fwd",
            "fwd": "anti_fwd",
            "forward": "anti_fwd",
            "anti_flood": "anti_flood",
            "flood": "anti_flood",
            "anti_profanity": "anti_profanity",
            "profanity": "anti_profanity",
            "swear": "anti_profanity",
        }
        
        key = args[1].lower()
        if key not in key_map:
            await m.reply_text(f"❌ گزینه نامعتبر! گزینه‌ها: {', '.join(key_map.keys())}")
            return
        
        settings_key = key_map[key]
        settings[settings_key] = not settings[settings_key]
        status = "✅ فعال" if settings[settings_key] else "❌ غیرفعال"
        await m.reply_text(f"✅ {key} → {status}")
    
    # /lock - سکوت کامل گروه
    @app.on_message(filters.command("lock") & filters.group)
    async def cmd_lock(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        try:
            # ریستریکت کردن همه اعضا (permissions گروه رو ببند)
            await c.set_chat_permissions(
                m.chat.id,
                ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                    can_invite_users=False,
                    can_pin_messages=False,
                    can_change_info=False,
                )
            )
            await m.reply_text("🔒 <b>گروه قفل شد!</b>\nهیچ‌کسی نمیتونه پیام بفرسته.\nبرای باز کردن: /unlock")
        except Exception as e:
            await m.reply_text(f"❌ خطا: {e}")
    
    # /unlock - باز کردن گروه
    @app.on_message(filters.command("unlock") & filters.group)
    async def cmd_unlock(c: Client, m: Message):
        if not await is_admin(c, m.chat.id, m.from_user.id):
            await m.reply_text("❌ فقط ادمین‌ها!")
            return
        
        try:
            await c.set_chat_permissions(
                m.chat.id,
                ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_invite_users=True,
                    can_pin_messages=True,
                    can_change_info=True,
                )
            )
            await m.reply_text("🔓 <b>گروه باز شد!</b>\nهمه میتونن پیام بفرستن.")
        except Exception as e:
            await m.reply_text(f"❌ خطا: {e}")
    
    # /help
    @app.on_message(filters.command("help") & filters.group)
    async def cmd_help(c: Client, m: Message):
        text = """🤖 <b>راهنمای ربات مدیریت گروه</b>
━━━━━━━━━━━━━━

<b>🛡️ دستورات مدیریتی:</b>
/ban — بن کاربر (ریپلای یا @username)
/unban — آنبن
/kick — اخراج
/mute — میوت (نمیتونه پیام بفرسته)
/unmute — آنمیوت
/warn — هشدار (۳ تا = بن)
/resetwarn — ریست هشدار
/pin — پین پیام (ریپلای)
/unpin — آنپین

<b>🔒 قفل گروه:</b>
/lock — قفل کامل (هیچ‌کس نتونه پیام بفرسته)
/unlock — باز کردن گروه

<b>⚙️ تنظیمات:</b>
/settings — نمایش تنظیمات
/toggle &lt;گزینه&gt; — روشن/خاموش کردن
  گزینه‌ها: welcome, goodbye, anti_link, anti_fwd, anti_flood, anti_profanity

<b>🚫 سیستم حفاظتی:</b>
✅ حذف خودکار لینک و تبلیغ
✅ حذف فوروارد از کانال‌ها
✅ حذف کلمات نامناسب
✅ میوت خودکار فلود (۵ پیام در ۵ ثانیه)
━━━━━━━━━━━━━━"""
        await m.reply_text(text)
    
    print("✅ Group manager handlers registered!", flush=True)
