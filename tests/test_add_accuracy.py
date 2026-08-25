"""
نگهبان صحت آمار ادد.

مشکل گزارش‌شده توسط مالک (نسخه ۱.۴.۲):
«وقتی ادد می‌زنم می‌نویسد ۱۰۰ نفر ادد شدند ولی داخل گروه فقط ۵ نفر هستند.»

علت ریشه‌ای:
Pyrogram وضعیت عضویت را به‌صورت enum برمی‌گرداند. str() آن می‌شود
'ChatMemberStatus.LEFT'. کد قبلی چنین چک می‌کرد:

    if any(tok in st for tok in ("member", "administrator", ...))

و رشته 'chatmemberstatus.left' شامل زیررشته 'member' است!
پس کاربری که گروه را ترک کرده یا بن شده «عضو» شمرده می‌شد.

نتیجه دوگانه:
  ۱) آمار ادد کاملاً غیرواقعی می‌شد
  ۲) سهمیه اکانت الکی مصرف می‌شد → اکانت‌ها زودتر محدود می‌شدند
"""
from enum import Enum

import pytest

from add_engine import _normalize_member_status, confirm_joined, invite_did_not_join


class ChatMemberStatus(Enum):
    """شبیه‌سازی enum واقعی Pyrogram."""
    OWNER = "owner"
    ADMINISTRATOR = "administrator"
    MEMBER = "member"
    RESTRICTED = "restricted"
    LEFT = "left"
    BANNED = "banned"


class _Member:
    def __init__(self, status):
        self.status = status


class _App:
    """اپ ساختگی؛ status=None یعنی کاربر عضو نیست و خطا پرتاب می‌شود."""

    def __init__(self, status, fail=False):
        self.status = status
        self.fail = fail
        self.calls = 0

    async def get_chat_member(self, chat_id, uid):
        self.calls += 1
        if self.fail:
            raise RuntimeError("USER_NOT_PARTICIPANT")
        return _Member(self.status)


# ─────────────────────────── normalize ───────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (ChatMemberStatus.MEMBER, "member"),
    (ChatMemberStatus.LEFT, "left"),
    (ChatMemberStatus.BANNED, "banned"),
    (ChatMemberStatus.OWNER, "owner"),
    ("member", "member"),
    ("LEFT", "left"),
])
def test_normalize_status(raw, expected):
    assert _normalize_member_status(_Member(raw)) == expected


def test_normalize_handles_enum_repr_without_value():
    """اگر enum مقدار .value نداشت، باید از نام بعد از نقطه استفاده کند."""

    class Bare:
        def __str__(self):
            return "ChatMemberStatus.LEFT"

    assert _normalize_member_status(_Member(Bare())) == "left"


# ─────────────────────────── confirm_joined ───────────────────────────

@pytest.mark.parametrize("status", [
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
    ChatMemberStatus.RESTRICTED,
    "member",
])
async def test_real_members_are_counted(status):
    assert await confirm_joined(_App(status), -100123, 555, retries=1) is True


@pytest.mark.parametrize("status", [
    ChatMemberStatus.LEFT,
    ChatMemberStatus.BANNED,
    "left",
    "banned",
    "kicked",
])
async def test_non_members_are_not_counted(status):
    """
    این هسته‌ی باگ است.

    ChatMemberStatus.LEFT شامل زیررشته 'MEMBER' است — کد قبلی آن را
    عضو می‌شمرد و آمار ادد را دروغ می‌کرد.
    """
    result = await confirm_joined(_App(status), -100123, 555, retries=1)
    assert result is False, (
        f"وضعیت {status!r} نباید «عضو» شمرده شود — این باعث آمار غلط "
        "و مصرف بیهوده سهمیه اکانت می‌شود"
    )


async def test_missing_user_is_not_counted():
    assert await confirm_joined(_App(None, fail=True), -100123, 555, retries=1) is False


async def test_definitive_left_does_not_retry():
    """
    وقتی تلگرام قطعاً می‌گوید کاربر داخل نیست، تلاش دوباره بی‌فایده است.
    این هم سرعت را بالا می‌برد هم فشار روی API را کم می‌کند (ضد FloodWait).
    """
    app = _App(ChatMemberStatus.LEFT)
    await confirm_joined(app, -100123, 555, retries=3)
    assert app.calls == 1, f"برای وضعیت قطعی LEFT نباید {app.calls} بار تلاش کند"


# ─────────────────────── missing_invitees ───────────────────────

class _Invitee:
    def __init__(self, uid):
        self.user_id = uid


class _Updates:
    def __init__(self, missing):
        self.missing_invitees = missing


def test_missing_invitee_detected():
    """تلگرام صریحاً می‌گوید چه کسانی اضافه نشدند."""
    assert invite_did_not_join(_Updates([_Invitee(555)]), 555) is True


def test_other_missing_invitee_ignored():
    assert invite_did_not_join(_Updates([_Invitee(999)]), 555) is False


def test_empty_updates_means_success():
    assert invite_did_not_join(_Updates([]), 555) is False
    assert invite_did_not_join(None, 555) is False


# ─────────────────────── حفاظت از سهمیه ───────────────────────

def test_all_add_paths_confirm_membership():
    """
    هر مسیری که InviteToChannel صدا می‌زند باید بعدش عضویت را تأیید کند.

    سه مسیر ادد در bot.py وجود دارد (تکی، دسته‌ای، موازی). اگر یکی
    تأیید نداشته باشد، همان آمار دروغ و مصرف بیهوده سهمیه برمی‌گردد.
    """
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parent.parent / "bot.py"
    text = src.read_text(encoding="utf-8")

    invite_lines = [
        i for i, line in enumerate(text.split("\n"))
        if "InviteToChannel(" in line and "invoke" in line
    ]
    assert invite_lines, "هیچ فراخوانی InviteToChannel پیدا نشد"

    lines = text.split("\n")
    unconfirmed = []
    for idx in invite_lines:
        window = "\n".join(lines[idx:idx + 12])
        if "confirm_joined" not in window:
            unconfirmed.append(idx + 1)

    assert not unconfirmed, (
        f"این خطوط InviteToChannel بدون تأیید عضویت هستند: {unconfirmed}. "
        "بدون confirm_joined آمار ادد دروغ می‌شود و سهمیه اکانت می‌سوزد."
    )
