"""
=================================================================
🧠 Add Engine — موتور ادد (ضد بن + ضد تکرار)
=================================================================
- تاخیرهای انسانی با نویز تصادفی و استراحت‌های دوره‌ای
- کش در حافظه لیست «هرگز دوباره ادد نشود» (صفر کوئری به ازای هر ممبر)
- ثبت کاربران ممنوعه (پروفایل قفل / لفت داده / آیدی نامعتبر)
"""
import time
import random
import logging
import re

import config
import db as _db

logger = logging.getLogger("antiscraper.add_engine")


# -----------------------------------------------------------------
# 🐌 Human-like delays (ضد بن شدن اکانت‌ها)
# -----------------------------------------------------------------

def human_delay(add_mode="fast"):
    """تاخیر شبیه‌سازی‌شده به رفتار انسان — با نویز تصادفی و مکث‌های گاه‌به‌گاه.

    ⚠️ در حالت «max» هیچ jitter اعمال نمی‌شود. jitter می‌توانست تأخیر را
    تا ۲.۸ برابر کند (۸ ثانیه ⇒ ۲۲ ثانیه) که با هدف «ادد حداکثری»
    در تضاد است.
    """
    lo, hi = config.DELAY_RANGES.get(add_mode, config.DELAY_RANGES["fast"])
    base = random.uniform(lo, hi)
    if add_mode in getattr(config, "NO_JITTER_MODES", frozenset()):
        return base
    # بعضی وقت‌ها مثل یک انسان واقعی بیشتر صبر می‌کند (چک کردن گوشی، حرف زدن و...)
    if random.random() < config.HUMAN_JITTER_CHANCE:
        base *= random.uniform(*config.HUMAN_JITTER_FACTOR)
    # نویز کوچک پایانی
    base *= random.uniform(0.9, 1.15)
    return base


def human_break_seconds(add_mode="fast"):
    """استراحت تصادفی بین دسته‌های ادد (قهوه/استراحت انسانی)"""
    lo, hi = config.BREAK_RANGES.get(add_mode, config.BREAK_RANGES["fast"])
    return random.randint(lo, hi)


# -----------------------------------------------------------------
# 🌐 Global Target Throttle — حداقل فاصله بین هر دو دعوت به یک گروه
# -----------------------------------------------------------------
import asyncio as _asyncio

_global_throttle_lock = _asyncio.Lock()
_global_last_invite_ts = 0.0


async def global_throttle(add_mode="fast"):
    """
    حداقل فاصله بین هر دو InviteToChannel به گروه مقصد،
    صرف‌نظر از اینکه کدام اکانت می‌فرستد.

    بدون این، با ۸ اکانت و تأخیر ۱-۳s، هر ۰.۲۵s یک دعوت به یک گروه
    می‌رود — تلگرام آن را هجوم هماهنگ می‌بیند و PEER_FLOOD می‌دهد.
    """
    if not getattr(config, "GLOBAL_THROTTLE_ENABLED", True):
        return
    lo, hi = getattr(config, "GLOBAL_THROTTLE_INTERVAL", {}).get(add_mode, (1.0, 1.0))
    # در حالت تست (بدون حلقه رویداد) ممکن است Lock ناسازگار باشد؛ امن بمان.
    try:
        interval = random.uniform(lo, hi)
    except Exception:
        interval = 1.0
    global _global_last_invite_ts
    # تلاش برای قفل — اگر حلقه اجرا نمی‌شود، فقط sleep ساده
    try:
        async with _global_throttle_lock:
            now = time.monotonic()
            wait = (_global_last_invite_ts + interval) - now
            if wait > 0:
                await _asyncio.sleep(wait)
            _global_last_invite_ts = time.monotonic()
    except RuntimeError:
        # خارج از حلقه async (مثلاً در تست همگام) — نادیده بگیر
        pass


def reset_global_throttle_for_tests():
    """فقط برای تست‌ها — ریست تایمر سراسری"""
    global _global_last_invite_ts
    _global_last_invite_ts = 0.0


# -----------------------------------------------------------------
# 🚫 Do-Not-Add cache (لیست ممنوعه در حافظه)
# -----------------------------------------------------------------

