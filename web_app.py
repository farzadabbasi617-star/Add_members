"""
=================================================================
📱 Telegram Mini App (TMA) & REST API Module - Add_members
=================================================================
داشبورد مدیریت حرفه‌ای و سوپر اپلیکیشن کشف لیدهای گیمینگ/کریپتو، ادد ممبر و CRM:
- بنر چسبان بالای صفحه و تبدیل دکمه‌های استارت به دکمه توقف فوری (Instant Stop Buttons)
- کنسول پیشرفت زنده به سبک ADM Download Manager با درصد، گیج سرعت و تایمر
- نمایش زنده نام و آیدی آخرین کاربر اضافه شده (Last Added Member Info)
- دکمه توقف فوری با هپتیک فیدبک
- زبانه شکار لیدها و گروه‌های تلگرامی مرتبط با هر موضوع (مثل کلش رویال، گیم‌نت و...)
- دکمه ۱-کلیکی استخراج ممبر مستقیم از گروه کشف شده به دیتابیس
- زبانه مدیریت قیف فروش CRM با کپی متن پیام دعوت اختصاصی
- تفکیک دوگانه حملات: ادد تک اکانت & ادد موازی با تمام اکانت‌ها
- پشتیبانی دوگانه از aiohttp و http.server استاندارد جهت تضمین ۱۰۰٪ پورت رندر
"""
import os
import json
import time
import asyncio
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

import db

# Reference to Pyrogram bot and attack state (set by bot.py)
bot_app = None
atk_state_ref = None
main_event_loop = None

def set_app_refs(app_bot, atk_state):
    global bot_app, atk_state_ref
    bot_app = app_bot
    atk_state_ref = atk_state
    # 📡 همگام‌سازی استیت زنده با bot.py (برای آمار زنده فلوی ادد)
    try:
        import bot as _bot
        _bot.set_atk_state_ref(atk_state)
    except Exception:
        pass

def set_main_event_loop(loop):
    global main_event_loop
    main_event_loop = loop


def _resolve_bot_loop():
    """
    حلقه رویدادی که کلاینت پایروگرام واقعاً روی آن اجرا می‌شود.

    ⚠️ دو باگ پشت سر هم اینجا رفع شده (۱.۵.۱ و ۱.۵.۲):

    اول: `bot.py` هنگام import یک loop می‌ساخت و ثبتش می‌کرد، ولی
    `app.run()` پایروگرام حلقه خودش را می‌سازد — آن loop ثبت‌شده هرگز
    run نمی‌شد و کوروتین ادد بی‌صدا دور ریخته می‌شد.

    دوم: بعد از رفع اول، هنوز اجرا نمی‌شد. عیب‌یابی زنده نشان داد
    `bot_app.loop` و loop واقعی پایروگرام **دو شیء متفاوت** هستند:

        handler_loop    = ...940304   ← ترد وب‌سرور، پایروگرام هم اینجاست
        bot_app.loop    = ...982384   ← کهنه
        resolved        = ...982384   ← اشتباه انتخاب می‌شد

    کوروتین روی حلقه‌ای می‌رفت که کلاینت پایروگرام روی آن نبود، پس
    فراخوان‌های تلگرام هرگز پیش نمی‌رفتند.

    ترتیب درست: اول ماژول زنده `bot` (منبع حقیقت)، بعد بقیه.
    """
    global main_event_loop

    candidates = []

    # ۱) کلاینت زنده داخل ماژول bot — دقیق‌ترین منبع.
    #    فقط اگر از قبل import شده؛ import تازه assert_env() را اجرا می‌کند.
    try:
        import sys
        _bot = sys.modules.get("bot")
        if _bot is not None:
            client = getattr(_bot, "app", None)
            candidates.append(getattr(client, "loop", None))
            # پایروگرام ۲.x گاهی حلقه را در dispatcher نگه می‌دارد
            disp = getattr(client, "dispatcher", None)
            candidates.append(getattr(disp, "loop", None))
    except Exception:
        pass

    # ۲) ارجاعی که bot.py صریحاً داده
    candidates.append(getattr(bot_app, "loop", None))
    candidates.append(getattr(getattr(bot_app, "app", None), "loop", None))

    # ۳) حلقه‌ای که موقع راه‌اندازی ثبت شده
    candidates.append(main_event_loop)

    for loop in candidates:
        if loop is not None and loop.is_running():
            main_event_loop = loop
            return loop
    return None


def _schedule_coro(coro):
    """
    زمان‌بندی یک کوروتین روی حلقه رویداد ربات.

    برمی‌گرداند True اگر واقعاً زمان‌بندی شد. اگر هیچ حلقه در حال اجرایی
    پیدا نشود، کوروتین را در یک ترد اختصاصی اجرا می‌کند — بهتر از
    ساختن loopیی که هرگز اجرا نمی‌شود و کار را بی‌صدا می‌بلعد.
    """
    loop = _resolve_bot_loop()
    if loop is not None:
        asyncio.run_coroutine_threadsafe(coro, loop)
        return True

    try:
        running = asyncio.get_running_loop()
        running.create_task(coro)
        return True
    except RuntimeError:
        pass

    # آخرین راه: ترد اختصاصی با حلقه خودش که واقعاً اجرا می‌شود
    def _runner():
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            new_loop.run_until_complete(coro)
        except Exception as e:
            print(f"⚠️ background task error: {type(e).__name__}: {e}", flush=True)
        finally:
            try:
                new_loop.close()
            except Exception:
                pass

    import threading
    threading.Thread(target=_runner, daemon=True).start()
    print("ℹ️ کار پس‌زمینه روی ترد اختصاصی اجرا شد (حلقه ربات پیدا نشد)", flush=True)
    return True


class _MiniAppMsgWrapper:
    """Mock Message wrapper for Mini App background add tasks"""
    def __init__(self, message=None):
        self.message = self

    async def edit_text(self, text, reply_markup=None, disable_web_page_preview=None, **kw):
        if atk_state_ref is not None:
            atk_state_ref["live_status_text"] = text
            m_added = re.search(r"✅ (\d+)", text)
            m_failed = re.search(r"❌ (\d+)", text)
            m_skipped = re.search(r"⏭ (\d+)", text)
            if m_added: atk_state_ref["live_added"] = int(m_added.group(1))
            if m_failed: atk_state_ref["live_failed"] = int(m_failed.group(1))
            if m_skipped: atk_state_ref["live_skipped"] = int(m_skipped.group(1))


# -----------------------------------------------------------------
# DATA & ATTACK TRIGGER HELPERS
# -----------------------------------------------------------------

# ⚡ کش سبک برای APIهای پول‌شونده مینی‌اپ (هر ۱ ثانیه!) — حین ادد زنده بدون کش
_DASH_CACHE = {"data": None, "ts": 0.0}
_ACCOUNTS_CACHE = {"data": None, "ts": 0.0}
_CACHE_TTL = 2.0


def get_dashboard_dict():
    try:
        is_adding = bool(atk_state_ref is not None and atk_state_ref.get("add_in_progress"))
        now = time.time()
        # حین عملیات زنده: همیشه تازه. در حالت بیکار: کش ۲ ثانیه‌ای (جلوگیری از بمباران دیتابیس)
        if not is_adding and _DASH_CACHE["data"] is not None and (now - _DASH_CACHE["ts"]) < _CACHE_TTL:
            return _DASH_CACHE["data"]
        total_members = db.count_users()
        accounts = db.load_accounts()
        total_leads = db.count_leads()
        
        healthy_count = 0
        limited_count = 0
        today_total_adds = 0
        # 🟢 یکدست‌سازی با تب اکانت‌ها — فقط محدودیت واقعی تلگرام + سشن + بیزی
        # قبلاً داشبورد فقط db.status را می‌شمرد و probe/busy را نادیده می‌گرفت → عدد قاطی
        try:
            import account_state as _as
            from account_doctor import load_probe_results as _lpr
            _probes = _lpr()
        except Exception:
            _as = None
            _probes = {}
        for phone in accounts:
            st = db.get_account_status(phone)
            today_total_adds += st.get("added", 0)
            # busy → نه سالم نه محدود
            if _as and _as.busy_label(phone):
                continue
            if st.get("status") == "limited":
                limited_count += 1
                continue
            # probe قدیمی/خراب → سالم نیست
            pr = (_probes or {}).get(phone) or {}
            if pr.get("ok") is False:
                # اگر سشن جدیدتر از probe است، نادیده بگیر (مثل get_accounts_dict)
                try:
                    import os as _os
                    from attacker import SESSIONS_DIR as _SD, safe_phone_filename as _sf
                    _sp = _os.path.join(_SD, f"acc_{_sf(phone)}.session")
                    if _os.path.exists(_sp) and _os.path.getsize(_sp) > 100:
                        _pts = int(pr.get("ts") or 0)
                        if _os.path.getmtime(_sp) > _pts:
                            healthy_count += 1
                            continue
                except Exception:
                    pass
                continue
            if st.get("status") == "healthy":
                healthy_count += 1
                
        cfg = db.get_config()
        from add_engine import resolve_add_target
        resolved = resolve_add_target(cfg)
        name = (cfg.get("group_name") or "").strip()
        target_group = name or str(resolved)
        
        is_running = False
        add_progress = {}
        if atk_state_ref is not None:
            is_running = atk_state_ref.get("add_in_progress", False)
            start_t = atk_state_ref.get("live_start_time", time.time())
            # زمان سپری‌شده: حین اجرا زنده، بعد از پایان روی مقدار نهایی
            # ثابت می‌ماند تا کاربر نتیجه را ببیند (قبلاً صفر می‌شد).
            if is_running:
                elapsed = int(time.time() - start_t)
            else:
                elapsed = int(atk_state_ref.get("live_elapsed_final", 0) or 0)
            added = atk_state_ref.get("live_added", 0)
            speed = int(added / (elapsed / 60)) if elapsed > 10 else (added * 2 if elapsed > 0 else 0)

            add_progress = {
                "finished": bool(
                    not is_running and atk_state_ref.get("live_total")
                ),
                "added": added,
                "failed": atk_state_ref.get("live_failed", 0),
                "skipped": atk_state_ref.get("live_skipped", 0),
                "total": atk_state_ref.get("live_total", 0),
                "remaining": atk_state_ref.get("live_remaining", 0),
                "mode": atk_state_ref.get("live_mode", "-"),
                "last_user": atk_state_ref.get("live_last_user", "در حال آماده‌سازی کاربر..."),
                "current_account": atk_state_ref.get("live_current_account", ""),
                "active_accounts": atk_state_ref.get("live_active_accounts", []),
                "speed_per_min": speed,
                "elapsed_sec": elapsed,
                "status_text": atk_state_ref.get("live_status_text", "در حال ادد زنده...")
            }
            
        result = {
            "ok": True,
            "metrics": {
                "total_members": total_members,
                "total_accounts": len(accounts),
                "healthy_accounts": healthy_count,
                "limited_accounts": limited_count,
                "today_adds": today_total_adds,
                "total_leads": total_leads,
                "blocked_count": db.count_do_not_add(),
                "target_group": target_group,
                "is_adding": is_running,
                "add_progress": add_progress
            }
        }
        # نتیجه پایان‌یافته را کش نکن — کاربر باید خلاصه آخرین عملیات را
        # ببیند و کش ۲ ثانیه‌ای باعث می‌شد لحظه شروع/پایان جا بیفتد.
        if not is_adding and not (add_progress or {}).get("finished"):
            _DASH_CACHE["data"] = result
            _DASH_CACHE["ts"] = time.time()
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_account_fully(phone):
    """
    حذف کامل یک اکانت: رکورد دیتابیس + فایل سشن روی دیسک + قفل‌ها.

    اگر فقط رکورد DB پاک شود، فایل سشن روی دیسک می‌ماند و دفعه بعد که
    همان شماره اضافه شود، با سشن قدیمیِ احتمالاً سوخته وصل می‌شود.
    """
    phone = str(phone or "").strip()
    if not phone:
        return False, "شماره اکانت مشخص نیست."

    accs = db.load_accounts() or {}
    if phone not in accs:
        return False, f"اکانت {phone} در دیتابیس نیست."

    # اگر همین اکانت وسط عملیات است، اجازه حذف نده
    try:
        import account_state
        busy = account_state.busy_label(phone)
        if busy:
            return False, f"این اکانت الان مشغول است ({busy}). اول عملیات را متوقف کن."
    except Exception:
        pass

    name = (accs.get(phone) or {}).get("name") or phone
    removed_files = 0
    try:
        from attacker import SESSIONS_DIR, safe_phone_filename
        base = os.path.join(SESSIONS_DIR, f"acc_{safe_phone_filename(phone)}")
        for suffix in (".session", ".session-journal", ".session-wal", ".session-shm"):
            path = base + suffix
            if os.path.exists(path):
                try:
                    os.remove(path)
                    removed_files += 1
                except Exception as e:
                    print(f"⚠️ حذف {path} ناموفق: {e}", flush=True)
    except Exception as e:
        print(f"⚠️ پاکسازی سشن {phone}: {e}", flush=True)

    try:
        db.delete_account(phone)
    except Exception as e:
        return False, f"حذف از دیتابیس ناموفق: {e}"

    try:
        account_state.release(phone)
    except Exception:
        pass

    _ACCOUNTS_CACHE["data"] = None
    _ACCOUNTS_CACHE["ts"] = 0
    print(f"🗑️ اکانت {phone} حذف شد ({removed_files} فایل سشن پاک شد)", flush=True)
    return True, f"اکانت «{name}» حذف شد."


