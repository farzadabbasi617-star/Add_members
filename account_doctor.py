"""
=================================================================
🔍 Account Doctor — تشخیص دقیق سلامت اکانت‌ها برای استخراج ممبر
=================================================================
برای هر اکانت به‌ترتیب بررسی می‌کند:
  ۱) آیا سشن در دیتابیس ابری (بکاپ) وجود دارد؟
  ۲) آیا فایل سشن روی دیسک هست؟ (بعد از ری‌استارت رندر پاک می‌شود!)
  ۳) آیا اتصال برقرار می‌شود؟ (auth key هنوز زنده است؟)
  ۴) هویت: get_me() با اطلاعات ثبت‌شده اکانت یکی است؟
  ۵) لیست دیالوگ‌ها (گروه‌ها/کانال‌ها) در دسترس است؟
  ۶) تست واقعی استخراج: از گروه هدف چند ممبر قابل خواندن است؟

نتیجه هر مرحله با علت دقیق فارسی گزارش می‌شود.
"""
import os
import time
import asyncio
import logging

import config
import db as _db
import account_state

from attacker import AdvancedScraper, SESSIONS_DIR, safe_phone_filename, DEVICE_FP, _enable_wal_on_session
from pyrogram.errors import (
    AuthKeyDuplicated, AuthKeyUnregistered, SessionPasswordNeeded,
    FloodWait, UserDeactivated, UserDeactivatedBan,
)

logger = logging.getLogger("antiscraper.doctor")


def _session_path(phone):
    return os.path.join(SESSIONS_DIR, f"acc_{safe_phone_filename(phone)}.session")


def session_disk_ok(phone):
    """چک ارزان فقط روی دیسک — مناسب پولینگ مینی‌اپ (بدون کوئری DB)"""
    path = _session_path(phone)
    try:
        return os.path.exists(path) and os.path.getsize(path) > 100
    except Exception:
        return False


def check_session_local(phone):
    """بررسی محلی: فایل سشن روی دیسک + بکاپ در دیتابیس"""
    res = {"phone": phone, "disk_file": False, "db_blob": False, "disk_size": 0, "blob_size": 0}
    path = _session_path(phone)
    if os.path.exists(path):
        size = os.path.getsize(path)
        res["disk_file"] = size > 100
        res["disk_size"] = size
    try:
        blob = _db.load_session_blob(phone)
        if blob:
            res["db_blob"] = True
            res["blob_size"] = len(blob)
    except Exception as e:
        logger.warning("check_session_local blob err for %s: %s", phone, e)
    return res


def inspect_session(phone):
    """باز کردن فایل سشن (SQLite) بدون اتصال به تلگرام."""
    import sqlite3
    import tempfile
    out = {
        "phone": phone, "ok": False, "user_id": None, "dc_id": None,
        "auth_key_len": 0, "error": "",
    }
    blob = None
    try:
        if session_disk_ok(phone):
            path = _session_path(phone)
            con = sqlite3.connect(path)
            cur = con.cursor()
            cur.execute("SELECT dc_id, user_id, length(auth_key) FROM sessions LIMIT 1")
            row = cur.fetchone()
            con.close()
        else:
            blob = _db.load_session_blob(phone)
            if not blob:
                out["error"] = "سشن در دیسک و بکاپ نیست"
                return out
            fd, path = tempfile.mkstemp(suffix=".session")
            os.close(fd)
            try:
                with open(path, "wb") as f:
                    f.write(blob)
                con = sqlite3.connect(path)
                cur = con.cursor()
                cur.execute("SELECT dc_id, user_id, length(auth_key) FROM sessions LIMIT 1")
                row = cur.fetchone()
                con.close()
            finally:
                try:
                    os.remove(path)
                except Exception:
                    pass
        if not row:
            out["error"] = "جدول sessions خالی است"
            return out
        out["dc_id"], out["user_id"], out["auth_key_len"] = row[0], row[1], row[2] or 0
        if not out["user_id"]:
            out["error"] = "لاگین ناتمام است (user_id خالی) — باید دوباره لاگین شود"
            return out
        if (out["auth_key_len"] or 0) < 64:
            out["error"] = "auth_key ناقص است — سشن سوخته"
            return out
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = f"سشن قابل خواندن نیست: {type(e).__name__}: {e}"
        return out