_BLOCKED_IDS_CACHE = {"ids": None, "ts": 0}
_BLOCKED_CACHE_TTL = 120  # ثانیه


def get_blocked_ids_cached():
    """ست آیدی کاربران ممنوعه (ادد شده قبلی + لیست ممنوعه) با کش ۲ دقیقه‌ای"""
    now = time.time()
    if _BLOCKED_IDS_CACHE["ids"] is None or (now - _BLOCKED_IDS_CACHE["ts"]) > _BLOCKED_CACHE_TTL:
        try:
            _BLOCKED_IDS_CACHE["ids"] = _db.get_blocked_user_ids()
            _BLOCKED_IDS_CACHE["ts"] = now
        except Exception as e:
            logger.warning("blocked cache err: %s", e)
            return set()
    return _BLOCKED_IDS_CACHE["ids"] or set()


def invalidate_blocked_cache():
    _BLOCKED_IDS_CACHE["ids"] = None
    _BLOCKED_IDS_CACHE["ts"] = 0


def mark_added_local(uid):
    """همگام‌سازی کش در حافظه بعد از هر ادد موفق (برای چک‌های بعدی بدون کوئری)"""
    try:
        if _BLOCKED_IDS_CACHE["ids"] is not None:
            _BLOCKED_IDS_CACHE["ids"].add(int(uid))
    except Exception:
        pass


def never_add_again(uid, reason=""):
    """حذف از دیتابیس ممبرها + ثبت در لیست «هرگز دوباره ادد نشود»"""
    try:
        _db.add_do_not_add(uid, reason)
    except Exception as e:
        logger.warning("dna err: %s", e)
    try:
        _db.delete_user(uid)
    except Exception as e:
        logger.warning("del err: %s", e)
    invalidate_blocked_cache()


def prefer_addable_members(members, drop_id_only=True):
    """
    اول یوزرنیم‌دارها، بعد شماره‌دارها.

    ⚠️ درس گران (۱.۷.۰): کاربرانی که فقط user_id دارند (نه یوزرنیم، نه
    شماره) تقریباً هرگز resolve نمی‌شوند — اکانت آن‌ها را در session
    cache خود ندارد. هر تلاش یک درخواست شبکه است که شکست می‌خورد و
    بودجه‌ی نرخ را می‌سوزاند.

    در دیتابیس این کاربر ۶٬۷۸۴ نفر از ۲۵٬۳۴۷ بودند (۲۷٪) — یعنی یک
    چهارم درخواست‌ها از پیش محکوم به شکست.

    حالا به‌طور پیش‌فرض کنار گذاشته می‌شوند.
    """
    out = []
    for u in (members or []):
        un = (u.get("username") or "").strip()
        ph = (u.get("phone") or "").strip()
        if drop_id_only and not un and not ph:
            continue
        out.append(u)

    def _key(u):
        un = (u.get("username") or "").strip()
        ph = (u.get("phone") or "").strip()
        if un:
            return (0, un)
        if ph:
            return (1, ph)
        return (2, str(u.get("user_id") or 0))
    return sorted(out, key=_key)


# ═══════════════════════════════════════════════════════════════
# 🔍 پیش‌فیلتر حریم خصوصی
# ═══════════════════════════════════════════════════════════════
#
# مشکل: تلاش برای ادد کاربری که تنظیمات حریم خصوصی‌اش را بسته،
# یک درخواست کامل به تلگرام می‌فرستد، شکست می‌خورد، و سهم اکانت
# از بودجه‌ی نرخ (rate budget) را می‌سوزاند — بدون هیچ نتیجه‌ای.
# با ۲۵ هزار ممبر در دیتابیس، این یعنی هزاران درخواست بیهوده و
# رسیدن سریع به FloodWait.
#
# راه‌حل: قبل از حلقه‌ی ادد، با یک درخواست دسته‌ای (getUsers) وضعیت
# را بررسی کن. تلگرام برای هر کاربر پرچم‌هایی برمی‌گرداند که نشان
# می‌دهد اصلاً قابل‌افزودن هست یا نه.

# حداکثر تعداد در هر فراخوانی get_users — تلگرام سقف ~200 دارد
_PREFILTER_BATCH = 100


