"""تست پروکسی‌های پول اتصال دیتابیس"""
import db as database


class FakeCursor:
    def __init__(self):
        self.closed = False

    def execute(self, *a, **k):
        pass

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self):
        self.autocommit = False

    def cursor(self, *a, **k):
        return FakeCursor()

    def poll(self):
        return None

    @property
    def closed(self):
        return 0


class FakePool:
    def __init__(self):
        self.checked_out = 0
        self.returned = 0

    def getconn(self):
        self.checked_out += 1
        return FakeConn()

    def putconn(self, conn):
        self.returned += 1


def test_cursor_close_releases_connection():
    pool = FakePool()
    conn = database._PooledConn(pool, FakeConn())
    cur = conn.cursor()
    assert isinstance(cur, database._PooledCursor)
    cur.execute("SELECT 1")
    cur.close()
    assert pool.returned == 1, "اتصال باید با close کرسور به پول برگردد"


def test_double_close_is_safe():
    pool = FakePool()
    conn = database._PooledConn(pool, FakeConn())
    cur = conn.cursor()
    cur.close()
    cur.close()  # نباید دو بار آزاد شود / خطا بدهد
    assert pool.returned == 1


def test_attribute_passthrough():
    pool = FakePool()
    conn = database._PooledConn(pool, FakeConn())
    assert conn.closed == 0
    assert conn.poll() is None


def test_release_then_gc_is_safe():
    pool = FakePool()
    conn = database._PooledConn(pool, FakeConn())
    conn.release()
    conn.release()
    conn.__del__()  # توری ایمنی
    assert pool.returned == 1
