"""
Database module - Neon PostgreSQL (Senior Architect Enhanced)
تمام داده‌های مهم اینجا ذخیره میشن که حتی با ریست رندر چیزی از بین نره.

ویژگی‌های نسخه ارشد:
- سیستم Auto-Reconnect و Health Check خودکار برای اتصالات قطع شده Neon
- دکوراتور @db_retry جهت بازیابی هوشمند خطاهای OperationalError/InterfaceError
- ایجاد ایندکس‌های با کارایی بالا (High-Performance Indexes) روی جداول اصلی
- پشتیبانی از رمزنگاری سشن‌ها (Session Encryption) در صورت تعریف SESSION_ENCRYPTION_KEY
- تابع async_db_call جهت اجرای غیرهمزمان پرس‌وجوهای سنگین بدون بلاک کردن Event Loop
"""
import os
import json
import time
import hmac
import hashlib
import threading
import functools
import asyncio
import logging
import psycopg2
from psycopg2.extras import Json, DictCursor
from psycopg2 import pool as _psycopg2_pool

try:
    import config
    DB_URL = config.DATABASE_URL
    DB_POOL_SIZE = config.DB_POOL_SIZE
except Exception:
    DB_URL = os.environ.get("DATABASE_URL", "")
    DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "6"))

logger = logging.getLogger("antiscraper.db")

# ⚡ پول اتصال — هر ترد اتصال مستقل خودش را می‌گیرد.
# قبلاً همه کوئری‌ها (اسکرپ + ادد موازی + مینی‌اپ) روی یک سوکت مشترک صف می‌شدند.
_pool = None
_pool_lock = threading.Lock()


def _init_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            size = int(os.environ.get("DB_POOL_SIZE", str(DB_POOL_SIZE)))
            _pool = _psycopg2_pool.ThreadedConnectionPool(2, max(4, size), DB_URL, connect_timeout=15)


def reset_db_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None


class _PooledCursor:
    """کرسور پروکسی — با close شدن، اتصال به‌طور خودکار به پول برمی‌گردد (بدون نشتی اتصال)"""
    __slots__ = ("_owner", "_cur")
    def __init__(self, owner, cur):
        self._owner = owner
        self._cur = cur
    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass
        self._owner.release()
    def __getattr__(self, name):
        return getattr(self._cur, name)