def load_probe_results():
    return _db.kv_get("account_probe_results", {}) or {}


def save_probe_result(phone, res):
    allr = load_probe_results()
    me = res.get("me") or {}
    allr[str(phone)] = {
        "ok": res.get("ok"),
        "error": res.get("error") or "",
        "stage": res.get("stage") or "",
        "me": me,
        "dialogs_count": res.get("dialogs_count") or 0,
        "ts": int(time.time()),
        "source": res.get("source") or "live",
        "session_user_id": me.get("id") or res.get("session_user_id"),
        "note": res.get("note") or "",
    }
    _db.kv_set("account_probe_results", allr)


def clear_probe_result(phone):
    """حذف نتیجه‌ی تست یک اکانت.

    ⚠️ بعد از لاگین موفق حتماً باید صدا زده شود. وگرنه نتیجه‌ی «خراب» که
    مربوط به *قبل* از لاگین است باقی می‌ماند و get_accounts_dict اکانت
    تازه و سالم را «خراب» نشان می‌دهد. دقیقاً همین اتفاق برای
    +989924237228 افتاد: probe در 10:57 گفت «سشن نیست» (درست بود)، سشن
    در 11:09 ذخیره شد، ولی نتیجه‌ی کهنه پاک نشد.
    """
    allr = load_probe_results()
    if str(phone) in allr:
        allr.pop(str(phone), None)
        _db.kv_set("account_probe_results", allr)
        return True
    return False


def session_login_complete(phone):
    ins = inspect_session(phone)
    return bool(ins.get("ok") and ins.get("user_id"))


async def probe_zero_add_accounts(quick=True):
    """تست زنده فقط اکانت‌هایی که هنوز ادد نزده‌اند."""
    accs = _db.load_accounts() or {}
    cfg = {}
    try:
        cfg = _db.get_config() or {}
    except Exception:
        pass
    target = cfg.get("group_id")
    results = []
    for phone, info in accs.items():
        try:
            st = _db.get_account_status(phone)
        except Exception:
            st = {"added": 0, "status": "healthy"}
        if (st.get("added") or 0) > 0:
            continue
        if st.get("status") == "limited":
            continue
        ins = inspect_session(phone)
        if not ins.get("ok"):
            rec = {
                "phone": phone, "ok": False, "error": ins.get("error"),
                "source": "inspect", "session_user_id": ins.get("user_id"),
            }
            save_probe_result(phone, rec)
            results.append(rec)
            continue
        res = await probe_account(phone, target_group_id=target, quick=quick)
        res["source"] = "live"
        save_probe_result(phone, res)
        # اگر وصل شد و اسم ناقص بود، از تلگرام اسم واقعی را بنویس
        if res.get("ok") and res.get("me"):
            me = res["me"]
            real_name = (me.get("first_name", "") + " " + me.get("last_name", "")).strip()
            if real_name and (not info.get("name") or info.get("name") in ("un", "none", "")):
                try:
                    _db.save_account(phone, real_name, me.get("username") or "", info.get("device_fp"))
                except Exception:
                    pass
        results.append(res)
        await asyncio.sleep(2)
    return results


