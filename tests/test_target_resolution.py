"""
resolve کردن گروه مقصد برای اکانت‌های یوزر.

🚨 ریشه باگ «ادد صفر با اینکه ربات ادمین است» (۱.۵.۴):

لاگ زنده Render نشان داد هر ۸ ورکر بلافاصله می‌میرند:

    ❌ Worker error on +989913928426: Peer id invalid: -1004316603248
    ❌ Worker error on +989038511300: Peer id invalid: -1004316603248
    ... (هر ۸ اکانت)

کد `client.get_chat(-1004316603248)` می‌زد. ولی در پایروگرام یک اکانت
فقط آی‌دی عددی چت‌هایی را می‌شناسد که در session cache خودش دیده باشد.
اکانتی که هرگز وارد آن گروه نشده، همیشه PeerIdInvalid می‌گیرد — حتی
وقتی ربات دسترسی کامل ادمین دارد.

راه درست: اول یوزرنیم عمومی (@group) که بدون cache resolve می‌شود.
"""
import pathlib
import re

import pytest

from add_engine import resolve_target_for_account, target_username_hint

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Chat:
    def __init__(self, cid, title="Gament Gp"):
        self.id = cid
        self.title = title


class _App:
    """کلاینت ساختگی که رفتار واقعی پایروگرام را تقلید می‌کند."""

    def __init__(self, knows_numeric=False, username="@gament_super_gp"):
        self.knows_numeric = knows_numeric
        self.username = username
        self.calls = []

    async def get_chat(self, ref):
        self.calls.append(ref)
        if isinstance(ref, str):
            if ref == self.username:
                return _Chat(-1004316603248)
            raise ValueError(f"Username not found: {ref}")
        if self.knows_numeric:
            return _Chat(ref)
        raise ValueError(f"Peer id invalid: {ref}")

    async def resolve_peer(self, ref):
        if isinstance(ref, int) and not self.knows_numeric:
            raise ValueError(f"Peer id invalid: {ref}")
        return f"peer:{ref}"


async def test_username_resolves_when_numeric_id_fails():
    """
    دقیقاً سناریوی باگ: اکانت آی‌دی عددی را نمی‌شناسد ولی یوزرنیم
    عمومی همیشه کار می‌کند.
    """
    app = _App(knows_numeric=False)
    dest, peer, title = await resolve_target_for_account(
        app, -1004316603248, "@gament_super_gp"
    )
    assert dest == -1004316603248
    # peer از طریق یوزرنیم گرفته می‌شود — همان چیزی که اکانت می‌شناسد
    assert peer == "peer:@gament_super_gp"
    assert app.calls[0] == "@gament_super_gp", "یوزرنیم باید اول امتحان شود"


async def test_numeric_still_works_when_account_knows_it():
    app = _App(knows_numeric=True)
    dest, peer, _ = await resolve_target_for_account(app, -1004316603248, None)
    assert dest == -1004316603248


async def test_username_tried_before_numeric():
    """ترتیب مهم است — آی‌دی عددی اول یعنی همان باگ قبلی."""
    app = _App(knows_numeric=True)
    await resolve_target_for_account(app, -1004316603248, "@gament_super_gp")
    assert isinstance(app.calls[0], str), "یوزرنیم باید قبل از عدد امتحان شود"


async def test_raises_when_nothing_resolves():
    app = _App(knows_numeric=False)
    with pytest.raises(Exception):
        await resolve_target_for_account(app, -1004316603248, "@does_not_exist")


async def test_accepts_link_form():
    app = _App(knows_numeric=False)
    dest, _, _ = await resolve_target_for_account(
        app, -1004316603248, "https://t.me/gament_super_gp"
    )
    assert dest == -1004316603248


# ───────────── تضمین اینکه همه مسیرها از آن استفاده می‌کنند ─────────────

def test_no_raw_numeric_resolve_on_target():
    """
    هیچ مسیری نباید مستقیم روی مقصد resolve_peer/get_chat عددی بزند —
    همان الگویی که هر ۸ ورکر را کشت.
    """
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    bad = []
    for i, line in enumerate(src.split("\n"), 1):
        st = line.strip()
        if st.startswith("#"):
            continue
        if re.search(r"resolve_peer\((?:target_gid|dest_gid)\)", st):
            bad.append(f"خط {i}: {st[:70]}")
        if re.search(r"get_chat\(target_gid\)", st):
            bad.append(f"خط {i}: {st[:70]}")
    assert not bad, (
        "resolve مستقیم با آی‌دی عددی روی مقصد — اکانت‌هایی که گروه را در "
        "cache ندارند PeerIdInvalid می‌گیرند:\n  " + "\n  ".join(bad)
    )


def test_all_add_paths_use_the_helper():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert src.count("resolve_target_for_account") >= 5, (
        "هر ۵ مسیر ادد باید از هلپر resolve استفاده کنند"
    )


def test_worker_reports_resolve_failure_to_ui():
    """
    اگر resolve شکست خورد کاربر باید در مینی‌اپ ببیند، نه اینکه
    عملیات بی‌صدا با صفر نتیجه تمام شود.
    """
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "نتوانست گروه مقصد را پیدا کند" in src