# وضعیت‌های last-seen که یعنی کاربر privacy را بسته است.
#
# 🔑 چرا این مهم است (۱.۸.۰):
# تلگرام برای کسی که «آخرین بازدید» را مخفی کرده، به جای زمان دقیق
# یکی از این مقادیر مبهم را برمی‌گرداند. و کسی که last-seen را بسته،
# با احتمال بسیار بالا «چه کسی می‌تواند مرا به گروه اضافه کند» را هم
# محدود کرده — این دو گزینه در تلگرام کنار هم در یک صفحه‌اند.
#
# داده‌ی واقعی ما: ۹ دعوت پیاپی، صفر عضویت. تلگرام اکانتی که مدام
# دعوت بی‌نتیجه می‌فرستد را اسپمر می‌بیند — حتی با فاصله‌ی ۱۰۰ ثانیه.
# پس باید *قبل از تلاش* حدس بزنیم چه کسی اضافه نمی‌شود.
_PRIVACY_HIDDEN_STATUSES = frozenset({
    "recently", "last_week", "lastweek", "last_month", "lastmonth",
})

# اکانت رهاشده — حتی اگر اضافه شود عضو مرده است
_STALE_STATUSES = frozenset({"long_ago", "longago", "empty"})


def _status_token(u):
    """نام وضعیت last-seen را به صورت توکن تمیز برمی‌گرداند."""
    st = getattr(u, "status", None)
    if st is None:
        return ""
    raw = getattr(st, "value", None) or getattr(st, "name", None) or str(st)
    raw = str(raw)
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return raw.strip().lower()


def _is_unaddable_user(u, strict_privacy=True):
    """
    آیا این آبجکت User غیرقابل‌ادد است؟

    سیگنال‌های قطعی (همیشه):
      • حساب حذف‌شده / ربات / خودِ ما

    سیگنال‌های احتمالی (وقتی strict_privacy روشن است):
      • last-seen مخفی  → privacy بسته، تقریباً قطعاً اضافه نمی‌شود
      • last-seen خیلی قدیمی → اکانت رهاشده
      • scam / fake

    برمی‌گرداند (غیرقابل‌ادد؟, دلیل)
    """
    if u is None:
        return True, "پیدا نشد"
    if getattr(u, "is_deleted", False) or getattr(u, "deleted", False):
        return True, "حساب حذف‌شده"
    if getattr(u, "is_bot", False) or getattr(u, "bot", False):
        return True, "ربات"
    if getattr(u, "is_self", False):
        return True, "خودِ اکانت"

    if not strict_privacy:
        return False, ""

    if getattr(u, "is_scam", False) or getattr(u, "is_fake", False):
        return True, "اسکم/جعلی"

    token = _status_token(u)
    if token in _PRIVACY_HIDDEN_STATUSES:
        return True, "پرایوسی بسته (last-seen مخفی)"
    if token in _STALE_STATUSES:
        return True, "اکانت رهاشده"

    return False, ""