def restore_session_from_db(phone):
    """بازیابی سشن از دیتابیس به دیسک (برای بعد از ری‌استارت رندر)"""
    res = check_session_local(phone)
    if res["disk_file"]:
        return True, "سشن روی دیسک موجود بود."
    if not res["db_blob"]:
        return False, "نه فایل سشن روی دیسک هست و نه بکاپی در دیتابیس — اکانت باید دوباره اضافه شود (لاگین مجدد)."
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        blob = _db.load_session_blob(phone)
        with open(_session_path(phone), "wb") as f:
            f.write(blob)
        base = _session_path(phone)[:-8]
        _enable_wal_on_session(base)
        return True, f"سشن از بکاپ دیتابیس بازیابی شد ({len(blob):,} بایت)."
    except Exception as e:
        return False, f"خطا در بازیابی سشن: {e}"


def diagnose_offline(phone, acc_info=None):
    """تشخیص سریع بدون اتصال به تلگرام — علت «سالم ولی کار نمی‌کند»"""
    acc_info = acc_info or {}
    local = check_session_local(phone)
    busy = account_state.busy_label(phone)
    try:
        st = _db.get_account_status(phone)
    except Exception:
        st = {"added": 0, "status": "healthy"}
    reasons = []
    status = "healthy"

    if busy:
        status = "busy"
        reasons.append(f"الان مشغول است ({busy})")
    if not local["disk_file"] and not local["db_blob"]:
        status = "no_session"
        reasons.append("سشن موجود نیست (نه روی دیسک، نه در بکاپ ابری) — باید دوباره لاگین شود")
    elif not local["disk_file"] and local["db_blob"]:
        reasons.append("سشن فقط در بکاپ ابری است و روی دیسک نیست (بعد از ری‌استارت رندر هنوز بازیابی نشده)")
    if st.get("status") == "limited":
        status = "limited"
        mins = int((st.get("remaining_seconds") or 0) / 60)
        reasons.append(f"محدودیت FloodWait فعال است (~{mins} دقیقه)")
    elif (st.get("added") or 0) >= getattr(config, "MAX_ADD_PER_ACCOUNT", 1000) and status == "healthy":
        # سقف از config می‌آید، نه عدد هاردکد — وگرنه اکانت سالم
        # بی‌دلیل «پر» نشان داده می‌شود.
        status = "full"
        reasons.append("ظرفیت روزانه ادد پر شده")

    if (st.get("added") or 0) == 0 and status == "healthy":
        status = "unused"
        reasons.append("تا حالا هیچ اددی نزده — یا هرگز انتخاب نشده، یا اتصال/استخراجش بی‌صدا شکست خورده")

    last_err = account_state.get_last_error(phone)
    if last_err:
        reasons.append(f"آخرین خطا: {last_err}")

    name = (acc_info.get("name") or "").strip()
    if name.lower() in ("un", "none", "null", "") or len(name) <= 1:
        reasons.append("نام اکانت ناقص است (احتمال لاگین ناتمام)")

    return {
        "phone": phone,
        "name": name or phone,
        "status": status,
        "reasons": reasons,
        "disk_file": local["disk_file"],
        "db_blob": local["db_blob"],
        "disk_size": local["disk_size"],
        "blob_size": local["blob_size"],
        "added": st.get("added") or 0,
        "busy": busy,
    }


def diagnose_offline_all():
    accs = _db.load_accounts() or {}
    return [diagnose_offline(phone, info) for phone, info in accs.items()]


def render_offline_report(rows):
    if not rows:
        return "🔍 هیچ اکانتی ثبت نشده."
    lines = [
        "🔍 <b>گزارش سریع سلامت اکانت‌ها</b>",
        "<i>بدون اتصال به تلگرام — فقط سشن و آمار محلی</i>",
        "━━━━━━━━━━━━━━━━━━",
    ]
    icons = {
        "healthy": "✅", "unused": "⚪", "no_session": "🔴",
        "limited": "⛔", "busy": "🟡", "full": "⚠️",
    }
    labels = {
        "healthy": "سالم", "unused": "هرگز استفاده نشده",
        "no_session": "بدون سشن", "limited": "محدود",
        "busy": "مشغول", "full": "ظرفیت پر",
    }
    for r in rows:
        ic = icons.get(r["status"], "•")
        lb = labels.get(r["status"], r["status"])
        lines.append(f"{ic} <b>{r['name']}</b> <code>{r['phone']}</code>")
        lines.append(f"   وضعیت: {lb} · ادد: {r['added']}/100")
        sess = []
        sess.append("دیسک✅" if r["disk_file"] else "دیسک❌")
        sess.append("بکاپ✅" if r["db_blob"] else "بکاپ❌")
        lines.append("   سشن: " + " · ".join(sess))
        for reason in r["reasons"][:3]:
            lines.append(f"   • {reason}")
        lines.append("")
    lines.append("💡 برای تست زنده اتصال، یک اکانت را جداگانه انتخاب کن.")
    return "\n".join(lines)



