"""تست ردیاب اشغال بودن اکانت‌ها"""
import account_state


def setup_function():
    account_state.reset_for_tests()


def test_mark_busy_and_release():
    ok, owner = account_state.mark_busy("98911", "اسکن")
    assert ok and owner is None
    ok2, owner2 = account_state.mark_busy("98911", "ادد")
    assert not ok2 and owner2 == "اسکن"
    account_state.release("98911")
    ok3, owner3 = account_state.mark_busy("98911", "ادد")
    assert ok3 and owner3 is None


def test_busy_label_and_all_busy():
    account_state.mark_busy("a", "x")
    account_state.mark_busy("b", "y")
    assert account_state.busy_label("a") == "x"
    assert account_state.all_busy() == {"a": "x", "b": "y"}
    account_state.release("a")
    assert account_state.busy_label("a") is None


def test_ttl_expires(monkeypatch):
    account_state.mark_busy("z", "old")
    # منقضی کردن مصنوعی
    with account_state._lock:
        account_state._busy["z"]["ts"] = 0
    assert account_state.busy_label("z") is None
    ok, _ = account_state.mark_busy("z", "new")
    assert ok


def test_last_used_and_error():
    assert account_state.last_used("p") == 0
    account_state.mark_used("p")
    assert account_state.last_used("p") > 0
    account_state.set_last_error("p", "boom")
    assert "boom" in account_state.get_last_error("p")
    account_state.set_last_error("p", "")
    assert account_state.get_last_error("p") == ""