async def prefilter_unaddable(client, members, mark_blocked=True, log=None, strict_privacy=True):
    """
    قبل از شروع ادد، کاربرانی که قطعاً اضافه نمی‌شوند را کنار بگذار.

    برمی‌گرداند: (لیست_قابل_ادد، آمار)

    چرا مهم است: هر تلاش ناموفق ادد، بودجه‌ی نرخ اکانت را مصرف می‌کند.
    حذف حساب‌های پاک‌شده و ربات‌ها *قبل* از حلقه یعنی اکانت‌ها خیلی
    دیرتر به FloodWait می‌خورند.

    این تابع محافظه‌کار است: اگر بررسی شکست بخورد، کاربر را نگه می‌دارد
    (بهتر است یک تلاش اضافه شود تا اینکه کاربر سالمی حذف گردد).
    """
    import asyncio

    members = list(members or [])
    stats = {"checked": 0, "removed": 0, "kept": 0, "reasons": {}, "errors": 0}
    if not members:
        return members, stats

    app = getattr(client, "app", client)

    # لیست ممنوعه‌ی از قبل شناخته‌شده — بدون هیچ درخواست شبکه‌ای
    try:
        blocked = get_blocked_ids_cached() or set()
    except Exception:
        blocked = set()

    staged = []
    for m in members:
        try:
            uid = int(m.get("user_id") or m.get("id") or 0)
        except Exception:
            uid = 0
        if uid and uid in blocked:
            stats["removed"] += 1
            stats["reasons"]["در لیست ممنوعه"] = stats["reasons"].get("در لیست ممنوعه", 0) + 1
            continue
        staged.append((uid, m))

    keep = []
    consecutive_errors = 0
    for i in range(0, len(staged), _PREFILTER_BATCH):
        chunk = staged[i:i + _PREFILTER_BATCH]
        ids = [uid for uid, _ in chunk if uid]

        fetched = {}
        if ids:
            try:
                users = await app.get_users(ids)
                if not isinstance(users, (list, tuple)):
                    users = [users]
                for u in users:
                    try:
                        fetched[int(getattr(u, "id", 0))] = u
                    except Exception:
                        continue
                stats["checked"] += len(ids)
                consecutive_errors = 0
            except Exception as e:
                # بررسی شکست خورد → محافظه‌کارانه همه را نگه دار
                stats["errors"] += 1
                name = type(e).__name__

                # 🚨 مهم‌ترین محافظ (۱.۶.۴):
                # هر get_users یک درخواست است. با ۹٬۶۰۰ کاربر یعنی ۹۶
                # درخواست پشت سر هم — و ۶ اکانت این کار را هم‌زمان
                # می‌کردند. اکانت‌ها تمام بودجه‌ی نرخشان را «قبل از اولین
                # ادد» می‌سوزاندند و با صفر ادد PEER_FLOOD می‌گرفتند.
                #
                # اگر تلگرام دارد rate-limit می‌دهد، پیش‌فیلتر را کلاً
                # رها کن. پیش‌فیلتر فقط یک بهینه‌سازی است؛ ادد نکردن
                # بدتر از ادد کردن بدون پیش‌فیلتر است.
                if "Flood" in name or "flood" in str(e).lower():
                    if log:
                        log(
                            f"⛔ پیش‌فیلتر متوقف شد ({name}) — تلگرام محدود می‌کند. "
                            f"{len(staged) - i} کاربر بدون بررسی نگه داشته شدند تا "
                            "بودجه نرخ اکانت برای خودِ ادد بماند."
                        )
                    keep.extend(m for _, m in staged[i:])
                    stats["aborted"] = True
                    break

                if log:
                    log(f"پیش‌فیلتر برای این دسته ناموفق بود ({name}) — همه نگه داشته شدند")
                keep.extend(m for _, m in chunk)

                # سه شکست پیاپی = بی‌فایده است، ادامه نده
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    if log:
                        log("⛔ پیش‌فیلتر بعد از ۳ شکست پیاپی رها شد — بقیه بدون بررسی نگه داشته شدند")
                    keep.extend(m for _, m in staged[i + _PREFILTER_BATCH:])
                    stats["aborted"] = True
                    break
                await asyncio.sleep(0.5)
                continue

        for uid, m in chunk:
            u = fetched.get(uid)
            if u is None and ids:
                # تلگرام این کاربر را برنگرداند = وجود ندارد
                stats["removed"] += 1
                stats["reasons"]["پیدا نشد"] = stats["reasons"].get("پیدا نشد", 0) + 1
                if mark_blocked and uid:
                    try:
                        never_add_again(uid, "invalid")
                    except Exception:
                        pass
                continue

            bad, why = _is_unaddable_user(u, strict_privacy) if u is not None else (False, "")
            if bad:
                stats["removed"] += 1
                stats["reasons"][why] = stats["reasons"].get(why, 0) + 1
                if mark_blocked and uid:
                    # دلیل درست را ثبت کن تا بعداً قابل تفکیک باشد.
                    # «پرایوسی» ممکن است روزی عوض شود؛ «ربات/حذف‌شده» نه.
                    reason = "privacy" if "پرایوسی" in why else (
                        "stale" if "رهاشده" in why else "invalid"
                    )
                    try:
                        never_add_again(uid, reason)
                    except Exception:
                        pass
                continue

            keep.append(m)

        # وقفه‌ی کوتاه بین دسته‌ها — get_users ارزان است ولی رایگان نیست
        await asyncio.sleep(0.4)

    stats["kept"] = len(keep)
    return keep, stats