def ensure_session(phone):
    """سشن را روی دیسک حاضر کن (از بکاپ ابری اگر لازم شد)."""
    if session_disk_ok(phone):
        return True, "ok"
    return restore_session_from_db(phone)


def ensure_all_sessions():
    """بازیابی سشن همه اکانت‌ها — بعد از ری‌استارت رندر ضروری است."""
    accs = _db.load_accounts() or {}
    restored, missing = [], []
    for phone in accs:
        ok, msg = ensure_session(phone)
        if ok:
            restored.append(phone)
        else:
            missing.append((phone, msg))
    return restored, missing


def collect_ready_accounts():
    """اکانت‌های قابل‌استفاده برای ادد/استخراج (شامل صفر-اددها)."""
    ensure_all_sessions()
    accs = _db.load_accounts() or {}
    ready = {}
    skipped = []
    for phone, info in accs.items():
        if account_state.busy_label(phone):
            skipped.append((phone, "مشغول"))
            continue
        try:
            st = _db.get_account_status(phone)
        except Exception:
            st = {"added": 0, "status": "healthy"}
        # ⚠️ اکانتِ «محدود» فقط وقتی رد می‌شود که مهلتش هنوز نگذشته باشد.
        # قبلاً هر اکانتی که برچسب limited داشت برای همیشه کنار گذاشته
        # می‌شد، حتی وقتی تلگرام مدت‌ها بود آزادش کرده بود.
        # get_account_status خودش مهلت گذشته را پاک می‌کند، پس اگر هنوز
        # limited است یعنی واقعاً هنوز وقتش نرسیده.
        if st.get("status") == "limited":
            rem = int(st.get("remaining_seconds") or 0)
            skipped.append((phone, f"محدود ({max(1, rem // 60)} دقیقه دیگر)"))
            continue

        # 🚫 سقف مصنوعی ۱۰۰ حذف شد. مالک خواست اکانت‌ها تا حداکثر ظرفیت
        # واقعی کار کنند؛ تلگرام خودش جلویشان را می‌گیرد.
        _hard_cap = getattr(config, "MAX_ADD_PER_ACCOUNT", 1000)
        if (st.get("added") or 0) >= _hard_cap:
            skipped.append((phone, "ظرفیت پر"))
            continue
        ok, msg = ensure_session(phone)
        if not ok:
            skipped.append((phone, msg))
            continue
        ins = inspect_session(phone)
        if not ins.get("ok"):
            skipped.append((phone, ins.get("error") or "سشن ناقص"))
            continue
        pr = (load_probe_results() or {}).get(phone) or {}
        if pr.get("ok") is False:
            skipped.append((phone, pr.get("error") or "تست زنده رد شد"))
            continue
        ready[phone] = info
    return ready, skipped


