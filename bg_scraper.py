"""
Background auto-scraper
======================================================
- طبق زمانی که کاربر تعیین میکنه (مثلا هر ۶۰ دقیقه) به صورت خودکار
  به اکانت ذخیره شده وصل میشه و اعضای گروه هدف رو استخراج میکنه.
- همه نتایج مستقیم به دیتابیس Neon ذخیره میشن.
- سشن‌ها فایل‌شان در دیسک موقت رندر هست + در DB هم بکاپ گرفته میشن.
- از همان فینگرپرینت ثابت قبلی استفاده میکنه که سشن منقضی نشه.
- از خروج از اکانت بعد از هر اسکن خودداری میکنه (تا حد امکان اتصال زنده نگه داشته میشه).
"""
import asyncio
import os
import time
import random

from pyrogram.errors import AuthKeyDuplicated, AuthKeyUnregistered, FloodWait

from db import (
    get_bg_scan, set_bg_status, mark_bg_run, load_accounts,
    get_config, load_session_blob, save_session_blob,
    bulk_save_users, count_users
)
from attacker import AdvancedScraper, SESSIONS_DIR, DEVICE_FP, _enable_wal_on_session

# ⚙️ استفاده از پیکربندی مرکزی (همان اعتبارنامه‌های bot.py — از ناسازگاری سشن‌ها جلوگیری می‌کند)
from config import API_ID, API_HASH
import account_state


_loop = None
_task = None

async def _ensure_session(phone):
    """Make sure the .session file exists for phone; restore from DB backup if missing."""
    from attacker import safe_phone_filename as sfn
    fname = sfn(phone)
    path = os.path.join(SESSIONS_DIR, f"acc_{fname}.session")
    if os.path.exists(path) and os.path.getsize(path) > 100:
        # فعال کردن WAL روی سشن موجود
        base = path[:-8]  # acc_98912xxx (بدون .session)
        _enable_wal_on_session(base)
        return path
    # restore from DB
    blob = load_session_blob(phone)
    if blob:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(blob)
        # روی سشن restore شده هم WAL فعال کن
        base = path[:-8]
        _enable_wal_on_session(base)
        return path
    return None


def _backup_session(phone):
    from attacker import safe_phone_filename as sfn
    fname = sfn(phone)
    path = os.path.join(SESSIONS_DIR, f"acc_{fname}.session")
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                blob = f.read()
            save_session_blob(phone, blob)
    except Exception as e:
        print(f"session backup err: {e}", flush=True)


