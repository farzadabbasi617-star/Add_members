"""تست ترتیب صف ادد.

این منطق قبلاً داخل تابع ۷۲۸ خطیِ ``_execute_parallel_add`` بود و برای تستش
باید به تلگرام وصل می‌شدی. حالا خالص است، پس می‌شود دقیقاً بررسی کرد که
قاعده‌های ضدبن حفظ شده‌اند.
"""

import random

from add_queue import MAX_MODE_HEAD_SIZE, order_members_for_add


def prefer_username_first(members):
    """جایگزین سادهٔ prefer_addable_members: یوزرنیم‌دارها اول."""
    return sorted(members, key=lambda m: 0 if m.get("username") else 1)


def make(n, with_username=0):
    return [
        {"id": i, "username": f"u{i}" if i < with_username else None} for i in range(n)
    ]


def test_non_max_modes_keep_quality_order():
    """safe/fast/ultra نباید ترتیب کیفی را به‌هم بزنند.

    در این حالت‌ها سرعت پایین است و ارزش دارد که بهترین کاندیداها اول امتحان
    شوند.
    """
    members = make(10, with_username=3)
    for mode in ("safe", "fast", "ultra"):
        result = order_members_for_add(members, mode, prefer_username_first)
        assert [m["id"] for m in result[:3]] == [0, 1, 2]


def test_max_mode_shuffles_small_pool():
    """در max ترتیب باید عوض شود، وگرنه هر بار همان سرِ صف تکرار می‌شود."""
    members = make(50)
    rng = random.Random(1234)
    result = order_members_for_add(members, "max", prefer_username_first, rng=rng)
    assert [m["id"] for m in result] != [m["id"] for m in members]
    # هیچ‌کس نباید گم یا تکرار شود
    assert sorted(m["id"] for m in result) == list(range(50))


def test_max_mode_splits_head_and_tail():
    """شافل دوتکه: هشتصد نفر برتر داخل خودشان جابه‌جا می‌شوند، نه با دُم.

    اگر یک شافل سراسری می‌زدیم، کاندیداهای بی‌کیفیت به ابتدای صف می‌آمدند و
    نرخ PEER_FLOOD بالا می‌رفت.
    """
    members = make(1000)
    rng = random.Random(7)
    result = order_members_for_add(members, "max", prefer_username_first, rng=rng)

    head_ids = {m["id"] for m in result[:MAX_MODE_HEAD_SIZE]}
    assert head_ids == set(range(MAX_MODE_HEAD_SIZE)), "کسی از دُم به سر صف نیامده باشد"

    tail_ids = {m["id"] for m in result[MAX_MODE_HEAD_SIZE:]}
    assert tail_ids == set(range(MAX_MODE_HEAD_SIZE, 1000))


def test_exactly_at_threshold_uses_simple_shuffle():
    """درست روی مرز ۸۰۰ نباید مسیر دوتکه فعال شود."""
    members = make(MAX_MODE_HEAD_SIZE)
    rng = random.Random(3)
    result = order_members_for_add(members, "max", prefer_username_first, rng=rng)
    assert len(result) == MAX_MODE_HEAD_SIZE
    assert sorted(m["id"] for m in result) == list(range(MAX_MODE_HEAD_SIZE))


def test_never_loses_or_duplicates_members():
    """مهم‌ترین ویژگی: هیچ ممبری نه گم شود نه دوبار ادد شود.

    ادد تکراری یعنی درخواست بی‌فایده به تلگرام، که مستقیم به سقف PEER_FLOOD
    نزدیک‌مان می‌کند.
    """
    for size in (0, 1, 799, 800, 801, 2500):
        members = make(size)
        for mode in ("safe", "max"):
            result = order_members_for_add(members, mode, prefer_username_first)
            assert len(result) == size
            assert sorted(m["id"] for m in result) == list(range(size))


def test_input_list_is_not_mutated():
    """تابع نباید لیست ورودی را دستکاری کند؛ فراخوان ممکن است بعداً لازمش
    داشته باشد."""
    members = make(20)
    snapshot = [m["id"] for m in members]
    order_members_for_add(members, "max", prefer_username_first, rng=random.Random(1))
    assert [m["id"] for m in members] == snapshot