def pick_scrape_account(preferred=None, skip_limited=True):
    """انتخاب عادلانه اکانت آزاد برای استخراج (کم‌استفاده‌ترین اول)."""
    accs = _db.load_accounts() or {}
    phones = list(accs.keys())
    skipped = []
    usable = []
    for phone in phones:
        lbl = account_state.busy_label(phone)
        if lbl:
            skipped.append((phone, f"مشغول:{lbl}"))
            continue
        if skip_limited:
            try:
                st = _db.get_account_status(phone)
                if st.get("status") == "limited":
                    skipped.append((phone, "محدود"))
                    continue
            except Exception:
                pass
        local = check_session_local(phone)
        if not local["disk_file"] and not local["db_blob"]:
            skipped.append((phone, "بدون سشن"))
            continue
        ins = inspect_session(phone)
        if not ins.get("ok"):
            skipped.append((phone, ins.get("error") or "سشن ناقص"))
            continue
        if not local["disk_file"] and local["db_blob"]:
            ok, msg = restore_session_from_db(phone)
            if not ok:
                skipped.append((phone, f"بازیابی ناموفق:{msg}"))
                continue
        usable.append(phone)

    if not usable:
        return None, None, skipped

    usable.sort(key=lambda p: account_state.last_used(p))
    if preferred and preferred in usable and account_state.last_used(preferred) == 0:
        phone = preferred
    else:
        phone = usable[0]
    return phone, accs.get(phone, {}), skipped


async def probe_account(phone, target_group_id=None, quick=False):
    """تست کامل سلامت استخراج یک اکانت. دیکشنری ساخت‌یافته برمی‌گرداند."""
    res = {
        "phone": phone,
        "ok": False,
        "stage": "",
        "error": "",
        "me": None,
        "saved_identity_ok": None,
        "dialogs_count": 0,
        "target_resolved": None,
        "test_members": 0,
        "duration": 0,
    }
    t0 = time.time()
    claimed = False
    client = None

    ok_b, lbl = account_state.mark_busy(phone, "تست سلامت")
    if not ok_b:
        res["error"] = f"اکانت در حال حاضر مشغول است ({lbl})."
        res["duration"] = int(time.time() - t0)
        return res
    claimed = True

    try:
        local = check_session_local(phone)
        if not local["disk_file"]:
            ok_r, msg_r = restore_session_from_db(phone)
            if not ok_r:
                res["error"] = msg_r
                return res
            res["note"] = msg_r

        accs = _db.load_accounts()
        info = accs.get(phone, {}) or {}
        fp = info.get("device_fp") or DEVICE_FP[0]

        res["stage"] = "اتصال"
        client = AdvancedScraper("", config.API_ID, config.API_HASH, phone=phone, device_fp=fp)
        _enable_wal_on_session(client.app.name)
        try:
            await asyncio.wait_for(client.connect(), timeout=30)
        except asyncio.TimeoutError:
            res["error"] = "اتصال بیش از ۳۰ ثانیه طول کشید (احتمالاً سشن خراب یا شبکه)."
            return res

        res["stage"] = "شناسایی هویت"
        me = await client.app.get_me()
        res["me"] = {
            "id": me.id,
            "first_name": me.first_name or "",
            "last_name": me.last_name or "",
            "username": me.username or "",
            "phone": getattr(me, "phone_number", "") or "",
        }
        saved_uid = info.get("user_id")
        if saved_uid and str(saved_uid) != str(me.id):
            res["saved_identity_ok"] = False
            res["error"] = (
                f"⚠️ سشن این اکانت متعلق به فرد دیگری است! "
                f"(ثبت‌شده: user_id={saved_uid}، واقعی: user_id={me.id}، نام: {me.first_name}) "
                f"— این سشن باید حذف و اکانت دوباره اضافه شود."
            )
            return res
        res["saved_identity_ok"] = True

        res["stage"] = "بارگذاری لیست چت‌ها"
        dcount = 0
        try:
            async for _ in client.app.get_dialogs(limit=60):
                dcount += 1
        except FloodWait as fw:
            res["error"] = f"اکانت در دیالوگ‌ها FloodWait خورده ({fw.value} ثانیه) — فعلاً قفل است."
            return res
        except Exception as e:
            res["error"] = f"لیست چت‌ها قابل خواندن نیست: {type(e).__name__} — {str(e)[:120]}"
            return res
        res["dialogs_count"] = dcount
        if dcount == 0:
            res["error"] = "لیست چت‌ها خالی است — اکانت احتمالاً توسط تلگرام محدود شده یا تازه ساخته شده است."
            return res

        if target_group_id and not quick:
            res["stage"] = "تست استخراج ممبر"
            try:
                chat = await client.app.get_chat(target_group_id)
                res["target_resolved"] = chat.title if chat else None
                cnt = 0
                async for m in client.app.get_chat_members(target_group_id, limit=15):
                    if m.user and not getattr(m.user, "is_bot", False):
                        cnt += 1
                res["test_members"] = cnt
            except FloodWait as fw:
                res["error"] = f"موقع استخراج FloodWait ({fw.value}s) — بعداً دوباره امتحان کنید."
                return res
            except Exception as e:
                res["error"] = f"گروه هدف برای این اکانت قابل اسکن نیست: {type(e).__name__} — {str(e)[:120]}"
                return res

        res["ok"] = True
        return res

    except (AuthKeyDuplicated, AuthKeyUnregistered):
        res["error"] = (
            "🔴 سشن منقضی/سوخته است (AUTH_KEY). "
            "دلیل معمول: استفاده همزمان از یک سشن در دو جا، یا خروج اجباری از سمت تلگرام. "
            "راه‌حل: حذف اکانت و لاگین مجدد."
        )
        return res
    except SessionPasswordNeeded:
        res["error"] = "اکانت تایید دومرحله‌ای (2FA) دارد و رمز آن ثبت نشده — لاگین مجدد لازم است."
        return res
    except (UserDeactivated, UserDeactivatedBan):
        res["error"] = "🚫 اکانت توسط تلگرام غیرفعال/بن شده است."
        return res
    except FloodWait as fw:
        res["error"] = f"اکانت فعلاً FloodWait دارد ({fw.value} ثانیه) — بعداً تست کنید."
        return res
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        return res
    finally:
        res["duration"] = int(time.time() - t0)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        if claimed:
            account_state.release(phone)
        if res.get("ok"):
            account_state.set_last_error(phone, "")
            account_state.mark_used(phone)
        elif res.get("error"):
            account_state.set_last_error(phone, res["error"])