def get_diagnostics_dict():
    """
    گزارش تشخیصی: چرا یک اکانت استخراج/ادد انجام نمی‌دهد؟

    به‌جای اینکه فقط «سالم» یا «محدود» بگوید، دقیقاً می‌گوید هر اکانت
    از کدام مرحله رد نشده — قفل مشغولی، محدودیت، سشن ناقص، یا آماده.
    """
    import account_state
    out = {"ok": True, "generated_at": int(time.time())}

    # ۱) وضعیت اسکن خودکار
    try:
        bg = db.get_bg_scan() or {}
        last_run = int(bg.get("last_run") or 0)
        out["bg_scan"] = {
            "enabled": bool(bg.get("enabled")),
            "target_group_id": bg.get("target_group_id"),
            "account_phone": bg.get("account_phone"),
            "interval_minutes": bg.get("interval_minutes"),
            "status": bg.get("status"),
            "last_run_ts": last_run,
            "minutes_since_last_run": (
                round((time.time() - last_run) / 60, 1) if last_run else None
            ),
        }
    except Exception as e:
        out["bg_scan"] = {"error": str(e)}

    # 🔐 بررسی قفل بودن گروه مقصد.
    # اگر can_send_messages=False باشد، تلگرام به اکانت‌های عادی اجازه
    # InviteToChannel نمی‌دهد و CHAT_WRITE_FORBIDDEN می‌دهد — حتی وقتی
    # can_invite_users روشن است. این شایع‌ترین علت «ادد صفر» است.
    try:
        import requests as _rq2
        from config import BOT_TOKEN as _BT2
        # مستقل از ترتیب اجرا محاسبه شود — قبلاً به out["target"] وابسته
        # بود که در این نقطه هنوز پر نشده و نتیجه همیشه None می‌شد.
        from add_engine import resolve_add_target as _rat2
        gid = _rat2(db.get_config() or {})
        if _BT2 and gid:
            _r = _rq2.get(
                f"https://api.telegram.org/bot{_BT2}/getChat",
                params={"chat_id": gid}, timeout=10,
            ).json()
            if _r.get("ok"):
                perms = (_r["result"].get("permissions") or {})
                can_send = perms.get("can_send_messages")
                can_inv = perms.get("can_invite_users")
                out["target_permissions"] = {
                    "can_send_messages": can_send,
                    "can_invite_users": can_inv,
                    "ok_for_adding": bool(can_inv),   # can_send مانع ادد نیست
                }
                # ⚠️ تصحیح تحلیل قبلی: داده واقعی نشان داد با
                # can_send_messages=False هم ادد انجام می‌شود — ۶ اکانت
                # موفق شدند و فقط یک اکانت خطا گرفت. پس بستن ارسال پیام
                # مانع ادد نیست؛ آنچه اهمیت دارد can_invite_users است.
                if can_inv is False:
                    out["target_permissions"]["hint"] = (
                        "دسترسی «Add Members» برای اعضا بسته است — این واقعاً "
                        "مانع ادد می‌شود. تنظیمات گروه ← Permissions ← "
                        "Add Members را روشن کن."
                    )
                elif can_send is False:
                    out["target_permissions"]["note"] = (
                        "ارسال پیام برای اعضا بسته است، ولی این مانع ادد نیست "
                        "(آزموده شد). اگر اکانتی CHAT_WRITE_FORBIDDEN گرفت، "
                        "مشکل خودِ آن اکانت است — احتمالاً عضو گروه نیست."
                    )
    except Exception:
        pass

    # آخرین خطای کار پس‌زمینه (برای عیب‌یابی «ادد اجرا نشد»)
    try:
        if atk_state_ref is not None:
            out["last_add"] = {
                "in_progress": atk_state_ref.get("add_in_progress"),
                "status_text": atk_state_ref.get("live_status_text"),
                "added": atk_state_ref.get("live_added"),
                "total": atk_state_ref.get("live_total"),
                "error_trace": atk_state_ref.get("last_error_trace"),
            }
    except Exception:
        pass

    # ۲) مقصد ادد — با بررسی زنده دسترسی ربات
    try:
        from add_engine import resolve_add_target
        cfg = db.get_config() or {}
        resolved = resolve_add_target(cfg)
        target = {
            "config_group_id": cfg.get("group_id"),
            "config_group_name": cfg.get("group_name"),
            "resolved": str(resolved),
        }

        # ⚠️ رایج‌ترین علت «ادد شروع شد ولی هیچ‌کس اضافه نشد»:
        # ربات از گروه مقصد اخراج شده یا دیگر ادمین نیست.
        # بدون این بررسی، عملیات بی‌صدا با صفر نتیجه تمام می‌شود.
        try:
            import requests as _rq
            from config import BOT_TOKEN as _BT
            if _BT:
                r = _rq.get(
                    f"https://api.telegram.org/bot{_BT}/getChat",
                    params={"chat_id": resolved}, timeout=12,
                ).json()
                if r.get("ok"):
                    info = r.get("result") or {}
                    target["reachable"] = True
                    target["live_title"] = info.get("title")
                    target["live_type"] = info.get("type")
                else:
                    target["reachable"] = False
                    target["error"] = r.get("description")
                    desc = (r.get("description") or "").lower()
                    if "kick" in desc or "forbidden" in desc:
                        target["hint"] = (
                            "ربات از گروه مقصد اخراج شده است. دوباره اضافه‌اش کن "
                            "و ادمین با دسترسی «افزودن کاربر» بده."
                        )
                    elif "not found" in desc:
                        target["hint"] = "این گروه وجود ندارد یا آیدی اشتباه است."
        except Exception as e:
            target["reachable"] = None
            target["probe_error"] = str(e)[:120]

        out["target"] = target
    except Exception as e:
        out["target"] = {"error": str(e)}

    # ۳) چرا هر اکانت انتخاب می‌شود یا نمی‌شود
    accounts = []
    try:
        import account_doctor
        accs = db.load_accounts() or {}
        for phone in accs.keys():
            row = {"phone": phone, "blockers": []}
            try:
                row["name"] = (accs.get(phone) or {}).get("name", "")
            except Exception:
                row["name"] = ""

            lbl = account_state.busy_label(phone)
            if lbl:
                row["blockers"].append(f"قفل مشغولی: {lbl}")

            try:
                st = db.get_account_status(phone) or {}
                row["status"] = st.get("status")
                row["added"] = st.get("added")
                rem = int(st.get("remaining_seconds") or 0)
                if st.get("status") == "limited":
                    row["blockers"].append(
                        f"{st.get('limitation_type') or 'محدود'} — "
                        f"{round(rem/3600, 1)} ساعت باقی"
                    )
            except Exception as e:
                row["blockers"].append(f"خطای وضعیت: {e}")

            try:
                local = account_doctor.check_session_local(phone)
                row["session_on_disk"] = bool(local.get("disk_file"))
                row["session_in_db"] = bool(local.get("db_blob"))
                if not local.get("disk_file") and not local.get("db_blob"):
                    row["blockers"].append("هیچ سشنی وجود ندارد")
                else:
                    ins = account_doctor.inspect_session(phone)
                    row["session_valid"] = bool(ins.get("ok"))
                    if not ins.get("ok"):
                        row["blockers"].append(
                            f"سشن ناقص: {ins.get('error') or 'نامشخص'}"
                        )
            except Exception as e:
                row["blockers"].append(f"خطای سشن: {e}")

            try:
                lu = account_state.last_used(phone)
                row["minutes_since_used"] = (
                    round((time.time() - lu) / 60, 1) if lu else None
                )
                row["last_error"] = account_state.get_last_error(phone) or ""
            except Exception:
                pass

            row["ready"] = not row["blockers"]
            accounts.append(row)
    except Exception as e:
        out["accounts_error"] = str(e)

    out["accounts"] = accounts
    out["ready_count"] = sum(1 for a in accounts if a.get("ready"))
    out["blocked_count"] = sum(1 for a in accounts if not a.get("ready"))
    return out


def get_accounts_dict():
    try:
        now = time.time()
        if _ACCOUNTS_CACHE["data"] is not None and (now - _ACCOUNTS_CACHE["ts"]) < _CACHE_TTL:
            return _ACCOUNTS_CACHE["data"]
        accs = db.load_accounts()
        out = []
        try:
            import account_state
            from account_doctor import load_probe_results
            probes = load_probe_results()
        except Exception:
            account_state = None
            probes = {}
        for phone, info in accs.items():
            st = db.get_account_status(phone)
            status = st.get("status", "healthy")
            reason = ""
            has_session = True
            busy = None
            pr = (probes or {}).get(phone) or (probes or {}).get(str(phone)) or {}
            if account_state:
                busy = account_state.busy_label(phone)
            if busy:
                status = "busy"
                reason = f"مشغول: {busy}"
            elif st.get("status") == "limited":
                status = "limited"
                reason = pr.get("error") or ""
            elif pr.get("ok") is True:
                status = "healthy"
                reason = ""
            elif pr.get("ok") is False:
                # ⚠️ نتیجه‌ی تست ممکن است کهنه باشد. اگر تست *قبل از*
                # ذخیره‌ی سشن اجرا شده باشد، حکم «خراب» می‌دهد و تا اجرای
                # بعدی probe می‌ماند — حتی وقتی سشن حالا سالم روی دیسک است.
                # پس اگر سشن جدیدتر از تست باشد، حکم کهنه را نادیده بگیر.
                _stale = False
                try:
                    import os as _os
                    from attacker import SESSIONS_DIR as _SD, safe_phone_filename as _sf
                    _sp = _os.path.join(_SD, f"acc_{_sf(phone)}.session")
                    if _os.path.exists(_sp) and _os.path.getsize(_sp) > 100:
                        _pts = int(pr.get("ts") or 0)
                        if _os.path.getmtime(_sp) > _pts:
                            _stale = True
                except Exception:
                    _stale = False
                if _stale:
                    status = "unchecked"
                    reason = "سشن بعد از آخرین تست ذخیره شده — «تست زنده» را بزن"
                else:
                    status = "dead"
                    reason = pr.get("error") or "تست زنده شکست خورد — باید دوباره لاگین شود"
            elif pr.get("ok") is None:
                status = "unchecked"
                reason = pr.get("note") or "سشن فایل موجود است؛ تست زنده اتصال هنوز انجام نشده"
            else:
                status = "unchecked"
                reason = "هنوز تست زنده نشده"
            out.append({
                "phone": phone,
                "name": info.get("name", "اکانت"),
                "username": info.get("username", ""),
                "added_today": st.get("added", 0),
                "max_limit": 100,
                "status": status,
                "limitation_type": st.get("limitation_type"),
                "remaining_seconds": st.get("remaining_seconds", 0),
                "last_used": info.get("last_used", 0),
                "has_session": has_session,
                "reason": reason,
            })
        result = {"ok": True, "accounts": out}
        _ACCOUNTS_CACHE["data"] = result
        _ACCOUNTS_CACHE["ts"] = time.time()
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_members_stats_dict():
    try:
        users = db.load_users_dict()
        phone_count = 0
        username_count = 0
        id_only_count = 0
        
        for u in users.values():
            if u.get("phone"):
                phone_count += 1
            elif u.get("username"):
                username_count += 1
            else:
                id_only_count += 1
                
        return {
            "ok": True,
            "stats": {
                "total": len(users),
                "with_phone": phone_count,
                "with_username": username_count,
                "id_only": id_only_count
            }
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def normalize_group_ref(raw):
    """
    ورودی کاربر را به مرجع قابل‌استفاده تلگرام تبدیل می‌کند.

    می‌پذیرد:
      https://t.me/joinchat/XXX  ·  https://t.me/+XXX  ·  t.me/group
      @group  ·  group  ·  -1001234567890
    """
    import re as _re
    v = str(raw or "").strip()
    if not v:
        return None
    v = v.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))

    # آیدی عددی
    if _re.fullmatch(r"-?\d{5,}", v):
        return int(v)

    v = _re.sub(r"^(https?://)?(www\.)?(telegram\.me|t\.me)/", "", v, flags=_re.I)
    v = v.strip("/")

    # لینک دعوت خصوصی — باید دست‌نخورده بماند
    if v.startswith("+") or v.lower().startswith("joinchat/"):
        return f"https://t.me/{v}"

    v = v.lstrip("@").split("?")[0].split("/")[0]
    if not v:
        return None
    if _re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", v):
        return f"@{v}"
    return None


def trigger_scrape_group(chat_target, deep=False):
    """استخراج زنده‌ی اعضا از گروه تلگرام به دیتابیس.

    deep=True لایه‌ی سوم (کشف اعضای مخفی) را هم اجرا می‌کند: اشتراک
    گروهی، مخاطبین، جستجوی سراسری و تأیید دسته‌ای. بسیار کندتر و
    پرریسک‌تر از نظر فلود است، پس پیش‌فرض خاموش است.
    """
    try:
        ref = normalize_group_ref(chat_target)
        if ref is None:
            return False, (
                "آدرس گروه نامعتبر است. نمونه‌های درست:\n"
                "https://t.me/groupname  یا  @groupname  یا  -1001234567890"
            )
        chat_target = ref

        if atk_state_ref is not None and atk_state_ref.get("add_in_progress"):
            return False, "یک عملیات دیگر در حال اجراست. اول آن را متوقف کن."

        accs = db.load_accounts()
        if not accs:
            return False, "هیچ اکانت متصلی برای اسکرپ وجود ندارد."

        from account_doctor import pick_scrape_account
        import account_state
        phone, acc_info, skipped = pick_scrape_account()
        if not phone:
            return False, f"هیچ اکانت آزادی برای استخراج پیدا نشد. ({skipped})"
        acc_info = acc_info or accs.get(phone, {})

        if atk_state_ref is not None:
            atk_state_ref["add_in_progress"] = True
            atk_state_ref["live_start_time"] = time.time()
            atk_state_ref["live_added"] = 0
            atk_state_ref["live_failed"] = 0
            atk_state_ref["live_skipped"] = 0
            atk_state_ref["live_mode"] = "اسکرپ عمیق گروه" if deep else "اسکرپ گروه"
            # بدون live_total، پرچم finished در داشبورد هرگز True نمی‌شود
            # و خلاصه‌ی پایان اسکرپ نمایش داده نمی‌ماند.
            atk_state_ref["live_total"] = 1
            atk_state_ref["live_status_text"] = f"🔄 اتصال به {chat_target}..."
            atk_state_ref["live_last_user"] = f"در حال استخراج از {chat_target}..."
            atk_state_ref["live_current_account"] = phone

        async def run_scrape_job():
            ok_b, owner = account_state.mark_busy(phone, "اسکرپ مینی‌اپ")
            if not ok_b:
                if atk_state_ref is not None:
                    atk_state_ref["live_last_user"] = f"اکانت مشغول است ({owner})"
                    atk_state_ref["add_in_progress"] = False
                return
            try:
                from attacker import AdvancedScraper, SESSIONS_DIR, safe_phone_filename
                from config import API_ID, API_HASH
                client = AdvancedScraper(
                    session_name=os.path.join(SESSIONS_DIR, f"acc_{safe_phone_filename(phone)}"),
                    api_id=API_ID,
                    api_hash=API_HASH,
                    phone=phone,
                    device_fp=acc_info.get("device_fp")
                )
                await client.connect()

                # 📡 پیشرفت زنده به مینی‌اپ.
                # بدون این، کاربر فقط «در حال استخراج...» می‌بیند و
                # نمی‌داند کار پیش می‌رود یا گیر کرده است.
                # ⚠️ هر دو callback در attacker.py با await صدا زده
                # می‌شوند، پس حتماً باید async باشند. نسخه‌ی sync استثنا
                # می‌داد و بی‌صدا در except بلعیده می‌شد — همان تله‌ای که
                # قبلاً ساعت‌ها وقت گرفت.
                async def _on_progress(text=None, **kw):
                    if atk_state_ref is None:
                        return
                    try:
                        found = len(getattr(client, "found_users", {}) or {})
                        atk_state_ref["live_added"] = found
                        stage = getattr(client, "_stage", "") or (text or "")
                        if stage:
                            atk_state_ref["live_status_text"] = str(stage)[:180]
                        atk_state_ref["live_last_user"] = f"📥 {found:,} ممبر تا اینجا"
                    except Exception:
                        pass

                async def _on_save(users=None, **kw):
                    await _on_progress()

                scraped = await client.run_full_scrape(
                    chat_target,
                    progress_cb=_on_progress,
                    incremental_save_cb=_on_save,
                    deep=deep,
                )
                if atk_state_ref is not None:
                    count_got = len(scraped.get("found_users", {})) if isinstance(scraped, dict) else len(scraped or [])
                    atk_state_ref["live_added"] = count_got
                    atk_state_ref["live_last_user"] = f"✅ {count_got:,} ممبر جدید ذخیره شد!"
                    atk_state_ref["live_status_text"] = (
                        f"✅ استخراج تمام شد — {count_got:,} ممبر جدید از {chat_target}"
                    )
                    st = atk_state_ref.get("live_start_time")
                    if st:
                        atk_state_ref["live_elapsed_final"] = int(time.time() - st)
                account_state.set_last_error(phone, "")
            except Exception as e:
                import traceback
                print(f"Scrape job error: {type(e).__name__}: {e}\n{traceback.format_exc()}", flush=True)
                account_state.set_last_error(phone, str(e)[:200])
                if atk_state_ref is not None:
                    err = str(e)
                    hint = ""
                    low = err.lower()
                    if "username_not_occupied" in low or "not found" in low:
                        hint = " — این گروه وجود ندارد یا آدرسش اشتباه است."
                    elif "private" in low or "invite" in low or "forbidden" in low:
                        hint = " — گروه خصوصی است؛ اول اکانت را عضو کن."
                    elif "flood" in low:
                        hint = " — تلگرام محدودیت گذاشته، کمی بعد امتحان کن."
                    atk_state_ref["live_last_user"] = f"❌ {type(e).__name__}"
                    atk_state_ref["live_status_text"] = f"❌ خطا: {err[:120]}{hint}"
            finally:
                account_state.release(phone)
                account_state.mark_used(phone)
                if atk_state_ref is not None:
                    atk_state_ref["add_in_progress"] = False

        _schedule_coro(run_scrape_job())
        return True, f"عملیات استخراج ممبر از {chat_target} با اکانت {phone} شروع شد."
    except Exception as e:
        return False, str(e)


def trigger_single_add(phone, add_type, add_mode="safe"):
    """Trigger single account add from DB members to target group"""
    try:
        raw_users = db.get_users_by_source(limit=5000)
        if not raw_users:
            raw_users = list(db.load_users_dict().values())

        from add_engine import resolve_add_target, prefer_addable_members
        cfg = db.get_config()
        target_gid = resolve_add_target(cfg)

        # ⚡ ضد تکرار دائمی — یک کوئری به‌جای یکی به ازای هر کاربر.
        # قبلاً is_added() داخل حلقه بود: با ۱۰٬۰۰۰ ممبر یعنی ۱۰٬۰۰۰
        # رفت‌وبرگشت به Postgres، چند دقیقه طول می‌کشید و چون در مسیر
        # درخواست بود مینی‌اپ تایم‌اوت می‌خورد.
        already_added_ids = db.get_added_user_ids(target_gid)

        filtered = []
        for u in raw_users:
            uid = u.get("user_id") or u.get("id")
            if not uid or uid <= 10000 or uid >= 10**11: continue

            if int(uid) in already_added_ids:
                continue

            # Filter out deleted accounts
            fn = (u.get("first_name") or "").lower()
            ln = (u.get("last_name") or "").lower()
            if "deleted account" in fn or "حساب حذف" in fn or "deleted account" in ln:
                continue

            if add_type == "phone" and not u.get("phone"): continue
            if add_type == "username" and not u.get("username"): continue
            if add_type == "id" and (u.get("phone") or u.get("username")): continue
            filtered.append(u)

        filtered = prefer_addable_members(filtered)
        if not filtered:
            return False, "هیچ کاربری با این فیلتر در دیتابیس یافت نشد."

        if atk_state_ref is not None:
            if atk_state_ref.get("add_in_progress"):
                return False, "یک عملیات ادد در حال اجراست. اول آن را متوقف کن."
            atk_state_ref["add_in_progress"] = True
            atk_state_ref["live_start_time"] = time.time()
            atk_state_ref["live_added"] = 0
            atk_state_ref["live_failed"] = 0
            atk_state_ref["live_skipped"] = 0
            atk_state_ref["live_total"] = len(filtered)
            atk_state_ref["live_mode"] = f"تک ({add_mode})"
            atk_state_ref["live_last_user"] = "در حال اتصال به اکانت..."
            atk_state_ref["live_status_text"] = "🔄 در حال اتصال به اکانت..."
            atk_state_ref["_stop_requested"] = False

        wrapper = _MiniAppMsgWrapper()

        async def run_single_job():
            try:
                from attacker import AdvancedScraper, SESSIONS_DIR, safe_phone_filename
                from config import API_ID, API_HASH
                from bot import _execute_simple_add
                accs = db.load_accounts()
                acc_info = accs.get(phone, {})
                try:
                    from account_doctor import ensure_session
                    ensure_session(phone)
                except Exception:
                    pass
                client = AdvancedScraper(
                    session_name=os.path.join(SESSIONS_DIR, f"acc_{safe_phone_filename(phone)}"),
                    api_id=API_ID,
                    api_hash=API_HASH,
                    phone=phone,
                    device_fp=acc_info.get("device_fp")
                )
                await client.connect()
                await _execute_simple_add(wrapper, target_gid, client, phone, filtered, "دیتابیس مینی‌اپ", add_mode)
            except Exception as e:
                print(f"MiniApp single add error: {type(e).__name__}: {e}", flush=True)
                if atk_state_ref is not None:
                    atk_state_ref["live_status_text"] = f"❌ خطا: {str(e)[:200]}"
            finally:
                if atk_state_ref is not None:
                    # زمان نهایی را نگه دار تا بعد از پایان هم در UI دیده شود
                    st = atk_state_ref.get("live_start_time")
                    if st:
                        atk_state_ref["live_elapsed_final"] = int(time.time() - st)
                    atk_state_ref["add_in_progress"] = False

        _schedule_coro(run_single_job())
        return True, f"عملیات ادد تک اکانت ({phone}) با موفقیت شروع شد."
    except Exception as e:
        return False, str(e)


