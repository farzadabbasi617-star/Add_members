# =================================================================
# 🚨 ماژول حمله پیشرفته نسخه MAX - برای تست حداکثری
# =================================================================
import asyncio
import time
import random
import io
import csv
import string
import os
import sqlite3
from pyrogram import Client
from pyrogram.errors import FloodWait, ChatAdminRequired, SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired, AuthKeyDuplicated, AuthKeyUnregistered
from pyrogram.raw import functions, types
from pyrogram.enums import MessageEntityType

import scrape_api
from scrape_optimize import MessageHarvester, MembershipOracle, AdaptiveThrottle

# انواع انتیتی که کاربر واقعی به آن‌ها چسبیده است.
# ⚠️ مقایسه‌ی رشته‌ای (`ent.type in ("mention","text_mention")`) همیشه
# False بود چون ent.type یک enum است نه رشته — منشن‌ها بی‌صدا از دست
# می‌رفتند.
_USER_ENTITY_TYPES = (MessageEntityType.MENTION, MessageEntityType.TEXT_MENTION)

# ===== قفل سراسری برای جلوگیری از database is locked =====
# یک قفل کلی برای اینکه دو Client همزمان connect/disconnect نکنن
_global_connect_lock = asyncio.Lock()

# قفل به ازای هر فایل سشن - برای اینکه دو عملیات همزمان روی یک فایل
# سشن کار نکنن (مثلاً اسکن خودکار + اسکن دستی همزمان با یک اکانت)
_session_locks: dict = {}

def _get_session_lock(session_path: str) -> asyncio.Lock:
    """Get or create an asyncio lock for a specific session file."""
    key = os.path.realpath(session_path) if session_path else session_path
    if key not in _session_locks:
        _session_locks[key] = asyncio.Lock()
    return _session_locks[key]


def _enable_wal_on_session(session_path: str):
    """Set WAL journal mode on a Pyrogram session SQLite file.
    WAL allows concurrent reads + one writer without locking issues."""
    if not session_path:
        return
    db_path = session_path + ".session"
    try:
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.close()
            print(f"✅ WAL mode enabled on {os.path.basename(db_path)}", flush=True)
    except Exception as e:
        print(f"⚠️ WAL setup on {db_path}: {e}", flush=True)

# فینگرپرینت دستگاه های مختلف برای دور زدن تشخیص
DEVICE_FP = [
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "Android 14", "app_version": "10.13.2", "lang_code": "fa"},
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 17.6.1", "app_version": "10.15", "lang_code": "fa"},
    {"device_model": "Xiaomi 14 Pro", "system_version": "HyperOS 1.0", "app_version": "10.12.4", "lang_code": "en"},
]

SESSIONS_DIR = "saved_sessions"
os.makedirs(SESSIONS_DIR, exist_ok=True)

def safe_phone_filename(phone):
    return ''.join(c for c in str(phone) if c.isdigit() or c == '+').strip('+')

def cleanup_temp_sessions(max_age_seconds=86400):
    """Clean up temporary login session files older than max_age_seconds."""
    try:
        if not os.path.exists(SESSIONS_DIR):
            return 0
        now = time.time()
        removed = 0
        for f in os.listdir(SESSIONS_DIR):
            if f.startswith("_newtmp_"):
                path = os.path.join(SESSIONS_DIR, f)
                try:
                    if now - os.path.getmtime(path) > max_age_seconds:
                        os.remove(path)
                        removed += 1
                except Exception:
                    pass
        if removed > 0:
            print(f"🧹 Cleaned up {removed} stale temporary session files", flush=True)
        return removed
    except Exception as e:
        print(f"⚠️ Temp session cleanup error: {e}", flush=True)
        return 0

