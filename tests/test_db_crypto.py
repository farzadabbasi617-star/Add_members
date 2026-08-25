"""تست رمزنگاری سشن‌ها — فرمت جدید (nonce+HMAC) + مهاجرت از فرمت قدیمی"""
import hashlib
import os

import pyaes

import db as database


KEY = "test-session-key-123"
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _make_legacy_blob(data: bytes) -> bytes:
    """ساخت بلاب با فرمت قدیمی (CTR بدون nonce) دقیقاً مثل کد قبلی"""
    key_32 = hashlib.sha256(KEY.encode()).digest()
    return pyaes.AESModeOfOperationCTR(key_32).encrypt(data)


def test_roundtrip_new_format(monkeypatch):
    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", KEY)
    data = _SQLITE_MAGIC + os.urandom(200)
    enc = database.encrypt_session_blob(data)
    assert enc[:4] == b"SES3"
    assert database.decrypt_session_blob(enc) == data


def test_legacy_format_migration(monkeypatch):
    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", KEY)
    data = _SQLITE_MAGIC + os.urandom(100)
    legacy = _make_legacy_blob(data)
    assert database.decrypt_session_blob(legacy) == data


def test_raw_passthrough(monkeypatch):
    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", KEY)
    raw = _SQLITE_MAGIC + os.urandom(50)
    assert database.decrypt_session_blob(raw) == raw


def test_tamper_detection_does_not_crash(monkeypatch):
    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", KEY)
    enc = bytearray(database.encrypt_session_blob(_SQLITE_MAGIC + os.urandom(100)))
    enc[-10] ^= 0xFF  # دستکاری
    out = database.decrypt_session_blob(bytes(enc))
    assert isinstance(out, bytes)


def test_no_key_no_encryption(monkeypatch):
    monkeypatch.delenv("SESSION_ENCRYPTION_KEY", raising=False)
    data = _SQLITE_MAGIC + os.urandom(50)
    assert database.encrypt_session_blob(data) == data
    assert database.decrypt_session_blob(data) == data