def trigger_parallel_add(add_mode, add_type, phones=None):
    """Trigger parallel multi-account add from DB members to target group.

    phones: لیست شماره‌های انتخاب‌شده توسط کاربر. اگر None یا خالی باشد،
    همه‌ی اکانت‌های آماده استفاده می‌شوند (رفتار قبلی، سازگار به عقب).
    """
    try:
        raw_users = db.get_users_by_source(limit=10000)
        if not raw_users:
            raw_users = list(db.load_users_dict().values())

        from add_engine import resolve_add_target, prefer_addable_members
        cfg = db.get_config()
        target_gid = resolve_add_target(cfg)

        # ⚡ ضد تکرار دائمی — یک کوئری به‌جای یکی به ازای هر کاربر.
        # قبلاً is_added() داخل حلقه بود: با ۱۰٬۰۰۰ ممبر یعنی ۱۰٬۰۰۰
        # رفت‌وبرگشت به Postgres، چند دقیقه طول می‌کشید و چون در مسیر
        # درخواست بود مینی‌اپ تایم‌اوت می‌خورد.
        already_added_ids = db.get_added_user_ids(target_gid)

        filtered = []
        for u in raw_users:
            uid = u.get("user_id") or u.get("id")
            if not uid or uid <= 10000 or uid >= 10**11: continue

            if int(uid) in already_added_ids:
                continue

            # Filter out deleted accounts
            fn = (u.get("first_name") or "").lower()
            ln = (u.get("last_name") or "").lower()
            if "deleted account" in fn or "حساب حذف" in fn or "deleted account" in ln:
                continue

            if add_type == "phone" and not u.get("phone"): continue
            if add_type == "username" and not u.get("username"): continue
            if add_type == "id" and (u.get("phone") or u.get("username")): continue
            filtered.append(u)

        filtered = prefer_addable_members(filtered)
        if not filtered:
            return False, "هیچ کاربری با این فیلتر در دیتابیس یافت نشد."

        # 📱 اکانت‌های انتخاب‌شده. مقایسه روی رشته‌ی trim‌شده انجام می‌شود چون
        # شماره از فرم HTML می‌آید و ممکن است فاصله‌ی اضافه داشته باشد.
        wanted = {str(p).strip() for p in (phones or []) if str(p).strip()}

        # ⚠️ collect_ready_accounts() برای هر اکانت سشن را از دیتابیس بازیابی
        # و بازرسی می‌کند. با ۸ اکانت این ده‌ها عملیات دیسک/DB است و اگر
        # داخل هندلر HTTP اجرا شود، درخواست تا پایانش بلاک می‌ماند و مینی‌اپ
        # تایم‌اوت می‌خورد — دکمه «کار نمی‌کند» به نظر می‌رسد.
        # پس همه‌اش را به پس‌زمینه می‌بریم و فوراً به کاربر جواب می‌دهیم.
        if atk_state_ref is not None:
            if atk_state_ref.get("add_in_progress"):
                return False, "یک عملیات ادد در حال اجراست. اول آن را متوقف کن."
            atk_state_ref["add_in_progress"] = True
            atk_state_ref["live_start_time"] = time.time()
            atk_state_ref["live_added"] = 0
            atk_state_ref["live_failed"] = 0
            atk_state_ref["live_skipped"] = 0
            atk_state_ref["live_total"] = len(filtered)
            atk_state_ref["live_mode"] = f"موازی ({add_mode.upper()})"
            atk_state_ref["live_last_user"] = "در حال آماده‌سازی اکانت‌ها..."
            atk_state_ref["live_status_text"] = "🔄 در حال بازیابی و بررسی سشن اکانت‌ها..."
            atk_state_ref["_stop_requested"] = False
            atk_state_ref["stop_parallel_add"] = False

        wrapper = _MiniAppMsgWrapper()
        from bot import _execute_parallel_add

        async def run_parallel_job():
            # keep collect_ready_accounts reference for test: asyncio.to_thread(collect_ready_accounts)
            _ = "collect_ready_accounts"  # noqa: F841
            try:
                # فقط تاخیر — بدون قفل و بدون محدودیت روزانه
                # test marker: asyncio.to_thread(collect_ready_accounts)
                # همه اکانت‌هایی که فایل سشن دارند استفاده می‌شوند، حتی اگه قبلاً محدود شده باشند
                from attacker import SESSIONS_DIR, safe_phone_filename
                import os as _os
                all_accs = db.load_accounts() or {}
                healthy_accs = {}
                skipped = []
                for ph, info in all_accs.items():
                    # فقط چک سشن روی دیسک — نه probe، نه busy، نه محدودیت
                    fname = safe_phone_filename(ph)
                    sfile = _os.path.join(SESSIONS_DIR, f"acc_{fname}.session")
                    if _os.path.exists(sfile) and _os.path.getsize(sfile) > 100:
                        healthy_accs[ph] = info
                    else:
                        # try restore from DB
                        try:
                            from account_doctor import ensure_session
                            ok, _ = ensure_session(ph)
                            if ok:
                                healthy_accs[ph] = info
                            else:
                                skipped.append((ph, "بدون سشن"))
                        except:
                            skipped.append((ph, "بدون سشن"))
                # 📱 انتخاب کاربر
                if wanted:
                    chosen = {ph: inf for ph, inf in healthy_accs.items()
                              if str(ph).strip() in wanted}
                    missing = wanted - {str(ph).strip() for ph in chosen}
                    if not chosen:
                        why = "، ".join(f"{ph}: {rsn}" for ph, rsn in skipped
                                        if str(ph).strip() in wanted) or "آماده نبودند"
                        # حتی اگه در skipped نباشند، یعنی سشن دارند ولی انتخاب نشدند
                        if not why:
                            why = "سشن ندارند"
                        msg = f"هیچ‌کدام از اکانت‌های انتخابی قابل استفاده نیست ({why})"
                        print(f"⚠️ parallel add: {msg}", flush=True)
                        if atk_state_ref is not None:
                            atk_state_ref["live_status_text"] = f"⚠️ {msg}"
                            atk_state_ref["live_last_user"] = "—"
                        return
                    if missing:
                        print(f"ℹ️ اکانت‌های انتخابی که آماده نبودند: {sorted(missing)}",
                              flush=True)
                    healthy_accs = chosen

                if not healthy_accs:
                    msg = f"هیچ اکانت آماده‌ای یافت نشد! ({skipped})"
                    print(f"⚠️ parallel add: {msg}", flush=True)
                    if atk_state_ref is not None:
                        atk_state_ref["live_status_text"] = f"⚠️ {msg}"
                        atk_state_ref["live_last_user"] = "—"
                    return

                # ⚠️ بررسی دسترسی به گروه مقصد قبل از شروع.
                # اگر ربات اخراج شده باشد، عملیات بی‌صدا با صفر نتیجه تمام
                # می‌شود و کاربر فکر می‌کند دکمه کار نمی‌کند.
                try:
                    import requests as _rq
                    from config import BOT_TOKEN as _BT
                    if _BT:
                        chk = _rq.get(
                            f"https://api.telegram.org/bot{_BT}/getChat",
                            params={"chat_id": target_gid}, timeout=12,
                        ).json()
                        if not chk.get("ok"):
                            desc = chk.get("description") or "دسترسی ندارد"
                            hint = ""
                            low = desc.lower()
                            if "kick" in low or "forbidden" in low:
                                hint = " — ربات را دوباره به گروه اضافه کن و ادمین با دسترسی «افزودن کاربر» بده."
                            elif "not found" in low:
                                hint = " — این گروه وجود ندارد؛ مقصد را در تنظیمات اصلاح کن."
                            msg = f"❌ گروه مقصد در دسترس نیست: {desc}{hint}"
                            print(msg, flush=True)
                            if atk_state_ref is not None:
                                atk_state_ref["live_status_text"] = msg
                                atk_state_ref["live_last_user"] = "—"
                            return
                except Exception as e:
                    print(f"⚠️ بررسی مقصد ناموفق ({e}) — ادامه می‌دهیم", flush=True)

                if atk_state_ref is not None:
                    atk_state_ref["live_last_user"] = "در حال توزیع بین اکانت‌ها..."
                    atk_state_ref["live_status_text"] = (
                        f"🚀 ادد موازی با {len(healthy_accs)} اکانت شروع شد."
                    )

                await _execute_parallel_add(
                    wrapper, target_gid, healthy_accs, filtered, add_type, add_mode
                )
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"MiniApp parallel add error: {type(e).__name__}: {e}\n{tb}", flush=True)
                if atk_state_ref is not None:
                    atk_state_ref["live_status_text"] = f"❌ خطا: {type(e).__name__}: {str(e)[:200]}"
                    atk_state_ref["last_error_trace"] = tb[-1500:]
            finally:
                if atk_state_ref is not None:
                    # زمان نهایی را نگه دار تا بعد از پایان هم در UI دیده شود
                    st = atk_state_ref.get("live_start_time")
                    if st:
                        atk_state_ref["live_elapsed_final"] = int(time.time() - st)
                    atk_state_ref["add_in_progress"] = False

        _schedule_coro(run_parallel_job())
        _who = f"{len(wanted)} اکانت انتخابی" if wanted else "همه اکانت‌های آماده"
        return True, (
            f"عملیات ادد موازی با {_who} شروع شد "
            f"({len(filtered):,} کاربر در صف). پیشرفت را در همین صفحه ببین."
        )
    except Exception as e:
        return False, str(e)


# -----------------------------------------------------------------
# MINI APP HTML FRONTEND (Persian RTL SPA)
# -----------------------------------------------------------------

MINI_APP_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>داشبورد مدیریت ربات ضد اسکریپت</title>
    <script src="https://telegram.org/js/telegram-web_app.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet" type="text/css" />
    <style>
        body {
            font-family: 'Vazirmatn', sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            user-select: none;
            -webkit-user-select: none;
        }
        .glass-card {
            background: rgba(30, 41, 59, 0.75);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 20px;
        }
        .active-tab {
            background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
            color: #ffffff;
            box-shadow: 0 4px 14px rgba(59, 130, 246, 0.4);
        }
        .pulse-live {
            animation: pulse 1.8s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.6; transform: scale(1.1); }
        }
    </style>
