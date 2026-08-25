"""
=================================================================
🧪 Pytest Conftest — محیط ایزوله تست (بدون شبکه / دیتابیس واقعی)
=================================================================
"""
import os
import sys

# دیتابیس غیرقابل دسترس با تایم‌اوت کوتاه — import ماژول‌ها نباید شبکه بخواهد
os.environ["DATABASE_URL"] = "postgresql://invalid:invalid@127.0.0.1:1/invalid?connect_timeout=1&sslmode=disable"
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_ID", "564234793")

# مسیر ریشه پروژه
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
