"""
🌐 Global Target Throttle — تضمین فاصله بین دعوت‌ها روی یک گروه.

پس‌زمینه (۱.۹.۶): با ۸ اکانت و DELAY 1-3s، هر ۰.۲۵s یک Invite به یک گروه
می‌رفت → تلگرام آن را هجوم هماهنگ می‌بیند و PEER_FLOOD می‌دهد.
Throttle سراسری حداقل فاصله بین هر دو Invite را تضمین می‌کند.
"""
import asyncio
import time
import pathlib

import config
from add_engine import global_throttle, reset_global_throttle_for_tests

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_global_throttle_interval_exists():
    for mode in ("max", "ultra", "fast", "safe"):
        assert mode in config.GLOBAL_THROTTLE_INTERVAL
        lo, hi = config.GLOBAL_THROTTLE_INTERVAL[mode]
        assert 0 < lo < hi < 10, f"{mode} interval invalid"


def test_global_throttle_max_is_fastest():
    lo_max, hi_max = config.GLOBAL_THROTTLE_INTERVAL["max"]
    lo_safe, hi_safe = config.GLOBAL_THROTTLE_INTERVAL["safe"]
    assert hi_max < lo_safe


def test_global_throttle_enabled_by_default():
    assert config.GLOBAL_THROTTLE_ENABLED is True


def test_global_throttle_can_be_disabled(monkeypatch):
    # غیرفعال → باید بدون وقفه برگردد
    monkeypatch.setattr(config, "GLOBAL_THROTTLE_ENABLED", False)
    reset_global_throttle_for_tests()
    async def run():
        t0 = time.monotonic()
        await global_throttle("max")
        await global_throttle("max")
        dt = time.monotonic() - t0
        assert dt < 0.3, "وقتی خاموش است نباید صبر کند"
    asyncio.run(run())


def test_global_throttle_enforces_interval():
    reset_global_throttle_for_tests()
    async def run():
        await global_throttle("max")  # اولی بدون وقفه
        t0 = time.monotonic()
        await global_throttle("max")
        dt = time.monotonic() - t0
        lo, hi = config.GLOBAL_THROTTLE_INTERVAL["max"]
        # حداقل باید lo باشد (با تلورانس 0.05)
        assert dt >= lo - 0.05, f"باید حداقل {lo}s صبر کند، ولی {dt:.2f}s صبر کرد"
        assert dt <= hi + 0.5, f"نباید بیش از {hi}s صبر کند"
    asyncio.run(run())


def test_global_throttle_serializes_concurrent_invites():
    reset_global_throttle_for_tests()
    async def run():
        # 4 دعوت هم‌زمان → باید سریال شوند با فاصله lo
        lo, _ = config.GLOBAL_THROTTLE_INTERVAL["max"]
        t0 = time.monotonic()
        await asyncio.gather(
            global_throttle("max"),
            global_throttle("max"),
            global_throttle("max"),
            global_throttle("max"),
        )
        dt = time.monotonic() - t0
        # 4 دعوت سریال = حداقل 3 * lo
        assert dt >= 3 * lo - 0.2, f"دعوت‌های هم‌زمان باید سریال شوند: {dt:.2f}s"
    asyncio.run(run())


def test_global_throttle_reset():
    reset_global_throttle_for_tests()
    async def run():
        await global_throttle("max")
        reset_global_throttle_for_tests()
        t0 = time.monotonic()
        await global_throttle("max")
        dt = time.monotonic() - t0
        assert dt < 0.3, "بعد از ریست نباید صبر کند"
    asyncio.run(run())


def test_bot_worker_uses_global_throttle():
    import ast
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert "global_throttle" in src, "ورکر باید از global_throttle استفاده کند"
    # باید قبل از InviteToChannel داخل همین ورکر باشد
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_worker_account_inner":
            worker = ast.get_source_segment(src, node)
            break
    else:
        raise AssertionError("_worker_account_inner پیدا نشد")
    assert "await global_throttle" in worker
    idx_throttle = worker.index("await global_throttle")
    idx_invite = worker.index("InviteToChannel(channel=target_peer")
    assert idx_throttle < idx_invite, "throttle باید قبل از دعوت باشد"
    assert "global_throttle" in src


def test_budget_still_one_request():
    # throttle فقط sleep است، نباید بودجه را بالا ببرد
    import re, ast
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(bot)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_worker_account_inner":
            worker = ast.get_source_segment(bot, node)
            break
    else:
        raise AssertionError("_worker_account_inner پیدا نشد")
    code = "\n".join(l for l in worker.split("\n") if not l.lstrip().startswith("#"))
    budget = (
        len(re.findall(r"await client\.get_users\(", code))
        + len(re.findall(r"InviteToChannel\(", code))
        + len(re.findall(r"await confirm_joined\(", code))
        + len(re.findall(r"AddContact\(", code))
    )
    assert budget == 1, f"بودجه باید ۱ بماند (الان {budget}) — throttle نباید درخواست اضافه کند"


def test_config_version_bumped():
    assert config.APP_VERSION == "1.9.14"
    # DB_POOL_SIZE افزایش یافته — مقدار فعلی ممکن است توسط تست دیگری monkeypatch شده باشد،
    # پس سورس را چک می‌کنیم
    import pathlib
    src = pathlib.Path(pathlib.Path(__file__).resolve().parent.parent / "config.py").read_text(encoding="utf-8")
    assert 'DB_POOL_SIZE = _int("DB_POOL_SIZE", 10)' in src
