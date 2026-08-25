"""تست تشخیص آفلاین و انتخاب اکانت استخراج"""
import account_doctor
import account_state


def setup_function():
    account_state.reset_for_tests()


def test_diagnose_offline_no_session(monkeypatch):
    monkeypatch.setattr(account_doctor, "check_session_local", lambda p: {
        "phone": p, "disk_file": False, "db_blob": False, "disk_size": 0, "blob_size": 0
    })
    monkeypatch.setattr(account_doctor._db, "get_account_status", lambda p: {
        "added": 0, "status": "healthy", "remaining_seconds": 0
    })
    r = account_doctor.diagnose_offline("98900", {"name": "un"})
    assert r["status"] == "no_session"
    assert any("سشن" in x for x in r["reasons"])
    assert any("ناقص" in x for x in r["reasons"])


def test_diagnose_offline_unused(monkeypatch):
    monkeypatch.setattr(account_doctor, "check_session_local", lambda p: {
        "phone": p, "disk_file": True, "db_blob": True, "disk_size": 200, "blob_size": 200
    })
    monkeypatch.setattr(account_doctor._db, "get_account_status", lambda p: {
        "added": 0, "status": "healthy", "remaining_seconds": 0
    })
    r = account_doctor.diagnose_offline("98901", {"name": "ریحانه حیدری"})
    assert r["status"] == "unused"
    assert any("ادد" in x or "استفاده" in x for x in r["reasons"])


def test_pick_scrape_skips_busy_and_no_session(monkeypatch):
    accs = {
        "busy1": {"name": "A"},
        "dead2": {"name": "B"},
        "ok3": {"name": "C"},
    }
    monkeypatch.setattr(account_doctor._db, "load_accounts", lambda: accs)
    monkeypatch.setattr(account_doctor._db, "get_account_status", lambda p: {
        "added": 0, "status": "healthy"
    })

    def fake_local(p):
        if p == "dead2":
            return {"disk_file": False, "db_blob": False, "disk_size": 0, "blob_size": 0}
        return {"disk_file": True, "db_blob": True, "disk_size": 100, "blob_size": 100}

    monkeypatch.setattr(account_doctor, "check_session_local", fake_local)
    monkeypatch.setattr(account_doctor, "inspect_session", lambda p: {"ok": True, "user_id": 1})
    account_state.mark_busy("busy1", "اسکن")
    phone, info, skipped = account_doctor.pick_scrape_account()
    assert phone == "ok3"
    skipped_phones = [p for p, _ in skipped]
    assert "busy1" in skipped_phones
    assert "dead2" in skipped_phones


def test_pick_prefers_never_used(monkeypatch):
    accs = {"old": {"name": "O"}, "fresh": {"name": "F"}}
    monkeypatch.setattr(account_doctor._db, "load_accounts", lambda: accs)
    monkeypatch.setattr(account_doctor._db, "get_account_status", lambda p: {"added": 1, "status": "healthy"})
    monkeypatch.setattr(account_doctor, "check_session_local", lambda p: {
        "disk_file": True, "db_blob": True, "disk_size": 1, "blob_size": 1
    })
    monkeypatch.setattr(account_doctor, "inspect_session", lambda p: {"ok": True, "user_id": 1})
    account_state.mark_used("old")
    phone, _, _ = account_doctor.pick_scrape_account(preferred="old")
    assert phone == "fresh"


def test_collect_ready_includes_zero_add(monkeypatch):
    accs = {"98900": {"name": "A"}, "98901": {"name": "B"}}
    monkeypatch.setattr(account_doctor._db, "load_accounts", lambda: accs)
    monkeypatch.setattr(account_doctor._db, "get_account_status", lambda p: {
        "added": 0, "status": "healthy"
    })
    monkeypatch.setattr(account_doctor, "ensure_session", lambda p: (True, "ok"))
    monkeypatch.setattr(account_doctor, "ensure_all_sessions", lambda: (["98900", "98901"], []))
    monkeypatch.setattr(account_doctor, "inspect_session", lambda p: {"ok": True, "user_id": 1})
    monkeypatch.setattr(account_doctor, "load_probe_results", lambda: {})
    ready, skipped = account_doctor.collect_ready_accounts()
    assert set(ready) == {"98900", "98901"}
    assert skipped == []


def test_collect_ready_skips_failed_live_probe(monkeypatch):
    accs = {"good": {"name": "G"}, "bad": {"name": "B"}}
    monkeypatch.setattr(account_doctor._db, "load_accounts", lambda: accs)
    monkeypatch.setattr(account_doctor._db, "get_account_status", lambda p: {"added": 0, "status": "healthy"})
    monkeypatch.setattr(account_doctor, "ensure_session", lambda p: (True, "ok"))
    monkeypatch.setattr(account_doctor, "ensure_all_sessions", lambda: (["good", "bad"], []))
    monkeypatch.setattr(account_doctor, "inspect_session", lambda p: {"ok": True, "user_id": 1})
    monkeypatch.setattr(account_doctor, "load_probe_results", lambda: {
        "bad": {"ok": False, "error": "لاگین ناتمام"}
    })
    ready, skipped = account_doctor.collect_ready_accounts()
    assert list(ready) == ["good"]
    assert any(p == "bad" for p, _ in skipped)


def test_render_offline_report_contains_names(monkeypatch):
    monkeypatch.setattr(account_doctor, "check_session_local", lambda p: {
        "phone": p, "disk_file": True, "db_blob": False, "disk_size": 10, "blob_size": 0
    })
    monkeypatch.setattr(account_doctor._db, "get_account_status", lambda p: {
        "added": 0, "status": "healthy", "remaining_seconds": 0
    })
    rows = [account_doctor.diagnose_offline("98922", {"name": "ریحانه حیدری"})]
    html = account_doctor.render_offline_report(rows)
    assert "ریحانه حیدری" in html
    assert "بی‌استفاده" in html or "هرگز استفاده نشده" in html