class _PooledConn:
    """اتصال پروکسی — تمام API قبلی (cursor, poll, closed, ...) حفظ شده است"""
    __slots__ = ("_pool", "_conn")
    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn
    def cursor(self, *args, **kwargs):
        return _PooledCursor(self, self._conn.cursor(*args, **kwargs))
    def release(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                self._pool.putconn(conn)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
    def __getattr__(self, name):
        return getattr(self._conn, name)
    def __del__(self):
        # توری ایمنی: اگر در مسیر استثنایی cur.close() جا ماند، اتصال با GC برمی‌گردد
        try:
            self.release()
        except Exception:
            pass


def get_conn():
    """گرفتن اتصال سالم و آماده (autocommit) از پول"""
    _init_pool()
    conn = _pool.getconn()
    conn.autocommit = True
    return _PooledConn(_pool, conn)


def db_retry(max_retries=2, delay=0.5):
    """دکوراتور اختصاصی جهت بازیابی خودکار خطاهای شبکه و قطعی اتصال دیتابیس"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (psycopg2.OperationalError, psycopg2.InterfaceError, psycopg2.DatabaseError, _psycopg2_pool.PoolError) as e:
                    last_err = e
                    logger.warning(f"⚠️ DB Reconnect [{func.__name__}] (attempt {attempt+1}/{max_retries}): {e}")
                    reset_db_pool()
                    if attempt < max_retries - 1:
                        time.sleep(delay)
                except Exception as e:
                    logger.error(f"❌ DB Error [{func.__name__}]: {e}")
                    raise
            if last_err:
                raise last_err
        return wrapper
    return decorator

async def async_db_call(func, *args, **kwargs):
    """اجرای توابع سنگین دیتابیس در ترید جداگانه جهت جلوگیری از بلاک شدن asyncio event loop"""
    return await asyncio.to_thread(func, *args, **kwargs)

# ---------------- Session Encryption Helpers ----------------
# فرمت جدید (SES3): MAGIC + nonce(16) + ciphertext + HMAC-SHA256(32)
#   - nonce تصادفی: دو سشن مشابه هرگز keystream یکسان ندارند
#   - HMAC: هر دستکاری در داده شناسایی می‌شود (احراز اصالت)
# فرمت قدیمی (بدون MAGIC): AES-CTR بدون nonce — موقع خواندن به‌صورت خودکار پشتیبانی می‌شود
_SES_MAGIC = b"SES3"
_SQLITE_MAGIC = b"SQLite format 3\x00"


def _enc_key_material(key_str: str) -> dict:
    h = hashlib.sha256(key_str.encode()).digest()
    return {
        "enc": hashlib.sha256(h + b":enc").digest(),
        "mac": hashlib.sha256(h + b":mac").digest(),
        "legacy": h,
    }


def encrypt_session_blob(blob_bytes: bytes) -> bytes:
    """رمزنگاری سشن‌ها — AES-CTR با nonce تصادفی + HMAC-SHA256"""
    key = os.environ.get("SESSION_ENCRYPTION_KEY")
    if not key or not blob_bytes:
        return blob_bytes
    try:
        import pyaes
        km = _enc_key_material(key)
        nonce = os.urandom(16)
        ct = pyaes.AESModeOfOperationCTR(km["enc"]).encrypt(blob_bytes)
        mac = hmac.new(km["mac"], nonce + ct, hashlib.sha256).digest()
        return _SES_MAGIC + nonce + ct + mac
    except Exception as e:
        logger.warning("Session encryption failed (storing raw): %s", e)
        return blob_bytes


def decrypt_session_blob(blob_bytes: bytes) -> bytes:
    """رمزگشایی سشن‌ها — با مهاجرت خودکار از فرمت قدیمی و داده خام"""
    key = os.environ.get("SESSION_ENCRYPTION_KEY")
    if not key or not blob_bytes:
        return blob_bytes
    try:
        import pyaes
        km = _enc_key_material(key)
        # ۱) فرمت جدید
        if blob_bytes[:4] == _SES_MAGIC:
            try:
                nonce = blob_bytes[4:20]
                mac = blob_bytes[-32:]
                ct = blob_bytes[20:-32]
                if not hmac.compare_digest(hmac.new(km["mac"], nonce + ct, hashlib.sha256).digest(), mac):
                    raise ValueError("HMAC mismatch")
                return pyaes.AESModeOfOperationCTR(km["enc"]).decrypt(ct)
            except Exception as e:
                logger.warning("SES3 decrypt failed (%s) — trying legacy/raw", e)
        # ۲) داده خام (SQLite session) — بدون رمزنگاری ذخیره شده
        if blob_bytes[:16] == _SQLITE_MAGIC:
            return blob_bytes
        # ۳) فرمت قدیمی: CTR بدون nonce
        try:
            pt = pyaes.AESModeOfOperationCTR(km["legacy"]).decrypt(blob_bytes)
            if pt[:16] == _SQLITE_MAGIC:
                return pt
        except Exception:
            pass
        return blob_bytes
    except Exception as e:
        logger.warning("Session decryption failed (returning raw): %s", e)
        return blob_bytes


@db_retry(max_retries=3)
def init_tables():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kv_store (
            key TEXT PRIMARY KEY,
            value JSONB NOT NULL,
            updated_at BIGINT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scraped_users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            source_group_id BIGINT,
            source_group_name TEXT,
            added_at BIGINT,
            extra JSONB DEFAULT '{}'::jsonb
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saved_accounts_tbl (
            phone TEXT PRIMARY KEY,
            name TEXT,
            username TEXT,
            device_fp JSONB NOT NULL,
            session_data BYTEA,
            created_at BIGINT,
            last_used BIGINT,
            added_count INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS added_history_tbl (
            group_id BIGINT,
            user_id BIGINT,
            added_at BIGINT,
            account_phone TEXT,
            PRIMARY KEY (group_id, user_id)
        )
    """)
    # 🚫 لیست «هرگز دوباره ادد نشود» — کاربرانی که privacy بسته‌اند، invalid شده‌اند،
    # یا یک‌بار ادد شدند و از گروه لفت دادند. این‌ها برای همیشه از چرخه ادد خارج می‌شوند.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS do_not_add_tbl (
            user_id BIGINT PRIMARY KEY,
            reason TEXT DEFAULT '',
            added_at BIGINT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_added_history_user ON added_history_tbl(user_id);")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS adder_limits_tbl (
            phone TEXT PRIMARY KEY,
            added INTEGER DEFAULT 0,
            last_used BIGINT,
            limitation_type TEXT DEFAULT NULL,
            limitation_until BIGINT DEFAULT 0
        )
    """)
    cur.execute("ALTER TABLE adder_limits_tbl ADD COLUMN IF NOT EXISTS limitation_type TEXT DEFAULT NULL;")
    cur.execute("ALTER TABLE adder_limits_tbl ADD COLUMN IF NOT EXISTS limitation_until BIGINT DEFAULT 0;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS config_tbl (
            group_id BIGINT PRIMARY KEY DEFAULT 0,
            group_name TEXT DEFAULT '',
            defense_enabled BOOLEAN DEFAULT TRUE,
            owner_phone TEXT DEFAULT ''
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS projects_tbl (
            url TEXT PRIMARY KEY,
            platform TEXT,
            full_name TEXT,
            category TEXT,
            data JSONB NOT NULL,
            found_at BIGINT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bg_scan_state (
            id INTEGER PRIMARY KEY DEFAULT 1,
            enabled BOOLEAN DEFAULT FALSE,
            target_group_id BIGINT,
            account_phone TEXT,
            interval_minutes INTEGER DEFAULT 60,
            last_run BIGINT,
            total_found INTEGER DEFAULT 0,
            status TEXT DEFAULT 'idle'
        )
    """)
    cur.execute("""
        INSERT INTO bg_scan_state (id, enabled, status)
        VALUES (1, FALSE, 'idle')
        ON CONFLICT (id) DO NOTHING
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanned_chats_tbl (
            chat_id BIGINT PRIMARY KEY,
            chat_name TEXT NOT NULL,
            chat_type TEXT DEFAULT 'group',
            category TEXT DEFAULT '',
            total_members_estimate INTEGER DEFAULT 0,
            extracted_count INTEGER DEFAULT 0,
            progress_pct INTEGER DEFAULT 0,
            last_scan BIGINT,
            first_scan BIGINT,
            scan_count INTEGER DEFAULT 0,
            is_favorite BOOLEAN DEFAULT FALSE,
            notes TEXT DEFAULT ''
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS leads_tbl (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            url TEXT UNIQUE,
            source TEXT,
            category TEXT,
            phone TEXT,
            telegram_username TEXT,
            instagram_username TEXT,
            score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            notes TEXT DEFAULT '',
            created_at BIGINT,
            extra JSONB DEFAULT '{}'::jsonb
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads_tbl(status);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_category ON leads_tbl(category);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_score ON leads_tbl(score DESC);")
    # High-Performance Indexes for maximum query speeds
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scraped_users_source ON scraped_users(source_group_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scraped_users_added ON scraped_users(added_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scraped_users_phone ON scraped_users(phone) WHERE phone IS NOT NULL AND phone != '';")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scraped_users_username ON scraped_users(username) WHERE username IS NOT NULL AND username != '';")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_added_history_group ON added_history_tbl(group_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_scanned_chats_category ON scanned_chats_tbl(category);")
    cur.close()

# ---------------- KV helpers ----------------
@db_retry()
def kv_get(key, default=None):
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT value FROM kv_store WHERE key=%s", (key,))
        row = cur.fetchone()
        cur.close()
        return row[0] if row else default
    except Exception as e:
        logger.error(f"kv_get {key} err: {e}")
        return default

@db_retry()
def kv_set(key, value):
    try:
        cur = get_conn().cursor()
        cur.execute("""
            INSERT INTO kv_store (key, value, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET value=%s, updated_at=%s
        """, (key, Json(value), int(time.time()), Json(value), int(time.time())))
        cur.close()
    except Exception as e:
        logger.error(f"kv_set {key} err: {e}")
# ---------------- Scraped users ----------------
@db_retry()
def save_user(user_id, username, first_name, last_name, phone, group_id, group_name):
    try:
        uid = int(user_id)
        # 🚫 کاربر ممنوعه/ادد‌شده قبلی هرگز دوباره ذخیره نمی‌شود
        if is_do_not_add(uid) or is_added_anywhere(uid):
            return
        cur = get_conn().cursor()
        cur.execute("""
            INSERT INTO scraped_users (user_id, username, first_name, last_name, phone, source_group_id, source_group_name, added_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name
        """, (uid, username, first_name, last_name, phone, int(group_id or 0), group_name or "", int(time.time())))
        cur.close()
    except Exception as e:
        logger.error(f"save_user err: {e}")
@db_retry()
def load_users_dict():
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        cur.execute("SELECT user_id, username, first_name, last_name, phone, source_group_id, source_group_name FROM scraped_users")
        out = {}
        for row in cur.fetchall():
            out[int(row["user_id"])] = {
                "user_id": int(row["user_id"]),
                "username": row["username"] or "",
                "first_name": row["first_name"] or "",
                "last_name": row["last_name"] or "",
                "phone": row["phone"] or "",
            }
        cur.close()
        return out
    except Exception as e:
        logger.error(f"load_users err: {e}")
        return {}

@db_retry()
def count_users():
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT COUNT(*) FROM scraped_users")
        r = cur.fetchone()[0]
        cur.close()
        return r
    except:
        return 0

@db_retry()
def bulk_save_users(users_list, group_id, group_name):
    """Bulk insert from scrape (list of dicts)"""
    if not users_list: return
    conn = get_conn()
    cur = conn.cursor()
    # 🚫 فیلتر ضد-ادد-تکراری در لایه DB: هر کسی که قبلاً ادد شده یا در لیست «هرگز دوباره ادد نشود»
    # است (لفت داده / پروفایل قفل / آیدی نامعتبر) هرگز دوباره وارد دیتابیس ممبرها نمی‌شود.
    blocked_ids = set()
    try:
        incoming_ids = []
        for u in users_list:
            try:
                incoming_ids.append(int(u.get("user_id")))
            except:
                pass
        if incoming_ids:
            cur.execute("SELECT user_id FROM do_not_add_tbl WHERE user_id = ANY(%s)", (incoming_ids,))
            blocked_ids.update(int(r[0]) for r in cur.fetchall())
            cur.execute("SELECT DISTINCT user_id FROM added_history_tbl WHERE user_id = ANY(%s)", (incoming_ids,))
            blocked_ids.update(int(r[0]) for r in cur.fetchall())
    except Exception as e:
        logger.error(f"bulk_save filter err: {e}")
    rows = []
    for u in users_list:
        try:
            uid = int(u.get("user_id"))
            if uid in blocked_ids:
                continue  # قبلاً ادد شده / لفت داده / ممنوع → ذخیره نمی‌شود
            phone = u.get("phone", "") or ""
            rows.append((uid, u.get("username",""), u.get("first_name",""), u.get("last_name",""), phone,
                         int(group_id or 0), group_name or "", int(time.time())))
        except:
            continue
    if blocked_ids:
        logger.error(f"🚫 {len(blocked_ids)} کاربر ممنوعه/ادد‌شده از ذخیره‌سازی رد شدند")
    if not rows:
        cur.close()
        return
    try:
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO scraped_users (user_id, username, first_name, last_name, phone, source_group_id, source_group_name, added_at)
            VALUES %s
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                phone = COALESCE(NULLIF(EXCLUDED.phone, ''), scraped_users.phone)
        """, rows, page_size=500)
    except Exception as e:
        logger.error(f"bulk_save_users err: {e}")
    cur.close()

# ---------------- Saved accounts (with session backup) ----------------
@db_retry()
def save_account(phone, name, username, device_fp, session_blob=None):
    try:
        cur = get_conn().cursor()
        encrypted_session = encrypt_session_blob(session_blob) if session_blob else None
        if session_blob:
            cur.execute("""
                INSERT INTO saved_accounts_tbl (phone, name, username, device_fp, session_data, created_at, last_used)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (phone) DO UPDATE SET
                    name=EXCLUDED.name, username=EXCLUDED.username, device_fp=EXCLUDED.device_fp,
                    session_data=COALESCE(EXCLUDED.session_data, saved_accounts_tbl.session_data),
                    last_used=EXCLUDED.last_used
            """, (phone, name or "", username or "", Json(device_fp), psycopg2.Binary(encrypted_session) if encrypted_session else None, int(time.time()), int(time.time())))
        else:
            cur.execute("""
                INSERT INTO saved_accounts_tbl (phone, name, username, device_fp, created_at, last_used)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (phone) DO UPDATE SET
                    name=EXCLUDED.name, username=EXCLUDED.username, device_fp=EXCLUDED.device_fp,
                    last_used=EXCLUDED.last_used
            """, (phone, name or "", username or "", Json(device_fp), int(time.time()), int(time.time())))
        cur.close()
    except Exception as e:
        logger.error(f"save_account err: {e}")
@db_retry()
def load_accounts():
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        cur.execute("SELECT phone, name, username, device_fp, added_count, last_used FROM saved_accounts_tbl ORDER BY created_at ASC")
        out = {}
        for row in cur.fetchall():
            out[row["phone"]] = {
                "name": row["name"],
                "username": row["username"],
                "device_fp": row["device_fp"],
                "added_count": row["added_count"] or 0,
                "last_used": row["last_used"],
            }
        cur.close()
        return out
    except Exception as e:
        logger.error(f"load_accounts err: {e}")
        return {}

@db_retry()
def delete_account(phone):
    try:
        cur = get_conn().cursor()
        cur.execute("DELETE FROM saved_accounts_tbl WHERE phone=%s", (phone,))
        cur.close()
    except: pass

@db_retry()
def save_session_blob(phone, blob_bytes):
    try:
        cur = get_conn().cursor()
        encrypted_session = encrypt_session_blob(blob_bytes) if blob_bytes else None
        cur.execute("UPDATE saved_accounts_tbl SET session_data=%s, last_used=%s WHERE phone=%s",
                    (psycopg2.Binary(encrypted_session) if encrypted_session else None, int(time.time()), phone))
        cur.close()
    except Exception as e:
        logger.error(f"save_session_blob err: {e}")
@db_retry()
def load_session_blob(phone):
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT session_data FROM saved_accounts_tbl WHERE phone=%s", (phone,))
        row = cur.fetchone()
        cur.close()
        if row and row[0]:
            raw_bytes = bytes(row[0])
            return decrypt_session_blob(raw_bytes)
        return None
    except Exception as e:
        logger.error(f"load_session_blob err: {e}")
        return None

# ---------------- Config ----------------
@db_retry()
def set_owner_phone(phone):
    try:
        cur = get_conn().cursor()
        cur.execute("""
            INSERT INTO config_tbl (group_id, owner_phone) VALUES (0, %s)
            ON CONFLICT (group_id) DO UPDATE SET owner_phone=EXCLUDED.owner_phone
        """, (phone,))
        cur.close()
    except Exception as e:
        logger.error(f"set_owner_phone err: {e}")
@db_retry()
def get_owner_phone():
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT owner_phone FROM config_tbl WHERE group_id=0")
        row = cur.fetchone()
        cur.close()
        return row[0] if row else ""
    except:
        return ""

@db_retry()
def set_config(group_id, group_name, defense_enabled=True, owner_phone=""):
    try:
        cur = get_conn().cursor()
        cur.execute("""
            INSERT INTO config_tbl (group_id, group_name, defense_enabled, owner_phone)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (group_id) DO UPDATE SET
                group_name=EXCLUDED.group_name,
                defense_enabled=EXCLUDED.defense_enabled,
                owner_phone=COALESCE(NULLIF(EXCLUDED.owner_phone,''), config_tbl.owner_phone)
        """, (int(group_id), group_name, defense_enabled, owner_phone))
        cur.close()
        try:
            kv_set("active_add_target", {
                "group_id": int(group_id or 0),
                "group_name": group_name or "",
            })
        except Exception:
            pass
    except Exception as e:
        logger.error(f"set_config err: {e}")
@db_retry()
def get_config(key=None, default=None):
    if key is not None:
        return kv_get(key, default)
    try:
        active = kv_get("active_add_target")
        if isinstance(active, dict) and (active.get("group_id") or active.get("group_name")):
            owner = get_owner_phone() or ""
            return {
                "group_id": active.get("group_id") or 0,
                "group_name": active.get("group_name") or "",
                "defense_enabled": True,
                "owner_phone": owner,
            }
        cur = get_conn().cursor(cursor_factory=DictCursor)
        cur.execute(
            "SELECT group_id, group_name, defense_enabled, owner_phone FROM config_tbl "
            "ORDER BY CASE WHEN COALESCE(group_id,0)=0 THEN 1 ELSE 0 END, group_id DESC"
        )
        row = cur.fetchone()
        cur.close()
        if row:
            return {
                "group_id": row["group_id"],
                "group_name": row["group_name"],
                "defense_enabled": row["defense_enabled"],
                "owner_phone": row["owner_phone"] or "",
            }
        return {"group_id": None, "group_name": "", "defense_enabled": True, "owner_phone": ""}
    except Exception as e:
        logger.error(f"get_config err: {e}")
        return {"group_id": None, "group_name": "", "defense_enabled": True, "owner_phone": ""}

# ---------------- Adder limits ----------------
@db_retry()
def get_adder_limits():
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        cur.execute("SELECT phone, added, last_used, limitation_type, limitation_until FROM adder_limits_tbl")
        out = {}
        for row in cur.fetchall():
            out[row["phone"]] = {
                "added": row["added"] or 0,
                "last_used": row["last_used"] or 0,
                "limitation_type": row["limitation_type"],
                "limitation_until": row["limitation_until"] or 0,
            }
        cur.close()
        return out
    except Exception as e:
        logger.error(f"get_adder_limits err: {e}")
        return {}

@db_retry()
def set_adder_limit(phone, added, limitation_type=None, limitation_until=0):
    try:
        cur = get_conn().cursor()
        cur.execute("""
            INSERT INTO adder_limits_tbl (phone, added, last_used, limitation_type, limitation_until)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (phone) DO UPDATE SET
                added = EXCLUDED.added,
                last_used = EXCLUDED.last_used,
                limitation_type = COALESCE(EXCLUDED.limitation_type, adder_limits_tbl.limitation_type),
                limitation_until = CASE WHEN EXCLUDED.limitation_until > 0 THEN EXCLUDED.limitation_until ELSE adder_limits_tbl.limitation_until END
        """, (phone, int(added), int(time.time()), limitation_type, int(limitation_until)))
        cur.close()
    except Exception as e:
        logger.error(f"set_adder_limit err: {e}")
@db_retry()
def reset_adder_limits():
    try:
        cur = get_conn().cursor()
        cur.execute("UPDATE adder_limits_tbl SET added=0, last_used=%s", (int(time.time()),))
        cur.close()
    except Exception as e:
        logger.error(f"reset_adder_limits err: {e}")
@db_retry()
def clear_account_limitation(phone):
    try:
        cur = get_conn().cursor()
        cur.execute("UPDATE adder_limits_tbl SET limitation_type=NULL, limitation_until=0 WHERE phone=%s", (phone,))
        cur.close()
    except Exception as e:
        logger.error(f"clear_account_limitation err: {e}")
@db_retry()
def get_account_status(phone):
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        cur.execute("SELECT added, last_used, limitation_type, limitation_until FROM adder_limits_tbl WHERE phone=%s", (phone,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return {"added": 0, "status": "healthy", "limitation_type": None, "limitation_until": 0}

        lim_type = row["limitation_type"]
        lim_until = row["limitation_until"] or 0
        now = int(time.time())

        if lim_until > 0 and now >= lim_until:
            clear_account_limitation(phone)
            lim_type = None
            lim_until = 0

        status = "healthy"
        if lim_type:
            status = "limited"
        elif (row["added"] or 0) >= 200:
            status = "full"

        return {
            "added": row["added"] or 0,
            "status": status,
            "limitation_type": lim_type,
            "limitation_until": lim_until,
            "remaining_seconds": max(0, lim_until - now) if lim_until > 0 else 0
        }
    except Exception as e:
        logger.error(f"get_account_status err: {e}")
        return {"added": 0, "status": "healthy", "limitation_type": None, "limitation_until": 0}

# ---------------- Added members history ----------------
@db_retry()
def mark_added(group_id, user_id, phone):
    try:
        cur = get_conn().cursor()
        cur.execute("""
            INSERT INTO added_history_tbl (group_id, user_id, added_at, account_phone)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (group_id, user_id) DO NOTHING
        """, (int(group_id), int(user_id), int(time.time()), phone or ""))
        cur.close()
        _maybe_autopurge()
    except Exception as e:
        logger.error(f"mark_added err: {e}")
@db_retry()
def is_added(group_id, user_id):
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT 1 FROM added_history_tbl WHERE group_id=%s AND user_id=%s", (int(group_id), int(user_id)))
        row = cur.fetchone()
        cur.close()
        return bool(row)
    except:
        return False


@db_retry()
def get_added_user_ids(group_id):
    """
    همه user_idهایی که قبلاً به این گروه اضافه شده‌اند — در یک کوئری.

    ⚠️ چرا این تابع لازم است (نسخه ۱.۴.۵):
    فیلتر ادد قبلاً به ازای *هر* کاربر یک بار is_added() صدا می‌زد. با
    ۱۰٬۰۰۰ ممبر یعنی ۱۰٬۰۰۰ رفت‌وبرگشت جداگانه به Postgres — چند دقیقه
    طول می‌کشید و چون داخل هندلر HTTP بود، مینی‌اپ تایم‌اوت می‌خورد و
    دکمه ادد «کار نمی‌کرد».

    یک کوئری به‌جای ۱۰٬۰۰۰ تا.
    """
    try:
        cur = get_conn().cursor()
        if group_id:
            cur.execute(
                "SELECT user_id FROM added_history_tbl WHERE group_id=%s",
                (int(group_id),),
            )
        else:
            cur.execute("SELECT user_id FROM added_history_tbl")
        ids = {int(r[0]) for r in cur.fetchall() if r and r[0] is not None}
        cur.close()
        return ids
    except Exception as e:
        logger.error(f"get_added_user_ids err: {e}")
        return set()

@db_retry()
def count_added_by_account(phone):
    """
    مجموع اددهای موفق تاریخی یک اکانت (همه گروه‌ها، همه روزها).

    مبنای «گرم کردن» اکانت است: اکانت بدون سابقه نباید روز اول ۱۰۰ نفر
    اضافه کند — مستقیم PEER_FLOOD می‌گیرد (درس ۱.۵.۸).
    """
    try:
        cur = get_conn().cursor()
        cur.execute(
            "SELECT COUNT(*) FROM added_history_tbl WHERE account_phone=%s",
            (str(phone),),
        )
        r = cur.fetchone()[0]
        cur.close()
        return int(r or 0)
    except Exception as e:
        logger.error(f"count_added_by_account err: {e}")
        return 0


@db_retry()
def count_added(group_id=None):
    try:
        cur = get_conn().cursor()
        if group_id:
            cur.execute("SELECT COUNT(*) FROM added_history_tbl WHERE group_id=%s", (int(group_id),))
        else:
            cur.execute("SELECT COUNT(*) FROM added_history_tbl")
        r = cur.fetchone()[0]
        cur.close()
        return r
    except:
        return 0


@db_retry()
def most_used_add_dest():
    """آیدی گروهی که بیشترین ادد ثبت‌شده را دارد (برای وقتی config.group_id=0 است)."""
    try:
        cur = get_conn().cursor()
        cur.execute(
            "SELECT group_id FROM added_history_tbl "
            "WHERE group_id IS NOT NULL AND group_id <> 0 "
            "GROUP BY group_id ORDER BY COUNT(*) DESC LIMIT 1"
        )
        row = cur.fetchone()
        cur.close()
        return int(row[0]) if row else None
    except Exception:
        return None

# ---------------- Background scanner state ----------------
@db_retry()
def set_bg_scan(enabled, target_group_id=None, account_phone=None, interval_minutes=60):
    try:
        cur = get_conn().cursor()
        cur.execute("""
            UPDATE bg_scan_state SET
                enabled=%s,
                target_group_id=COALESCE(%s, target_group_id),
                account_phone=COALESCE(%s, account_phone),
                interval_minutes=%s
            WHERE id=1
        """, (bool(enabled), int(target_group_id) if target_group_id else None, account_phone, int(interval_minutes)))
        cur.close()
    except Exception as e:
        logger.error(f"set_bg_scan err: {e}")
@db_retry()
def get_bg_scan():
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        cur.execute("SELECT enabled, target_group_id, account_phone, interval_minutes, last_run, total_found, status FROM bg_scan_state WHERE id=1")
        row = cur.fetchone()
        cur.close()
        if row:
            return dict(row)
        return {"enabled": False, "target_group_id": None, "account_phone": None, "interval_minutes": 60, "last_run": 0, "total_found": 0, "status": "idle"}
    except Exception as e:
        logger.error(f"get_bg_scan err: {e}")
        return {"enabled": False, "target_group_id": None, "account_phone": None, "interval_minutes": 60, "last_run": 0, "total_found": 0, "status": "idle"}

@db_retry()
def mark_bg_run(total_new):
    try:
        cur = get_conn().cursor()
        cur.execute("UPDATE bg_scan_state SET last_run=%s, total_found=total_found+%s WHERE id=1", (int(time.time()), int(total_new)))
        cur.close()
    except Exception as e:
        logger.error(f"mark_bg_run err: {e}")
@db_retry()
def set_bg_status(status):
    try:
        cur = get_conn().cursor()
        cur.execute("UPDATE bg_scan_state SET status=%s WHERE id=1", (str(status),))
        cur.close()
    except Exception as e:
        logger.error(f"set_bg_status err: {e}")
# ---------------- Projects storage (Hunter / Finder) ----------------
@db_retry()
def save_project(url, platform, full_name, category, data):
    try:
        cur = get_conn().cursor()
        cur.execute("""
            INSERT INTO projects_tbl (url, platform, full_name, category, data, found_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET
                full_name=EXCLUDED.full_name, category=EXCLUDED.category, data=EXCLUDED.data
        """, (url, platform, full_name, category, Json(data), int(time.time())))
        cur.close()
    except Exception as e:
        logger.error(f"save_project err: {e}")
@db_retry()
def load_projects(category=None):
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        if category:
            cur.execute("SELECT url, platform, full_name, category, data, found_at FROM projects_tbl WHERE category=%s ORDER BY found_at DESC", (category,))
        else:
            cur.execute("SELECT url, platform, full_name, category, data, found_at FROM projects_tbl ORDER BY found_at DESC")
        out = []
        for row in cur.fetchall():
            d = dict(row["data"]) if row["data"] else {}
            d.update({
                "url": row["url"],
                "platform": row["platform"],
                "full_name": row["full_name"],
                "category": row["category"],
                "found_at": row["found_at"]
            })
            out.append(d)
        cur.close()
        return out
    except Exception as e:
        logger.error(f"load_projects err: {e}")
        return []

@db_retry()
def count_projects():
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT COUNT(*) FROM projects_tbl")
        r = cur.fetchone()[0]
        cur.close()
        return r
    except:
        return 0

@db_retry()
def clear_projects():
    try:
        cur = get_conn().cursor()
        cur.execute("DELETE FROM projects_tbl")
        cur.close()
    except Exception as e:
        logger.error(f"clear_projects err: {e}")
# ----- Sync JSON->DB on first run ----------------
def migrate_json_to_db():
    """Import existing JSON files into DB on first run (one-shot)."""
    try:
        if os.path.exists("scraped_users.json"):
            with open("scraped_users.json", "r", encoding="utf-8") as f:
                d = json.load(f)
            users = d.get("users", []) or []
            gid = d.get("group_id", 0)
            gname = d.get("group_name", "")
            bulk_save_users(users, gid, gname)
            logger.info(f"[migrate] imported {len(users)} users from JSON")
        if os.path.exists("saved_accounts.json"):
            with open("saved_accounts.json","r",encoding="utf-8") as f:
                accs = json.load(f)
            for phone, info in accs.items():
                save_account(phone, info.get("name",""), info.get("username",""), info.get("device_fp"))
            logger.info(f"[migrate] imported {len(accs)} accounts")
        if os.path.exists("adder_limits.json"):
            with open("adder_limits.json","r",encoding="utf-8") as f:
                lim = json.load(f)
            for phone, info in lim.items():
                set_adder_limit(phone, info.get("added",0))

        if os.path.exists("config.json"):
            with open("config.json","r",encoding="utf-8") as f:
                c = json.load(f)
            if c.get("group_id"):
                set_config(c["group_id"], c.get("group_name",""), c.get("defense_enabled",True))
    except Exception as e:
        logger.error(f"migrate err: {e}")
# Initialize tables at import time (محافظت‌شده: اگر دیتابیس موقتاً در دسترس نبود،
# ماژول همچنان import می‌شود و bot.py موقع استارت دوباره تلاش می‌کند)
try:
    init_tables()
except Exception as e:
    logger.warning("init_tables deferred at import: %s", e)


# ---------------- Scanned Chats History (group/channel tracker) ----------------
@db_retry()
def upsert_scanned_chat(chat_id, chat_name, chat_type="group", category="",
                         total_members=0, extracted_new=0, progress_pct=None):
    """Register or update a chat in scan history"""
    try:
        cur = get_conn().cursor()
        existing = get_scanned_chat(chat_id)
        now = int(time.time())
        if existing:
            new_extracted = (existing.get("extracted_count") or 0) + int(extracted_new or 0)
            new_pct = progress_pct if progress_pct is not None else existing.get("progress_pct") or 0
            new_total = int(total_members or 0) or existing.get("total_members_estimate") or 0
            new_cat = category if category else existing.get("category") or ""
            cur.execute("""
                UPDATE scanned_chats_tbl SET
                    chat_name=%s, chat_type=%s, category=%s,
                    total_members_estimate=%s, extracted_count=%s, progress_pct=%s,
                    last_scan=%s, scan_count=COALESCE(scan_count,0)+1
                WHERE chat_id=%s
            """, (chat_name, chat_type, new_cat, new_total, new_extracted, new_pct,
                  now, int(chat_id)))
        else:
            cur.execute("""
                INSERT INTO scanned_chats_tbl
                (chat_id, chat_name, chat_type, category, total_members_estimate,
                 extracted_count, progress_pct, last_scan, first_scan, scan_count)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            """, (int(chat_id), chat_name, chat_type, category or "",
                  int(total_members or 0), int(extracted_new or 0),
                  int(progress_pct or 0), now, now))
        cur.close()
    except Exception as e:
        logger.error(f"upsert_scanned_chat err: {e}")
@db_retry()
def get_scanned_chats(category=None):
    """Get list of scanned chats, optionally filtered by category"""
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        if category:
            cur.execute("SELECT * FROM scanned_chats_tbl WHERE category=%s ORDER BY last_scan DESC", (category,))
        else:
            cur.execute("SELECT * FROM scanned_chats_tbl ORDER BY is_favorite DESC, last_scan DESC")
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"get_scanned_chats err: {e}")
        return []

@db_retry()
def get_scanned_chat(chat_id):
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        cur.execute("SELECT * FROM scanned_chats_tbl WHERE chat_id=%s", (int(chat_id),))
        r = cur.fetchone()
        cur.close()
        return dict(r) if r else None
    except:
        return None

@db_retry()
def update_chat_category(chat_id, category):
    try:
        cur = get_conn().cursor()
        cur.execute("UPDATE scanned_chats_tbl SET category=%s WHERE chat_id=%s", (category, int(chat_id)))
        cur.close()
    except: pass

@db_retry()
def update_chat_progress(chat_id, extracted_new, progress_pct):
    try:
        cur = get_conn().cursor()
        cur.execute("""
            UPDATE scanned_chats_tbl SET
                extracted_count = COALESCE(extracted_count,0) + %s,
                progress_pct = %s,
                last_scan = %s
            WHERE chat_id = %s
        """, (int(extracted_new), int(progress_pct or 0), int(time.time()), int(chat_id)))
        cur.close()
    except: pass

@db_retry()
def get_users_by_source(source_chat_id=None, category=None, limit=2000, offset=0):
    """Get users filtered by source chat or category"""
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        if category:
            cur.execute("""
                SELECT u.user_id, u.username, u.first_name, u.last_name, u.source_group_id, u.source_group_name
                FROM scraped_users u
                JOIN scanned_chats_tbl c ON u.source_group_id = c.chat_id
                WHERE c.category = %s
                ORDER BY u.added_at DESC LIMIT %s OFFSET %s
            """, (category, limit, offset))
        elif source_chat_id:
            cur.execute("""
                SELECT user_id, username, first_name, last_name, source_group_id, source_group_name
                FROM scraped_users WHERE source_group_id=%s
                ORDER BY added_at DESC LIMIT %s OFFSET %s
            """, (int(source_chat_id), limit, offset))
        else:
            cur.execute("""
                SELECT user_id, username, first_name, last_name, source_group_id, source_group_name
                FROM scraped_users ORDER BY added_at DESC LIMIT %s OFFSET %s
            """, (limit, offset))
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"get_users_by_source err: {e}")
        return []

@db_retry()
def count_users_by_source(source_chat_id=None, category=None):
    try:
        cur = get_conn().cursor()
        if category:
            cur.execute("""
                SELECT COUNT(*) FROM scraped_users u
                JOIN scanned_chats_tbl c ON u.source_group_id = c.chat_id WHERE c.category=%s
            """, (category,))
        elif source_chat_id:
            cur.execute("SELECT COUNT(*) FROM scraped_users WHERE source_group_id=%s", (int(source_chat_id),))
        else:
            cur.execute("SELECT COUNT(*) FROM scraped_users")
        r = cur.fetchone()[0]
        cur.close()
        return r
    except:
        return 0

@db_retry()
def get_all_categories():
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT DISTINCT category FROM scanned_chats_tbl WHERE category IS NOT NULL AND category != '' ORDER BY category")
        res = [r[0] for r in cur.fetchall()]
        cur.close()
        return res
    except:
        return []

@db_retry()
def get_category_stats():
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        cur.execute("""
            SELECT category, COUNT(*) as chat_count,
                   COALESCE(SUM(extracted_count),0) as total_users
            FROM scanned_chats_tbl WHERE category != ''
            GROUP BY category ORDER BY total_users DESC
        """)
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    except:
        return []

@db_retry()
def delete_scanned_chat(chat_id):
    try:
        cur = get_conn().cursor()
        cur.execute("DELETE FROM scanned_chats_tbl WHERE chat_id=%s", (int(chat_id),))
        cur.close()
    except: pass

@db_retry()
def toggle_chat_favorite(chat_id):
    try:
        cur = get_conn().cursor()
        cur.execute("UPDATE scanned_chats_tbl SET is_favorite = NOT COALESCE(is_favorite, FALSE) WHERE chat_id=%s", (int(chat_id),))
        cur.close()
    except: pass

# ---------------- Favorites / bookmarks ----------------
@db_retry()
def fav_add(url):
    try:
        cur = get_conn().cursor()
        cur.execute("INSERT INTO kv_store (key, value, updated_at) VALUES ('favorites', %s, %s) ON CONFLICT (key) DO NOTHING",
                    (Json([]), int(time.time())))
        cur.execute("UPDATE kv_store SET value = value || %s::jsonb WHERE key='favorites' AND NOT value @> %s::jsonb",
                    (Json([url]), Json([url])))
        cur.close()
    except Exception as e:
        logger.error(f"fav_add err: {e}")
@db_retry()
def fav_remove(url):
    try:
        cur = get_conn().cursor()
        cur.execute("UPDATE kv_store SET value = (SELECT jsonb_agg(elem) FROM jsonb_array_elements(value) elem WHERE elem::text <> %s::text) WHERE key='favorites'",
                    (json.dumps(url),))
        cur.close()
    except Exception as e:
        logger.error(f"fav_remove err: {e}")
def fav_list():
    v = kv_get("favorites", []) or []
    return list(v)

def is_fav(url):
    return url in fav_list()

@db_retry()
def delete_user(user_id: int) -> bool:
    try:
        cur = get_conn().cursor()
        cur.execute("DELETE FROM scraped_users WHERE user_id=%s", (int(user_id),))
        deleted = cur.rowcount > 0
        cur.close()
        return deleted
    except: return False

@db_retry()
def delete_users_bulk(user_ids: list) -> int:
    if not user_ids: return 0
    try:
        cur = get_conn().cursor()
        cur.execute("DELETE FROM scraped_users WHERE user_id = ANY(%s)", (list(map(int, user_ids)),))
        deleted = cur.rowcount
        cur.close()
        return deleted
    except: return 0


# ---------------- 🚫 Do-Not-Add Blacklist (هرگز دوباره ادد نشود) ----------------

@db_retry()
def add_do_not_add(user_id: int, reason: str = "") -> bool:
    """افزودن کاربر به لیست «هرگز دوباره ادد نشود» (پرایوسی بسته / لفت داده / آیدی نامعتبر)"""
    try:
        cur = get_conn().cursor()
        cur.execute("""
            INSERT INTO do_not_add_tbl (user_id, reason, added_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                reason = EXCLUDED.reason,
                added_at = EXCLUDED.added_at
        """, (int(user_id), reason or "", int(time.time())))
        cur.close()
        return True
    except Exception as e:
        logger.error(f"add_do_not_add err: {e}")
        return False


@db_retry()
def is_do_not_add(user_id: int) -> bool:
    """آیا کاربر در لیست ممنوعه است؟"""
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT 1 FROM do_not_add_tbl WHERE user_id=%s", (int(user_id),))
        row = cur.fetchone()
        cur.close()
        return bool(row)
    except:
        return False


@db_retry()
def count_do_not_add() -> int:
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT COUNT(*) FROM do_not_add_tbl")
        r = cur.fetchone()[0]
        cur.close()
        return r
    except:
        return 0


@db_retry()
def is_added_anywhere(user_id: int) -> bool:
    """آیا این کاربر قبلاً توسط بات به *هر* گروهی ادد شده؟ (جلوگیری از ادد تکراری برای همیشه)"""
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT 1 FROM added_history_tbl WHERE user_id=%s LIMIT 1", (int(user_id),))
        row = cur.fetchone()
        cur.close()
        return bool(row)
    except:
        return False


def get_blocked_user_ids() -> set:
    """ست کامل آیدی کاربرانی که هرگز نباید دوباره ادد/استخراج شوند:
    (۱) هر کسی که قبلاً ادد شده + (۲) هر کسی که در لیست ممنوعه است."""
    blocked = set()
    try:
        cur = get_conn().cursor()
        cur.execute("SELECT user_id FROM added_history_tbl")
        blocked.update(int(r[0]) for r in cur.fetchall())
        cur.close()
        cur = get_conn().cursor()
        cur.execute("SELECT user_id FROM do_not_add_tbl")
        blocked.update(int(r[0]) for r in cur.fetchall())
        cur.close()
    except Exception as e:
        logger.error(f"get_blocked_user_ids err: {e}")
    return blocked



# ---------------- Leads CRM Storage (Game Lead Finder) ----------------
@db_retry()
def save_lead(title, url, source="web", category="گیمینگ", phone="", telegram="", instagram="", score=50, status="new", notes="", extra=None):
    try:
        cur = get_conn().cursor()
        now = int(time.time())
        import random
        clean_url = url or f"custom_{now}_{random.randint(1000,9999)}"
        cur.execute("""
            INSERT INTO leads_tbl (title, url, source, category, phone, telegram_username, instagram_username, score, status, notes, created_at, extra)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET
                title = EXCLUDED.title,
                category = COALESCE(NULLIF(EXCLUDED.category, ''), leads_tbl.category),
                phone = COALESCE(NULLIF(EXCLUDED.phone, ''), leads_tbl.phone),
                telegram_username = COALESCE(NULLIF(EXCLUDED.telegram_username, ''), leads_tbl.telegram_username),
                instagram_username = COALESCE(NULLIF(EXCLUDED.instagram_username, ''), leads_tbl.instagram_username),
                score = GREATEST(EXCLUDED.score, leads_tbl.score),
                extra = EXCLUDED.extra
        """, (title, clean_url, source, category or "گیمینگ", phone or "", telegram or "", instagram or "", int(score), status or "new", notes or "", now, Json(extra or {})))
        cur.close()
        return True
    except Exception as e:
        logger.error(f"save_lead err: {e}")
        return False

@db_retry()
def load_leads(category=None, status=None, limit=200, offset=0):
    try:
        cur = get_conn().cursor(cursor_factory=DictCursor)
        query = "SELECT * FROM leads_tbl WHERE 1=1"
        params = []
        if category and category != 'all':
            query += " AND category=%s"
            params.append(category)
        if status and status != 'all':
            query += " AND status=%s"
            params.append(status)
        query += " ORDER BY score DESC, created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        cur.execute(query, tuple(params))
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"load_leads err: {e}")
        return []

@db_retry()
def count_leads(status=None):
    try:
        cur = get_conn().cursor()
        if status and status != 'all':
            cur.execute("SELECT COUNT(*) FROM leads_tbl WHERE status=%s", (status,))
        else:
            cur.execute("SELECT COUNT(*) FROM leads_tbl")
        r = cur.fetchone()[0]
        cur.close()
        return r
    except:
        return 0

@db_retry()
def update_lead_status(lead_id, status):
    try:
        cur = get_conn().cursor()
        cur.execute("UPDATE leads_tbl SET status=%s WHERE id=%s", (status, int(lead_id)))
        cur.close()
        return True
    except Exception as e:
        logger.error(f"update_lead_status err: {e}")
        return False

@db_retry()
def delete_lead(lead_id):
    try:
        cur = get_conn().cursor()
        cur.execute("DELETE FROM leads_tbl WHERE id=%s", (int(lead_id),))
        cur.close()
        return True
    except Exception as e:
        logger.error(f"delete_lead err: {e}")
        return False



# ═══════════════ 🧹 پاکسازی خودکار دیتابیس ═══════════════
# رکورد کاملِ اددشده‌ها لازم نیست؛ دفتر کلِ added_history_tbl و do_not_add_tbl
# جلوی ادد دوباره را می‌گیرند (bulk_save_users همان‌ها را فیلتر می‌کند).
_ADD_PURGE_EVERY = 300
_add_purge_state = {"n": 0}


def purge_added_users() -> int:
    """حذف رکورد کامل کاربران اددشده؛ تاریخچه در added_history_tbl می‌ماند."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM scraped_users
            WHERE user_id IN (SELECT DISTINCT user_id FROM added_history_tbl)
        """)
        n = cur.rowcount or 0
        conn.commit()
        cur.close()
        conn.close()
        return int(n)
    except Exception as e:
        logger.error(f"purge_added_users err: {e}")
        return 0


def purge_do_not_add_users() -> int:
    """حذف رکورد کامل لیست‌سیاه‌ها؛ خودشان در do_not_add_tbl می‌مانند."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM scraped_users
            WHERE user_id IN (SELECT DISTINCT user_id FROM do_not_add_tbl)
        """)
        n = cur.rowcount or 0
        conn.commit()
        cur.close()
        conn.close()
        return int(n)
    except Exception as e:
        logger.error(f"purge_do_not_add_users err: {e}")
        return 0


def _maybe_autopurge():
    """هر ۳۰۰ ادد، یک پاکسازی خودکارِ سبک."""
    try:
        _add_purge_state["n"] += 1
        if _add_purge_state["n"] >= _ADD_PURGE_EVERY:
            _add_purge_state["n"] = 0
            purge_added_users()
    except Exception:
        pass
