# =================================================================
# ⚡ بهینه‌سازی هسته‌ی استخراج
# =================================================================
"""دو گلوگاه اصلی استخراج و راه‌حلشان.

**گلوگاه ۱ — تاریخچه شش بار خوانده می‌شد.**

شش روش مختلف هرکدام مستقلاً کل تاریخچه‌ی چت را از اول می‌خواندند:

    scrape_full_history        ← فرستنده، فروارد، ریپلای، منشن
    scrape_join_events         ← پیام‌های ورود
    scrape_forwarded_messages  ← فروارد، ریپلای، نظرسنجی
    scrape_deep_history        ← همان‌ها با offset متفاوت
    scrape_reactions_dedicated ← ری‌اکشن‌ها
    scrape_channel_posts       ← پست‌های کانال

روی گروهی با ۱۰٬۰۰۰ پیام یعنی **۶۰٬۰۰۰ پیام دانلود** برای داده‌ای که
یک بار خواندن کافی بود. `MessageHarvester` همه‌ی این استخراج‌ها را در
**یک عبور** انجام می‌دهد.

**گلوگاه ۲ — تأیید عضویت تک‌به‌تک (N+1).**

چند روش برای هر نامزد یک `get_chat_member` جدا می‌فرستادند. برای
۵٬۰۰۰ نامزد یعنی ۵٬۰۰۰ درخواست و فلود قطعی.

`MembershipOracle` سه لایه دارد:
  ۱. کش — هر شناسه فقط یک بار پرسیده می‌شود
  ۲. مجموعه‌ی «قطعاً عضو» از روش‌های سمت سرور ⇒ صفر درخواست
  ۳. فقط نامزدهای باقی‌مانده واقعاً پرسیده می‌شوند
"""
import asyncio
import time


class MembershipOracle:
    """تصمیم‌گیرنده‌ی «آیا این کاربر عضو گروه است؟» با کمترین درخواست.

    چرا لازم است: چند روش استخراج نامزد تولید می‌کنند (از جستجوی
    سراسری، از گروه‌های دیگر، از مخاطبین) و باید بفهمیم کدامشان واقعاً
    عضو گروه هدف‌اند. نسخه‌ی قبلی برای هرکدام یک درخواست می‌فرستاد و
    نتیجه را هم کش نمی‌کرد، پس یک کاربر که در سه روش مختلف پیدا می‌شد
    سه بار پرسیده می‌شد.
    """

    def __init__(self, app, chat_id, on_flood=None):
        self.app = app
        self.chat_id = chat_id
        self._on_flood = on_flood
        self.known_members = set()    # قطعاً عضو (از منابع معتبر)
        self.known_outsiders = set()  # قطعاً غیرعضو
        self.requests_made = 0
        self.requests_saved = 0

    def mark_member(self, uid):
        """ثبت عضویتِ قطعی بدون هزینه.

        نتایج `channels.GetParticipants` و `get_chat_members` ذاتاً عضو
        هستند؛ پرسیدن دوباره‌شان اتلاف محض است.
        """
        self.known_members.add(int(uid))

    def mark_members(self, uids):
        self.known_members.update(int(u) for u in uids)

    def is_settled(self, uid):
        uid = int(uid)
        return uid in self.known_members or uid in self.known_outsiders

    async def is_member(self, uid):
        uid = int(uid)
        if uid in self.known_members:
            self.requests_saved += 1
            return True
        if uid in self.known_outsiders:
            self.requests_saved += 1
            return False

        from pyrogram.errors import FloodWait
        self.requests_made += 1
        try:
            mem = await self.app.get_chat_member(self.chat_id, uid)
        except FloodWait as e:
            if self._on_flood:
                await self._on_flood(e)
            else:
                await asyncio.sleep(e.value)
            return False
        except Exception:
            # خطای «عضو نیست» و خطای دسترسی از هم قابل تفکیک نیستند؛
            # محتاطانه غیرعضو در نظر می‌گیریم ولی کش هم می‌کنیم تا
            # دوباره پرسیده نشود.
            self.known_outsiders.add(uid)
            return False

        if mem is None:
            self.known_outsiders.add(uid)
            return False
        self.known_members.add(uid)
        return True

    @property
    def stats(self):
        total = self.requests_made + self.requests_saved
        pct = (self.requests_saved / total * 100) if total else 0.0
        return {
            "made": self.requests_made,
            "saved": self.requests_saved,
            "saved_pct": round(pct, 1),
        }


