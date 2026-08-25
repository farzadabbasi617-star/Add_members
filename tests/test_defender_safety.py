"""
نگهبان رفتار دفاعی — جلوگیری از آزار اعضای گروه.

پس‌زمینه (نسخه ۱.۴.۱):
هانی‌پات نسخه قبلی هر ۱۰ دقیقه پیامی با کد قابل‌مشاهده `hp_trap_123456`
در گروه می‌کاشت. اعضا فکر کردند گروه هک/اسکم شده و بسیاری گروه را ترک
کردند. بدتر اینکه هر کس آن کد را کپی می‌کرد یا درباره‌اش سؤال می‌پرسید،
فوراً بن می‌شد.

این تست‌ها نمی‌گذارند آن رفتار برگردد.
"""
import inspect
import os
import re

import pytest

import defender
from defender import AdvancedDefender


class _FakeApp:
    """اپ ساختگی که هر پیام ارسالی به گروه را ثبت می‌کند."""

    def __init__(self):
        self.sent_to_group = []
        self.sent_to_admin = []

    async def send_message(self, chat_id, text, **kwargs):
        target = self.sent_to_admin if chat_id == 999 else self.sent_to_group
        target.append(text)

        class _Msg:
            id = 1

            async def delete(self_inner):
                return True

        return _Msg()

    async def get_chat_member(self, *a, **kw):
        raise RuntimeError("no such member")


def _make(monkeypatch, mode="off"):
    monkeypatch.setenv("HONEYPOT_MODE", mode)
    monkeypatch.setattr(defender.db, "get_config", lambda *a, **kw: "")
    monkeypatch.setattr(defender.db, "set_config", lambda *a, **kw: None)
    app = _FakeApp()
    return AdvancedDefender(app, group_id=-100123, admin_id=999), app


def test_honeypot_is_off_by_default(monkeypatch):
    """پیش‌فرض باید خاموش باشد — تله نباید بدون تصمیم صریح مالک کاشته شود."""
    monkeypatch.delenv("HONEYPOT_MODE", raising=False)
    monkeypatch.setattr(defender.db, "get_config", lambda *a, **kw: "")
    d = AdvancedDefender(_FakeApp(), group_id=-100123, admin_id=999)
    assert d.HONEYPOT_MODE == "off"


@pytest.mark.asyncio
async def test_honeypot_off_sends_nothing_to_group(monkeypatch):
    """در حالت خاموش هیچ پیامی نباید وارد گروه شود."""
    d, app = _make(monkeypatch, mode="off")
    await d.deploy_honeypot()
    assert app.sent_to_group == [], (
        f"در حالت خاموش پیام به گروه ارسال شد: {app.sent_to_group}"
    )


@pytest.mark.asyncio
async def test_trap_message_has_no_human_readable_text(monkeypatch):
    """
    اگر تله فعال شود، متنِ ارسالی نباید هیچ کاراکتر قابل‌خواندنی داشته باشد.

    این دقیقاً همان چیزی است که کاربران را ترساند: کد `hp_trap_123456`
    داخل تگ <code> برای همه دیده می‌شد.
    """
    d, app = _make(monkeypatch, mode="invisible_link")
    await d.deploy_honeypot()

    assert len(app.sent_to_group) == 1
    sent = app.sent_to_group[0]

    # متن قابل مشاهده = هر چیزی خارج از تگ‌های HTML و کاراکترهای نامرئی
    visible = re.sub(r"<[^>]+>", "", sent)
    visible = visible.replace("\u200b", "").replace("\u200c", "")
    visible = visible.replace("\u200d", "").replace("\ufeff", "")
    visible = visible.replace("\u2060", "").strip()

    assert visible == "", (
        f"تله متن قابل مشاهده دارد: {visible!r} — کاربران این را می‌بینند "
        "و فکر می‌کنند گروه اسکم است."
    )

    # مطمئن شو کد تله داخل تگ <code> نیست (خطای نسخه قبلی)
    assert "<code>" not in sent, (
        "کد تله داخل <code> است و برای همه اعضا نمایش داده می‌شود"
    )


@pytest.mark.asyncio
async def test_mentioning_trap_token_does_not_ban(monkeypatch):
    """
    کاربری که درباره کد تله سؤال می‌کند نباید بن شود.

    قبلاً هر پیامی که رشته "hp_trap_" داشت → بن فوری.
    """
    d, app = _make(monkeypatch, mode="invisible_link")

    banned = []
    d.ban = lambda uid: banned.append(uid)

    class _User:
        id = 555
        first_name = "علی"
        last_name = ""
        username = "ali"

    class _Msg:
        from_user = _User()
        text = "بچه‌ها این hp_trap_123456 که تو گروه اومد چیه؟ گروه هک شده؟"
        caption = None

        async def delete(self):
            return True

    await d.monitor_message(_Msg())
    assert banned == [], "کاربری که فقط درباره تله پرسید بن شد!"


@pytest.mark.asyncio
async def test_unknown_trap_token_is_ignored(monkeypatch):
    """توکنی که خودمان نکاشته‌ایم نباید هیچ واکنشی ایجاد کند."""
    d, app = _make(monkeypatch, mode="invisible_link")
    d.active_traps = {"hp_trap_111111"}

    class _User:
        id = 777
        first_name = "رضا"
        last_name = ""
        username = None

    class _Msg:
        from_user = _User()
        text = "hp_trap_999999"   # توکنی که ما نکاشته‌ایم
        caption = None

        async def delete(self):
            return True

    await d.monitor_message(_Msg())
    assert d.user_risk_score[777] < 50, "توکن ناشناس باعث افزایش ریسک شد"


def test_no_automatic_ban_on_honeypot_hit():
    """
    برخورد با تله نباید مستقیماً به ban منجر شود.

    سورس متد monitor_message نباید در شاخه هانی‌پات self.ban صدا بزند —
    فقط امتیاز ریسک و اطلاع به مالک.
    """
    src = inspect.getsource(AdvancedDefender.monitor_message)
    honeypot_section = src.split("امتیازدهی بر اساس نام")[0]
    assert "self.ban(" not in honeypot_section, (
        "شاخه هانی‌پات هنوز بن خودکار دارد — کاربر عادی که متن را کپی کند بن می‌شود"
    )


def test_alerts_never_go_to_group():
    """متد alert فقط باید به admin_id بفرستد، هرگز به group_id."""
    src = inspect.getsource(AdvancedDefender.alert)
    assert "self.admin_id" in src
    assert "self.group_id" not in src, "هشدارها نباید در گروه منتشر شوند"
