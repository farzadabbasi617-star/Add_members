"""
منشأ هر محدودیت: تلگرام یا ما؟

مالک دید که چند اکانت «۱۴۰۰ دقیقه محدود» هستند و پرسید آیا این را ما
اعمال کرده‌ایم. بررسی لاگ زنده نشان داد:

    ⏱️ [+989377649452] FloodWait 82213s   ← ۲۲.۸ ساعت، از خود تلگرام
    ⏱️ [+989302206873] FloodWait 82147s   ← ۲۲.۸ ساعت، از خود تلگرام
    ⏱️ [+989034694783] FloodWait 443s     ← ۷ دقیقه
    ⏱️ [+989913928426] FloodWait 440s     ← ۷ دقیقه

اعداد برای هر اکانت متفاوت‌اند و ما هیچ‌جا چنین مقادیری نمی‌سازیم —
پس واقعی‌اند و باید رعایت شوند.

قاعده‌ی نهایی:
  • FloodWait  → تلگرام مقدار دقیق می‌دهد ⇒ دقیقاً همان، بدون دستکاری
  • PEER_FLOOD → تلگرام چیزی نمی‌گوید ⇒ صبر کوتاه + تست دوباره
  • بقیه       → هیچ مهلتی اختراع نمی‌کنیم
"""
import pathlib
import re

from add_engine import should_wait_inline, MAX_INLINE_FLOODWAIT

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_floodwait_uses_telegram_value_verbatim():
    """مقدار تلگرام نباید ضرب/تقسیم یا جایگزین شود."""
    hits = re.findall(r"limitation_until=int\(time\.time\(\)\) \+ ([\w.]+)", BOT)
    assert "fw.value" in hits, "FloodWait باید دقیقاً مقدار تلگرام را ثبت کند"
    for h in hits:
        assert h in ("fw.value", "cooldown"), (
            f"مهلت از منبع نامعتبر: {h} — فقط مقدار تلگرام یا بک‌آف PEER_FLOOD"
        )


def test_no_invented_fixed_deadlines():
    """
    هیچ عدد ثابتی نباید به‌عنوان مهلت محدودیت نوشته شود.
    (۳۶۰۰ برای WriteForbidden و ۸۶۴۰۰ برای PeerFlood حذف شدند.)
    """
    bad = re.findall(r"limitation_until=int\(time\.time\(\)\) \+ (\d+)", BOT)
    assert not bad, f"مهلت هاردکد پیدا شد: {bad}"


def test_write_forbidden_records_no_deadline():
    i = BOT.index('if "CHAT_WRITE_FORBIDDEN" in err')
    window = BOT[i:i + 2000]
    assert "limitation_until" not in window, (
        "برای این خطا تلگرام مدتی نداده — نباید مهلت بسازیم"
    )


# ───────── FloodWait طولانی نباید ورکر را قفل کند ─────────

def test_long_floodwait_is_not_slept_inline():
    """
    دیده شده تلگرام ۸۲۲۱۳ ثانیه (۲۳ ساعت) می‌دهد. خوابیدن درجا یعنی
    ورکر تا فردا قفل بماند.
    """
    assert should_wait_inline(60) is True
    assert should_wait_inline(300) is True
    assert should_wait_inline(82213) is False
    assert MAX_INLINE_FLOODWAIT <= 600


def test_no_unguarded_long_sleep_on_floodwait():
    for m in re.finditer(r"await asyncio\.sleep\(fw\.value[^)]*\)", BOT):
        before = BOT[max(0, m.start() - 400):m.start()]
        assert "should_wait_inline" in before or "_swi(" in before, (
            "sleep روی fw.value بدون بررسی طولانی بودن — ورکر ساعت‌ها قفل می‌شود"
        )


def test_parallel_worker_does_not_sleep_on_floodwait():
    """مسیر موازی باید مهلت را ثبت کند و برود، نه اینکه بخوابد."""
    lines = BOT.split("\n")
    st = next(i for i, l in enumerate(lines)
              if l.startswith("async def _execute_parallel_add("))
    body = []
    for l in lines[st + 1:]:
        if l and not l[0].isspace():
            break
        body.append(l)
    b = "\n".join(body)
    i = b.index("except FloodWait as fw:")
    window = b[i:i + 500]
    assert "asyncio.sleep(fw.value" not in window
    assert "limitation_until" in window


# ───────── PEER_FLOOD: حدسِ کوتاه ─────────

def test_peer_flood_guess_stays_short():
    from add_engine import peer_flood_cooldown
    assert peer_flood_cooldown(99) <= 3600, (
        "PEER_FLOOD مدت ندارد — حدس ما نباید بیش از یک ساعت باشد"
    )


# ───────── سایه‌اندازی روی config ─────────

def test_no_local_shadowing_of_config_caps():
    """
    🚨 باگی که مالک حس کرد و درست هم بود (۱.۶.۲):

    نسخه ۱.۶.۰ سقف را در `config.py` به ۱۰۰۰ برد، ولی داخل
    `_execute_parallel_add` این خط بود:

        MODE_DAILY_CAP = {"ultra": 50, "fast": 100, "safe": 100}

    یک متغیر محلی که مقدار ایمپورت‌شده از config را سایه می‌انداخت.
    نتیجه: سقف ۱۰۰ همچنان اعمال می‌شد و «برداشتن محدودیت» بی‌اثر بود.

    این تست هر انتساب محلی به نام‌های تنظیماتی را ممنوع می‌کند.
    """
    import re

    watched = (
        "MODE_DAILY_CAP", "MAX_ADD_PER_ACCOUNT", "DELAY_RANGES",
        "BREAK_RANGES", "WARMUP_STAGES", "STAGGER_START",
    )
    offenders = []
    for fname in ("bot.py", "add_engine.py", "web_app.py", "account_doctor.py"):
        src = (ROOT / fname).read_text(encoding="utf-8")
        for i, line in enumerate(src.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "import" in stripped:
                continue
            indent = len(line) - len(line.lstrip())
            if indent == 0:
                continue          # تعریف سراسری در خود config مجاز است
            for name in watched:
                if re.match(rf"{name}\s*=[^=]", stripped):
                    offenders.append(f"{fname}:{i} → {stripped[:70]}")

    assert not offenders, (
        "مقدار config به‌صورت محلی بازنویسی شده — تغییر تنظیمات بی‌اثر می‌شود:\n  "
        + "\n  ".join(offenders)
    )


def test_no_hardcoded_hundred_cap_anywhere():
    """سقف ۱۰۰ نباید در منطق تصمیم‌گیری هاردکد باشد."""
    import re

    offenders = []
    for fname in ("bot.py", "account_doctor.py", "add_engine.py"):
        src = (ROOT / fname).read_text(encoding="utf-8")
        for i, line in enumerate(src.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # مقایسه added با عدد ثابت
            if re.search(r"added[\"'\]\s\w]*\)?\s*>=\s*100\b", stripped):
                offenders.append(f"{fname}:{i} → {stripped[:70]}")

    assert not offenders, (
        "سقف ۱۰۰ هاردکد شده — باید از config.MAX_ADD_PER_ACCOUNT بیاید:\n  "
        + "\n  ".join(offenders)
    )
