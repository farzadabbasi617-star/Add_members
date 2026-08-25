"""تست پیکربندی مرکزی"""
import os
import importlib

import config


def test_defaults(monkeypatch):
    monkeypatch.setenv("API_ID", "2040")
    monkeypatch.setenv("ADMIN_ID", "111")
    monkeypatch.setenv("PORT", "2000")
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    importlib.reload(config)
    assert config.API_ID == 2040
    assert config.ADMIN_ID == 111
    assert config.PORT == 2000
    assert config.DB_POOL_SIZE == 3


def test_delay_ranges_valid():
    for mode in ("ultra", "fast", "safe"):
        lo, hi = config.DELAY_RANGES[mode]
        assert 0 < lo < hi
        blo, bhi = config.BREAK_RANGES[mode]
        assert 0 < blo < bhi


def test_mode_caps():
    assert config.MODE_DAILY_CAP["ultra"] <= config.MODE_DAILY_CAP["fast"]
    assert config.MAX_ADD_PER_ACCOUNT > 0


def test_dotenv_missing_is_ok():
    # بدون فایل .env هم باید کار کند
    assert isinstance(config.BOT_TOKEN, str) and config.BOT_TOKEN
    assert isinstance(config.APP_VERSION, str)