def format_prefilter_report(stats):
    """گزارش خوانا از نتیجه‌ی پیش‌فیلتر."""
    if not stats or not stats.get("removed"):
        return ""
    lines = [f"🔍 پیش‌فیلتر: {stats['removed']} نفر قبل از شروع کنار گذاشته شدند"]
    for reason, count in sorted(stats.get("reasons", {}).items(), key=lambda x: -x[1]):
        lines.append(f"   • {reason}: {count}")
    lines.append(f"   ✅ باقی‌مانده برای ادد: {stats.get('kept', 0)}")
    lines.append("   💡 این‌ها سهمیه اکانت را مصرف نکردند.")
    return "\n".join(lines)


def _looks_like_chat_ref(raw):
    s = (raw or "").strip()
    if not s:
        return False
    if s.lstrip("-").isdigit():
        return True
    if s.startswith("@") or "t.me/" in s or s.startswith("http"):
        return True
    # یوزرنیم عمومی تلگرام بدون @
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", s):
        return True
    return False


def normalize_chat_ref(raw):
    """لینک / یوزرنیم / آیدی را به چیزی که get_chat می‌فهمد تبدیل می‌کند."""
    s = (raw or "").strip()
    if not s:
        return s
    if s.lstrip("-").isdigit():
        return int(s)
    if "t.me/" in s:
        tail = s.split("t.me/", 1)[1].split("?")[0].strip("/")
        if not tail:
            return s
        if tail.startswith("+") or tail.startswith("joinchat/"):
            return s
        if tail.lstrip("-").isdigit():
            return int(tail)
        return "@" + tail.lstrip("@")
    if s.startswith("@"):
        return s
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", s):
        return "@" + s
    return s


def resolve_add_target(cfg=None):
    """مقصد ادد را از config واقعی می‌گیرد — group_id=0 را «خالی» حساب نمی‌کند اگر نام معتبر باشد.
    دیگر بی‌صدا به @gament_super_gp سوییچ نمی‌کند مگر اینکه هیچ مقصدی نباشد."""
    if cfg is None:
        cfg = _db.get_config() or {}
    try:
        gid = int(cfg.get("group_id") or 0)
    except Exception:
        gid = 0
    name = (cfg.get("group_name") or "").strip()
    if gid:
        return gid
    # لینک / @یوزرنیم / آیدی عددی در نام → همان مقصد
    explicit = bool(name) and (
        name.startswith("@") or "t.me/" in name or name.startswith("http") or name.lstrip("-").isdigit()
    )
    if explicit:
        return normalize_chat_ref(name)
    try:
        dest = _db.most_used_add_dest()
        if dest:
            return dest
    except Exception:
        pass
    if _looks_like_chat_ref(name):
        return normalize_chat_ref(name)
    try:
        import config as _cfg
        fallback = getattr(_cfg, "DEFAULT_TARGET_USERNAME", "gament_super_gp")
    except Exception:
        fallback = "gament_super_gp"
    return "@" + str(fallback).lstrip("@")


def persist_target_setting(raw):
    """ذخیره مقصد از مینی‌اپ: آیدی عددی را جدا از نام نگه می‌دارد."""
    ref = normalize_chat_ref(raw)
    if isinstance(ref, int):
        _db.set_config(ref, str(raw).strip() or str(ref), True)
        return ref
    name = str(ref or raw or "").strip()
    _db.set_config(0, name, True)
    return name


def invite_did_not_join(updates, uid):
    """اگر InviteToChannel کاربر را در missing_invitees برگرداند، ادد واقعی نبوده."""
    if updates is None:
        return False
    missing = getattr(updates, "missing_invitees", None) or []
    for item in missing:
        mid = getattr(item, "user_id", None)
        if mid is None:
            mid = getattr(getattr(item, "user", None), "id", None)
        try:
            if int(mid) == int(uid):
                return True
        except Exception:
            continue
    return False


# وضعیت‌هایی که یعنی کاربر واقعاً داخل گروه است
_JOINED_STATUSES = frozenset({
    "member", "administrator", "creator", "owner", "admin", "restricted",
})