def render_report(res):
    """گزارش خوانا برای نمایش در تلگرام"""
    phone = res.get("phone", "?")
    if not res.get("ok"):
        return (
            "🔍 <b>گزارش سلامت اکانت</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📱 <code>{phone}</code>\n"
            f"⏱ زمان تست: {res.get('duration', 0)} ثانیه\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>مشکل پیدا شد:</b>\n{res.get('error', 'نامشخص')}\n"
        )

    me = res.get("me") or {}
    name = (me.get("first_name", "") + " " + me.get("last_name", "")).strip() or "؟"
    lines = [
        "🔍 <b>گزارش سلامت اکانت</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📱 <code>{phone}</code>",
        f"👤 هویت: {name}" + (f" (@{me['username']})" if me.get("username") else ""),
        f"⏱ زمان تست: {res.get('duration', 0)} ثانیه",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if res.get("note"):
        lines.append(f"♻️ {res['note']}")
    lines.append("✅ اتصال و سشن: سالم")
    lines.append(f"💬 تعداد چت‌های قابل دسترسی: {res.get('dialogs_count', 0)}")
    if res.get("target_resolved"):
        lines.append(f"🎯 گروه هدف: {res['target_resolved']}")
        lines.append(f"🧪 تست استخراج: {res.get('test_members', 0)} ممبر در ۱۵ عضو اول")
        if res.get("test_members", 0) == 0:
            lines.append("⚠️ صفر ممبر! یا گروه خالی است یا اکانت دسترسی خواندن اعضا را ندارد.")
    lines.append("")
    lines.append("نتیجه: این اکانت برای استخراج مشکلی ندارد. 💪")
    return "\n".join(lines)
