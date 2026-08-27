"""همه‌ی ۱۲ روش استخراج باید سالم و *قابل‌دسترس* باشند.

مالک صریحاً گفت: «همه‌ی ۱۲ روش بمانند و بهتر شوند» و ساده‌سازی را رد
کرد. ولی ممیزی نشان داد وضعیت واقعی این بود:

    ۷ روش از ۱۲ تا اصلاً هرگز صدا زده نمی‌شدند  (کد مرده)
    ۴ روش به API‌هایی صدا می‌زدند که وجود ندارند (شکست خاموش)

یعنی عملاً فقط ۵ روش کار می‌کرد و کاربر خبر نداشت، چون همه‌ی خطاها در
`except: pass` بلعیده می‌شدند.

این فایل هر دو حالت را قفل می‌کند.
"""
import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = (ROOT / "attacker.py").read_text(encoding="utf-8")
TREE = ast.parse(RAW)


def _code_only(text):
    """کامنت‌ها و داک‌استرینگ‌ها را با فاصله جایگزین می‌کند.

    چیدمان و شماره‌ی ستون‌ها دست‌نخورده می‌ماند (برخلاف untokenize که
    کد را بازقالب‌بندی می‌کند و جستجوی زیررشته‌ای را می‌شکند)، ولی متنِ
    توضیحات دیگر در نتیجه نیست.

    چرا لازم است: داک‌استرینگ‌های ما عمداً نام API‌های شکسته را نقل
    می‌کنند تا آینده بداند چرا حذف شدند. آن نقل‌قول‌ها نباید تستِ
    «این API نباید صدا زده شود» را قرمز کنند.
    """
    import io
    import tokenize

    lines = text.splitlines(keepends=True)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))

    def off(row, col):
        return starts[row - 1] + col

    buf = list(text)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError):
        return text

    prev = None
    for tok in toks:
        blank = tok.type == tokenize.COMMENT or (
            tok.type == tokenize.STRING and (
                prev is None or prev.type in (
                    tokenize.INDENT, tokenize.NEWLINE, tokenize.NL,
                    tokenize.DEDENT) or prev.string == ":")
        )
        if blank:
            s, e = off(*tok.start), off(*tok.end)
            for i in range(s, e):
                if buf[i] != "\n":
                    buf[i] = " "
        if tok.type not in (tokenize.NL, tokenize.COMMENT):
            prev = tok
    return "".join(buf)


SRC = _code_only(RAW)
CLS = next(n for n in ast.walk(TREE)
           if isinstance(n, ast.ClassDef) and n.name == "AdvancedScraper")
METHODS = [n for n in CLS.body
           if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("scrape_")]
NAMES = [m.name for m in METHODS]


def _src(name):
    return _code_only(ast.get_source_segment(RAW, next(n for n in CLS.body
                                            if isinstance(n, ast.AsyncFunctionDef)
                                            and n.name == name)))


TWELVE = [
    "scrape_direct_paginated", "scrape_full_history", "scrape_join_events",
    "scrape_reactions_dedicated", "scrape_channel_posts",
    "scrape_imported_contacts", "scrape_global_search", "scrape_deep_history",
    "scrape_aggressive_pagination", "scrape_group_intersection",
    "scrape_forwarded_messages", "scrape_mtproto_super_resolve",
]


def test_all_twelve_methods_still_exist():
    """مالک ساده‌سازی را رد کرد — هیچ روشی نباید حذف شود.

    بهینه‌سازی چند روش را به «عبور واحد» واگذار کرد، ولی خودِ روش‌ها
    به‌عنوان نقطه‌ی ورود مستقل باقی مانده‌اند.
    """
    missing = [m for m in TWELVE if m not in NAMES]
    assert not missing, f"این روش‌ها حذف شده‌اند: {missing}"


def test_delegating_methods_still_reach_a_real_implementation():
    """واگذاری نباید به حلقه یا بن‌بست ختم شود."""
    for name in ("scrape_full_history", "scrape_join_events",
                 "scrape_reactions_dedicated", "scrape_channel_posts"):
        body = _src(name)
        assert "scrape_unified_history" in body, f"{name} به جایی وصل نیست"
        assert "get_chat_history" not in body, (
            f"{name} نباید مستقیم تاریخچه بخواند — کار عبور واحد است")


def test_no_scrape_method_is_dead_code():
    """⚠️ هسته‌ی مشکل: ۷ روش هرگز صدا زده نمی‌شدند.

    از جمله scrape_group_intersection که در داک‌استرینگش وعده‌ی «تا ۹۰٪
    اعضای مخفی» می‌داد ولی هیچ‌وقت اجرا نمی‌شد.
    """
    reachable = set()
    for caller in ("_run_strategy", "run_full_scrape", "scan_all_chats"):
        reachable |= set(re.findall(r"self\.(scrape_\w+)", _src(caller)))
    # روش‌های واگذارشده از طریق عبور واحد پوشش داده می‌شوند
    delegating = {n for n in NAMES
                  if "scrape_unified_history" in _src(n) and n != "scrape_unified_history"}
    dead = sorted(set(NAMES) - reachable - delegating)
    assert not dead, f"این روش‌ها کد مرده‌اند و هرگز اجرا نمی‌شوند: {dead}"


# ------------------------------------------- API‌های ناموجود

@pytest.mark.parametrize("missing", [
    "get_message_reactions",
    "get_poll_voters",
])
def test_no_call_to_nonexistent_client_helper(missing):
    """این هلپرها در Pyrogram 2.0.106 وجود ندارند.

    فراخوانی‌شان AttributeError می‌داد که بلافاصله بلعیده می‌شد ⇒ روش
    «موفق» گزارش می‌شد با صفر نتیجه.
    """
    assert f"self.app.{missing}(" not in SRC, (
        f"{missing} وجود ندارد — باید از scrape_api استفاده شود"
    )