# وضعیت‌هایی که یعنی کاربر داخل گروه نیست — حتی اگر API ادد «موفق» گفته باشد
_NOT_JOINED_STATUSES = frozenset({
    "left", "banned", "kicked", "restricted_banned",
})


def _normalize_member_status(mem):
    """
    استخراج نام وضعیت به‌صورت یک توکن تمیز.

    ⚠️ باگی که این تابع رفع می‌کند (نسخه ۱.۴.۲):
    Pyrogram وضعیت را به‌صورت enum برمی‌گرداند و str() آن می‌شود
    'ChatMemberStatus.LEFT'. کد قبلی چک می‌کرد آیا زیررشته 'member'
    در آن هست — و رشته 'ChatMemberStatus.LEFT' شامل 'MEMBER' است!
    نتیجه: کاربرانی که گروه را ترک کرده یا بن شده بودند «عضو» شمرده
    می‌شدند و آمار ادد کاملاً غیرواقعی می‌شد.
    """
    st = getattr(mem, "status", None)
    if st is None:
        return ""
    # enum پایتون: از .value یا .name استفاده کن، نه str(enum)
    raw = getattr(st, "value", None)
    if raw is None:
        raw = getattr(st, "name", None)
    if raw is None:
        raw = str(st)
        # 'ChatMemberStatus.LEFT' → 'LEFT'
        if "." in raw:
            raw = raw.rsplit(".", 1)[-1]
    return str(raw).strip().lower()


async def confirm_joined(client, chat_id, uid, retries=2, pause=1.1):
    """
    فقط وقتی True که کاربر واقعاً عضو گروه/کانال باشد.

    API تلگرام حتی وقتی کاربر به‌خاطر تنظیمات حریم خصوصی اضافه نشده
    ممکن است پاسخ موفق بدهد. تنها راه مطمئن، پرسیدن وضعیت عضویت است.
    """
    import asyncio
    app = getattr(client, "app", client)
    for i in range(max(1, retries)):
        try:
            mem = await app.get_chat_member(chat_id, int(uid))
            st = _normalize_member_status(mem)
            if st in _NOT_JOINED_STATUSES:
                return False          # قطعاً داخل گروه نیست — تلاش دوباره بی‌فایده
            if st in _JOINED_STATUSES:
                return True
        except Exception:
            pass
        if i + 1 < retries:
            await asyncio.sleep(pause)
    return False


def reset_cache_for_tests():
    """فقط برای تست‌ها"""
    global _BLOCKED_IDS_CACHE
    _BLOCKED_IDS_CACHE = {"ids": None, "ts": 0}


async def resolve_target_for_account(client, target_gid, username_hint=None):
    """
    مقصد ادد را برای یک اکانت یوزر resolve می‌کند.

    🚨 باگی که این تابع رفع می‌کند (نسخه ۱.۵.۴):

    کد قبلی مستقیم `client.get_chat(-1004316603248)` می‌زد. ولی در
    پایروگرام، یک اکانت فقط آی‌دی عددیِ چت‌هایی را می‌شناسد که قبلاً
    در session cache خودش دیده باشد. برای اکانتی که تازه لاگین کرده
    یا هرگز وارد آن گروه نشده، نتیجه همیشه:

        Peer id invalid: -1004316603248

    و هر ۸ ورکر بلافاصله می‌مردند — عملیات با صفر ادد تمام می‌شد در
    حالی که خود ربات به گروه دسترسی کامل داشت.

    ترتیب درست:
      ۱) یوزرنیم عمومی (@group) — همیشه بدون cache قابل resolve است
      ۲) آی‌دی عددی — اگر اکانت از قبل بشناسد
      ۳) پیوستن با لینک دعوت، اگر داده شده باشد

    برمی‌گرداند (dest_gid, target_peer, title) یا استثنا پرتاب می‌کند.
    """
    app = getattr(client, "app", client)
    attempts = []

    hint = (username_hint or "").strip()
    if hint:
        ref = normalize_chat_ref(hint)
        if isinstance(ref, str):
            attempts.append(ref)

    attempts.append(target_gid)

    last_err = None
    for ref in attempts:
        try:
            chat = await app.get_chat(ref)
        except Exception as e:
            last_err = e
            continue

        dest = getattr(chat, "id", None) or ref
        title = getattr(chat, "title", "") or str(dest)

        # peer را با همان مرجعی بگیر که جواب داد. اگر اکانت آی‌دی عددی
        # را نشناسد، resolve_peer(dest) دوباره PeerIdInvalid می‌دهد و
        # همان باگ برمی‌گردد — پس هر دو را امتحان کن.
        for peer_ref in (ref, dest):
            try:
                peer = await app.resolve_peer(peer_ref)
                return dest, peer, title
            except Exception as e:
                last_err = e

    # آخرین تلاش: شاید peer در cache باشد ولی get_chat شکست خورده
    try:
        peer = await app.resolve_peer(target_gid)
        return target_gid, peer, str(target_gid)
    except Exception:
        pass

    raise last_err or RuntimeError(f"مقصد {target_gid} قابل resolve نیست")