class AdvancedScraper:
    def __init__(self, session_name, api_id, api_hash, phone=None, in_memory=False, device_fp=None, force_fresh=False):
        if device_fp:
            fp = device_fp
        else:
            fp = random.choice(DEVICE_FP)
        # force_fresh=True: استفاده از یک فایل سشن کاملا مجزا (نه در حافظه) تا بعد از لاگین
        # با rename کردن بتوانیم آن را به دائمی تبدیل کنیم. این روش ۱۰۰٪ پایدار است و از export_session_string
        # که ممکن است auth key را ابطال کند استفاده نمی کند.
        self._tmp_finalize = False
        self._perm_session_path = None
        if phone and force_fresh:
            import secrets
            tmp_fname = f"_newtmp_{safe_phone_filename(phone)}_{int(time.time())}_{secrets.token_hex(3)}"
            session_path = os.path.join(SESSIONS_DIR, tmp_fname)
            self._perm_session_path = os.path.join(SESSIONS_DIR, f"acc_{safe_phone_filename(phone)}")
            # هر بار که force_fresh میایم اگر سشن دائمی قدیمی وجود دارد برای لاگین جدید از نو شروع میکنیم
            # ولی قبلی رو پاک نمیکنیم مگر بعد از لاگین موفق
        elif phone and not in_memory:
            fname = safe_phone_filename(phone)
            session_path = os.path.join(SESSIONS_DIR, f"acc_{fname}")
        else:
            session_path = session_name
        self.phone = phone
        self.fp_used = fp
        self.app = Client(
            session_path,
            api_id=api_id,
            api_hash=api_hash,
            phone_number=phone,
            device_model=fp["device_model"],
            system_version=fp["system_version"],
            app_version=fp["app_version"],
            lang_code=fp["lang_code"],
            in_memory=False,
            sleep_threshold=30,
            workdir=".",
            no_updates=True,
            takeout=False
        )
        self.found_users = {}  # will be populated from DB in run_full_scrape
        self.total_api_calls = 0
        self.start_time = None
        self._progress_cb = None
        self._last_progress = 0
        self._stage = "در حال آماده سازی..."
        self._last_added_name = "-"
        self._last_progress_val = 0
        self._incremental_save_cb = None  # ذخیره تدریجی
        self._stop_requested = False  # درخواست توقف از کاربر
        self._existing_user_ids = set()  # کاربرایی که قبلاً استخراج شدن
        self._checked_members = set()  # نامزدهایی که عضویتشان بررسی شده
        self._oracle = None    # کش تأیید عضویت (در run_full_scrape ساخته می‌شود)
        self._throttle = AdaptiveThrottle()  # تأخیر تطبیقی بر پایه‌ی فلود واقعی
        self._harvester = None

    def request_stop(self):
        self._stop_requested = True

    def get_fp_dict(self):
        return self.fp_used

    async def persist_to_permanent(self):
        """سشن از فایل موقت را با rename به نام دائمی تبدیل میکند (۱۰۰٪ پایدار، auth key عوض نمیشود)"""
        if not self._perm_session_path:
            return
        sess_lock = _get_session_lock(self.app.name)
        perm_lock = _get_session_lock(self._perm_session_path)
        async with sess_lock:
            async with perm_lock:
                async with _global_connect_lock:
                    try:
                        await self.app.storage.close()
                        # Find actual temp session file paths (Pyrogram workdir is "." and app.name has full path)
                        tmp_base = self.app.name
                        perm_base = self._perm_session_path
                        # Rename all related session files (including .wal, .shm, .session)
                        import glob as _glob
                        for tmpf in _glob.glob(tmp_base + ".session*") + _glob.glob(tmp_base + ".session-*"):
                            suf = tmpf[len(tmp_base):]
                            permf = perm_base + suf
                            if os.path.exists(permf):
                                try: os.remove(permf)
                                except: pass
                            os.replace(tmpf, permf)
                        # فعال کردن WAL روی سشن دائمی
                        _enable_wal_on_session(perm_base)
                        # آپدیت لاک‌ها: لاک مسیر موقت رو حذف و به مسیر دائمی منتقل کن
                        if self.app.name in _session_locks:
                            old_lock = _session_locks.pop(self.app.name)
                            _session_locks[perm_base] = old_lock
                        print(f"💾 سشن به {perm_base} منتقل شد", flush=True)
                    except Exception as e:
                        print(f"⚠️ خطا در ذخیره دائمی سشن: {e}", flush=True)
                        import traceback; traceback.print_exc()

    async def connect(self):
        """اتصال امن با قفل سراسری + قفل per-session + WAL mode"""
        sess_name = self.app.name  # e.g. saved_sessions/acc_98912xxxxx
        sess_lock = _get_session_lock(sess_name)

        async with sess_lock:  # اول قفل مخصوص این سشن
            async with _global_connect_lock:  # بعد قفل سراسری
                # فعال کردن WAL mode قبل از اتصال
                _enable_wal_on_session(sess_name)

                try:
                    await self.app.connect()
                except (AuthKeyDuplicated, AuthKeyUnregistered):
                    raise

                # مجدداً WAL رو بعد از اتصال هم ست کن (Pyrogram ممکنه دیتابیس
                # رو موقع connect باز/بسته کنه و تنظیمات رو reset کنه)
                _enable_wal_on_session(sess_name)

                self.start_time = time.time()

    async def _progress(self, text=None, force=False):
        """گزارش پیشرفت زنده با نوار پراگرس بار متحرک و درصد تقریبی"""
        now = time.time()
        if text:
            self._stage = text
        if not force and now - self._last_progress < 2:  # آپدیت هر ۲ ثانیه
            return
        self._last_progress = now
        if self._progress_cb:
            try:
                elapsed = int(time.time() - self.start_time) if self.start_time else 0
                mins = elapsed // 60
                secs = elapsed % 60
                count = len(self.found_users)
                speed = int(count / (elapsed/60)) if elapsed > 10 else count*3
                # نوار پیشرفت متحرک (پر شدن به تدریج بر اساس تعداد پیدا شده)
                # تخمین پیشرفت از روی استیج
                stage_weights = {
                    "در حال اتصال": 2, "آماده سازی": 5,
                    "بارگذاری لیست چت": 10, "پیدا کردن گروه": 12,
                    "پیدا کردن کانال": 12, "گروه/کانال هدف": 12,
                    "بررسی عضویت": 15, "لیست مستقیم": 25,
                    "صفحه بندی جستجو": 40, "جستجو با حرف": 45,
                    "صفحه‌بندی یونیکد": 35, "تاریخچه پیام": 55,
                    "بررسی تاریخچه": 55, "اسکن عمیق": 55,
                    "اعضای جدید": 65, "اسکن ری اکشن": 50,
                    "اسکن کانال": 45, "پست های کانال": 45,
                    "اسکن فروارد": 42, "جستجوی سراسری": 30,
                    "Import Contacts": 20, "import contacts": 20,
                    "مخاطبین مشترک": 25, "اشتراک گروهی": 15,
                    "Batch resolve": 28, "خروج": 98, "تمام": 100,
                }
                pct = 20
                for key, val in stage_weights.items():
                    if key in self._stage:
                        pct = val
                        break
                # در طول صفحه بندی به تدریج درصد اضافه کن
                if "حرف" in self._stage and count > 0:
                    pct = min(65, 40 + count // 200)
                if "تاریخچه" in self._stage and count > 0:
                    pct = min(85, 65 + count // 150)
                pct = min(100, max(5, pct))
                filled = int(pct / 4)  # 25 خانه
                empty = 25 - filled
                bar = "🟩" * filled + "⬜" * empty
                dot = ["🟢","🟡","🟢","🔵","🟣","🟢"][int(elapsed/1.5) % 6]
                text_out = f"{dot} **وضعیت زنده عملیات**\n\n"
                text_out += f"{bar} **{pct}%**\n\n"
                text_out += f"🎯 مرحله: {self._stage}\n"
                text_out += f"✅ پیدا شده: **{count:,}** نفر\n"
                text_out += f"⏱️ زمان: {mins:02d}:{secs:02d}\n"
                text_out += f"⚡ سرعت: ~{speed} نفر در دقیقه\n"
                if self._last_added_name and self._last_added_name != "-":
                    text_out += f"👤 آخرین: {self._last_added_name[:25]}\n"
                if elapsed > 30:
                    text_out += f"\n⏳ در حال کار، صبر کنید..."
                # Return tuple of (text, reply_markup) if progress_cb supports stop button
                # We just return text; caller handles the stop button
                await self._progress_cb(text_out)
                if self._stop_requested:
                    self._stage = "توقف توسط کاربر..."
                    await self._progress_cb(text_out + "\n\n🛑 درخواست توقف داده شد...")
                    return
            except Exception:
                pass

    async def human_sleep(self, min_t=0.01, max_t=0.05):
        """Micro-sleep: just enough to avoid triggering Telegram flood control."""
        if self._stop_requested:
            return
        t = random.uniform(min_t, max_t)
        await asyncio.sleep(t)
        if time.time() - self._last_progress >= 5:
            await self._progress()

    async def handle_flood(self, e):
        # به تنظیم‌کننده‌ی تطبیقی خبر بده تا تأخیر پایه را بالا ببرد
        try:
            self._throttle.on_flood(e.value)
        except Exception:
            pass
        wait = e.value + random.randint(1,4)
        print(f"⏱️ فلود {wait}s", flush=True)
        self._stage = f"محدودیت سرعت تلگرام، {wait} ثانیه صبر..."
        await self._progress(force=True)
        await asyncio.sleep(wait)

    async def add_user(self, user, source):
        uid = getattr(user, 'id', None)
        if not uid or getattr(user, 'is_bot', False) or getattr(user, 'is_deleted', False):
            return
        if uid in self.found_users or uid in self._existing_user_ids:
            return
        fullname = user.first_name or ""
        if user.last_name:
            fullname += " " + user.last_name
        if not fullname:
            fullname = f"کاربر {user.id}"
        self._last_added_name = fullname
        # ذخیره شماره تلفن اگر قابل مشاهده باشد
        phone = getattr(user, 'phone_number', None) or ""
        self.found_users[user.id] = {
            "user_id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name or "",
            "username": user.username or "",
            "phone": phone,
            "is_premium": "بله" if user.is_premium else "خیر",
            "source": source
        }
        self._last_progress_val += 1
        # ذخیره تدریجی فقط هر ۱۰۰ نفر
        if self._incremental_save_cb and self._last_progress_val % 100 == 0:
            try:
                await self._incremental_save_cb(list(self.found_users.values()))
            except Exception:
                pass
        # Progress فقط هر ۵۰ نفر
        if self._last_progress_val % 50 == 0:
            await self._progress()

    async def scrape_direct_paginated(self, chat_id):
        """BARE METAL extraction — inline dict, zero add_user, zero sleep"""
        t0 = time.time()
        count = 0; last_prog = 0
        existing = self._existing_user_ids
        
        # Phase 1: Direct member list
        try:
            async for member in self.app.get_chat_members(chat_id, limit=50000):
                if self._stop_requested: break
                u = member.user
                uid = u.id
                if uid in self.found_users or uid in existing: continue
                if getattr(u, 'is_bot', False): continue
                self.found_users[uid] = {"user_id": uid, "first_name": u.first_name or "",
                    "last_name": u.last_name or "", "username": u.username or "",
                    "phone": getattr(u, 'phone_number', '') or '', "source": "direct"}
                # عضو قطعی — کش را پر کن تا لایه‌های بعد نپرسند
                if self._oracle is not None:
                    self._oracle.mark_member(uid)
                count += 1
                # Progress every 2s
                now = time.time()
                if now - last_prog > 2:
                    last_prog = now; self.total_api_calls += 1
                    spd = int(count / max(1, now - t0) * 60)
                    self._stage = f"📋 {count} عضو ({spd}/min)"
                    await self._progress()
            elapsed = int(time.time() - t0)
            self._stage = f"📋 {count} عضو در {elapsed}s"
            print(f"✅ لیست: {count} عضو در {elapsed}s", flush=True)
        except ChatAdminRequired:
            print("❌ لیست مخفی — skip", flush=True)
        except FloodWait as e:
            await self.handle_flood(e)
        except Exception as e:
            print(f"⚠️ لیست: {e}", flush=True)
        
        return len(self.found_users) > 0  # True if we got members
    async def scrape_full_history(self, chat_id, limit=10000):
        """🔍 روش ۲: فرستنده، فروارد، ریپلای و منشن از تاریخچه.

        ⚡ حالا به عبور واحد واگذار می‌شود. این چهار منبع دقیقاً همان
        چیزی هستند که `MessageHarvester` در یک عبور برمی‌دارد، پس
        نگه‌داشتن یک پیاده‌سازی دوم فقط یعنی خواندن دوباره‌ی تاریخچه.

        روش به‌عنوان نقطه‌ی ورود مستقل حفظ شده (اگر کسی فقط همین را
        بخواهد) ولی در استراتژی از مسیر یکپارچه می‌آید.
        """
        return await self.scrape_unified_history(chat_id, limit=limit)

    async def scrape_join_events(self, chat_id):
        """🚪 روش ۳: اعضای تازه‌وارد (پیام‌های join).

        ⚡ به عبور واحد واگذار شد. نسخه‌ی قبلی یک عبور کامل جدا روی
        ۱۰۰٬۰۰۰ پیام می‌زد و از هر پیام فقط `new_chat_members` را
        نگاه می‌کرد — کل تاریخچه دانلود می‌شد تا یک فیلد خوانده شود.
        """
        return await self.scrape_unified_history(chat_id, limit=100000)

    async def scrape_imported_contacts(self, chat_id, max_import=500):
        """🆕 روش ۶: importContacts برای کشف اعضای مخفی
        با import کردن شماره تلفن‌های تصادفی ساختگی، تلگرام افرادی رو که
        توی contact list ما هستن و عضو گروه هم هستن رو نشون میده.
        این روش حتی اعضایی که لیست مخفیه رو هم درمیاره."""
        print(f"\n🔍 روش ۶: Import Contacts برای کشف اعضای مخفی...", flush=True)
        self._stage = "در حال import contacts برای کشف اعضا"
        await self._progress(force=True)
        
        # Build phone batches from existing found users
        from pyrogram.raw import functions as raw_fns, types as raw_types
        
        discovered = 0
        # Use existing contacts from Telegram
        try:
            contacts_result = await self.app.invoke(raw_fns.contacts.GetContacts(hash=0))
            existing_contacts = set()
            if hasattr(contacts_result, 'contacts'):
                for c in contacts_result.contacts:
                    existing_contacts.add(c.user_id)
            
            # For each contact, check if they're in the target chat
            for uid in list(existing_contacts)[:200]:
                if self._stop_requested: return
                self.total_api_calls += 1
                if uid in self.found_users:
                    continue
                if await self._oracle.is_member(uid):
                    try:
                        await self.add_user(await self.app.get_users(uid),
                                            "imported_contact")
                        discovered += 1
                    except Exception:
                        pass
                await self._throttle.wait()
        except Exception as e:
            print(f"⚠️ Import contacts err: {e}", flush=True)
        
        # Also check dialog participants through common chats
        self._stage = f"بررسی مخاطبین مشترک ({discovered} جدید)"
        await self._progress()
        try:
            async for dialog in self.app.get_dialogs(limit=500):
                if self._stop_requested: return
                if dialog.chat and dialog.chat.id != chat_id:
                    try:
                        async for member in self.app.get_chat_members(dialog.chat.id, limit=50):
                            if self._stop_requested: return
                            self.total_api_calls += 1
                            uid = member.user.id
                            if uid not in self.found_users:
                                if await self._oracle.is_member(uid):
                                    await self.add_user(member.user, "common_chat")
                                    discovered += 1
                    except: pass
                await self.human_sleep(0.2, 0.5)
        except Exception as e:
            print(f"⚠️ Common chats err: {e}", flush=True)
        
        print(f"✅ Import Contacts: {discovered} کاربر جدید", flush=True)


    # ═══════════════ 🔥 ULTIMATE SCRAPING METHODS ═══════════════

    async def scrape_aggressive_pagination(self, chat_id, max_prefixes=400):
        """🔥 روش ۹: صفحه‌بندی تهاجمی روی لیست اعضا (بازنویسی‌شده)

        ⚡ چرا این بازنویسی مهم‌ترین بهبود است:

        نسخه‌ی قبلی برای هر پیشوند یک `contacts.Search` **سراسری** می‌زد
        — یعنی در کل تلگرام دنبال آن حرف می‌گشت، نه در گروه هدف. بعد
        برای تک‌تک نتایج یک `get_chat_member` می‌فرستاد تا ببیند اصلاً
        عضو هست یا نه. با ۵۰۰ پیشوند × ۱۰۰ نتیجه یعنی تا ۵۰٬۰۰۰ درخواست
        تأیید، برای کاربرانی که تقریباً هیچ‌کدام عضو گروه نبودند. نتیجه:
        فلود قطعی و نرخ اصابت نزدیک صفر.

        نسخه‌ی جدید از `channels.GetParticipants` با فیلتر
        `ChannelParticipantsSearch` استفاده می‌کند: جستجو را **سرور
        تلگرام روی لیست اعضای همان کانال** انجام می‌دهد. پس

          • هر نتیجه قطعاً عضو است ⇒ صفر درخواست تأیید
          • آبجکت کامل کاربر در همان پاسخ می‌آید ⇒ صفر get_users
          • سقف ۱۰٬۰۰۰ نفریِ لیست معمولی دور زده می‌شود، چون هر پیشوند
            سهمیه‌ی مستقل دارد — این تنها راه شناخته‌شده برای بیرون
            کشیدن اعضای گروه‌های خیلی بزرگ است.

        از ~۵۰٬۰۰۰ درخواست به ~۴۰۰ درخواست، با نرخ اصابت بسیار بالاتر.
        """
        print("\n🔥 روش ۹: صفحه‌بندی تهاجمی روی لیست اعضا...", flush=True)
        self._stage = "صفحه‌بندی تهاجمی (جستجوی سمت سرور)"
        await self._progress(force=True)

        input_channel = await scrape_api.as_input_channel(self.app, chat_id)
        if input_channel is None:
            print("⚠️ گروه ساده است (کانال نیست) — این روش کاربرد ندارد", flush=True)
            return

        prefixes = self._build_search_prefixes()
        discovered = 0
        exhausted = 0

        for pi, prefix in enumerate(prefixes[:max_prefixes]):
            if self._stop_requested:
                return
            before = len(self.found_users)
            self.total_api_calls += 1
            async for u in scrape_api.iter_participants_search(
                    self.app, chat_id, query=prefix, limit=200,
                    max_total=10000, on_flood=self.handle_flood):
                if self._stop_requested:
                    return
                if u.id in self.found_users or u.id in self._existing_user_ids:
                    continue
                if getattr(u, "bot", False) or getattr(u, "deleted", False):
                    continue
                self.found_users[u.id] = {
                    "user_id": u.id,
                    "first_name": getattr(u, "first_name", "") or "",
                    "last_name": getattr(u, "last_name", "") or "",
                    "username": getattr(u, "username", "") or "",
                    "phone": getattr(u, "phone", "") or "",
                    "is_premium": "بله" if getattr(u, "premium", False) else "خیر",
                    "source": f"search_{prefix}",
                }
                discovered += 1

            gained = len(self.found_users) - before
            # پیشوندهایی که چیزی نمی‌دهند نشانه‌ی پوشش کامل‌اند؛ اگر
            # پشت‌سرهم ۴۰ تا خالی بود ادامه دادن اتلاف درخواست است.
            exhausted = exhausted + 1 if gained == 0 else 0
            if exhausted >= 40 and discovered > 0:
                print(f"⏹️ پوشش کامل شد بعد از {pi + 1} پیشوند", flush=True)
                break

            if pi % 10 == 0:
                self._stage = f"جستجوی اعضا: '{prefix}' | {discovered} پیدا شده"
                await self._progress()
            await self.human_sleep(0.15, 0.35)

        print(f"✅ Aggressive Pagination: {discovered} کاربر جدید", flush=True)

    @staticmethod
    def _build_search_prefixes():
        """ساخت فهرست پیشوندهای جستجو.

        ⚠️ نسخه‌ی قبلی `random.shuffle(prefixes[50:])` می‌نوشت که یک
        no-op کامل است: برش یک لیست جدید می‌سازد، شافل روی همان کپی
        اعمال می‌شود و دور ریخته می‌شود. لیست اصلی دست‌نخورده می‌ماند.
        """
        prefixes = []
        prefixes.extend(chr(c) for c in range(0x61, 0x7B))          # a-z
        prefixes.extend(chr(c) for c in range(0x30, 0x3A))          # 0-9
        # فارسی/عربی — پرکاربردترین‌ها اول
        prefixes.extend(list("امحسنرتبدکیلپفگعشقزوهجخطصضثذغظژچ"))
        prefixes.extend(chr(c) for c in range(0x0600, 0x0700) if chr(c).isalpha())
        prefixes.extend(chr(c) for c in range(0x0400, 0x0450) if chr(c).isalpha())
        prefixes.extend(["ş", "ğ", "ç", "ö", "ü", "ı"])
        prefixes.extend([chr(0x4E00), chr(0x4E2D), chr(0x56FD),
                         chr(0x6587), chr(0x5927), chr(0x4EBA)])
        prefixes.extend([chr(0x0915), chr(0x092E), chr(0x0938)])

        # دو-حرفی‌های پرتکرار: وقتی یک حرف به سقف نتایج می‌خورد،
        # ترکیب‌ها لایه‌ی بعدی اعضا را بیرون می‌کشند.
        for a in "abcdefghijklmnopqrstuvwxyz":
            prefixes.append(a + "a")
        for a in "امحسنرتبدکی":
            prefixes.append(a + "ا")

        seen = set()
        out = []
        for p in prefixes:
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out

    async def scrape_group_intersection(self, chat_id, max_other_groups=30):
        """🔥 روش ۱۰: اسکن اشتراک گروهی (Group Intersection)
        پیشرفته‌ترین روش برای کشف اعضای مخفی! بررسی میکنه اعضای
        گروه‌های دیگه‌ای که توش هستیم، کدومشون عضو این گروه هم هستن.
        حتی اگه لیست مخفی باشه و کاربر هیچ پیامی نداده باشه.
        این روش میتونه تا ۹۰٪ اعضای مخفی رو دربیاره."""
        print(f"\n🔥 روش ۱۰: Group Intersection (اشتراک گروهی)...", flush=True)
        self._stage = "اسکن اشتراک گروهی"
        await self._progress(force=True)
        
        discovered = 0
        checked = 0
        skipped = 0
        
        # Get all our groups
        try:
            all_my_groups = []
            async for dialog in self.app.get_dialogs(limit=2000):
                cht = dialog.chat
                if cht and cht.id != chat_id:
                    cht_type = str(cht.type).lower()
                    if 'group' in cht_type or 'supergroup' in cht_type:
                        cnt = getattr(cht, 'members_count', 0) or 0
                        all_my_groups.append((cht.id, cht.title, cnt))
            
            # Sort by member count (prefer smaller groups for faster scanning)
            all_my_groups.sort(key=lambda x: x[2])
            
            for gid, gname, gcount in all_my_groups[:max_other_groups]:
                if self._stop_requested: return
                
                self._stage = f"اشتراک گروهی: {gname[:20]}..."
                await self._progress()
                
                try:
                    async for member in self.app.get_chat_members(gid, limit=500):
                        if self._stop_requested: return
                        checked += 1
                        uid = member.user.id
                        if uid in self.found_users:
                            skipped += 1
                            continue
                        
                        if await self._oracle.is_member(uid):
                            await self.add_user(member.user,
                                                f"intersection_{gname[:15]}")
                            discovered += 1

                        if checked % 100 == 0:
                            self._stage = f"اشتراک: {discovered} جدید | {checked} بررسی"
                            await self._progress()
                        await self._throttle.wait()
                        
                except FloodWait as e:
                    await self.handle_flood(e)
                except Exception:
                    pass
                
                await self.human_sleep(0.5, 1.5)
                
                if discovered % 50 == 0 and discovered > 0:
                    self._stage = f"🔥 اشتراک گروهی: {discovered} عضو مخفی کشف شد!"
                    await self._progress(force=True)
        
        except Exception as e:
            print(f"⚠️ Group intersection err: {e}", flush=True)
        
        print(f"✅ Group Intersection: {discovered} جدید از {checked} بررسی", flush=True)


    async def scrape_forwarded_messages(self, chat_id, limit=5000):
        """🔥 روش ۱۱: فرواردها، ریپلای‌ها و رأی‌دهنده‌های نظرسنجی.

        ⚡ این متد حالا **داده‌ی جمع‌آوری‌شده در عبور واحد** را پردازش
        می‌کند و تاریخچه را دوباره نمی‌خواند.

        نسخه‌ی قبلی یک عبور کامل جدا روی تاریخچه می‌زد و بدتر، برای
        فرستنده‌ی *هر* فروارد و *هر* ریپلای یک `get_chat_member`
        تأییدی می‌فرستاد — روی ۵٬۰۰۰ پیام یعنی هزاران درخواست اضافه.

        حالا `MessageHarvester` فرواردها و ریپلای‌ها را در همان عبور
        اول برداشته و شناسه‌ی نظرسنجی‌ها را نگه داشته است. تنها کار
        باقی‌مانده تأیید نامزدهایی است که هنوز در کش قطعی نشده‌اند.
        """
        h = getattr(self, "_harvester", None)
        if h is None:
            # عبور واحد اجرا نشده — یعنی این روش مستقل صدا زده شده.
            h = await self.scrape_unified_history(chat_id, limit=limit)
            return

        pending = [uid for uid in list(self.found_users.keys())
                   if not self._oracle.is_settled(uid)]
        if not pending:
            print("✅ فروارد/ریپلای: همه در عبور واحد پوشش داده شدند", flush=True)
            return

        self._stage = f"تأیید {len(pending):,} نامزد فروارد/ریپلای"
        await self._progress(force=True)
        confirmed = 0
        for i, uid in enumerate(pending):
            if self._stop_requested:
                return
            if await self._oracle.is_member(uid):
                confirmed += 1
            if i % 50 == 0:
                self._stage = f"تأیید: {i:,}/{len(pending):,}"
                await self._progress()
            await self._throttle.wait()
        print(f"✅ فروارد/ریپلای: {confirmed:,} تأیید شد", flush=True)

    async def scrape_mtproto_super_resolve(self, chat_id, user_ids_batch=None):
        """🔥 روش ۱۲: Batch resolve با MTProto raw API
        به جای get_chat_member تک‌تک (۱ API call per user)،
        تا ۱۰۰ کاربر رو یکجا با messages.CheckChatInvite بررسی میکنه.
        این روش میتونه تا ۱۰ برابر سریع‌تر از روش عادی باشه.
        مخصوص cross-reference کردن لیست‌های بزرگ."""
        print(f"\n🔥 روش ۱۲: Batch MTProto Resolve...", flush=True)
        self._stage = "Batch resolve اعضا"
        await self._progress(force=True)
        
        discovered = 0
        batch_size = 20  # Safe batch size to avoid overload
        
        # Collect user IDs to check
        ids_to_check = []
        if user_ids_batch:
            ids_to_check = user_ids_batch
        else:
            ids_to_check = list(self.found_users.keys())
        
        # ⚠️ نسخه‌ی قبلی self._checked_members را *قبل از* ساختنش
        # می‌خواند ⇒ AttributeError در همان خط اول. حالا در __init__
        # ساخته می‌شود و اینجا فقط محض احتیاط تضمین می‌گردد.
        if not hasattr(self, '_checked_members'):
            self._checked_members = set()
        unchecked = [uid for uid in ids_to_check
                     if uid not in self._checked_members]

        import random as _rnd
        _rnd.shuffle(unchecked)
        
        # ⚠️ نسخه‌ی قبلی `users.GetFullUser(InputUser(user_id=uid,
        # access_hash=0))` می‌زد. access_hash صفر تقریباً همیشه نامعتبر
        # است، پس آن فراخوانی همیشه شکست می‌خورد — و حتی اگر موفق
        # می‌شد نتیجه‌اش دور ریخته می‌شد و بلافاصله get_chat_member
        # صدا زده می‌شد. یعنی یک درخواست کاملاً هدررفته به ازای هر
        # کاربر. حالا حذف شده و مستقیم تأیید انجام می‌شود.
        for i in range(0, min(5000, len(unchecked)), batch_size):
            if self._stop_requested:
                return
            batch = unchecked[i:i + batch_size]
            for uid in batch:
                if self._stop_requested:
                    return
                self._checked_members.add(uid)
                self.total_api_calls += 1
                # از کش عبور می‌کند: کاربری که قبلاً تأیید شده دوباره
                # پرسیده نمی‌شود.
                if not await self._oracle.is_member(uid):
                    continue
                if uid in self.found_users:
                    continue
                try:
                    await self.add_user(await self.app.get_users(uid),
                                        "mtproto_resolve")
                    discovered += 1
                except Exception:
                    continue

            if discovered and discovered % 20 == 0:
                self._stage = f"MTProto resolve: {discovered} تایید شده"
                await self._progress()
            await self.human_sleep(0.2, 0.5)

        print(f"✅ MTProto Resolve: {discovered} کاربر جدید تایید شد", flush=True)


    async def scrape_global_search(self, chat_id, search_terms=None):
        """🆕 روش ۷: جستجوی سراسری و cross-reference با گروه هدف

        ⚠️ نسخه‌ی قبلی `raw_fns.types.InputMessagesFilterEmpty()` را صدا
        می‌زد؛ تایپ‌ها زیر `raw.types` هستند نه زیر `raw.functions`، پس
        همان اولین تکرار AttributeError می‌داد و `except Exception:
        continue` آن را می‌بلعید. این متد هرگز حتی یک کاربر نداد.

        ⚡ بهبود دوم: نسخه‌ی قبلی برای هر نتیجه یک `get_chat_member` و
        سپس یک `get_users` می‌زد (۲ درخواست به ازای هر نفر) در حالی که
        نتایج جستجوی سراسری اصلاً ربطی به گروه هدف نداشتند. حالا
        فرستنده‌ها یکجا جمع می‌شوند و با یک عبورِ دسته‌ای تأیید می‌شوند.
        """
        if not search_terms:
            search_terms = ["سلام", "hello", "ok", "بله", "👍", "🙂", "مرسی",
                            "لینک", "https", "عکس", "فیلم", "ممنون"]

        print(f"\n🔍 روش ۷: Global Search با {len(search_terms)} عبارت...", flush=True)
        self._stage = "جستجوی سراسری برای کشف اعضا"
        await self._progress(force=True)

        candidates = {}
        for term in search_terms[:15]:
            if self._stop_requested:
                return
            self.total_api_calls += 1
            result = await scrape_api.search_global(
                self.app, term, limit=50, on_flood=self.handle_flood)
            if result is None:
                continue
            users = {u.id: u for u in getattr(result, "users", []) or []}
            for msg in getattr(result, "messages", []) or []:
                uid = scrape_api.peer_user_id(getattr(msg, "from_id", None))
                if not uid or uid in self.found_users or uid in self._existing_user_ids:
                    continue
                # کاربر از خود پاسخ می‌آید ⇒ get_users لازم نیست
                candidates.setdefault(uid, (users.get(uid), term))
            self._stage = f"جستجوی سراسری: {len(candidates)} نامزد"
            await self._progress()
            await self.human_sleep(0.5, 1.0)

        discovered = await self._confirm_and_add(
            chat_id, candidates, "global_search")
        print(f"✅ Global Search: {discovered} کاربر جدید", flush=True)

    async def _confirm_and_add(self, chat_id, candidates, source):
        """تأیید عضویت نامزدها در گروه هدف و افزودنشان.

        هر نامزد دقیقاً **یک** درخواست `get_chat_member` مصرف می‌کند.
        نسخه‌های قبلی دو تا می‌زدند (یکی هم `get_users`) چون آبجکت کاربر
        را دور می‌ریختند.
        """
        discovered = 0
        for uid, (user, tag) in list(candidates.items()):
            if self._stop_requested:
                return discovered
            if uid in self.found_users or uid in self._existing_user_ids:
                continue
            self.total_api_calls += 1
            if not await self._oracle.is_member(uid):
                continue
            if user is not None:
                await self.add_user(user, f"{source}_{tag}")
            else:
                try:
                    await self.add_user(await self.app.get_users(uid), f"{source}_{tag}")
                except Exception:
                    continue
            discovered += 1
            await self._throttle.wait()
        return discovered

    async def scrape_deep_history(self, chat_id, limit=20000, batch_size=500):
        """🔍 روش ۸: اسکن تاریخچه فراتر از سقف عبور اول.

        ⚡ این تنها روشی است که مجاز است تاریخچه را دوباره بخواند، و
        فقط وقتی که عبور واحد به سقفش خورده باشد (یعنی گروه پیام‌های
        بیشتری از حدی دارد که خواندیم). با پرش‌های offset به بازه‌های
        زمانی قدیمی‌تر می‌رود.

        استخراج از هر پیام به همان `MessageHarvester` سپرده می‌شود، پس
        منطق دوباره‌نویسی نشده و ری‌اکشن/نظرسنجی‌های تازه‌کشف‌شده هم
        جمع می‌شوند.
        """
        print(f"\n🔍 روش ۸: اسکن عمیق تاریخچه (تا {limit:,})...", flush=True)
        self._stage = "اسکن عمیق تاریخچه"
        await self._progress(force=True)

        harvester = MessageHarvester(
            sink=self.add_user,
            existing_ids=self._existing_user_ids,
            stop_flag=lambda: self._stop_requested,
        )
        start = getattr(self, "_harvester", None)
        # از جایی شروع کن که عبور اول تمام کرد
        offset = start.messages_seen if start else 0
        scanned = 0

        while scanned < limit:
            if self._stop_requested:
                break
            got = 0
            try:
                async for msg in self.app.get_chat_history(
                        chat_id, limit=batch_size, offset=offset + scanned):
                    if self._stop_requested:
                        break
                    await harvester.consume(msg)
                    got += 1
                    self._throttle.on_success()
                    await self._throttle.wait()
            except FloodWait as e:
                await self.handle_flood(e)
                continue
            except Exception as e:
                print(f"⚠️ اسکن عمیق: {type(e).__name__}: {e}", flush=True)
                break

            if got == 0:
                break  # تاریخچه تمام شد
            scanned += got
            self._stage = f"اسکن عمیق: {scanned:,} پیام | {len(self.found_users):,} کاربر"
            await self._progress()

        print(f"✅ اسکن عمیق: {harvester.summary()}", flush=True)
        await self._harvest_collected(chat_id, harvester)

    async def _harvest_reactions(self, chat_id, msg, source_prefix):
        """استخراج ری‌اکت‌دهنده‌های یک پیام — مسیر مشترک و درست.

        ⚠️ نسخه‌ی قبلی `self.app.get_message_reactions(...)` را صدا می‌زد
        که در Pyrogram 2.0.106 **وجود ندارد**. AttributeError بلافاصله
        توسط `except: break` بلعیده می‌شد، پس این «روش» همیشه صفر کاربر
        برمی‌گرداند بدون هیچ خطایی در لاگ.

        ضمناً نسخه‌ی قبلی به ازای هر ری‌اکت‌دهنده یک `get_users` جداگانه
        می‌زد؛ حالا آبجکت کاربر از خود payload می‌آید ⇒ حذف صدها
        درخواست و ریسک فلود.
        """
        if not msg.reactions or not getattr(msg.reactions, "reactions", None):
            return 0
        found = 0
        for react in msg.reactions.reactions:
            if self._stop_requested:
                return found
            emoji = getattr(react, "emoji", None) or getattr(react, "emoticon", None)
            count_hint = getattr(react, "count", 0) or 0
            self.total_api_calls += 1
            async for uid, user in scrape_api.iter_message_reactors(
                self.app, chat_id, msg.id, reaction=emoji,
                limit=100, max_total=max(100, min(5000, count_hint * 2)),
                on_flood=self.handle_flood,
            ):
                if self._stop_requested:
                    return found
                if uid in self.found_users or uid in self._existing_user_ids:
                    continue
                label = f"{source_prefix}_{emoji}" if emoji else source_prefix
                if user is not None:
                    await self.add_user(user, label)
                else:
                    # کاربر در payload نبود — حداقل شناسه را نگه دار
                    self.found_users[uid] = {
                        "user_id": uid, "first_name": str(uid), "last_name": "",
                        "username": "", "phone": "", "is_premium": "نامشخص",
                        "source": label,
                    }
                found += 1
            await self.human_sleep(0.05, 0.15)
        return found

    async def scrape_reactions_dedicated(self, chat_id, limit=5000):
        """🔍 روش ۴: ری‌اکت‌دهنده‌ها — عالی برای کسانی که هرگز پیام نمی‌دهند.

        ⚡ عبور واحد شناسه‌ی پیام‌های ری‌اکشن‌دار را جمع می‌کند و سپس
        فقط همان‌ها را می‌پرسد. نسخه‌ی قبلی برای *هر* پیام بررسی
        می‌کرد و برای *هر ایموجی* یک درخواست جدا می‌فرستاد.
        """
        return await self.scrape_unified_history(chat_id, limit=limit)

    async def scrape_channel_posts(self, chat_id, limit=5000):
        """🔍 روش ۵: اسکن کانال — نویسنده‌ها، فرواردها، ری‌اکشن‌ها.

        ⚡ به عبور واحد واگذار شد؛ همان منابع را پوشش می‌دهد بدون
        خواندن دوباره‌ی تاریخچه.
        """
        return await self.scrape_unified_history(
            chat_id, limit=limit, is_channel=True)

    async def scan_all_chats(self, chat_type="all", progress_cb=None, incremental_save_cb=None):
        """🔥 اسکن دسته‌جمعی همه گروه‌ها یا کانال‌ها"""
        self._progress_cb = progress_cb
        self._incremental_save_cb = incremental_save_cb
        self._last_progress = 0
        self.start_time = time.time()
        
        # Get all matching chats
        chats = []
        async for d in self.app.get_dialogs(limit=2000):
            cht = d.chat
            if not cht: continue
            cht_type = str(cht.type).lower()
            is_group = 'group' in cht_type or 'supergroup' in cht_type
            is_channel = 'channel' in cht_type and not is_group
            if chat_type == "groups" and is_group:
                chats.append((cht.id, cht.title, getattr(cht, 'members_count', 0)))
            elif chat_type == "channels" and is_channel:
                chats.append((cht.id, cht.title, getattr(cht, 'members_count', 0)))
        
        total = len(chats)
        print(f"🔥 Bulk scan: {total} {chat_type}", flush=True)
        all_found = {}
        
        for idx, (cid, cname, _) in enumerate(chats, 1):
            if self._stop_requested: break
            self._stage = f"[{idx}/{total}] {cname[:25]}"
            await self._progress(force=True)
            try:
                # Fast scan - just paginated + history
                await self.scrape_direct_paginated(cid)
                await self.scrape_deep_history(cid, limit=5000, batch_size=300)
            except: pass
            if self._incremental_save_cb and idx % 3 == 0:
                try: await self._incremental_save_cb(list(self.found_users.values()))
                except: pass
        
        return self.found_users

    async def scrape_unified_history(self, chat_id, limit=10000, is_channel=False):
        """⚡ یک عبور واحد روی تاریخچه، به‌جای شش عبور جداگانه.

        قبلاً شش روش هرکدام کل تاریخچه را از اول می‌خواندند:
        full_history، join_events، forwarded_messages، deep_history،
        reactions_dedicated و channel_posts. روی گروهی با ۱۰٬۰۰۰ پیام
        یعنی **۶۰٬۰۰۰ پیام دانلود** برای داده‌ای که یک بار خواندن کافی
        بود.

        حالا یک بار می‌خوانیم و همه‌چیز را همزمان برمی‌داریم. ری‌اکشن‌ها
        و نظرسنجی‌ها درخواست جدا لازم دارند، ولی شناسه‌ی پیام‌هایشان در
        همین عبور جمع می‌شود تا بدون خواندن دوباره‌ی تاریخچه پردازش
        شوند.

        صرفه‌جویی: ۸۳٪ کمتر پیام دانلود می‌شود.
        """
        t0 = time.time()
        self._stage = f"اسکن یکپارچه تاریخچه (تا {limit:,} پیام)"
        await self._progress(force=True)

        harvester = MessageHarvester(
            sink=self.add_user,
            existing_ids=self._existing_user_ids,
            stop_flag=lambda: self._stop_requested,
        )
        self._harvester = harvester
        last_prog = 0.0

        try:
            async for msg in self.app.get_chat_history(chat_id, limit=limit):
                if self._stop_requested:
                    break
                await harvester.consume(msg)
                self._throttle.on_success()

                now = time.time()
                if now - last_prog > 2:
                    last_prog = now
                    self.total_api_calls += 1
                    seen = harvester.messages_seen
                    spd = int(seen / max(0.001, now - t0) * 60)
                    self._stage = (f"📜 {seen:,} پیام | "
                                   f"{len(self.found_users):,} کاربر | ⚡{spd:,}/min")
                    await self._progress()
                # تأخیر تطبیقی: در حالت عادی صفر، فقط بعد از فلود بالا می‌رود
                await self._throttle.wait()
        except FloodWait as e:
            await self.handle_flood(e)
        except Exception as e:
            print(f"⚠️ اسکن تاریخچه: {type(e).__name__}: {e}", flush=True)

        el = max(0.001, time.time() - t0)
        print(f"✅ عبور واحد: {harvester.summary()} در {int(el)}s", flush=True)

        # حالا ری‌اکشن‌ها و رأی‌ها — فقط روی پیام‌هایی که واقعاً دارند
        await self._harvest_collected(chat_id, harvester)
        return harvester

    async def _harvest_collected(self, chat_id, harvester):
        """پردازش ری‌اکشن‌ها و نظرسنجی‌های جمع‌آوری‌شده.

        نکته‌ی کلیدی: فقط پیام‌هایی پرسیده می‌شوند که در عبور اول
        معلوم شد ری‌اکشن/نظرسنجی دارند. نسخه‌ی قبلی برای *هر* پیام
        بررسی می‌کرد.
        """
        rids = harvester.reaction_msg_ids
        if rids:
            self._stage = f"استخراج ری‌اکشن از {len(rids):,} پیام"
            await self._progress(force=True)
            got = 0
            for i, mid in enumerate(rids):
                if self._stop_requested:
                    break
                got += await self._harvest_reactions_by_id(chat_id, mid)
                if i % 25 == 0:
                    self._stage = f"ری‌اکشن: {i:,}/{len(rids):,} | {got:,} کاربر"
                    await self._progress()
            print(f"✅ ری‌اکشن‌ها: {got:,} کاربر از {len(rids):,} پیام", flush=True)

        pids = harvester.poll_msg_ids
        if pids:
            self._stage = f"استخراج رأی از {len(pids):,} نظرسنجی"
            await self._progress(force=True)
            got = 0
            for mid in pids:
                if self._stop_requested:
                    break
                self.total_api_calls += 1
                async for uid, user in scrape_api.iter_poll_voters(
                        self.app, chat_id, mid, limit=100, max_total=500,
                        on_flood=self.handle_flood):
                    if self._stop_requested:
                        break
                    if uid in self.found_users or uid in self._existing_user_ids:
                        continue
                    if user is not None:
                        # رأی‌دهنده قطعاً عضو است
                        self._oracle.mark_member(uid)
                        await self.add_user(user, "poll_voter")
                        got += 1
                await self._throttle.wait()
            print(f"✅ نظرسنجی‌ها: {got:,} رأی‌دهنده", flush=True)

    async def _harvest_reactions_by_id(self, chat_id, msg_id):
        """ری‌اکت‌دهنده‌های یک پیام بر اساس شناسه (بدون آبجکت پیام).

        `reaction=None` یعنی «همه‌ی ری‌اکشن‌ها» — یک درخواست به‌جای یک
        درخواست به ازای هر ایموجی. نسخه‌ی قبلی روی پستی با ۸ ایموجی
        مختلف، ۸ برابر درخواست می‌فرستاد.
        """
        found = 0
        self.total_api_calls += 1
        async for uid, user in scrape_api.iter_message_reactors(
                self.app, chat_id, msg_id, reaction=None,
                limit=100, max_total=3000, on_flood=self.handle_flood):
            if self._stop_requested:
                return found
            if uid in self.found_users or uid in self._existing_user_ids:
                continue
            self._oracle.mark_member(uid)  # ری‌اکت‌دهنده عضو است
            if user is not None:
                await self.add_user(user, "ری‌اکشن")
            else:
                self.found_users[uid] = {
                    "user_id": uid, "first_name": str(uid), "last_name": "",
                    "username": "", "phone": "", "is_premium": "نامشخص",
                    "source": "ری‌اکشن",
                }
            found += 1
        await self._throttle.wait()
        return found

    async def _run_strategy(self, chat_id, is_channel, deep=False):
        """اجرای مرحله‌بندی‌شده‌ی روش‌های استخراج.

        ⚠️ مشکلی که این متد حل می‌کند: از ۱۲ روش نوشته‌شده، فقط ۵ تا
        اصلاً صدا زده می‌شدند. هفت روش دیگر — از جمله «اشتراک گروهی» که
        در مستندات ۹۰٪ اعضای مخفی را وعده می‌داد — کد مرده بودند.

        روش‌ها به سه لایه تقسیم شده‌اند و هر لایه فقط وقتی اجرا می‌شود
        که لایه‌ی قبل کافی نبوده باشد. دلیلش این است که لایه‌های بعدی
        بسیار پرهزینه‌ترند و بی‌دلیل اجرا کردنشان یعنی فلود.
        """
        # ── لایه ۱: ارزان و پربازده ───────────────────────────────
        # لیست مستقیم اول: نتایجش ذاتاً عضو هستند و کش عضویت را پر
        # می‌کنند، پس لایه‌های بعدی درخواست تأیید کمتری لازم دارند.
        await self._safe(self.scrape_direct_paginated(chat_id), "لیست مستقیم")
        # یک عبور واحد به‌جای شش عبور جداگانه (۸۳٪ کمتر دانلود)
        await self._safe(
            self.scrape_unified_history(
                chat_id, limit=10000 if is_channel else 5000,
                is_channel=is_channel),
            "تاریخچه یکپارچه")

        base = len(self.found_users)
        expected = getattr(self, "_target_members_count", 0) or 0
        # اگر بیش از ۸۵٪ اعضا را داریم، ادامه دادن فقط ریسک فلود است.
        covered = expected and base >= expected * 0.85
        if covered and not deep:
            print(f"✅ پوشش کافی ({base}/{expected}) — لایه‌های بعدی لازم نیست", flush=True)
            return

        # ── لایه ۲: جستجوی سمت سرور، همچنان ارزان ────────────────
        print("🔥 لایه ۲: جستجوی عمیق اعضا", flush=True)
        # صفحه‌بندی سمت سرور — ارزان‌ترین راه برای اعضایی که هیچ پیامی
        # نداده‌اند، و نتایجش ذاتاً عضو هستند.
        await self._safe(self.scrape_aggressive_pagination(chat_id), "صفحه‌بندی")
        # تاریخچه‌ی عمیق فقط اگر عبور اول به سقف خورده باشد؛ وگرنه
        # همان پیام‌ها را دوباره می‌خواند.
        h = getattr(self, "_harvester", None)
        capped = h is not None and h.messages_seen >= (5000 if not is_channel else 10000)
        if not is_channel and capped:
            await self._safe(
                self.scrape_deep_history(chat_id, limit=20000), "تاریخچه عمیق")
        elif not is_channel:
            print("   ↳ «تاریخچه عمیق» لازم نیست — عبور اول کل تاریخچه را دید",
                  flush=True)

        after2 = len(self.found_users)
        if not deep:
            print(f"📊 لایه ۲ تمام شد: {after2} کاربر", flush=True)
            return

        # ── لایه ۳: گران، فقط در حالت عمیق ───────────────────────
        print("🕳️ لایه ۳: کشف اعضای مخفی (حالت عمیق)", flush=True)
        await self._safe(self.scrape_forwarded_messages(chat_id), "تأیید فروارد")
        await self._safe(self.scrape_group_intersection(chat_id), "اشتراک گروهی")
        await self._safe(self.scrape_imported_contacts(chat_id), "مخاطبین")
        await self._safe(self.scrape_global_search(chat_id), "جستجوی سراسری")
        await self._safe(self.scrape_mtproto_super_resolve(chat_id), "تأیید دسته‌ای")

    async def _safe(self, coro, label):
        """اجرای یک روش با گزارش خطا.

        ⚠️ سراسر این فایل پر از `except: pass` بود؛ به همین دلیل چهار
        روشِ کاملاً شکسته ماه‌ها بی‌سروصدا صفر برمی‌گرداندند. اینجا خطا
        بلعیده می‌شود (یک روش نباید کل عملیات را بخواباند) ولی
        **حتماً چاپ می‌شود**.
        """
        before = len(self.found_users)
        try:
            await coro
        except FloodWait as e:
            await self.handle_flood(e)
        except Exception as e:
            print(f"⚠️ روش «{label}» شکست خورد: {type(e).__name__}: {e}", flush=True)
            return 0
        gained = len(self.found_users) - before
        print(f"   ↳ «{label}»: +{gained} کاربر (مجموع {len(self.found_users)})", flush=True)
        return gained

    async def run_full_scrape(self, chat_id, progress_cb=None,
                              incremental_save_cb=None, deep=False):
        self._progress_cb = progress_cb
        self._incremental_save_cb = incremental_save_cb
        self._last_progress = 0
        self.start_time = time.time()
        self._stage = "در حال اتصال..."
        # 🆕 بارگذاری فقط ID کاربران قبلی از DB (سریع، بدون full load)
        try:
            import db as _db
            cur = _db.get_conn().cursor()
            cur.execute("SELECT user_id FROM scraped_users")
            self._existing_user_ids = {int(r[0]) for r in cur.fetchall()}
            cur.close()
            n = len(self._existing_user_ids)
            if n: print(f"📦 {n:,} کاربر قبلی — Skip", flush=True)
        except: self._existing_user_ids = set()
        
        print("="*60, flush=True)
        print("🚀 شروع حمله MAX MODE", flush=True)
        print("="*60, flush=True)

        # یک وظیفه پس زمینه که هر ۲ ثانیه وضعیت را آپدیت نگه میدارد
        heartbeat_on = True
        async def heartbeat():
            while heartbeat_on:
                await self._progress(force=True)
                await asyncio.sleep(2)

        hb_task = asyncio.create_task(heartbeat())

        try:
            self._stage = "🔄 در حال بارگذاری لیست چت ها..."
            await self._progress(force=True)
            print("🔄 در حال بارگذاری لیست چت ها...", flush=True)
            all_chats = {}
            try:
                cnt = 0
                async for d in self.app.get_dialogs(limit=200):
                    all_chats[d.chat.id] = d.chat
                    cnt += 1
                print(f"✅ لیست چت ها بارگذاری شد: {cnt} چت", flush=True)
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"خطا در چت ها: {e}", flush=True)

            self._stage = "🔎 در حال پیدا کردن گروه/کانال هدف"
            await self._progress(force=True)
            target_found = None
            target_id_resolved = None
            try:
                peer = await self.app.resolve_peer(chat_id)
                target_found = await self.app.get_chat(chat_id)
                target_id_resolved = target_found.id
                print(f"🎯 هدف: {target_found.title} | {target_found.id} | type={target_found.type}", flush=True)
            except Exception as e:
                print(f"🔍 رزول مستقیم نشد: {e}", flush=True)
                if chat_id in all_chats:
                    target_found = all_chats[chat_id]
                    target_id_resolved = target_found.id
                else:
                    async for d in self.app.get_dialogs(limit=2000):
                        if d.chat.id == chat_id:
                            target_found = d.chat
                            target_id_resolved = d.chat.id
                            break
                    await asyncio.sleep(1)
                if not target_found:
                    try:
                        await asyncio.sleep(2)
                        target_found = await self.app.get_chat(chat_id)
                        target_id_resolved = target_found.id
                    except:
                        raise Exception("❌ گروه/کانال پیدا نشد! لطفا یک بار در تلگرام باز و اسکرول کنید.")
            chat_id = target_id_resolved

            # تشخیص نوع چت: کانال یا گروه/سوپرگروه + تعداد اعضا برای درصد پیشرفت
            is_channel = str(target_found.type).lower() == "chattype.channel"
            chat_type_str = "کانال" if is_channel else "گروه"
            total_members = getattr(target_found, 'members_count', 0) or 0
            chat_type_db = "channel" if is_channel else "group"
            self._target_members_count = total_members
            # کش تأیید عضویت — از اینجا به بعد همه‌ی روش‌ها از آن استفاده
            # می‌کنند تا یک کاربر دو بار پرسیده نشود.
            self._oracle = MembershipOracle(
                self.app, chat_id, on_flood=self.handle_flood)
            print(f"✅ هدف: {target_found.title} | نوع: {chat_type_str} | اعضا: {total_members or '?'}", flush=True)

            # 🆕 ذخیره در تاریخچه چت‌های اسکن شده (بدون AI - سرعت)
            try:
                import db as _db
                _db.upsert_scanned_chat(
                    chat_id=chat_id,
                    chat_name=target_found.title,
                    chat_type=chat_type_db,
                    total_members=total_members,
                    extracted_new=0,
                    progress_pct=0
                )
            except Exception as e:
                print(f"save chat history err: {e}", flush=True)

            # 🆕 ارسال callback برای forward کردن group_id و group_name به incremental save
            self._scanned_group_id = chat_id
            self._scanned_group_name = target_found.title

            self._stage = f"✅ هدف: {target_found.title} | نوع: {chat_type_str} | 👥 ~{total_members or '?'} عضو"
            await self._progress(force=True)

            self._stage = f"🚀 شروع استخراج از {target_found.title}"
            await self._progress(force=True)

            await self._run_strategy(chat_id, is_channel, deep=deep)

            # محاسبه درصد پیشرفت و آپدیت تاریخچه
            extracted = len(self.found_users)
            pct = 0
            if is_channel:
                pct = min(95, extracted) if extracted > 0 else 0
            else:
                if total_members and total_members > 0:
                    pct = min(99, int(extracted * 100 / total_members))

            # ذخیره نهایی
            if self._incremental_save_cb:
                try:
                    await self._incremental_save_cb(list(self.found_users.values()))
                except Exception:
                    pass

            # 🆕 آپدیت تاریخچه با نتیجه نهایی
            try:
                import db as _db
                _db.upsert_scanned_chat(
                    chat_id=chat_id,
                    chat_name=target_found.title,
                    chat_type=chat_type_db,
                    total_members=total_members,
                    extracted_new=extracted,
                    progress_pct=pct
                )
            except: pass

            # گزارش صرفه‌جویی کش عضویت
            try:
                st = self._oracle.stats
                if st["made"] or st["saved"]:
                    print(f"💾 کش عضویت: {st['made']:,} درخواست انجام شد، "
                          f"{st['saved']:,} صرفه‌جویی ({st['saved_pct']}%)",
                          flush=True)
                if self._throttle.floods:
                    print(f"⏱️ فلود: {self._throttle.floods} بار | "
                          f"تأخیر نهایی {self._throttle.delay:.2f}s", flush=True)
            except Exception:
                pass

            total = time.time() - self.start_time
            pct_str = f" | 📊 {pct}% پیشرفت" if pct > 0 else ""
            print(f"\n🏁 تمام شد در {int(total)}s، مجموع {extracted} کاربر{pct_str}", flush=True)
            self._stage = f"✅ تمام شد! {extracted:,} کاربر{pct_str}"
            await self._progress(force=True)
            return self.found_users
        finally:
            heartbeat_on = False
            try:
                await hb_task
            except:
                pass

    def export_csv(self):
        out = io.StringIO()
        if self.found_users:
            keys = list(list(self.found_users.values())[0].keys())
            w = csv.DictWriter(out, fieldnames=keys)
            w.writeheader()
            w.writerows(self.found_users.values())
        return out.getvalue().encode("utf-8-sig")

    async def disconnect(self):
        sess_lock = _get_session_lock(self.app.name)
        async with sess_lock:
            async with _global_connect_lock:
                try:
                    await asyncio.sleep(0.3)
                    await self.app.disconnect()
                except Exception as e:
                    print(f"هنگام قطع: {e}", flush=True)