def test_no_functions_dot_types_path():
    """`raw.functions.types` مسیر غلط است؛ تایپ‌ها زیر `raw.types` هستند."""
    assert not re.search(r"raw_fns\.types\.|functions\.types\.", SRC)


def test_entity_type_compared_against_enum_not_string():
    """⚠️ `ent.type in ("mention","text_mention")` همیشه False بود.

    ent.type یک enum است. مقایسه با رشته هرگز صادق نمی‌شد، پس تمام
    منشن‌ها بی‌صدا رد می‌شدند.
    """
    assert '"mention", "text_mention"' not in SRC
    assert "_USER_ENTITY_TYPES" in SRC
    from pyrogram.enums import MessageEntityType
    assert MessageEntityType.MENTION not in ("mention", "text_mention"), (
        "اثبات اینکه مقایسه‌ی رشته‌ای واقعاً شکست می‌خورد"
    )


def test_no_zero_access_hash_input_user():
    """access_hash=0 تقریباً همیشه نامعتبر است — درخواست هدررفته."""
    assert "access_hash=0" not in SRC


# ------------------------------------------- باگ‌های منطقی

def test_checked_members_initialised_in_constructor():
    """قبلاً *قبل از* ساخته‌شدن خوانده می‌شد ⇒ AttributeError."""
    init = ast.get_source_segment(SRC, next(
        n for n in CLS.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"))
    assert "self._checked_members" in init

    body = _src("scrape_mtproto_super_resolve")
    read = body.index("uid not in self._checked_members")
    guard = body.index("hasattr(self, '_checked_members')")
    assert guard < read, "محافظ باید قبل از خواندن بیاید"


def test_no_shuffle_on_a_throwaway_slice():
    """⚠️ `random.shuffle(x[:10])` یک no-op کامل است.

    برش یک کپی می‌سازد؛ شافل روی کپی اعمال و دور ریخته می‌شود. لیست
    اصلی هرگز تغییر نمی‌کند. دو جا این اشتباه بود.
    """
    assert not re.search(r"shuffle\(\s*\w+\[[^\]]*:[^\]]*\]\s*\)", SRC), (
        "شافل روی برش بی‌اثر است — باید روی خود لیست اعمال شود"
    )


def test_deep_history_starts_where_the_first_pass_stopped():
    """بازنویسی: به‌جای شافل کردن offsetها، از سقف عبور اول ادامه می‌دهد.

    شافل در نسخه‌ی قبلی هم بی‌اثر بود (روی برش اعمال می‌شد) و هم بی‌فایده:
    هدف رسیدن به پیام‌های *قدیمی‌تر* است، نه ترتیب تصادفی. حالا offset
    از جایی شروع می‌شود که عبور واحد تمام کرد، پس هیچ پیامی دو بار
    دانلود نمی‌شود.
    """
    body = _src("scrape_deep_history")
    assert "start.messages_seen if start else 0" in body
    assert "offset + scanned" in body


# ------------------------------------------- کیفیت اجرا

def test_failures_are_reported_not_silently_swallowed():
    """`except: pass` دلیل اصلی ۹ ماه ندیدن این باگ‌ها بود."""
    runner = _src("_safe")
    assert "print" in runner, "شکست هر روش باید چاپ شود"
    assert "FloodWait" in runner


def test_strategy_is_layered_by_cost():
    """روش‌های گران نباید بی‌دلیل اجرا شوند — ریسک فلود."""
    body = _src("_run_strategy")
    cheap = body.index("scrape_direct_paginated")
    mid = body.index("scrape_aggressive_pagination")
    pricey = body.index("scrape_group_intersection")
    assert cheap < mid < pricey, "ترتیب لایه‌ها باید از ارزان به گران باشد"
    assert "deep" in body, "لایه‌ی گران باید پشت پرچم deep باشد"


def test_expensive_layer_is_gated_behind_deep_flag():
    body = _src("_run_strategy")
    i = body.index("scrape_group_intersection")
    guard = body.rindex("if not deep:", 0, i)
    assert "return" in body[guard:i]


def test_aggressive_pagination_uses_server_side_member_search():
    """بهبود اصلی: جستجو در لیست اعضا، نه جستجوی سراسری + تأیید تک‌تک.

    نسخه‌ی قبلی تا ~۵۰٬۰۰۰ درخواست تأیید می‌فرستاد برای کاربرانی که
    عمدتاً عضو گروه نبودند.
    """
    body = _src("scrape_aggressive_pagination")
    assert "iter_participants_search" in body
    assert "get_chat_member" not in body, (
        "نتایج جستجوی سمت سرور قطعاً عضو هستند — تأیید لازم نیست"
    )
    assert "contacts.Search" not in body


def test_reaction_harvest_avoids_per_user_lookup():
    """آبجکت کاربر در همان پاسخ می‌آید؛ get_users جداگانه هدررفت است."""
    body = _src("_harvest_reactions")
    assert "get_users" not in body
    assert "iter_message_reactors" in body


def test_prefix_list_has_no_duplicates_and_is_ordered():
    import importlib
    import sys
    sys.path.insert(0, str(ROOT))
    attacker = importlib.import_module("attacker")
    p = attacker.AdvancedScraper._build_search_prefixes()
    assert len(p) == len(set(p)), "پیشوند تکراری = درخواست هدررفته"
    assert p[0] == "a" and len(p) > 100
    assert any(len(x) == 2 for x in p), "ترکیب‌های دوحرفی برای گروه‌های بزرگ لازم‌اند"