def target_username_hint(cfg=None):
    """
    یوزرنیم عمومی مقصد را از config درمی‌آورد — کلید resolve شدن گروه
    برای اکانت‌هایی که آن را در cache ندارند.
    """
    if cfg is None:
        try:
            cfg = _db.get_config() or {}
        except Exception:
            cfg = {}
    name = (cfg.get("group_name") or "").strip()
    if name and (name.startswith("@") or "t.me/" in name or name.startswith("http")):
        ref = normalize_chat_ref(name)
        if isinstance(ref, str):
            return ref
    try:
        import config as _cfg
        fb = getattr(_cfg, "DEFAULT_TARGET_USERNAME", "") or ""
    except Exception:
        fb = ""
    fb = fb.strip().lstrip("@")
    return f"@{fb}" if fb else None


async def get_target_title(client, target_gid, username_hint=None, default="گروه مقصد"):
    """
    فقط عنوان گروه مقصد را می‌گیرد — برای نمایش.

    مثل resolve_target_for_account اول یوزرنیم را امتحان می‌کند، چون
    اکانتی که گروه را در cache ندارد با آی‌دی عددی PeerIdInvalid می‌گیرد.
    هرگز استثنا پرتاب نمی‌کند؛ در بدترین حالت مقدار پیش‌فرض برمی‌گردد.
    """
    app = getattr(client, "app", client)
    refs = []
    hint = (username_hint or "").strip()
    if hint:
        ref = normalize_chat_ref(hint)
        if isinstance(ref, str):
            refs.append(ref)
    refs.append(target_gid)

    for ref in refs:
        try:
            chat = await app.get_chat(ref)
            title = getattr(chat, "title", None)
            if title:
                return title
        except Exception:
            continue
    return default


# ─────────────────── بک‌آف PEER_FLOOD ───────────────────

# ⚠️ درس گران (نسخه ۱.۵.۸):
# نسخه ۱.۵.۵ در هر PEER_FLOOD اکانت را ۲۴ ساعت کنار می‌گذاشت. آن عدد
# را ما هاردکد کرده بودیم — تلگرام هیچ مدتی اعلام نمی‌کند. نتیجه:
# اکانتی که فقط ۱ نفر ادد کرده بود، ۲۴ ساعت از دست می‌رفت.
#
# PEER_FLOOD یعنی «فعلاً آهسته‌تر» نه «اکانت سوخت». معمولاً با چند ده
# دقیقه استراحت برطرف می‌شود. پس بک‌آف تدریجی بر اساس تعداد دفعات:

# ⚠️ تلگرام برای PEER_FLOOD هیچ مدتی اعلام نمی‌کند و هیچ APIی هم برای
# پرسیدن «کِی آزاد می‌شوم؟» ندارد. پس هر عددی که اینجا بنویسیم حدس است.
#
# نسخه ۱.۵.۸ حدسِ بد زد (۲۴ ساعت) و اکانت سالم را یک روز بیکار کرد.
# راه درست: بازه‌های **کوتاه** و تست مکرر — اولین باری که ادد موفق شد،
# یعنی تلگرام آزادش کرده. عقب‌نشینی فقط برای اینکه بی‌وقفه به دیوار
# نکوبیم، نه به‌عنوان جریمه.
_PEER_FLOOD_BACKOFF = (
    3 * 60,       # بار اول: ۳ دقیقه
    8 * 60,       # بار دوم: ۸ دقیقه
    15 * 60,      # بار سوم: ۱۵ دقیقه
    30 * 60,      # بار چهارم: ۳۰ دقیقه
)
_PEER_FLOOD_MAX = 45 * 60     # هرگز بیش از ۴۵ دقیقه حدس نمی‌زنیم