async def run_one_scan(phone, group_id, group_name, app_bot=None, admin_id=None):
    """Perform one background scrape using the given phone account."""
    set_bg_status(f"scanning:{phone}")
    # Load device fingerprint for the account
    accs = load_accounts()
    fp = accs.get(phone, {}).get("device_fp") or random.choice(DEVICE_FP)
    path = await _ensure_session(phone)
    if not path:
        set_bg_status("no_session")
        return 0, "no_session_file"

    # Pass phone=phone to use proper permanent session path
    sc = AdvancedScraper("", API_ID, API_HASH, phone=phone, device_fp=fp)
    try:
        # WAL mode قبل از connect
        _enable_wal_on_session(sc.app.name)
        await sc.connect()
        _enable_wal_on_session(sc.app.name)
        await sc.app.get_me()
    except (AuthKeyDuplicated, AuthKeyUnregistered, ConnectionError) as e:
        set_bg_status(f"auth_error:{str(e)[:50]}")
        try: await sc.disconnect()
        except: pass
        return 0, f"auth_error: {e}"
    except Exception as e:
        set_bg_status(f"connect_err:{str(e)[:50]}")
        try: await sc.disconnect()
        except: pass
        return 0, f"connect: {e}"

    # تشخیص نوع چت (کانال یا گروه)
    is_channel = False
    target_chat = None
    try:
        target_chat = await sc.app.get_chat(group_id)
        is_channel = str(target_chat.type).lower() == "chattype.channel"
    except:
        pass

    chat_type_str = "کانال" if is_channel else "گروه"
    # Warm up caches (two passes)
    status_msg = None
    try:
        try:
            async for _ in sc.app.get_dialogs(limit=2000):
                pass
            await asyncio.sleep(3)
            async for _ in sc.app.get_dialogs(limit=2000):
                pass
        except:
            pass
        if app_bot and admin_id:
            try:
                status_msg = await app_bot.send_message(admin_id,
                    f"🔄 <b>اسکن خودکار پس‌زمینه شروع شد</b>\n"
                    f"👤 اکانت: <code>{phone}</code>\n"
                    f"📡 نوع: {chat_type_str}\n"
                    f"👥 هدف: {group_name}")
            except: status_msg = None

        users_found = {}
        async def on_progress(text):
            if status_msg:
                try:
                    await status_msg.edit_text(text)
                except:
                    pass

        sc._progress_cb = on_progress

        # Run multiple strategies in sequence (same as manual but quieter)
        async def add_user(u):
            try:
                uid = u.id
            except: return
            if getattr(u, "is_bot", False) or getattr(u, "deleted", False): return
            info = {
                "user_id": uid,
                "username": getattr(u, "username", None) or "",
                "first_name": (getattr(u, "first_name", "") or "").replace("\t"," ").strip(),
                "last_name": (getattr(u, "last_name", "") or "").replace("\t"," ").strip(),
                "phone": getattr(u, "phone_number", None) or "",
            }
            users_found[uid] = info
            sc._last_added_name = (info["first_name"] + " " + info["last_name"]).strip() or info.get("username") or str(uid)
            sc.total_api_calls += 1

        # Helper to extract reactors from a message
        async def extract_reactors(msg, source_label):
            count = 0
            if not msg.reactions or not msg.reactions.reactions:
                return 0
            for react in msg.reactions.reactions:
                emoji = getattr(react, 'emoji', '👍')
                count_hint = getattr(react, 'count', 0) or 0
                batch_limit = min(100, max(30, count_hint))
                offset = 0
                while True:
                    try:
                        reactors = await sc.app.get_message_reactions(
                            group_id, msg.id, emoji, limit=batch_limit, offset=offset
                        )
                        if not reactors:
                            break
                        for r in reactors:
                            if r and getattr(r, 'peer', None) and getattr(r.peer, 'user_id', None):
                                try:
                                    u = await sc.app.get_users(r.peer.user_id)
                                    await add_user(u)
                                    count += 1
                                except:
                                    uid = r.peer.user_id
                                    if uid not in users_found:
                                        users_found[uid] = {
                                            "user_id": uid, "username": "", "first_name": str(uid),
                                            "last_name": "", "phone": ""
                                        }
                                        count += 1
                        if len(reactors) < batch_limit:
                            break
                        offset += batch_limit
                        await sc.human_sleep(0.3, 0.6)
                    except FloodWait as e:
                        await sc.handle_flood(e)
                    except:
                        break
            return count

        if is_channel:
            # ═══════ استراتژی اسکن کانال (پس‌زمینه) ═══════
            sc._stage = "اسکن پست‌های کانال"
            post_count = 0; author_count = 0; reactor_count = 0
            try:
                async for msg in sc.app.get_chat_history(group_id, limit=5000):
                    post_count += 1
                    if msg.from_user:
                        await add_user(msg.from_user); author_count += 1
                    if msg.forward_from:
                        await add_user(msg.forward_from)
                    if msg.entities:
                        for ent in msg.entities:
                            if ent.type in ("mention", "text_mention") and ent.user:
                                await add_user(ent.user)
                    reactor_count += await extract_reactors(msg, "react")
                    if post_count % 150 == 0:
                        sc._stage = f"کانال: {post_count} پست | {author_count} نویسنده | {reactor_count} ری‌اکت‌دهنده"
                        await sc._progress()
                    await sc.human_sleep(0.04, 0.12)
            except: pass

            # تلاش برای get_chat_members (فقط برای ادمین جواب میده)
            try:
                sc._stage = "تلاش لیست اعضای کانال"
                async for m in sc.app.get_chat_members(group_id, limit=5000):
                    u = m.user if hasattr(m, "user") else m
                    await add_user(u)
            except: pass

        else:
            # ═══════ استراتژی اسکن گروه (پس‌زمینه) ═══════
            # Strategy 1: alphabetical prefixes
            sc._stage = "جستجوی الفبایی"
            prefixes = list("اآبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیabcdefghijklmnopqrstuvwxyz")
            random.shuffle(prefixes)
            for p in prefixes[:25]:
                try:
                    async for m in sc.app.get_chat_members(group_id, query=p, limit=200):
                        u = m.user if hasattr(m, "user") else m
                        await add_user(u)
                    await sc.human_sleep(0.8, 1.8)
                except FloodWait as e:
                    await sc.handle_flood(e)
                except:
                    await asyncio.sleep(1)
                if len(users_found) % 300 == 0:
                    await sc._progress(force=True)

            # Strategy 2: message history
            sc._stage = "اسکن تاریخچه پیام"
            try:
                offset = 0; scanned = 0
                while scanned < 5000:
                    cnt = 0
                    async for msg in sc.app.get_chat_history(group_id, limit=200, offset=offset):
                        scanned += 1; cnt += 1
                        if msg.from_user: await add_user(msg.from_user)
                        if msg.forward_from: await add_user(msg.forward_from)
                        if msg.reply_to_message and msg.reply_to_message.from_user:
                            await add_user(msg.reply_to_message.from_user)
                        if msg.entities:
                            for ent in msg.entities:
                                if ent.user: await add_user(ent.user)
                    if cnt == 0: break
                    offset += cnt
                    await sc.human_sleep(1.0, 2.0)
                    await sc._progress()
            except:
                pass

            # Strategy 3: new-join service messages
            sc._stage = "اعضای تازه‌وارد"
            try:
                async for msg in sc.app.get_chat_history(group_id, limit=2000):
                    if msg.new_chat_members:
                        for u in msg.new_chat_members: await add_user(u)
            except:
                pass

            # 🆕 Strategy 4: reaction scanning (groups)
            sc._stage = "اسکن ری‌اکشن‌ها"
            reactor_count = 0; msg_count = 0
            try:
                async for msg in sc.app.get_chat_history(group_id, limit=3000):
                    msg_count += 1
                    reactor_count += await extract_reactors(msg, "react")
                    if msg_count % 200 == 0:
                        sc._stage = f"اسکن ری‌اکشن: {msg_count} پیام | {reactor_count} کاربر"
                        await sc._progress()
            except: pass

        # Persist all to DB
        before_count = count_users()
        bulk_save_users(list(users_found.values()), group_id, group_name)
        after_count = count_users()
        new_added = after_count - before_count

        # Auto-analyze chat topic for background scans too
        try:
            from chat_analyzer import smart_analyze
            desc = getattr(target_chat, 'description', '') or '' if target_chat else ''
            analysis = smart_analyze(group_name, desc)
            if analysis.get("category"):
                from db import update_chat_category as _ucc
                _ucc(group_id, analysis["category"])
        except: pass

        # Backup session file to DB
        try: await sc.disconnect()
        except: pass
        _backup_session(phone)

        # Final status
        set_bg_status("idle")
        mark_bg_run(new_added)
        if status_msg:
            try:
                await status_msg.edit_text(
                    f"✅ <b>اسکن خودکار تمام شد</b>\n\n"
                    f"👥 در این نوبت پیدا شد: <b>{len(users_found):,}</b>\n"
                    f"🆕 جدیدا به دیتابیس اضافه شد: <b>{new_added:,}</b>\n"
                    f"📦 مجموع کل در دیتابیس: <b>{after_count:,}</b>")
            except: pass
        return len(users_found), "ok"
    except Exception as e:
        set_bg_status(f"error:{str(e)[:80]}")
        if status_msg:
            try: await status_msg.edit_text(f"❌ خطا در اسکن خودکار: {str(e)[:200]}")
            except: pass
        try: await sc.disconnect()
        except: pass
        return 0, str(e)