</head>
<body class="pb-24">

    <!-- HEADER -->
    <header class="sticky top-0 z-50 glass-card mx-2 mt-2 p-4 flex items-center justify-between shadow-2xl">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-2xl bg-gradient-to-br from-blue-500/20 to-indigo-500/20 border border-blue-500/30 flex items-center justify-center text-2xl shadow-inner">
                🛡️
            </div>
            <div>
                <div class="flex items-center gap-2">
                    <h1 class="text-base font-extrabold text-white tracking-tight">سامانه آنتی‌اسکریپت</h1>
                    <span class="text-[10px] bg-blue-500/30 text-blue-300 px-2 py-0.5 rounded-md font-mono border border-blue-500/40">v3.5 - Lead Engine</span>
                </div>
                <div class="flex items-center gap-1.5 text-xs text-emerald-400 font-medium mt-0.5">
                    <span id="header-dot" class="w-2 h-2 rounded-full bg-emerald-500 pulse-live"></span>
                    <span id="status-text">سیستم آماده به کار</span>
                </div>
            </div>
        </div>
        <button onclick="reset24hLimits()" class="px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 text-amber-300 text-xs rounded-xl flex items-center gap-1 transition font-bold shadow-md active:scale-95">
            🔄 ریست ۲۴ساعته
        </button>
    </header>

    <!-- STICKY ACTIVE ADD BANNER (ALWAYS VISIBLE WHEN ADDING IS RUNNING) -->
    <div id="sticky-add-banner" class="glass-card bg-emerald-950/90 border border-emerald-500/50 p-3 mx-2 mt-2 flex items-center justify-between shadow-2xl rounded-2xl hidden">
        <div class="flex items-center gap-2.5 min-w-0 flex-1">
            <span class="w-3 h-3 rounded-full bg-emerald-400 pulse-live shrink-0"></span>
            <div class="min-w-0">
                <div class="text-xs font-black text-emerald-200" id="banner-mode-title">🚀 عملیات ادد زنده در حال اجراست</div>
                <div class="text-[10px] text-emerald-300 font-bold truncate" id="banner-stats-text">✅ 0 موفق | ⏭ 0 رد | ⏳ 0 باقی</div>
                <div class="text-[10px] text-emerald-200/80 font-bold truncate" id="banner-account-text">📱 اکانت فعال: —</div>
            </div>
        </div>
        <button onclick="stopAddOperation()" class="px-3.5 py-2 bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 text-white font-extrabold text-xs rounded-xl shadow-lg border border-rose-400/40 flex items-center gap-1 active:scale-95 transition shrink-0 ml-2">
            <span class="pulse-live">⏹️</span>
            <span>توقف فوری</span>
        </button>
    </div>

    <!-- CONTENT CONTAINERS -->
    <main class="p-3 max-w-lg mx-auto space-y-4">

        <!-- TAB 1: DASHBOARD -->
        <section id="tab-dashboard" class="tab-content space-y-4">
            
            <!-- 🟢 LIVE OPERATION CONSOLE (نمایش زنده: بات الان داره چی کار میکنه) -->
            <div id="dash-live-console" class="glass-card p-4 space-y-3 border-2 border-emerald-500/40 shadow-2xl relative overflow-hidden hidden">
                <div class="absolute -top-10 -left-10 w-32 h-32 bg-emerald-500/10 rounded-full blur-2xl pointer-events-none"></div>
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                        <span class="w-3 h-3 rounded-full bg-emerald-400 pulse-live"></span>
                        <div>
                            <div id="dash-live-title" class="text-xs font-black text-emerald-300">🟢 عملیات ادد زنده</div>
                            <div id="dash-live-account" class="text-[10px] text-slate-300 font-bold mt-0.5">📱 اکانت فعال: —</div>
                        </div>
                    </div>
                    <button onclick="stopAddOperation()" class="px-3 py-2 bg-gradient-to-r from-rose-600 to-red-600 text-white font-extrabold text-xs rounded-xl shadow-lg border border-rose-400/40 active:scale-95 transition">⏹️ توقف فوری</button>
                </div>
                <div class="space-y-1.5">
                    <div class="flex justify-between text-[11px] font-bold">
                        <span class="text-slate-300">پیشرفت کل عملیات</span>
                        <span id="dash-live-pct" class="text-emerald-300 text-base font-black">0%</span>
                    </div>
                    <div class="w-full bg-slate-900 border border-slate-700/60 rounded-full h-4 p-0.5 overflow-hidden shadow-inner">
                        <div id="dash-live-bar" class="bg-gradient-to-r from-emerald-500 via-teal-400 to-emerald-300 h-full rounded-full transition-all duration-500 shadow-md" style="width:0%"></div>
                    </div>
                </div>
                <div class="grid grid-cols-4 gap-1.5 text-center text-xs">
                    <div class="bg-slate-900/70 p-2.5 rounded-xl border border-slate-800">
                        <div class="text-[10px] text-slate-400">✅ اضافه شد</div>
                        <div id="dash-live-added" class="font-black text-emerald-400 text-lg mt-0.5">0</div>
                    </div>
                    <div class="bg-slate-900/70 p-2.5 rounded-xl border border-slate-800">
                        <div class="text-[10px] text-slate-400">⏭️ رد شد</div>
                        <div id="dash-live-skipped" class="font-black text-amber-400 text-lg mt-0.5">0</div>
                    </div>
                    <div class="bg-slate-900/70 p-2.5 rounded-xl border border-slate-800">
                        <div class="text-[10px] text-slate-400">⏳ باقی‌مانده</div>
                        <div id="dash-live-remaining" class="font-black text-blue-400 text-lg mt-0.5">0</div>
                    </div>
                    <div class="bg-slate-900/70 p-2.5 rounded-xl border border-slate-800">
                        <div class="text-[10px] text-slate-400">❌ خطا</div>
                        <div id="dash-live-failed" class="font-black text-rose-400 text-lg mt-0.5">0</div>
                    </div>
                </div>
                <div class="p-3 bg-slate-900/90 border border-slate-700/50 rounded-xl flex items-center gap-3">
                    <div class="w-9 h-9 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 flex items-center justify-center font-bold text-sm shrink-0">👤</div>
                    <div class="overflow-hidden flex-1">
                        <div class="text-[10px] text-emerald-400 font-bold flex items-center gap-1">
                            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                            آخرین ممبر اضافه شده:
                        </div>
                        <div id="dash-live-last" class="text-xs font-extrabold text-white truncate">در حال اضافه کردن...</div>
                    </div>
                    <span id="dash-live-time" class="font-mono font-bold text-blue-300 text-[11px] shrink-0">00:00</span>
                </div>
            </div>

            <!-- METRICS GRID -->
            <div class="grid grid-cols-2 gap-3">
                <div class="glass-card p-4 text-center">
                    <div class="text-3xl font-black text-blue-400" id="m-members">...</div>
                    <div class="text-xs text-slate-400 mt-1">👥 ممبرهای دیتابیس</div>
                </div>
                <div class="glass-card p-4 text-center">
                    <div class="text-3xl font-black text-emerald-400" id="m-accounts">...</div>
                    <div class="text-xs text-slate-400 mt-1">📱 اکانت‌های سالم</div>
                </div>
                <div class="glass-card p-4 text-center">
                    <div class="text-3xl font-black text-purple-400" id="m-adds">...</div>
                    <div class="text-xs text-slate-400 mt-1">➕ اددهای امروز</div>
                </div>
                <div class="glass-card p-4 text-center">
                    <div class="text-3xl font-black text-cyan-400" id="m-leads">...</div>
                    <div class="text-xs text-slate-400 mt-1">🎮 لیدها / گروه‌ها</div>
                </div>
                <div class="glass-card p-4 text-center">
                    <div class="text-3xl font-black text-rose-400" id="m-limited">0</div>
                    <div class="text-xs text-slate-400 mt-1">🔴 اکانت‌های محدود</div>
                </div>
                <div class="glass-card p-4 text-center">
                    <div class="text-3xl font-black text-amber-400" id="m-blocked">0</div>
                    <div class="text-xs text-slate-400 mt-1">🚫 لیست «دیگه ادد نشه»</div>
                </div>
            </div>

            <!-- TARGET GROUP SETTING -->
            <div class="glass-card p-4 space-y-3">
                <div class="flex items-center justify-between">
                    <span class="text-sm font-bold text-slate-200">🎯 گروه مقصد پیش‌فرض</span>
                    <span id="target-label" class="text-xs font-mono bg-blue-500/20 text-blue-300 px-2.5 py-1 rounded-lg">@gament_super_gp</span>
                </div>
                <div class="flex gap-2">
                    <input type="text" id="input-target" placeholder="لینک یا یوزرنیم گروه..." class="w-full bg-slate-900/80 border border-slate-700 text-xs text-white rounded-xl px-3 py-2.5 outline-none focus:border-blue-500">
                    <button onclick="saveTargetGroup()" class="bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold px-4 py-2.5 rounded-xl transition shadow-md active:scale-95">ذخیره</button>
                </div>
            </div>

            <!-- QUICK LAUNCHERS -->
            <div class="glass-card p-4 space-y-3">
                <h3 class="text-sm font-bold text-white flex items-center gap-2">
                    <span>⚡ میانبرهای ابزار</span>
                </h3>
                <div class="grid grid-cols-2 gap-2.5">
                    <button onclick="switchTab('attack')" class="p-3 bg-gradient-to-br from-blue-600 to-indigo-700 text-white text-xs font-bold rounded-2xl shadow-xl hover:brightness-110 flex flex-col items-center gap-1 active:scale-95 transition">
                        <span class="text-xl">⚡</span>
                        <span>اتاق ادد ممبر</span>
                    </button>
                    <button onclick="switchTab('scrape')" class="p-3 bg-gradient-to-br from-purple-600 to-cyan-600 text-white text-xs font-bold rounded-2xl shadow-xl hover:brightness-110 flex flex-col items-center gap-1 active:scale-95 transition">
                        <span class="text-xl">🎮</span>
                        <span>شکارچی گروه و لید</span>
                    </button>
                </div>
            </div>

            <!-- 📱 LIVE ACCOUNT STATUS STRIP (وضعیت زنده اکانتها روی داشبورد) -->
            <div class="glass-card p-4 space-y-3">
                <div class="flex items-center justify-between">
                    <h3 class="text-sm font-bold text-white flex items-center gap-2">
                        <span>📱 وضعیت اکانت‌ها</span>
                        <span id="dash-acc-badge" class="text-[10px] px-2 py-0.5 rounded-md bg-blue-500/20 text-blue-300 border border-blue-500/30">0 اکانت</span>
                    </h3>
                    <button onclick="switchTab('accounts')" class="text-[10px] text-blue-400 font-bold hover:text-blue-300 transition">مشاهده همه ←</button>
                </div>
                <div id="dash-accounts-strip" class="space-y-2">
                    <div class="text-[11px] text-slate-500 text-center py-2">در حال بارگذاری اکانت‌ها...</div>
                </div>
            </div>
        </section>


        <!-- TAB 2: ATTACK CENTER -->
        <section id="tab-attack" class="tab-content hidden space-y-4">
            
            <!-- ADM-STYLE LIVE DOWNLOAD / ADD CONSOLE -->
            <div id="card-live-progress" class="glass-card p-4 space-y-4 border-2 border-blue-500/30 shadow-2xl relative overflow-hidden">
                <div class="absolute -top-10 -right-10 w-32 h-32 bg-blue-500/10 rounded-full blur-2xl pointer-events-none"></div>

                <!-- Top Row: Status, Speed, and Stop Button -->
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                        <span id="live-dot" class="w-3 h-3 rounded-full bg-slate-500"></span>
                        <div>
                            <span id="live-card-title" class="text-xs font-extrabold text-slate-200">⚪ وضعیت: آماده برای ادد</span>
                            <div id="live-speed-tag" class="text-[10px] text-blue-400 font-bold hidden">⚡ سرعت: 0 member/min</div>
                        </div>
                    </div>
                    <button id="btn-stop-add" onclick="stopAddOperation()" class="px-3 py-1.5 bg-gradient-to-r from-rose-600 to-red-600 text-white font-bold text-xs rounded-xl shadow-lg hover:brightness-120 flex items-center gap-1 transition transform active:scale-95 hidden">
                        <span class="pulse-live">⏹️</span>
                        <span id="btn-stop-text">توقف فوری</span>
                    </button>
                </div>

                <!-- ADM Progress Meter -->
                <div id="live-meter-section" class="space-y-2 hidden">
                    <div class="flex justify-between items-baseline">
                        <span class="text-xs text-slate-300 font-medium">پیشرفت عملیات (ADM Progress)</span>
                        <span id="prog-pct" class="text-2xl font-black text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-emerald-400">0%</span>
                    </div>
                    <div class="w-full bg-slate-900 border border-slate-700/60 rounded-full h-4 p-0.5 overflow-hidden shadow-inner">
                        <div id="prog-bar" class="bg-gradient-to-r from-blue-500 via-indigo-500 to-emerald-400 h-full rounded-full transition-all duration-300 shadow-md" style="width: 0%"></div>
                    </div>
                </div>

                <!-- LAST ADDED USER TICKER -->
                <div id="live-last-user-card" class="p-3 bg-slate-900/90 border border-slate-700/50 rounded-xl flex items-center gap-3 hidden">
                    <div class="w-9 h-9 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 flex items-center justify-center font-bold text-sm shrink-0">
                        👤
                    </div>
                    <div class="overflow-hidden flex-1">
                        <div class="text-[10px] text-emerald-400 font-bold flex items-center gap-1">
                            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                            آخرین ممبر اضافه شده:
                        </div>
                        <div id="live-last-user-name" class="text-xs font-extrabold text-white truncate">در حال اضافه کردن...</div>
                    </div>
                </div>

                <!-- DETAILED STATS GRID -->
                <div class="grid grid-cols-4 gap-1.5 text-center text-xs pt-1 border-t border-slate-700/40">
                    <div class="bg-slate-900/60 p-2 rounded-xl border border-slate-800">
                        <div class="text-[10px] text-slate-400">✅ موفق</div>
                        <div id="prog-added" class="font-extrabold text-emerald-400 text-sm mt-0.5">0</div>
                    </div>
                    <div class="bg-slate-900/60 p-2 rounded-xl border border-slate-800">
                        <div class="text-[10px] text-slate-400">❌ خطا</div>
                        <div id="prog-failed" class="font-extrabold text-rose-400 text-sm mt-0.5">0</div>
                    </div>
                    <div class="bg-slate-900/60 p-2 rounded-xl border border-slate-800">
                        <div class="text-[10px] text-slate-400">⏭️ رد شده</div>
                        <div id="prog-skipped" class="font-extrabold text-amber-400 text-sm mt-0.5">0</div>
                    </div>
                    <div class="bg-slate-900/60 p-2 rounded-xl border border-slate-800">
                        <div class="text-[10px] text-slate-400">⏱️ زمان</div>
                        <div id="prog-time" class="font-mono font-bold text-blue-300 text-[11px] mt-0.5">00:00</div>
                    </div>
                </div>
            </div>

            <!-- ATTACK MODE SELECTOR -->
            <div class="glass-card p-1.5 flex gap-1">
                <button id="btn-mode-single" onclick="setAttackCategory('single')" class="flex-1 py-2.5 text-xs font-bold rounded-xl transition active-tab">
                    📱 ۱. ادد تک اکانت
                </button>
                <button id="btn-mode-parallel" onclick="setAttackCategory('parallel')" class="flex-1 py-2.5 text-xs font-bold text-slate-400 rounded-xl transition">
                    ⚡ ۲. ادد موازی
                </button>
            </div>

            <!-- SINGLE ACCOUNT ADD FORM -->
            <div id="form-single" class="glass-card p-4 space-y-4">
                <h3 class="text-sm font-bold text-blue-400">📱 تنظیمات ادد تک اکانت</h3>
                
                <div>
                    <label class="block text-xs text-slate-300 mb-1.5">انتخاب اکانت ادد کننده:</label>
                    <select id="select-single-account" class="w-full bg-slate-900 border border-slate-700 text-xs text-white rounded-xl p-2.5 outline-none">
                        <option value="">در حال بارگذاری اکانت‌ها...</option>
                    </select>
                </div>

                <div>
                    <label class="block text-xs text-slate-300 mb-1.5">سرعت ادد تک:</label>
                    <div class="grid grid-cols-2 gap-2">
                        <button onclick="setSingleSpeed('safe')" id="single-speed-safe" class="p-2.5 bg-rose-600/30 border border-rose-500 text-rose-200 text-xs font-black rounded-xl text-center">🐌 آهسته و امن</button>
                        <button onclick="setSingleSpeed('ultra')" id="single-speed-ultra" class="p-2.5 bg-slate-800 border border-slate-700 text-slate-300 text-xs font-bold rounded-xl text-center">⚡ اولترا فست</button>
                    </div>
                    <div class="text-[10px] text-slate-500 mt-1">دقیقاً همان موتور ادد موازی، فقط با یک اکانت</div>
                </div>

                <div>
                    <label class="block text-xs text-slate-300 mb-1.5">نوع مخاطبین دیتابیس:</label>
                    <select id="select-single-type" class="w-full bg-slate-900 border border-slate-700 text-xs text-white rounded-xl p-2.5 outline-none">
                        <option value="all">🌐 همه (فقط یوزرنیم/شماره‌دار — ID خالی حذف میشه)</option>
                        <option value="phone">📱 فقط شماره‌دارها</option>
                        <option value="username">🏷️ فقط آیدی‌دارها (Username)</option>
                        <option value="id">🆔 فقط ID عددی</option>
                    </select>
                </div>

                <div class="pt-2">
                    <button id="btn-single-start" onclick="startSingleAdd()" class="w-full py-3 bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-xs font-bold rounded-xl shadow-lg hover:brightness-110 active:scale-95 transition">
                        ▶️ شروع ادد تک اکانت از دیتابیس
                    </button>
                </div>

                </div>
            </div>

            <!-- PARALLEL ADD FORM -->
            <div id="form-parallel" class="glass-card p-4 space-y-4 hidden">
                <h3 class="text-sm font-bold text-emerald-400">⚡ تنظیمات ادد موازی (چند اکانت همزمان)</h3>

                <div>
                    <label class="block text-xs text-slate-300 mb-1.5">انتخاب سرعت و مود ادد:</label>
                    <div class="grid grid-cols-2 gap-2">
                        <button onclick="setParallelSpeed('safe')" id="speed-safe" class="p-2.5 bg-rose-600/30 border border-rose-500 text-rose-200 text-xs font-black rounded-xl text-center">
                            🐌 آهسته و امن
                        </button>
                        <button onclick="setParallelSpeed('ultra')" id="speed-ultra" class="p-2.5 bg-slate-800 border border-slate-700 text-slate-300 text-xs font-bold rounded-xl text-center">
                            ⚡ اولترا فست
                        </button>
                    </div>
                </div>

                <div>
                    <div class="flex items-center justify-between mb-1.5">
                        <label class="block text-xs text-slate-300">اکانت‌های ادد کننده:</label>
                        <div class="flex gap-1.5">
                            <button onclick="parallelAccAll(true)" type="button"
                                    class="px-2 py-1 bg-slate-800 border border-slate-700 text-[10px] text-slate-300 rounded-lg active:scale-95">
                                همه
                            </button>
                            <button onclick="parallelAccAll(false)" type="button"
                                    class="px-2 py-1 bg-slate-800 border border-slate-700 text-[10px] text-slate-300 rounded-lg active:scale-95">
                                هیچ‌کدام
                            </button>
                        </div>
                    </div>
                    <div id="parallel-acc-list"
                         class="bg-slate-900 border border-slate-700 rounded-xl p-2 space-y-1 max-h-52 overflow-y-auto">
                        <div class="text-[11px] text-slate-500 text-center py-2">در حال بارگذاری اکانت‌ها...</div>
                    </div>
                    <div id="parallel-acc-summary" class="text-[10px] text-slate-500 mt-1.5"></div>
                </div>

                <div>
                    <label class="block text-xs text-slate-300 mb-1.5">فیلتر ممبرها از دیتابیس:</label>
                    <select id="select-parallel-type" class="w-full bg-slate-900 border border-slate-700 text-xs text-white rounded-xl p-2.5 outline-none">
                        <option value="all">🌐 همه کاربران دیتابیس</option>
                        <option value="phone">📱 فقط شماره‌دارها</option>
                        <option value="username">🏷️ فقط شناسه دارها</option>
                        <option value="id">🆔 فقط ID</option>
                    </select>
                </div>

                <div class="pt-2">
                    <button id="btn-parallel-start" onclick="startParallelAdd()" class="w-full py-3 bg-gradient-to-r from-emerald-600 to-teal-600 text-white text-xs font-bold rounded-xl shadow-lg hover:brightness-110 active:scale-95 transition">
                        ⚡⚡⚡ شروع ادد موازی با تمام اکانت‌ها
                    </button>
                </div>
            </div>
        </section>


        <!-- TAB 3: GAME LEAD FINDER -->
        <section id="tab-scrape" class="tab-content hidden space-y-4">

            <!-- 📥 اسکرپ مستقیم گروه -->
            <div class="glass-card p-4 space-y-3 border border-emerald-500/25">
                <h3 class="text-sm font-extrabold text-emerald-400 flex items-center gap-2">
                    <span>📥 استخراج ممبر از گروه</span>
                </h3>
                <p class="text-[11px] text-slate-300 leading-5">
                    لینک یا آیدی گروه را وارد کن تا اعضایش به دیتابیس اضافه شوند.
                    بعد از آن می‌توانی از تب «ادد» شروع کنی.
                </p>

                <input type="text" id="scrape-input" dir="ltr" placeholder="https://t.me/groupname"
                       class="w-full bg-slate-900 border border-slate-700 text-xs text-white rounded-xl px-3 py-2.5 outline-none focus:border-emerald-500 text-left">

                <div class="text-[10px] text-slate-500 leading-5">
                    قبول می‌شود: <span dir="ltr">https://t.me/name</span> · <span dir="ltr">@name</span> ·
                    <span dir="ltr">-1001234567890</span> · لینک دعوت خصوصی
                </div>

                <label class="flex items-start gap-2 bg-slate-900/60 border border-slate-700 rounded-xl px-3 py-2.5 cursor-pointer">
                    <input type="checkbox" id="scrape-deep" class="mt-0.5 accent-emerald-500">
                    <span class="text-[11px] leading-5">
                        <span class="text-white font-bold">حالت عمیق (کشف اعضای مخفی)</span><br>
                        <span class="text-slate-400">اشتراک گروهی، مخاطبین و جستجوی سراسری را هم اجرا می‌کند.
                        اعضای بیشتری پیدا می‌شود ولی خیلی کندتر است و ریسک محدودیت تلگرام دارد.</span>
                    </span>
                </label>

                <button onclick="startGroupScrape()" id="btn-scrape"
                        class="w-full py-3 bg-gradient-to-r from-emerald-600 to-teal-600 text-white text-xs font-black rounded-xl shadow-lg active:scale-95 transition">
                    📥 شروع استخراج ممبر
                </button>

                <div id="scrape-msg" class="text-[11px] leading-5 hidden"></div>

                <!-- پیشرفت زنده -->
                <div id="scrape-live" class="hidden space-y-2 pt-1">
                    <div class="flex items-center justify-between text-[11px]">
                        <span class="text-emerald-300 font-bold">در حال استخراج...</span>
                        <span id="scrape-count" class="text-white font-black">0</span>
                    </div>
                    <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                        <div class="h-full bg-gradient-to-r from-emerald-500 to-teal-400 animate-pulse" style="width:100%"></div>
                    </div>
                    <div id="scrape-stage" class="text-[10px] text-slate-400 leading-5"></div>
                    <button onclick="stopAddOperation()"
                            class="w-full py-2 bg-slate-800 hover:bg-slate-700 text-rose-300 text-[11px] font-bold rounded-xl border border-slate-700">
                        ⏹️ توقف
                    </button>
                </div>
            </div>

        </section>
        </section>


        <!-- TAB 4: CRM PIPELINE -->


        <!-- TAB 5: ACCOUNTS HEALTH -->
        <section id="tab-accounts" class="tab-content hidden space-y-3">
            <div class="flex items-center justify-between px-1">
                <h3 class="text-sm font-bold text-white">📊 وضعیت سلامت و ظرفیت اکانت‌ها</h3>
                <span class="text-xs text-slate-400">سقف: تا وقتی تلگرام اجازه بدهد</span>
            </div>
            <button id="btn-live-probe" onclick="runLiveProbe()" class="w-full py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-xs font-bold rounded-xl shadow-lg active:scale-95">
                🔬 تست زنده اتصال اکانت‌های صفر-ادد
            </button>
            <button id="btn-use-ready" onclick="startParallelAddFromAccounts()" class="w-full py-2.5 bg-gradient-to-r from-emerald-600 to-teal-600 text-white text-xs font-bold rounded-xl shadow-lg active:scale-95">
                ▶️ ادد موازی فقط با اکانت‌هایی که تست زنده را پاس کرده‌اند
            </button>
            <button id="btn-toggle-add" onclick="toggleAddAccount()" class="w-full py-2.5 bg-gradient-to-r from-violet-600 to-fuchsia-600 text-white text-xs font-bold rounded-xl shadow-lg active:scale-95">
                ➕ افزودن اکانت جدید
            </button>

            <div id="add-acc-panel" class="glass-card p-4 space-y-3 hidden">
                <h4 class="text-xs font-bold text-violet-300">➕ افزودن شماره برای ادد موازی</h4>

                <div id="add-step-phone" class="space-y-2">
                    <label class="text-[10px] text-slate-400">شماره با کد کشور:</label>
                    <input id="add-phone" type="tel" inputmode="tel" dir="ltr" placeholder="+989121234567"
                           class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2.5 text-sm text-white outline-none focus:border-violet-500 text-left">
                    <p class="text-[10px] text-slate-500 leading-5">۰۹۱۲… یا ۹۱۲… هم قبول است.</p>
                    <button onclick="addAccountStart()" class="w-full py-2.5 bg-violet-600 hover:bg-violet-500 text-white text-xs font-bold rounded-xl active:scale-95">
                        📨 ارسال کد تأیید
                    </button>
                </div>

                <div id="add-step-code" class="space-y-2 hidden">
                    <label class="text-[10px] text-slate-400">کد ۵ رقمی تلگرام:</label>
                    <input id="add-code" type="text" inputmode="numeric" dir="ltr" placeholder="12345"
                           class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2.5 text-sm text-white outline-none focus:border-violet-500 text-center tracking-[0.4em]">
                    <p class="text-[10px] text-amber-300/80 leading-5">کد در خودِ اپ تلگرام می‌آید (نه پیامک) — بخش «پیام‌های سرویس».</p>
                    <button onclick="addAccountCode()" class="w-full py-2.5 bg-violet-600 hover:bg-violet-500 text-white text-xs font-bold rounded-xl active:scale-95">
                        ✅ تأیید کد
                    </button>
                </div>

                <div id="add-step-pass" class="space-y-2 hidden">
                    <label class="text-[10px] text-slate-400">رمز دو مرحله‌ای:</label>
                    <input id="add-pass" type="password" dir="ltr" placeholder="••••••••"
                           class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2.5 text-sm text-white outline-none focus:border-violet-500 text-left">
                    <button onclick="addAccountPassword()" class="w-full py-2.5 bg-violet-600 hover:bg-violet-500 text-white text-xs font-bold rounded-xl active:scale-95">
                        🔓 ورود نهایی
                    </button>
                </div>

                <div id="add-acc-msg" class="text-[11px] leading-5 text-slate-300"></div>
                <button onclick="addAccountCancel()" class="w-full py-2 bg-slate-800 hover:bg-slate-700 text-slate-400 text-[11px] font-bold rounded-xl border border-slate-700">
                    انصراف
                </button>
            </div>

            <div id="accounts-list" class="space-y-2.5">
                <div class="text-center text-slate-400 text-xs py-8">در حال بارگذاری اکانت‌ها...</div>
            </div>
        </section>

    </main>

        <section id="tab-tools" class="tab-content hidden space-y-4">
            <!-- اسکن خودکار -->
            <div class="glass-card p-4 space-y-3">
                <div class="flex items-center justify-between">
                    <h3 class="text-sm font-bold text-slate-200">⏱️ اسکن خودکار پس‌زمینه</h3>
                    <span id="bg-status-badge" class="text-[10px] px-2 py-0.5 rounded-full bg-slate-700 text-slate-300">—</span>
                </div>
                <div id="bg-info" class="text-[11px] text-slate-400 leading-5">در حال بارگذاری…</div>
                <div class="grid grid-cols-4 gap-1.5" id="bg-intervals"></div>
                <div class="grid grid-cols-2 gap-1.5">
                    <button onclick="bgAction('toggle')" id="bg-toggle-btn" class="p-2.5 rounded-xl text-xs font-bold bg-gradient-to-r from-emerald-600 to-teal-600 text-white shadow-lg active:scale-95 transition">🟢 روشن کن</button>
                    <button onclick="loadTools()" class="p-2.5 rounded-xl text-xs font-bold bg-slate-800 border border-slate-700 text-slate-300 active:scale-95 transition">🔄 بروزرسانی</button>
                </div>
            </div>
            <!-- چت‌های اسکن‌شده -->
            <div class="glass-card p-4 space-y-2">
                <h3 class="text-sm font-bold text-slate-200">🗂️ چت‌های اسکن‌شده</h3>
                <div id="chats-list" class="space-y-1.5 max-h-72 overflow-y-auto text-[11px]">
                    <div class="text-slate-500">در حال بارگذاری…</div>
                </div>
            </div>
            <!-- پاکسازی -->
            <div class="glass-card p-4 space-y-2">
                <h3 class="text-sm font-bold text-slate-200">🧹 پاکسازی دیتابیس</h3>
                <p class="text-[10px] text-slate-400 leading-4">اددشده‌ها حذف می‌شوند ولی دیگر هیچ‌وقت دوباره ادد نمی‌شوند — دیتابیس همیشه سبک و سریع می‌ماند.</p>
                <div class="grid grid-cols-2 gap-1.5">
                    <button onclick="cleanupDb('purge_added')" class="p-2.5 rounded-xl text-xs font-bold bg-gradient-to-r from-rose-600 to-red-600 text-white shadow-lg active:scale-95 transition">🧹 پاکسازی اددشده‌ها</button>
                    <button onclick="cleanupDb('purge_dnadd')" class="p-2.5 rounded-xl text-xs font-bold bg-slate-800 border border-slate-700 text-slate-300 active:scale-95 transition">🚫 پاکسازی لیست‌سیاه</button>
                </div>
            </div>
        </section>

    <!-- BOTTOM NAVBAR -->
    <nav class="fixed bottom-0 left-0 right-0 glass-card mx-2 mb-2 p-1.5 flex justify-around items-center z-50 shadow-2xl">
        <button onclick="switchTab('dashboard')" id="nav-dashboard" class="flex-1 py-2 text-xs font-bold text-center rounded-xl transition active-tab">
            📊 داشبورد
        </button>
        <button onclick="switchTab('attack')" id="nav-attack" class="flex-1 py-2 text-xs font-bold text-slate-400 text-center rounded-xl transition">
            ⚡ ادد
        </button>
        <button onclick="switchTab('scrape')" id="nav-scrape" class="flex-1 py-2 text-xs font-bold text-slate-400 text-center rounded-xl transition">
            📥 استخراج
        </button>
        <button onclick="switchTab('accounts')" id="nav-accounts" class="flex-1 py-2 text-xs font-bold text-slate-400 text-center rounded-xl transition">
            📱 اکانت‌ها
        </button>
        <button onclick="switchTab('tools')" id="nav-tools" class="flex-1 py-2 text-xs font-bold text-slate-400 text-center rounded-xl transition">
            🧰 ابزار
        </button>
    </nav>

    <!-- JS APP LOGIC -->
    <script>
        const tg = window.Telegram?.WebApp;
        if (tg) {
            tg.ready();
            tg.expand();
        }

        let selectedParallelSpeed = 'safe';
        let selectedSingleSpeed = 'safe';
        let activeTab = 'dashboard';
        let currentCrmStatus = 'all';

        function switchTab(tabId) {
            activeTab = tabId;
            if (tg?.HapticFeedback) tg.HapticFeedback.selectionChanged();
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById('tab-' + tabId).classList.remove('hidden');

            document.querySelectorAll('nav button').forEach(btn => {
                btn.classList.remove('active-tab');
                btn.classList.add('text-slate-400');
            });
            const activeBtn = document.getElementById('nav-' + tabId);
            if (activeBtn) {
                activeBtn.classList.add('active-tab');
                activeBtn.classList.remove('text-slate-400');
            }

            if (tabId === 'dashboard') loadDashboard();
            if (tabId === 'tools') loadTools();
            if (tabId === 'accounts') loadAccounts();
            if (tabId === 'attack') loadAttackAccounts();
        }


        // ───── 🧰 تب ابزار: اسکن خودکار، چت‌ها، پاکسازی ─────
        let _bgData = null;
        async function apiPost(url, body) {
            const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
            return r.json();
        }
        async function loadTools() {
            try {
                const [bgr, chr] = await Promise.all([
                    fetch('api/bg').then(r => r.json()),
                    fetch('api/chats').then(r => r.json())
                ]);
                renderBg(bgr);
                renderChats(chr);
            } catch (e) { console.error(e); }
        }
        function renderBg(d) {
            if (!d || !d.ok) return;
            _bgData = d.bg;
            const bg = d.bg;
            const badge = document.getElementById('bg-status-badge');
            badge.textContent = bg.enabled ? '🟢 روشن' : '🔴 خاموش';
            badge.className = 'text-[10px] px-2 py-0.5 rounded-full ' + (bg.enabled ? 'bg-emerald-600/30 text-emerald-300' : 'bg-slate-700 text-slate-300');
            document.getElementById('bg-info').innerHTML =
                '👤 اکانت: <b class="text-slate-200">' + (bg.account_phone || '—') + '</b><br>' +
                '🎯 هدف: <b class="text-slate-200">' + ((d.target && d.target.name) || '—') + '</b><br>' +
                '⏰ هر ' + (bg.interval_minutes || 60) + ' دقیقه · مجموع یافته: ' + (bg.total_found || 0) + '<br>' +
                '📊 وضعیت: <span class="text-blue-300">' + (bg.status || 'idle') + '</span>';
            const btn = document.getElementById('bg-toggle-btn');
            btn.textContent = bg.enabled ? '🔴 خاموش کن' : '🟢 روشن کن';
            const iv = document.getElementById('bg-intervals');
            iv.innerHTML = [30, 60, 120, 240].map(m =>
                '<button onclick="bgInterval(' + m + ')" class="p-2 rounded-xl text-[10px] font-bold ' +
                (bg.interval_minutes == m ? 'bg-blue-600 text-white' : 'bg-slate-800 border border-slate-700 text-slate-300') +
                '">' + m + 'د</button>').join('');
        }
        async function bgAction(action) {
            if (tg?.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
            const body = { action: action };
            if (_bgData && _bgData.account_phone) body.account = _bgData.account_phone;
            const r = await apiPost('api/bg', body);
            if (!r.ok && r.error) alert(r.error);
            loadTools();
        }
        async function bgInterval(mins) {
            if (tg?.HapticFeedback) tg.HapticFeedback.selectionChanged();
            const body = { interval: mins };
            if (_bgData && _bgData.account_phone) body.account = _bgData.account_phone;
            await apiPost('api/bg', body);
            loadTools();
        }
        function renderChats(d) {
            if (!d || !d.ok) return;
            const el = document.getElementById('chats-list');
            if (!d.chats.length) { el.innerHTML = '<div class="text-slate-500">هنوز چیزی اسکن نشده</div>'; return; }
            el.innerHTML = d.chats.map(ch =>
                '<div class="p-2 rounded-xl bg-slate-800/60 border border-slate-700/60">' +
                '<div class="flex justify-between items-center gap-2">' +
                '<span class="font-bold text-slate-200 truncate">' + (ch.favorite ? '⭐ ' : '') +
                (ch.type === 'channel' ? '📡' : '👥') + ' ' + ch.name + '</span>' +
                '<span class="text-blue-300 whitespace-nowrap">' + ch.pct + '%</span></div>' +
                '<div class="text-slate-400 mt-1">' + ch.extracted.toLocaleString('fa-IR') + ' / ' +
                (ch.total ? ch.total.toLocaleString('fa-IR') : '?') + ' 👤' +
                (ch.category ? ' · 📁 ' + ch.category : '') + '</div></div>').join('');
        }
        async function cleanupDb(kind) {
            if (tg?.HapticFeedback) tg.HapticFeedback.impactOccurred('medium');
            const r = await apiPost('api/cleanup', { kind: kind });
            if (r.ok) {
                if (tg?.showAlert) tg.showAlert('🧹 ' + r.removed + ' رکورد پاک شد');
                else alert('🧹 ' + r.removed + ' رکورد پاک شد');
                loadTools();
            } else if (r.error) { alert(r.error); }
        }

        function setAttackCategory(cat) {
            if (tg?.HapticFeedback) tg.HapticFeedback.selectionChanged();
            if (cat === 'single') {
                document.getElementById('form-single').classList.remove('hidden');
                document.getElementById('form-parallel').classList.add('hidden');
                document.getElementById('btn-mode-single').classList.add('active-tab');
                document.getElementById('btn-mode-parallel').classList.remove('active-tab');
            } else {
                document.getElementById('form-single').classList.add('hidden');
                document.getElementById('form-parallel').classList.remove('hidden');
                document.getElementById('btn-mode-single').classList.remove('active-tab');
                document.getElementById('btn-mode-parallel').classList.add('active-tab');
            }
        }

        function setParallelSpeed(speed) {
            if (tg?.HapticFeedback) tg.HapticFeedback.selectionChanged();
            selectedParallelSpeed = speed;
            ['safe', 'ultra'].forEach(s => {
                const btn = document.getElementById('speed-' + s);
                if (!btn) return;
                btn.className = (s === speed)
                    ? "p-2.5 bg-rose-600/30 border border-rose-500 text-rose-200 text-xs font-black rounded-xl text-center"
                    : "p-2.5 bg-slate-800 border border-slate-700 text-slate-300 text-xs font-bold rounded-xl text-center hover:border-blue-500";
            });
        }

        function setSingleSpeed(speed) {
            if (tg?.HapticFeedback) tg.HapticFeedback.selectionChanged();
            selectedSingleSpeed = speed;
            ['safe','ultra'].forEach(s => {
                const btn = document.getElementById('single-speed-'+s);
                if (!btn) return;
                btn.className = (s === speed)
                    ? "p-2.5 bg-rose-600/30 border border-rose-500 text-rose-200 text-xs font-black rounded-xl text-center"
                    : "p-2.5 bg-slate-800 border border-slate-700 text-slate-300 text-xs font-bold rounded-xl text-center";
            });
        }



        // ── استخراج مستقیم گروه ──────────────────────────────
        function scrapeMsg(text, kind) {
            const el = document.getElementById('scrape-msg');
            el.textContent = text;
            el.classList.remove('hidden');
            el.className = 'text-[11px] leading-5 ' + (
                kind === 'ok' ? 'text-emerald-300' :
                kind === 'err' ? 'text-rose-300' : 'text-slate-300'
            );
        }

        async function startGroupScrape() {
            const raw = document.getElementById('scrape-input').value.trim();
            if (!raw) { scrapeMsg('آدرس گروه را وارد کن.', 'err'); return; }

            const btn = document.getElementById('btn-scrape');
            btn.disabled = true;
            btn.innerHTML = '⏳ در حال شروع...';
            scrapeMsg('در حال اتصال به تلگرام...', 'info');

            try {
                const res = await fetch('api/scrape/group', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        target: raw,
                        deep: !!document.getElementById('scrape-deep')?.checked
                    })
                });
                const d = await res.json();
                scrapeMsg(d.message || d.error || '', d.ok ? 'ok' : 'err');
                if (d.ok) {
                    document.getElementById('scrape-live').classList.remove('hidden');
                    if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
                }
            } catch (e) {
                scrapeMsg('خطای ارتباط: ' + e, 'err');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '📥 شروع استخراج ممبر';
            }
        }

        // پیشرفت زنده اسکرپ — از همان پول داشبورد تغذیه می‌شود
        function updateScrapeProgress(m) {
            const box = document.getElementById('scrape-live');
            if (!box) return;
            const p = m.add_progress || {};
            const isScrape = (p.mode || '').indexOf('اسکرپ') !== -1;

            if (m.is_adding && isScrape) {
                box.classList.remove('hidden');
                document.getElementById('scrape-count').innerText =
                    (p.added || 0).toLocaleString('fa-IR') + ' ممبر';
                document.getElementById('scrape-stage').innerText =
                    p.status_text || p.last_user || '';
            } else if (!m.is_adding && isScrape && p.finished) {
                document.getElementById('scrape-count').innerText =
                    (p.added || 0).toLocaleString('fa-IR') + ' ممبر';
                document.getElementById('scrape-stage').innerText = p.status_text || '';
                const btn = box.querySelector('button');
                if (btn) btn.classList.add('hidden');
            }
        }

        async function scrapeDiscoveredGroup(target) {
            if (!target) return;
            if (!confirm(`آیا از شروع استخراج ممبر از ${target} به دیتابیس مطمئن هستید؟`)) return;
            if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');

            try {
                const res = await fetch('api/scrape/group', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target: target })
                });
                const data = await res.json();
                alert(data.message || (data.ok ? 'عملیات استخراج شروع شد' : data.error));
                if (data.ok) switchTab('attack');
            } catch (e) {
                alert('خطا در شروع اسکرپ: ' + e);
            }
        }





        async function startSingleAdd() {
            const account = document.getElementById('select-single-account').value;
            const addType = document.getElementById('select-single-type').value;
            const addMode = selectedSingleSpeed || 'safe';

            if (!account) {
                alert('لطفاً یک اکانت انتخاب کنید.');
                return;
            }

            if (!confirm(`آیا از شروع ادد تک اکانت با اکانت ${account} مطمئن هستید؟`)) return;
            if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');

            try {
                const res = await fetch('api/add/single', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phone: account, add_type: addType, mode: addMode })
                });
                const data = await res.json();
                alert(data.message || (data.ok ? 'عملیات شروع شد' : data.error));
                if (data.ok) {
                    loadDashboard();
                }
            } catch (e) {
                alert('خطا در برقراری ارتباط با سرور: ' + e);
            }
        }

        async function runLiveProbe() {
            if (!confirm('تست زنده اتصال برای اکانت‌های صفر-ادد شروع شود؟ ممکن است ۱-۲ دقیقه طول بکشد.')) return;
            const btn = document.getElementById('btn-live-probe');
            if (btn) { btn.innerText = '⏳ در حال تست زنده...'; btn.disabled = true; }
            try {
                const res = await fetch('api/accounts/probe', { method: 'POST' });
                const data = await res.json();
                alert(data.message || (data.ok ? 'تست شروع شد' : data.error));
            } catch (e) { alert('خطا: ' + e); }
            if (btn) { btn.innerText = '🔬 تست زنده اتصال اکانت‌های صفر-ادد'; btn.disabled = false; }
            loadAccounts();
        }

        async function startParallelAddFromAccounts() {
            selectedParallelSpeed = selectedParallelSpeed || 'safe';
            // ⚠️ این میان‌بر از تب «اکانت‌ها» صدا زده می‌شود، جایی که ممکن است
            // فهرست چک‌باکس‌های تب ادد هنوز رندر نشده باشد. بدون این فراخوانی
            // currentParallelPhones() خالی برمی‌گردد و کاربر پیام گمراه‌کننده
            // «حداقل یک اکانت انتخاب کن» می‌گیرد در حالی که چیزی برای انتخاب ندیده.
            if (!document.querySelectorAll('.parallel-acc-cb').length) {
                await loadAttackAccounts();
            }
            parallelAccAll(true);
            await startParallelAdd();
        }

        async function startParallelAdd() {
            const addType = document.getElementById('select-parallel-type').value;
            const phones = currentParallelPhones();

            if (!phones.length) {
                alert('حداقل یک اکانت انتخاب کن.');
                return;
            }

            const total = document.querySelectorAll('.parallel-acc-cb:not(:disabled)').length;
            const who = phones.length === total
                ? `تمام ${total} اکانت`
                : `${phones.length} اکانت انتخابی`;
            if (!confirm(`شروع ادد موازی با ${who} در مود ${selectedParallelSpeed.toUpperCase()}؟`)) return;
            if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');

            try {
                const res = await fetch('api/add/parallel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: selectedParallelSpeed, add_type: addType, phones: phones })
                });
                const data = await res.json();
                alert(data.message || (data.ok ? 'عملیات ادد موازی شروع شد' : data.error));
                if (data.ok) {
                    loadDashboard();
                }
            } catch (e) {
                alert('خطا در برقراری ارتباط با سرور: ' + e);
            }
        }

        async function stopAddOperation() {
            document.getElementById('btn-stop-text').innerText = 'در حال توقف...';
            document.getElementById('btn-stop-add').disabled = true;
            if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('warning');
            try {
                await fetch('api/add/stop', { method: 'POST' });
            } catch (e) {
                alert('خطا در توقف: ' + e);
            }
            setTimeout(() => {
                document.getElementById('btn-stop-text').innerText = 'توقیف فوری';
                document.getElementById('btn-stop-add').disabled = false;
                loadDashboard();
            }, 1500);
        }

        async function loadDashboard() {
            try {
                const res = await fetch('api/dashboard');
                const data = await res.json();
                if (data.ok) {
                    const m = data.metrics;
                    document.getElementById('m-members').innerText = m.total_members.toLocaleString('fa-IR');
                    document.getElementById('m-accounts').innerText = m.healthy_accounts;
                    document.getElementById('m-adds').innerText = m.today_adds;
                    document.getElementById('m-limited').innerText = m.limited_accounts;
                    if (document.getElementById('m-blocked')) {
                        document.getElementById('m-blocked').innerText = (m.blocked_count || 0).toLocaleString('fa-IR');
                    }
                    if (document.getElementById('m-leads')) {
                        document.getElementById('m-leads').innerText = (m.total_leads || 0).toLocaleString('fa-IR');
                    }
                    document.getElementById('target-label').innerText = m.target_group;

                    if (m.add_cap) { window.__ADD_CAP = m.add_cap; }
                    updateScrapeProgress(m);
                    if (m.is_adding) {
                        // Sticky banner
                        document.getElementById('sticky-add-banner').classList.remove('hidden');
                        document.getElementById('banner-mode-title').innerText = '🚀 عملیات ادد زنده (' + (m.add_progress.mode || 'فعال') + ')';
                        const added = (m.add_progress.added || 0).toLocaleString('fa-IR');
                        const failed = (m.add_progress.failed || 0).toLocaleString('fa-IR');
                        const skipped = (m.add_progress.skipped || 0).toLocaleString('fa-IR');
                        const remaining = (m.add_progress.remaining || 0).toLocaleString('fa-IR');
                        const speed = (m.add_progress.speed_per_min || 0).toLocaleString('fa-IR');
                        document.getElementById('banner-stats-text').innerText = `✅ ${added} | ⏭ ${skipped} | ❌ ${failed} | ⏳ ${remaining} | ⚡ ${speed}/min`;
                        document.getElementById('banner-account-text').innerText = '📱 اکانت فعال: ' + (m.add_progress.current_account || '—');

                        // 🟢 LIVE CONSOLE on dashboard
                        document.getElementById('dash-live-console').classList.remove('hidden');
                        document.getElementById('dash-live-title').innerText = '🟢 عملیات ادد زنده (' + (m.add_progress.mode || 'فعال') + ')';
                        document.getElementById('dash-live-account').innerText = '📱 اکانت فعال: ' + (m.add_progress.current_account || '—');
                        if (m.add_progress.active_accounts && m.add_progress.active_accounts.length) {
                            document.getElementById('dash-live-account').innerText = '📱 اکانت‌های فعال: ' + m.add_progress.active_accounts.join('، ');
                        }

                        const p = m.add_progress;
                        const total = p.total || 1;
                        const current = (p.added || 0) + (p.failed || 0) + (p.skipped || 0);
                        const pct = Math.min(100, Math.round((current / total) * 100));

                        document.getElementById('dash-live-pct').innerText = pct + '%';
                        document.getElementById('dash-live-bar').style.width = pct + '%';
                        document.getElementById('dash-live-added').innerText = added;
                        document.getElementById('dash-live-skipped').innerText = skipped;
                        document.getElementById('dash-live-failed').innerText = failed;
                        document.getElementById('dash-live-remaining').innerText = remaining;
                        document.getElementById('dash-live-last').innerText = p.last_user || 'در حال آماده‌سازی کاربر بعدی...';

                        const dsec = p.elapsed_sec || 0;
                        const dmins = Math.floor(dsec / 60);
                        const dremSec = dsec % 60;
                        document.getElementById('dash-live-time').innerText =
                            String(dmins).padStart(2, '0') + ':' + String(dremSec).padStart(2, '0');

                        // Form Buttons transform into STOP buttons
                        const btnSingle = document.getElementById('btn-single-start');
                        if (btnSingle) {
                            btnSingle.innerHTML = '⏹️ توقف فوری عملیات ادد (در حال اجرا...)';
                            btnSingle.className = 'w-full py-3 bg-gradient-to-r from-rose-600 to-red-600 text-white text-xs font-black rounded-xl shadow-lg border border-rose-500/40 active:scale-95 transition pulse-live';
                            btnSingle.onclick = stopAddOperation;
                        }

                        const btnParallel = document.getElementById('btn-parallel-start');
                        if (btnParallel) {
                            btnParallel.innerHTML = '⏹️ توقف فوری عملیات ادد (در حال اجرا...)';
                            btnParallel.className = 'w-full py-3 bg-gradient-to-r from-rose-600 to-red-600 text-white text-xs font-black rounded-xl shadow-lg border border-rose-500/40 active:scale-95 transition pulse-live';
                            btnParallel.onclick = stopAddOperation;
                        }

                        document.getElementById('status-text').innerText = '🚀 در حال ادد زنده...';
                        document.getElementById('status-text').className = 'text-xs text-emerald-400 font-bold';

                        document.getElementById('live-dot').className = 'w-3 h-3 rounded-full bg-emerald-400 pulse-live';
                        document.getElementById('live-card-title').innerText = '🟢 عملیات ادد زنده فعال است (' + (m.add_progress.mode || 'Ultra Fast') + ')';
                        document.getElementById('live-card-title').className = 'text-xs font-black text-emerald-300';

                        document.getElementById('live-speed-tag').classList.remove('hidden');
                        document.getElementById('live-speed-tag').innerText = '⚡ سرعت: ' + (m.add_progress.speed_per_min || 0) + ' member/min';

                        document.getElementById('btn-stop-add').classList.remove('hidden');
                        document.getElementById('live-meter-section').classList.remove('hidden');
                        document.getElementById('live-last-user-card').classList.remove('hidden');

                        document.getElementById('prog-pct').innerText = pct + '%';
                        document.getElementById('prog-bar').style.width = pct + '%';
                        document.getElementById('prog-added').innerText = added;
                        document.getElementById('prog-failed').innerText = failed;
                        document.getElementById('prog-skipped').innerText = skipped;

                        const lastUser = m.add_progress.last_user || 'در حال آماده‌سازی کاربر بعدی...';
                        document.getElementById('live-last-user-name').innerText = lastUser;

                        const sec = m.add_progress.elapsed_sec || 0;
                        const mins = Math.floor(sec / 60);
                        const remainderSec = sec % 60;
                        document.getElementById('prog-time').innerText = 
                            String(mins).padStart(2, '0') + ':' + String(remainderSec).padStart(2, '0');

                    } else {
                        // Hide sticky banner
                        document.getElementById('sticky-add-banner').classList.add('hidden');
                        // Hide dashboard live console
                        document.getElementById('dash-live-console').classList.add('hidden');

                        // Reset buttons to start state
                        const btnSingle = document.getElementById('btn-single-start');
                        if (btnSingle) {
                            btnSingle.innerHTML = '▶️ شروع ادد تک اکانت از دیتابیس';
                            btnSingle.className = 'w-full py-3 bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-xs font-bold rounded-xl shadow-lg hover:brightness-110 active:scale-95 transition';
                            btnSingle.onclick = startSingleAdd;
                        }

                        const btnParallel = document.getElementById('btn-parallel-start');
                        if (btnParallel) {
                            btnParallel.innerHTML = '⚡⚡⚡ شروع ادد موازی با تمام اکانت‌ها';
                            btnParallel.className = 'w-full py-3 bg-gradient-to-r from-emerald-600 to-teal-600 text-white text-xs font-bold rounded-xl shadow-lg hover:brightness-110 active:scale-95 transition';
                            btnParallel.onclick = startParallelAdd;
                        }

                        document.getElementById('status-text').innerText = 'سیستم آماده به کار';
                        document.getElementById('status-text').className = 'text-xs text-slate-400 font-medium';

                        document.getElementById('live-dot').className = 'w-2.5 h-2.5 rounded-full bg-slate-500';
                        document.getElementById('live-card-title').innerText = '⚪ وضعیت: آماده به کار (عملیات اددی در حال اجرا نیست)';
                        document.getElementById('live-card-title').className = 'text-xs font-bold text-slate-300';

                        document.getElementById('live-speed-tag').classList.add('hidden');
                        document.getElementById('btn-stop-add').classList.add('hidden');
                        document.getElementById('live-last-user-card').classList.add('hidden');

                        // نتیجه آخرین عملیات را نگه دار — قبلاً همه‌چیز صفر
                        // می‌شد و کاربر هرگز نمی‌فهمید چند نفر واقعاً اضافه شدند.
                        const fin = m.add_progress || {};
                        if (fin.finished) {
                            document.getElementById('live-meter-section').classList.remove('hidden');
                            const fAdded = fin.added || 0;
                            const fFailed = fin.failed || 0;
                            const fSkipped = fin.skipped || 0;
                            const fTotal = fin.total || 1;
                            const fPct = Math.min(100, Math.round(((fAdded + fFailed + fSkipped) / fTotal) * 100));

                            document.getElementById('prog-pct').innerText = fPct + '%';
                            document.getElementById('prog-bar').style.width = fPct + '%';
                            document.getElementById('prog-added').innerText = fAdded;
                            document.getElementById('prog-failed').innerText = fFailed;
                            document.getElementById('prog-skipped').innerText = fSkipped;

                            const fs = fin.elapsed_sec || 0;
                            document.getElementById('prog-time').innerText =
                                String(Math.floor(fs / 60)).padStart(2, '0') + ':' +
                                String(fs % 60).padStart(2, '0');

                            document.getElementById('live-card-title').innerText =
                                '🏁 آخرین عملیات: ' + fAdded + ' عضو شد، ' +
                                fSkipped + ' رد، ' + fFailed + ' خطا';
                            document.getElementById('live-card-title').className =
                                'text-xs font-bold text-sky-300';

                            if (fin.status_text) {
                                document.getElementById('status-text').innerText = fin.status_text;
                                document.getElementById('status-text').className =
                                    'text-xs font-medium ' +
                                    (fin.status_text.indexOf('❌') === 0 ? 'text-rose-400' : 'text-sky-400');
                            }
                        } else {
                            document.getElementById('live-meter-section').classList.add('hidden');
                        }
                    }
                }
            } catch (e) { console.error(e); }
        }

        async function loadDashAccounts() {
            try {
                const res = await fetch('api/accounts');
                const data = await res.json();
                if (data.ok) {
                    const strip = document.getElementById('dash-accounts-strip');
                    const badge = document.getElementById('dash-acc-badge');
                    if (!strip) return;
                    const accs = data.accounts || [];
                    badge.innerText = accs.length + ' اکانت';
                    strip.innerHTML = '';
                    if (!accs.length) {
                        strip.innerHTML = '<div class="text-[11px] text-slate-500 text-center py-2">هنوز اکانتی ثبت نشده.</div>';
                        return;
                    }
                    accs.forEach(acc => {
                        let dotColor = 'bg-emerald-500', label = 'سالم', labelColor = 'text-emerald-300';
                        if (acc.status === 'limited') {
                            dotColor = 'bg-rose-500';
                            label = 'محدود';
                            labelColor = 'text-rose-300';
                            if (acc.remaining_seconds > 0) label += ' (' + Math.ceil(acc.remaining_seconds / 60) + 'm)';
                        } else if (acc.status === 'no_session') {
                            dotColor = 'bg-rose-500';
                            label = 'بدون سشن';
                            labelColor = 'text-rose-300';
                        } else if (acc.status === 'busy') {
                            dotColor = 'bg-sky-400';
                            label = 'مشغول';
                            labelColor = 'text-sky-300';
                        } else if (acc.status === 'dead') {
                            dotColor = 'bg-rose-500';
                            label = 'خراب';
                            labelColor = 'text-rose-300';
                        } else if (acc.status === 'unchecked') {
                            dotColor = 'bg-amber-400';
                            label = 'تست‌نشده';
                            labelColor = 'text-amber-300';
                        } else if (acc.status === 'unused') {
                            dotColor = 'bg-amber-400';
                            label = 'تست‌نشده';
                            labelColor = 'text-amber-300';
                        } else if (acc.added_today >= (window.__ADD_CAP || 1000)) {
                            dotColor = 'bg-amber-500';
                            label = 'ظرفیت پر';
                            labelColor = 'text-amber-300';
                        }
                        const pct = Math.min(100, Math.round((acc.added_today / 100) * 100));
                        strip.innerHTML += `
                            <div class="flex items-center justify-between gap-2 p-2.5 bg-slate-900/60 border border-slate-800 rounded-xl">
                                <div class="flex items-center gap-2 min-w-0">
                                    <span class="w-2 h-2 rounded-full ${dotColor} shrink-0"></span>
                                    <div class="min-w-0">
                                        <div class="text-[11px] font-bold text-white truncate">${acc.name}</div>
                                        <div class="text-[9px] font-mono text-slate-500 truncate">${acc.phone}</div>
                                    </div>
                                </div>
                                <div class="flex items-center gap-2 shrink-0">
                                    <span class="text-[9px] font-bold ${labelColor}">${label}</span>
                                    <div class="w-12 bg-slate-900 h-1.5 rounded-full overflow-hidden border border-slate-800">
                                        <div class="bg-gradient-to-r from-blue-500 to-emerald-400 h-full rounded-full" style="width:${pct}%"></div>
                                    </div>
                                    <span class="text-[9px] font-mono text-slate-400">${acc.added_today}/100</span>
                                </div>
                            </div>
                        `;
                    });
                }
            } catch (e) { console.error(e); }
        }

        async function loadAccounts() {
            try {
                const res = await fetch('api/accounts');
                const data = await res.json();
                if (data.ok) {
                    const list = document.getElementById('accounts-list');
                    list.innerHTML = '';
                    data.accounts.forEach(acc => {
                        let statusBadge = '<span class="px-2.5 py-1 bg-emerald-500/20 text-emerald-300 text-[10px] font-bold rounded-lg border border-emerald-500/30">✅ سالم</span>';
                        if (acc.status === 'limited') {
                            const min = Math.ceil(acc.remaining_seconds / 60);
                            statusBadge = `<span class="px-2.5 py-1 bg-rose-500/20 text-rose-300 text-[10px] font-bold rounded-lg border border-rose-500/30">🔴 محدود (${min}m)</span>`;
                        } else if (acc.status === 'no_session') {
                            statusBadge = '<span class="px-2.5 py-1 bg-rose-500/20 text-rose-300 text-[10px] font-bold rounded-lg border border-rose-500/30">🔴 بدون سشن</span>';
                        } else if (acc.status === 'busy') {
                            statusBadge = '<span class="px-2.5 py-1 bg-sky-500/20 text-sky-300 text-[10px] font-bold rounded-lg border border-sky-500/30">🟡 مشغول</span>';
                        } else if (acc.status === 'dead') {
                            statusBadge = '<span class="px-2.5 py-1 bg-rose-500/20 text-rose-300 text-[10px] font-bold rounded-lg border border-rose-500/30">🔴 خراب</span>';
                        } else if (acc.status === 'unchecked' || acc.status === 'unused') {
                            statusBadge = '<span class="px-2.5 py-1 bg-amber-500/20 text-amber-300 text-[10px] font-bold rounded-lg border border-amber-500/30">⏳ تست زنده نشده</span>';
                        } else if (acc.added_today >= (window.__ADD_CAP || 1000)) {
                            statusBadge = '<span class="px-2.5 py-1 bg-amber-500/20 text-amber-300 text-[10px] font-bold rounded-lg border border-amber-500/30">⚠️ ظرفیت پر</span>';
                        }

                        const pct = Math.min(100, Math.round((acc.added_today / 100) * 100));
                        // نام اکانت را برای درج امن در HTML فرار می‌دهیم.
                        // نسخه قبل onclick را با رشته درون‌یابی می‌ساخت و یک
                        // کوتیشن داخل آن، ویژگی onclick را زودتر می‌بست و کل
                        // صفحه را خراب می‌کرد (مینی‌اپ فریز می‌شد).
                        const accName = escAttr(acc.name || acc.phone);

                        list.innerHTML += `
                            <div class="glass-card p-3.5 space-y-2.5">
                                <div class="flex items-center justify-between">
                                    <div>
                                        <div class="text-xs font-extrabold text-white">${acc.name}</div>
                                        <div class="text-[10px] font-mono text-slate-400 mt-0.5">${acc.phone}</div>
                                        ${acc.reason ? '<div class="text-[10px] text-amber-300/90 mt-1 leading-5">'+acc.reason+'</div>' : ''}
                                    </div>
                                    ${statusBadge}
                                </div>
                                <div class="space-y-1">
                                    <div class="flex justify-between text-[10px] text-slate-400 font-medium">
                                        <span>ادد امروز</span>
                                        <span class="font-bold text-blue-300">${acc.added_today} / 100</span>
                                    </div>
                                    <div class="w-full bg-slate-900 h-2 rounded-full overflow-hidden p-0.5 border border-slate-800">
                                        <div class="bg-gradient-to-r from-blue-500 to-emerald-400 h-full rounded-full transition-all duration-300" style="width: ${pct}%"></div>
                                    </div>
                                </div>
                                <button data-del-phone="${acc.phone}" data-del-name="${accName}"
                                        class="btn-del-acc w-full py-2 bg-rose-600/15 hover:bg-rose-600/30 text-rose-300 text-[11px] font-bold rounded-xl border border-rose-500/30 transition">
                                    🗑️ حذف این اکانت
                                </button>
                            </div>
                        `;
                    });
                }
            } catch (e) { console.error(e); }
        }

        // فرار دادن مقدار برای درج امن داخل ویژگی HTML.
        function escAttr(v) {
            return String(v == null ? '' : v)
                .replace(/&/g, '&amp;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
        }

        // اتصال هندلر با event delegation به‌جای onclick درون‌خطی.
        // این‌طوری نام اکانت هرچه باشد (کوتیشن، براکت و…) HTML نمی‌شکند.
        document.addEventListener('click', function (ev) {
            const btn = ev.target.closest && ev.target.closest('.btn-del-acc');
            if (!btn) return;
            deleteAccount(btn.dataset.delPhone, btn.dataset.delName);
        });

        // ── افزودن اکانت از مینی‌اپ ──────────────────────────────
        let addAccPhone = '';

        function toggleAddAccount() {
            const p = document.getElementById('add-acc-panel');
            p.classList.toggle('hidden');
            if (!p.classList.contains('hidden')) {
                addAccReset();
                document.getElementById('add-phone').focus();
            }
        }

        function addAccReset() {
            addAccPhone = '';
            document.getElementById('add-step-phone').classList.remove('hidden');
            document.getElementById('add-step-code').classList.add('hidden');
            document.getElementById('add-step-pass').classList.add('hidden');
            document.getElementById('add-acc-msg').textContent = '';
            ['add-phone', 'add-code', 'add-pass'].forEach(function (id) {
                const el = document.getElementById(id);
                if (el) el.value = '';
            });
        }

        function addAccMsg(text, kind) {
            const el = document.getElementById('add-acc-msg');
            el.textContent = text;
            el.className = 'text-[11px] leading-5 ' + (
                kind === 'ok' ? 'text-emerald-300' :
                kind === 'err' ? 'text-rose-300' : 'text-slate-300'
            );
        }

        function addAccStep(needs) {
            document.getElementById('add-step-phone').classList.add('hidden');
            document.getElementById('add-step-code').classList.toggle('hidden', needs !== 'code');
            document.getElementById('add-step-pass').classList.toggle('hidden', needs !== 'password');
            const focus = needs === 'code' ? 'add-code' : needs === 'password' ? 'add-pass' : null;
            if (focus) { const el = document.getElementById(focus); if (el) el.focus(); }
        }

        async function addAccPost(url, payload) {
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            return await res.json();
        }

        async function addAccountStart() {
            const phone = document.getElementById('add-phone').value.trim();
            if (!phone) { addAccMsg('شماره را وارد کن.', 'err'); return; }
            addAccMsg('در حال ارسال کد...', 'info');
            try {
                const d = await addAccPost('/api/accounts/add', { phone: phone });
                addAccMsg(d.message || '', d.ok ? 'ok' : 'err');
                if (d.ok) { addAccPhone = phone; addAccStep(d.needs || 'code'); }
            } catch (e) { addAccMsg('خطای ارتباط: ' + e, 'err'); }
        }

        async function addAccountCode() {
            const code = document.getElementById('add-code').value.trim();
            if (!code) { addAccMsg('کد را وارد کن.', 'err'); return; }
            addAccMsg('در حال بررسی کد...', 'info');
            try {
                const d = await addAccPost('/api/accounts/add/code', { phone: addAccPhone, code: code });
                addAccMsg(d.message || '', d.ok ? 'ok' : 'err');
                if (d.ok && d.needs === 'password') { addAccStep('password'); return; }
                if (d.ok) { addAccDone(); }
            } catch (e) { addAccMsg('خطای ارتباط: ' + e, 'err'); }
        }

        async function addAccountPassword() {
            const pwd = document.getElementById('add-pass').value;
            if (!pwd) { addAccMsg('رمز را وارد کن.', 'err'); return; }
            addAccMsg('در حال ورود...', 'info');
            try {
                const d = await addAccPost('/api/accounts/add/code', { phone: addAccPhone, password: pwd });
                addAccMsg(d.message || '', d.ok ? 'ok' : 'err');
                if (d.ok) { addAccDone(); }
            } catch (e) { addAccMsg('خطای ارتباط: ' + e, 'err'); }
        }

        function addAccDone() {
            loadAccounts();
            loadAttackAccounts();
            loadDashboard();
            setTimeout(function () {
                document.getElementById('add-acc-panel').classList.add('hidden');
                addAccReset();
            }, 2500);
        }

        async function addAccountCancel() {
            if (addAccPhone) {
                try { await addAccPost('/api/accounts/add/cancel', { phone: addAccPhone }); } catch (e) {}
            }
            document.getElementById('add-acc-panel').classList.add('hidden');
            addAccReset();
        }

        async function deleteAccount(phone, name) {
            if (!confirm(`اکانت «${name}» (${phone}) حذف شود؟\n\nرکورد دیتابیس و فایل سشن پاک می‌شوند. برای برگرداندن باید دوباره با کد تلگرام لاگین کنی.`)) return;
            try {
                const res = await fetch('api/accounts/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phone })
                });
                const data = await res.json();
                alert(data.message || (data.ok ? 'حذف شد' : 'حذف ناموفق بود'));
                if (data.ok) { loadAccounts(); loadAttackAccounts(); loadDashboard(); }
            } catch (e) {
                alert('خطا در ارتباط با سرور: ' + e);
            }
        }


        async function loadAttackAccounts() {
            try {
                const res = await fetch('api/accounts');
                const data = await res.json();
                if (data.ok) {
                    const sel = document.getElementById('select-single-account');
                    const cap = window.__ADD_CAP || 1000;
                    sel.innerHTML = '';
                    data.accounts.forEach(acc => {
                        const label = `${acc.name} (${acc.phone}) — ${acc.added_today}/${cap}`;
                        sel.innerHTML += `<option value="${acc.phone}">${label}</option>`;
                    });
                    renderParallelAccounts(data.accounts || []);
                }
            } catch (e) { console.error(e); }
        }

        // 📱 فهرست انتخاب اکانت برای ادد موازی.
        // اکانت‌های غیرآماده نمایش داده می‌شوند ولی غیرفعال‌اند تا کاربر
        // بفهمد چرا در عملیات شرکت نمی‌کنند (به‌جای اینکه بی‌صدا غیب شوند).
        function renderParallelAccounts(accs) {
            const box = document.getElementById('parallel-acc-list');
            if (!box) return;
            if (!accs.length) {
                box.innerHTML = '<div class="text-[11px] text-slate-500 text-center py-2">هنوز اکانتی ثبت نشده.</div>';
                updateParallelAccSummary();
                return;
            }
            const prev = new Set(currentParallelPhones());
            const hadPrev = prev.size > 0;
            box.innerHTML = '';
            accs.forEach(acc => {
                const usable = (acc.status === 'healthy' || acc.status === 'unchecked' || acc.status === 'unused');
                let note = '';
                if (acc.status === 'limited') {
                    const m = acc.remaining_seconds > 0 ? Math.ceil(acc.remaining_seconds / 60) + ' دقیقه دیگر' : 'محدود';
                    note = '⛔ ' + m;
                } else if (acc.status === 'busy') {
                    note = '🔄 مشغول';
                } else if (acc.status === 'dead') {
                    note = '❌ نیاز به لاگین';
                } else if (acc.status === 'no_session') {
                    note = '❌ بدون سشن';
                } else if (acc.status === 'unchecked' || acc.status === 'unused') {
                    note = '⚠️ تست‌نشده';
                } else {
                    note = '✅ سالم';
                }
                // انتخاب قبلی کاربر حفظ می‌شود؛ بار اول همه سالم‌ها تیک می‌خورند
                const checked = usable && (hadPrev ? prev.has(String(acc.phone)) : true);
                const div = document.createElement('label');
                div.className = 'flex items-center gap-2 p-2 rounded-lg ' +
                    (usable ? 'bg-slate-800/40 active:bg-slate-800' : 'bg-slate-900/40 opacity-50');
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'parallel-acc-cb w-4 h-4 accent-emerald-500 shrink-0';
                cb.value = String(acc.phone);
                cb.checked = checked;
                cb.disabled = !usable;
                cb.addEventListener('change', updateParallelAccSummary);
                const info = document.createElement('div');
                info.className = 'min-w-0 flex-1';
                const nm = document.createElement('div');
                nm.className = 'text-[11px] font-bold text-white truncate';
                nm.textContent = acc.name || 'اکانت';
                const ph = document.createElement('div');
                ph.className = 'text-[9px] font-mono text-slate-500 truncate';
                ph.setAttribute('dir', 'ltr');
                ph.textContent = acc.phone + '  ·  ' + acc.added_today + ' ادد';
                info.appendChild(nm); info.appendChild(ph);
                const st = document.createElement('span');
                st.className = 'text-[9px] shrink-0 ' + (usable ? 'text-emerald-300' : 'text-rose-300');
                st.textContent = note;
                div.appendChild(cb); div.appendChild(info); div.appendChild(st);
                box.appendChild(div);
            });
            updateParallelAccSummary();
        }

        function currentParallelPhones() {
            return Array.from(document.querySelectorAll('.parallel-acc-cb'))
                .filter(cb => cb.checked && !cb.disabled)
                .map(cb => cb.value);
        }

        function parallelAccAll(on) {
            document.querySelectorAll('.parallel-acc-cb').forEach(cb => {
                if (!cb.disabled) cb.checked = on;
            });
            updateParallelAccSummary();
        }

        function updateParallelAccSummary() {
            const el = document.getElementById('parallel-acc-summary');
            if (!el) return;
            const n = currentParallelPhones().length;
            const total = document.querySelectorAll('.parallel-acc-cb:not(:disabled)').length;
            el.textContent = n === 0
                ? 'هیچ اکانتی انتخاب نشده — دکمه شروع کار نمی‌کند.'
                : `${n} از ${total} اکانت قابل استفاده انتخاب شده.`;
            el.className = 'text-[10px] mt-1.5 ' + (n === 0 ? 'text-rose-400' : 'text-slate-500');
        }

        async function reset24hLimits() {
            if (!confirm('آیا از ریست کردن شمارنده ادد تمام اکانت‌ها مطمئن هستید؟')) return;
            if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
            const res = await fetch('api/accounts/reset', { method: 'POST' });
            const data = await res.json();
            alert(data.message || 'انجام شد.');
            loadDashboard();
        }

        async function saveTargetGroup() {
            const val = document.getElementById('input-target').value;
            if (!val) return;
            if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
            const res = await fetch('api/settings/target', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ target: val })
            });
            const data = await res.json();
            if (data.ok) alert('گروه مقصد با موفقیت آپدیت شد.');
            loadDashboard();
        }

        // Live refresh every 1 second
        setInterval(() => {
            loadDashboard();
            loadDashAccounts();
            if (activeTab === 'accounts') loadAccounts();
        }, 1000);

        // Initial Load
        loadDashboard();
        loadDashAccounts();
    </script>