class MessageHarvester:
    """استخراج همه‌چیز از هر پیام، در یک عبور.

    به‌جای اینکه شش روش هرکدام تاریخچه را جدا بخوانند، یک بار می‌خوانیم
    و از هر پیام **همزمان** استخراج می‌کنیم:

        فرستنده · فروارد · نویسنده‌ی ریپلای · فرواردِ ریپلای ·
        منشن‌ها · اعضای تازه‌وارد · شناسه‌ی پیام‌های ری‌اکشن‌دار ·
        شناسه‌ی نظرسنجی‌ها

    ری‌اکشن‌ها و رأی‌ها نمی‌توانند در همین عبور خوانده شوند (هرکدام
    درخواست جدا لازم دارند) ولی **شناسه‌شان جمع می‌شود** تا بعداً بدون
    خواندن دوباره‌ی تاریخچه پردازش شوند.
    """

    def __init__(self, sink, existing_ids=None, stop_flag=None):
        """
        sink: تابعی که (user, source) می‌گیرد و کاربر را ثبت می‌کند
        existing_ids: شناسه‌هایی که قبلاً داریم و باید رد شوند
        stop_flag: تابعی که True برگرداند یعنی کاربر توقف خواسته
        """
        self.sink = sink
        self.existing = existing_ids if existing_ids is not None else set()
        self.stop_flag = stop_flag or (lambda: False)
        self.messages_seen = 0
        self.reaction_msg_ids = []
        self.poll_msg_ids = []
        self.counts = {}
        self._seen_users = set()

    def _take(self, user, source):
        uid = getattr(user, "id", None)
        if not uid or uid in self._seen_users or uid in self.existing:
            return False
        if getattr(user, "is_bot", False) or getattr(user, "is_deleted", False):
            return False
        self._seen_users.add(uid)
        self.counts[source] = self.counts.get(source, 0) + 1
        return True

    async def consume(self, msg):
        """پردازش یک پیام. همه‌ی منابع همزمان بررسی می‌شوند."""
        self.messages_seen += 1
        found = 0

        if msg.from_user and self._take(msg.from_user, "msg"):
            await self.sink(msg.from_user, "msg")
            found += 1

        if msg.forward_from and self._take(msg.forward_from, "fwd"):
            await self.sink(msg.forward_from, "fwd")
            found += 1

        rep = getattr(msg, "reply_to_message", None)
        if rep is not None:
            if rep.from_user and self._take(rep.from_user, "reply"):
                await self.sink(rep.from_user, "reply")
                found += 1
            if rep.forward_from and self._take(rep.forward_from, "reply_fwd"):
                await self.sink(rep.forward_from, "reply_fwd")
                found += 1

        # اعضای تازه‌وارد — این تنها منبعی است که scrape_join_events
        # داشت، پس آن عبور جداگانه کاملاً حذف می‌شود.
        for u in (getattr(msg, "new_chat_members", None) or []):
            if self._take(u, "join"):
                await self.sink(u, "join")
                found += 1

        from pyrogram.enums import MessageEntityType
        wanted = (MessageEntityType.MENTION, MessageEntityType.TEXT_MENTION)
        for ent in (getattr(msg, "entities", None) or []):
            u = getattr(ent, "user", None)
            if u is not None and getattr(ent, "type", None) in wanted:
                if self._take(u, "mention"):
                    await self.sink(u, "mention")
                    found += 1

        # این‌ها درخواست جدا لازم دارند — فقط شناسه را نگه می‌داریم تا
        # بعداً بدون خواندن دوباره‌ی تاریخچه پردازششان کنیم.
        if getattr(msg, "reactions", None) and getattr(msg.reactions, "reactions", None):
            self.reaction_msg_ids.append(msg.id)
        if getattr(msg, "poll", None):
            self.poll_msg_ids.append(msg.id)

        return found

    def summary(self):
        parts = " · ".join(f"{k}:{v}" for k, v in sorted(
            self.counts.items(), key=lambda x: -x[1]) if v)
        return (f"{self.messages_seen:,} پیام → {len(self._seen_users):,} کاربر"
                + (f"  ({parts})" if parts else ""))


class AdaptiveThrottle:
    """تنظیم پویای تأخیر بر اساس فلودهای واقعی.

    نسخه‌ی قبلی تأخیرهای ثابت داشت (مثلاً ۰.۰۳ تا ۰.۰۸ ثانیه به ازای
    هر پیام). این هم کند است وقتی تلگرام راحت است، و هم کافی نیست وقتی
    تلگرام سخت‌گیر شده.

    اینجا از صفر تأخیر شروع می‌کنیم و فقط بعد از دیدن `FloodWait`
    تأخیر اضافه می‌شود؛ سپس با گذشت زمانِ بدون خطا دوباره کم می‌شود.
    این یعنی در حالت عادی با حداکثر سرعت کار می‌کنیم.
    """

    def __init__(self, base=0.0, ceiling=2.0):
        self.base = base
        self.ceiling = ceiling
        self.penalty = 0.0
        self.floods = 0
        self._last_flood = 0.0

    def on_flood(self, seconds):
        self.floods += 1
        self._last_flood = time.time()
        # هر فلود جریمه را دو برابر می‌کند (شروع از ۰.۰۵)
        self.penalty = min(self.ceiling, max(0.05, self.penalty * 2))

    def on_success(self):
        # بعد از ۳۰ ثانیه بدون فلود، جریمه را نصف کن
        if self.penalty and time.time() - self._last_flood > 30:
            self.penalty = max(0.0, self.penalty * 0.5)
            self._last_flood = time.time()

    @property
    def delay(self):
        return self.base + self.penalty

    async def wait(self):
        d = self.delay
        if d > 0:
            await asyncio.sleep(d)