async def bg_loop(app_bot, admin_id):
    """Main background loop: check bg_scan_state every 60s, run scan when interval elapsed."""
    print("[bg_scraper] background loop started", flush=True)
    try:
        from account_doctor import probe_zero_add_accounts
        print("[bg_scraper] live-probing zero-add accounts...", flush=True)
        probed = await probe_zero_add_accounts(quick=True)
        print(f"[bg_scraper] probe done: {len(probed)} accounts", flush=True)
        for r in probed:
            print(f"  probe {r.get('phone')}: ok={r.get('ok')} err={str(r.get('error') or '')[:80]}", flush=True)
    except Exception as e:
        print(f"[bg_scraper] probe err: {e}", flush=True)
    while True:
        try:
            st = get_bg_scan()
            if st.get("enabled"):
                now = int(time.time())
                last = st.get("last_run") or 0
                interval = max(15, int(st.get("interval_minutes",60))) * 60
                if now - last >= interval:
                    cfg = get_config()
                    gid = st.get("target_group_id") or cfg.get("group_id", 0)
                    gname = cfg.get("group_name", "گروه هدف")
                    preferred = st.get("account_phone")
                    if gid:
                        from account_doctor import pick_scrape_account
                        phone, _info, skipped = pick_scrape_account(preferred=preferred)
                        if not phone:
                            set_bg_status("no_usable_account")
                            print(f"[bg_scraper] no usable account (skipped={skipped})", flush=True)
                            await asyncio.sleep(120)
                            continue
                        ok_b, owner = account_state.mark_busy(phone, "اسکن خودکار")
                        if not ok_b:
                            set_bg_status(f"busy:{owner}")
                            print(f"[bg_scraper] {phone} busy ({owner}), skip", flush=True)
                            continue
                        try:
                            set_bg_status("preparing")
                            print(f"[bg_scraper] using {phone} (preferred={preferred}, skipped={skipped})", flush=True)
                            count, status = await run_one_scan(phone, gid, gname, app_bot, admin_id)
                            print(f"[bg_scraper] run complete: {count} users, status={status}", flush=True)
                            if status == "ok":
                                account_state.set_last_error(phone, "")
                            else:
                                account_state.set_last_error(phone, status)
                            account_state.mark_used(phone)
                        finally:
                            account_state.release(phone)
                    else:
                        set_bg_status("no_target")
                        print("[bg_scraper] no target/account set, skipping", flush=True)
                        await asyncio.sleep(120)
        except Exception as e:
            print(f"[bg_scraper] loop err: {e}", flush=True)
        await asyncio.sleep(60)


def start_in_background(app_bot, admin_id):
    """Start bg loop in asyncio."""
    global _task
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            _task = asyncio.create_task(bg_loop(app_bot, admin_id))
            return _task
    except:
        pass
    _task = asyncio.ensure_future(bg_loop(app_bot, admin_id))
    return _task