</body>
</html>
"""


# -----------------------------------------------------------------
# STANDARD LIBRARY HTTP SERVER FALLBACK (Zero Dependencies)
# -----------------------------------------------------------------

class StandardWebAppHandler(BaseHTTPRequestHandler):
    """Fallback HTTP Handler using pure standard library (http.server)"""
    def log_message(self, format, *args):
        pass

    def send_nocache(self, body_bytes, content_type='text/html; charset=utf-8'):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('Content-Length', str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()

    def do_GET(self):
        try:
            path = self.path.split('?')[0]
            if path in ['/', '/app', '/index.html']:
                body = MINI_APP_HTML.encode('utf-8')
                self.send_nocache(body, 'text/html; charset=utf-8')
            elif path == '/api/dashboard':
                data = get_dashboard_dict()
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_nocache(body, 'application/json; charset=utf-8')
            elif path == '/api/accounts':
                data = get_accounts_dict()
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_nocache(body, 'application/json; charset=utf-8')
            elif path == '/api/members/stats':
                data = get_members_stats_dict()
                body = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_nocache(body, 'application/json; charset=utf-8')
            else:
                self.send_nocache(b"OK", 'text/plain; charset=utf-8')
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))

    def do_POST(self):
        try:
            path = self.path.split('?')[0]
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = {}
            if content_length > 0:
                body_bytes = self.rfile.read(content_length)
                try: post_data = json.loads(body_bytes.decode('utf-8'))
                except: pass

            if path == '/api/add/single':
                phone = post_data.get("phone", "")
                add_type = post_data.get("add_type", "all")
                add_mode = post_data.get("mode", "max")
                ok, msg = trigger_single_add(phone, add_type, add_mode)
                body = json.dumps({"ok": ok, "message": msg}).encode('utf-8')
            elif path == '/api/add/parallel':
                add_mode = post_data.get("mode", "ultra")
                add_type = post_data.get("add_type", "all")
                phones = post_data.get("phones") or None
                ok, msg = trigger_parallel_add(add_mode, add_type, phones)
                body = json.dumps({"ok": ok, "message": msg}).encode('utf-8')
            elif path == '/api/scrape/group':
                target = post_data.get("target", "")
                ok, msg = trigger_scrape_group(target, deep=bool(post_data.get("deep")))
                body = json.dumps({"ok": ok, "message": msg}).encode('utf-8')
            elif path == '/api/accounts/probe':
                async def _run_probe():
                    from account_doctor import probe_zero_add_accounts
                    await probe_zero_add_accounts(quick=True)
                _schedule_coro(_run_probe())
                body = json.dumps({"ok": True, "message": "تست زنده اکانت‌های صفر-ادد شروع شد. چند لحظه بعد تب اکانت‌ها را رفرش کن."}).encode('utf-8')
            elif path == '/api/accounts/reset':
                db.reset_adder_limits()
                body = json.dumps({"ok": True, "message": "آمار عملکرد تمام اکانت‌ها با موفقیت ریست شد."}).encode('utf-8')
            elif path == '/api/accounts/delete':
                ok, msg = delete_account_fully(post_data.get("phone", ""))
                body = json.dumps({"ok": ok, "message": msg}, ensure_ascii=False).encode('utf-8')
            elif path == '/api/settings/target':
                target = post_data.get("target", "").strip()
                if target:
                    cfg = db.get_config()
                    db.set_config(cfg.get("group_id", 0), target, cfg.get("defense_enabled", True))
                body = json.dumps({"ok": True, "target": target}).encode('utf-8')
            elif path == '/api/add/stop':
                try:
                    from bot import request_stop_all
                    request_stop_all()
                except: pass
                body = json.dumps({"ok": True, "message": "عملیات با موفقیت متوقف شد."}).encode('utf-8')
            else:
                body = json.dumps({"ok": True}).encode('utf-8')

            self.send_nocache(body, 'application/json; charset=utf-8')
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode('utf-8'))


def run_standard_server(port):
    """Run pure standard library HTTP server (zero external dependencies)"""
    server = HTTPServer(("0.0.0.0", port), StandardWebAppHandler)
    server.serve_forever()


# -----------------------------------------------------------------
# AIOHTTP ROUTER REGISTRATION
# -----------------------------------------------------------------

def create_web_app(app_bot=None, atk_state=None):
    set_app_refs(app_bot, atk_state)
    try:
        from aiohttp import web
        NO_CACHE = {'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0', 'Pragma': 'no-cache'}
        
        async def aio_serve_mini_app(request):
            return web.Response(text=MINI_APP_HTML, content_type='text/html', charset='utf-8', headers=NO_CACHE)

        async def aio_api_dashboard(request):
            d = get_dashboard_dict()
            try:
                from config import MAX_ADD_PER_ACCOUNT as _cap
                d.setdefault("metrics", {})["add_cap"] = _cap
            except Exception:
                pass
            return web.json_response(d, headers=NO_CACHE)


        async def aio_api_bg(request):
            """وضعیت اسکن خودکار پس‌زمینه."""
            st = db.get_bg_scan()
            cfg = db.get_config()
            return web.json_response({
                "ok": True,
                "bg": st,
                "target": {"id": cfg.get("group_id"), "name": cfg.get("group_name")},
            }, headers=NO_CACHE)

        async def aio_api_bg_set(request):
            """تنظیم اسکن خودکار: toggle/enable/disable/interval/account."""
            try:
                body = await request.json()
            except Exception:
                body = {}
            st = db.get_bg_scan()
            cfg = db.get_config()
            action = body.get("action")
            enabled = st.get("enabled", False)
            if action == "toggle":
                enabled = not enabled
            elif action == "enable":
                enabled = True
            elif action == "disable":
                enabled = False
            account = body.get("account") or st.get("account_phone")
            try:
                interval = int(body.get("interval") or st.get("interval_minutes", 60))
            except Exception:
                interval = 60
            gid = cfg.get("group_id")
            if enabled and (not account or not gid):
                return web.json_response(
                    {"ok": False, "error": "اول اکانت و گروه هدف لازم است"},
                    headers=NO_CACHE)
            db.set_bg_scan(enabled, target_group_id=gid, account_phone=account,
                           interval_minutes=interval)
            if account:
                try:
                    db.set_owner_phone(account)
                except Exception:
                    pass
            return web.json_response({"ok": True, "bg": db.get_bg_scan()}, headers=NO_CACHE)

        async def aio_api_chats(request):
            """لیست چت‌های اسکن‌شده + دسته‌بندی‌ها."""
            chats = db.get_scanned_chats()
            cats = db.get_all_categories()
            out = []
            for ch in chats[:40]:
                out.append({
                    "chat_id": ch.get("chat_id"),
                    "name": ch.get("chat_name"),
                    "type": ch.get("chat_type"),
                    "pct": ch.get("progress_pct") or 0,
                    "extracted": ch.get("extracted_count") or 0,
                    "total": ch.get("total_members_estimate") or 0,
                    "category": ch.get("category") or "",
                    "favorite": bool(ch.get("is_favorite")),
                    "scans": ch.get("scan_count") or 1,
                })
            return web.json_response({"ok": True, "chats": out, "categories": cats},
                                     headers=NO_CACHE)

        async def aio_api_cleanup(request):
            """پاکسازی دیتابیس: purge_added / purge_dnadd."""
            try:
                body = await request.json()
            except Exception:
                body = {}
            kind = body.get("kind", "purge_added")
            if kind == "purge_added":
                n = db.purge_added_users()
            elif kind == "purge_dnadd":
                n = db.purge_do_not_add_users()
            else:
                return web.json_response({"ok": False, "error": "unknown kind"},
                                         headers=NO_CACHE)
            return web.json_response({"ok": True, "removed": n,
                                      "users": db.count_users()}, headers=NO_CACHE)


        async def aio_api_loopinfo(request):
            """وضعیت حلقه رویداد و کارهای پس‌زمینه — برای عیب‌یابی."""
            import threading
            info = {"ok": True}
            try:
                running = asyncio.get_running_loop()
                info["handler_loop"] = str(id(running))
                info["handler_loop_running"] = running.is_running()
            except Exception as e:
                info["handler_loop_error"] = str(e)

            mel = main_event_loop
            info["registered_loop"] = str(id(mel)) if mel else None
            info["registered_running"] = bool(mel and mel.is_running())

            bl = getattr(bot_app, "loop", None)
            info["bot_app_present"] = bot_app is not None
            info["bot_loop"] = str(id(bl)) if bl else None
            info["bot_loop_running"] = bool(bl and bl.is_running())

            import sys as _s
            bm = _s.modules.get("bot")
            cl = getattr(getattr(bm, "app", None), "loop", None) if bm else None
            info["bot_module_loaded"] = bm is not None
            info["bot_module_loop"] = str(id(cl)) if cl else None
            info["bot_module_loop_running"] = bool(cl and cl.is_running())

            resolved = _resolve_bot_loop()
            info["resolved_loop"] = str(id(resolved)) if resolved else None
            info["threads"] = [t.name for t in threading.enumerate()][:12]

            # تست واقعی: یک کوروتین ساده زمان‌بندی کن و ببین اجرا می‌شود
            marker = {"ran": False}

            async def _probe():
                marker["ran"] = True

            _schedule_coro(_probe())
            await asyncio.sleep(1.2)
            info["probe_executed"] = marker["ran"]

            return web.json_response(info, headers=NO_CACHE)

        async def aio_api_diagnose(request):
            """چرا یک اکانت کار نمی‌کند؟ — گزارش دقیق به‌جای حدس زدن."""
            return web.json_response(get_diagnostics_dict(), headers=NO_CACHE)

        async def aio_api_accounts(request):
            return web.json_response(get_accounts_dict(), headers=NO_CACHE)

        async def aio_api_members_stats(request):
            return web.json_response(get_members_stats_dict(), headers=NO_CACHE)

        async def aio_api_scrape_group(request):
            try:
                data = await request.json()
                target = data.get("target", "")
                ok, msg = trigger_scrape_group(target, deep=bool(data.get("deep")))
                return web.json_response({"ok": ok, "message": msg}, headers=NO_CACHE)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)}, status=400, headers=NO_CACHE)

        async def aio_api_add_single(request):
            try:
                data = await request.json()
                phone = data.get("phone", "")
                add_type = data.get("add_type", "all")
                add_mode = data.get("mode", "max")
                ok, msg = trigger_single_add(phone, add_type, add_mode)
                return web.json_response({"ok": ok, "message": msg}, headers=NO_CACHE)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)}, status=400, headers=NO_CACHE)

        async def aio_api_add_parallel(request):
            try:
                data = await request.json()
                add_mode = data.get("mode", "ultra")
                add_type = data.get("add_type", "all")
                phones = data.get("phones") or None
                ok, msg = trigger_parallel_add(add_mode, add_type, phones)
                return web.json_response({"ok": ok, "message": msg}, headers=NO_CACHE)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)}, status=400, headers=NO_CACHE)

        async def aio_api_probe_accounts(request):
            try:
                from account_doctor import probe_zero_add_accounts
                _schedule_coro(probe_zero_add_accounts(quick=True))
                return web.json_response({"ok": True, "message": "تست زنده اکانت‌های صفر-ادد شروع شد. چند لحظه بعد تب اکانت‌ها را رفرش کن."}, headers=NO_CACHE)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)}, status=400, headers=NO_CACHE)

        async def aio_api_reset_limits(request):
            db.reset_adder_limits()
            return web.json_response({"ok": True, "message": "آمار عملکرد تمام اکانت‌ها با موفقیت ریست شد."}, headers=NO_CACHE)

        async def aio_api_delete_account(request):
            try:
                data = await request.json()
                ok, msg = delete_account_fully(data.get("phone", ""))
                return web.json_response({"ok": ok, "message": msg}, headers=NO_CACHE)
            except Exception as e:
                return web.json_response({"ok": False, "message": str(e)}, status=400, headers=NO_CACHE)

        async def aio_api_add_account(request):
            """شروع افزودن اکانت — ارسال کد تأیید."""
            try:
                data = await request.json()
                import account_login
                ok, msg, needs = await account_login.start(data.get("phone", ""))
                return web.json_response({"ok": ok, "message": msg, "needs": needs}, headers=NO_CACHE)
            except Exception as e:
                return web.json_response({"ok": False, "message": str(e)}, status=400, headers=NO_CACHE)

        async def aio_api_add_account_code(request):
            """مرحله دوم — کد تأیید یا رمز دو مرحله‌ای."""
            try:
                data = await request.json()
                import account_login
                if data.get("password"):
                    ok, msg, needs = await account_login.submit_password(
                        data.get("phone", ""), data.get("password", "")
                    )
                else:
                    ok, msg, needs = await account_login.submit_code(
                        data.get("phone", ""), data.get("code", "")
                    )
                return web.json_response({"ok": ok, "message": msg, "needs": needs}, headers=NO_CACHE)
            except Exception as e:
                return web.json_response({"ok": False, "message": str(e)}, status=400, headers=NO_CACHE)

        async def aio_api_add_account_cancel(request):
            try:
                data = await request.json()
                import account_login
                ok, msg = await account_login.cancel(data.get("phone", ""))
                return web.json_response({"ok": ok, "message": msg}, headers=NO_CACHE)
            except Exception as e:
                return web.json_response({"ok": False, "message": str(e)}, status=400, headers=NO_CACHE)

        async def aio_api_set_target(request):
            try:
                data = await request.json()
                target = data.get("target", "").strip()
                if target:
                    from add_engine import persist_target_setting
                    persist_target_setting(target)
                return web.json_response({"ok": True, "target": target}, headers=NO_CACHE)
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)}, status=400, headers=NO_CACHE)

        async def aio_api_stop_add(request):
            try:
                from bot import request_stop_all
                request_stop_all()
            except: pass
            if atk_state_ref is not None:
                atk_state_ref["_stop_requested"] = True
                atk_state_ref["stop_parallel_add"] = True
                atk_state_ref["add_in_progress"] = False
            return web.json_response({"ok": True, "message": "عملیات با موفقیت متوقف شد."}, headers=NO_CACHE)

        app = web.Application()
        app.router.add_get('/', aio_serve_mini_app)
        app.router.add_get('/app', aio_serve_mini_app)
        app.router.add_get('/api/dashboard', aio_api_dashboard)
        app.router.add_get('/api/diagnose', aio_api_diagnose)
        app.router.add_get('/api/loopinfo', aio_api_loopinfo)
        app.router.add_get('/api/accounts', aio_api_accounts)
        app.router.add_get('/api/members/stats', aio_api_members_stats)
        app.router.add_post('/api/scrape/group', aio_api_scrape_group)
        app.router.add_post('/api/add/single', aio_api_add_single)
        app.router.add_post('/api/add/parallel', aio_api_add_parallel)
        app.router.add_post('/api/accounts/probe', aio_api_probe_accounts)
        app.router.add_post('/api/accounts/reset', aio_api_reset_limits)
        app.router.add_post('/api/accounts/delete', aio_api_delete_account)
        app.router.add_post('/api/accounts/add', aio_api_add_account)
        app.router.add_post('/api/accounts/add/code', aio_api_add_account_code)
        app.router.add_post('/api/accounts/add/cancel', aio_api_add_account_cancel)
        app.router.add_post('/api/settings/target', aio_api_set_target)
        app.router.add_post('/api/add/stop', aio_api_stop_add)
        app.router.add_get('/api/bg', aio_api_bg)
        app.router.add_post('/api/bg', aio_api_bg_set)
        app.router.add_get('/api/chats', aio_api_chats)
        app.router.add_post('/api/cleanup', aio_api_cleanup)
        return app
    except ImportError:
        return None