def peer_flood_cooldown(strike_count):
    """
    مدت استراحت بعد از PEER_FLOOD بر اساس تعداد دفعات پشت سر هم.

    strike_count از ۱ شروع می‌شود (اولین بار).
    """
    try:
        n = max(1, int(strike_count))
    except Exception:
        n = 1
    if n <= len(_PEER_FLOOD_BACKOFF):
        return _PEER_FLOOD_BACKOFF[n - 1]
    return _PEER_FLOOD_MAX


def describe_cooldown(seconds):
    """توضیح فارسی خوانا از مدت استراحت."""
    s = int(seconds)
    if s < 3600:
        return f"{s // 60} دقیقه"
    h = s / 3600
    if h < 24:
        return f"{int(h)} ساعت" if h == int(h) else f"{h:.1f} ساعت"
    return f"{int(h // 24)} روز"


def warmup_cap(historical_added, mode_cap=None):
    """
    سقف ادد این اجرا.

    🚫 پیش‌فرض: **بدون سقف مصنوعی** (فقط mode_cap).

    نسخه ۱.۵.۸ یک warm-up اختراع کرد که اکانت‌ها را به ۱۲ ادد محدود
    می‌کرد. آن محدودیتِ ما بود نه تلگرام، و باعث می‌شد اکانت‌های سالم
    بی‌دلیل بیکار بمانند. مالک صریحاً خواست اکانت‌ها تا حداکثر ظرفیت
    واقعی کار کنند و فقط وقتی تلگرام جلویشان را گرفت صبر کنیم.

    با `WARMUP_ENABLED=true` می‌توان دوباره فعالش کرد.
    """
    if not getattr(config, "WARMUP_ENABLED", False):
        return int(mode_cap) if mode_cap else config.MAX_ADD_PER_ACCOUNT

    stages = getattr(config, "WARMUP_STAGES", None)
    if not stages:
        return int(mode_cap) if mode_cap else config.MAX_ADD_PER_ACCOUNT

    try:
        n = max(0, int(historical_added or 0))
    except Exception:
        n = 0
    cap = stages[0][1]
    for threshold, allowed in stages:
        if n >= threshold:
            cap = allowed
    if mode_cap:
        cap = min(cap, int(mode_cap))
    return cap


def stagger_delay(index, add_mode="safe"):
    """
    تأخیر شروع اکانت شماره index تا همه با هم هجوم نبرند.

    اگر ۸ اکانت دقیقاً هم‌زمان شروع کنند، تلگرام الگوی هماهنگ
    تشخیص می‌دهد.
    """
    lo, hi = config.STAGGER_START.get(add_mode, config.STAGGER_START["safe"])
    if hi <= 0:
        return 0.0
    base = index * random.uniform(lo, hi)
    return base + random.uniform(0, max(1.0, lo))


# ─────────────── سقف انتظار درجا برای FloodWait ───────────────

# تلگرام گاهی FloodWait چند ساعته می‌دهد (دیده شده: ۸۲۲۱۳ ثانیه ≈ ۲۳ ساعت).
# خوابیدنِ درجا به آن اندازه یعنی ورکر تا فردا قفل می‌ماند و منابع را
# نگه می‌دارد. راه درست: انتظارهای کوتاه را همان‌جا صبر کن، انتظارهای
# طولانی را با ثبت مهلت در دیتابیس رها کن تا اجرای بعدی سراغش برود.
MAX_INLINE_FLOODWAIT = 300     # ۵ دقیقه


def should_wait_inline(seconds):
    """آیا این FloodWait آن‌قدر کوتاه هست که همان‌جا صبر کنیم؟"""
    try:
        return int(seconds) <= MAX_INLINE_FLOODWAIT
    except Exception:
        return False
