"""
Parallel multi-account scraper/adder engine.
اجرا همزمان اسکرپ یا اضافه کردن با چند اکانت ذخیره شده
"""

import asyncio
import time
import random
import os
import json
import io
import csv
from pyrogram.errors import (
    FloodWait, UserPrivacyRestricted, UserNotMutualContact,
    UsersTooMuch, UserBannedInChannel, ChatAdminRequired,
    UserAlreadyParticipant, AuthKeyDuplicated, AuthKeyUnregistered,
    PeerIdInvalid, BadRequest
)

from attacker import AdvancedScraper, safe_phone_filename, SESSIONS_DIR, DEVICE_FP, _enable_wal_on_session, _get_session_lock

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")

# shared dash state
dash = {
    "running": False,
    "mode": None,  # "scrape" or "add"
    "started_at": 0,
    "workers": {},   # phone -> state dict
    "global": {
        "total_found": 0,
        "total_added": 0,
        "total_errors": 0,
        "total_skipped": 0,
    },
    "chat_id": None,
    "chat_title": "",
    "log": [],
}

def reset_dash():
    dash["running"] = False
    dash["mode"] = None
    dash["started_at"] = 0
    dash["workers"] = {}
    dash["global"] = {"total_found":0,"total_added":0,"total_errors":0,"total_skipped":0}
    dash["chat_id"] = None
    dash["chat_title"] = ""
    dash["log"] = []

def log(line):
    dash["log"].append(f"[{int(time.time())}] {line}")
    if len(dash["log"]) > 80:
        dash["log"] = dash["log"][-80:]


# ---------- Build scrapers from saved accounts ----------
def make_scraper_for_phone(phone):
    acc_path = os.path.join("saved_accounts.json")
    fp = None
    name = phone
    try:
        with open(acc_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if phone in data:
            fp = data[phone].get("device_fp")
            name = data[phone].get("name") or phone
    except:
        pass
    if not fp:
        fp = random.choice(DEVICE_FP)
    # Pass phone=phone so AdvancedScraper resolves the permanent session path correctly
    sc = AdvancedScraper("", API_ID, API_HASH, phone=phone, in_memory=False, device_fp=fp)
    if not os.path.exists(sc.app.name + ".session"):
        return None, name
    # WAL mode on existing session
    _enable_wal_on_session(sc.app.name)
    sc.phone = phone
    return sc, name


# ---------- Parallel scrape ----------
async def parallel_scrape(chat_id, phones, progress_cb=None, users_store=None, users_lock=None):
    """
    Run multiple scrapers in parallel on same chat, all adding found users
    into users_store dict (keyed by user_id) under lock.
    Each worker runs a different phase so they don't hammer same endpoint.
    """
    dash["running"] = True
    dash["mode"] = "scrape"
    dash["started_at"] = time.time()
    dash["chat_id"] = chat_id

    # Divide strategies across workers
    strategies = ["alphabet", "history", "new_members", "groups_in_common", "recent_active"]
    workers = []

    async def run_worker(phone, strategy_list):
        sc, name = make_scraper_for_phone(phone)
        if not sc:
            dash["workers"][phone] = {"state": "error", "error": "فایل سشن پیدا نشد", "found": 0, "name": phone}
            log(f"❌ {phone}: سشن یافت نشد")
            return
        dash["workers"][phone] = {"state": "connecting", "found": 0, "name": name, "stage": "در حال اتصال...", "speed":0, "elapsed":0}
        log(f"🔌 {name} در حال اتصال (استراتژی: {strategy_list[0]})...")
        try:
            # WAL mode قبل از connect
            _enable_wal_on_session(sc.app.name)
            await sc.connect()  # حالا خودش لاک سراسری + per-session داره
            _enable_wal_on_session(sc.app.name)
            dash["workers"][phone]["state"] = "warming"
            # warm up caches
            try:
                async for _ in sc.app.get_dialogs(limit=500):
                    pass
                await asyncio.sleep(3)
                async for _ in sc.app.get_dialogs(limit=2000):
                    pass
            except:
                pass

            dash["workers"][phone]["state"] = "running"
            start_t = time.time()
            local_found = 0

            async def prog_cb(text):
                dash["workers"][phone]["stage"] = text
                if progress_cb:
                    try:
                        await progress_cb(render_dashboard())
                    except:
                        pass

            sc._progress_cb = prog_cb
            # reimplement mini scrape with strategy split
            for strat in strategy_list:
                dash["workers"][phone]["stage"] = f"استراتژی {strat}"
                count_before = len(sc.found_users)
                try:
                    await _run_strategy(sc, chat_id, strat, users_store, users_lock)
                except Exception as e:
                    log(f"⚠️ {name} در استراتژی {strat} ارور: {str(e)[:80]}")
                local_found = len(sc.found_users)
                dash["workers"][phone]["found"] = local_found
                elapsed = int(time.time()-start_t)
                speed = int(local_found/(elapsed/60)) if elapsed > 10 else 0
                dash["workers"][phone]["speed"] = speed
                dash["workers"][phone]["elapsed"] = elapsed
                if progress_cb:
                    try:
                        await progress_cb(render_dashboard())
                    except:
                        pass
                await asyncio.sleep(2)

            dash["workers"][phone]["state"] = "done"
            dash["workers"][phone]["stage"] = "✅ تمام شد"
            log(f"✅ {name} تمام شد - {local_found} نفر")
        except (AuthKeyDuplicated, AuthKeyUnregistered) as e:
            dash["workers"][phone]["state"] = "auth_error"
            dash["workers"][phone]["error"] = f"سشن منقضی: {e}"
            log(f"❌ {name} سشن منقضی شد")
        except FloodWait as e:
            dash["workers"][phone]["state"] = "flood"
            dash["workers"][phone]["error"] = f"فلود {e.value}s"
            log(f"⏱️ {name} فلود {e.value} ثانیه")
        except Exception as e:
            dash["workers"][phone]["state"] = "error"
            dash["workers"][phone]["error"] = str(e)[:120]
            log(f"❌ {name} ارور: {str(e)[:100]}")
        finally:
            try:
                await sc.disconnect()
            except:
                pass
            # merge totals
            if users_store is not None:
                total = len(users_store)
                dash["global"]["total_found"] = total
            if progress_cb:
                try:
                    await progress_cb(render_dashboard())
                except:
                    pass

    # distribute strategies round-robin
    for i, phone in enumerate(phones):
        my_strats = []
        for j, s in enumerate(strategies):
            if j % max(1,len(phones)) == i % max(1,len(phones)):
                my_strats.append(s)
        if not my_strats:
            my_strats = [strategies[i % len(strategies)]]
        workers.append(run_worker(phone, my_strats))

    await asyncio.gather(*workers, return_exceptions=True)
    dash["running"] = False
    if users_store is not None:
        dash["global"]["total_found"] = len(users_store)
    if progress_cb:
        try:
            await progress_cb(render_dashboard(final=True))
        except:
            pass


async def _run_strategy(sc, chat_id, strat, users_store, users_lock):
    """Run one strategy for a scraper; pushes users into users_store."""
    # Helper to add a user
    async def add_user(u):
        try:
            uid = u.id
        except:
            return
        if getattr(u, "is_bot", False) or getattr(u, "deleted", False):
            return
        # build user info
        info = {
            "user_id": uid,
            "username": getattr(u, "username", None) or "",
            "first_name": (getattr(u, "first_name", "") or "").replace("\t"," ").strip(),
            "last_name": (getattr(u, "last_name", "") or "").replace("\t"," ").strip(),
            "phone": getattr(u, "phone_number", None) or "",
        }
        sc.found_users[uid] = info
        sc._last_added_name = (info["first_name"] + " " + info["last_name"]).strip() or info.get("username") or str(uid)
        sc.total_api_calls += 1
        if users_store is not None and users_lock is not None:
            async with users_lock:
                if uid not in users_store:
                    users_store[uid] = info
        await sc.human_sleep(0.05, 0.2)

    if strat == "alphabet":
        # search by Persian/Arabic/English alphabet prefixes
        prefixes = list("اآبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیabcdefghijklmnopqrstuvwxyz")
        random.shuffle(prefixes)
        for p in prefixes[:25]:
            if self._stop_requested:
                return
            try:
                async for u in sc.app.get_chat_members(chat_id, query=p, limit=200):
                    await add_user(u.user if hasattr(u, "user") else u)
                await sc._progress(f"جستجوی الفبا حرف '{p}' - {len(sc.found_users):,} نفر")
                await sc.human_sleep(0.8, 1.8)
            except FloodWait as e:
                await sc.handle_flood(e)
            except Exception:
                await asyncio.sleep(1)

    elif strat == "history":
        # scan recent messages history for senders
        offset = 0
        scanned = 0
        max_msgs = 8000
        while scanned < max_msgs:
            try:
                count = 0
                async for msg in sc.app.get_chat_history(chat_id, limit=200, offset=offset):
                    scanned += 1
                    count += 1
                    if msg.from_user:
                        await add_user(msg.from_user)
                    if msg.forward_from:
                        await add_user(msg.forward_from)
                    if msg.reply_to_message and msg.reply_to_message.from_user:
                        await add_user(msg.reply_to_message.from_user)
                    # mentions via entities
                    if msg.entities:
                        for ent in msg.entities:
                            if ent.user:
                                await add_user(ent.user)
                    if scanned % 400 == 0:
                        await sc._progress(f"اسکن تاریخچه پیام {scanned:,}/{max_msgs} - {len(sc.found_users):,}")
                if count == 0:
                    break
                offset += count
                await sc.human_sleep(1.0, 2.0)
            except FloodWait as e:
                await sc.handle_flood(e)
            except Exception:
                await asyncio.sleep(2)
                break

    elif strat == "new_members":
        # new joined members via recent service messages
        try:
            cnt = 0
            async for msg in sc.app.get_chat_history(chat_id, limit=3000):
                cnt += 1
                if msg.new_chat_members:
                    for u in msg.new_chat_members:
                        await add_user(u)
                if cnt % 500 == 0:
                    await sc._progress(f"اعضای جدید {cnt} پیام - {len(sc.found_users):,}")
                await asyncio.sleep(0.02)
        except:
            pass

    elif strat == "groups_in_common":
        # iterate discovered users and get their common groups (limited)
        try:
            uids = list(sc.found_users.keys())
            random.shuffle(uids)
            done = 0
            for uid in uids[:150]:
                if self._stop_requested:
                    return
                try:
                    async for g in sc.app.get_common_chats(uid):
                        try:
                            async for m in sc.app.get_chat_members(g.id, limit=50):
                                await add_user(m.user if hasattr(m,"user") else m)
                        except:
                            pass
                        await asyncio.sleep(0.5)
                    done += 1
                    if done % 10 == 0:
                        await sc._progress(f"گروه‌های مشترک {done} کاربر - {len(sc.found_users):,}")
                except:
                    await asyncio.sleep(1)
        except:
            pass

    elif strat == "recent_active":
        # iterate through members directly with offset (may fail on big groups but still adds)
        try:
            async for m in sc.app.get_chat_members(chat_id, limit=5000):
                await add_user(m.user if hasattr(m,"user") else m)
                if len(sc.found_users) % 200 == 0:
                    await sc._progress(f"لیست مستقیم اعضا - {len(sc.found_users):,}")
        except:
            pass


# ---------- Parallel add ----------
async def parallel_add(chat_id, user_ids, phones, adder_limits_load, save_adder_limits_fn,
                       add_history_check, mark_added, max_per_account=50, progress_cb=None):
    dash["running"] = True
    dash["mode"] = "add"
    dash["started_at"] = time.time()
    dash["chat_id"] = chat_id

    # Shuffle user ids and split across phones round-robin
    queue = asyncio.Queue()
    for uid in user_ids:
        if not add_history_check(chat_id, uid):
            await queue.put(uid)
        else:
            dash["global"]["total_skipped"] += 1
    total_in_queue = queue.qsize()

    sem = asyncio.Semaphore(2)  # limit parallel adds to avoid global flood

    async def add_worker(phone):
        sc, name = make_scraper_for_phone(phone)
        if not sc:
            dash["workers"][phone] = {"state":"error","error":"سشن یافت نشد","added":0,"errors":0,"name":phone}
            return
        limits = adder_limits_load()
        already = limits.get(phone,{}).get("added",0)
        remaining = max(0, max_per_account - already)
        added = 0
        errors = 0
        dash["workers"][phone] = {"state":"connecting","added":added,"errors":errors,"remaining":remaining,"name":name,"stage":"در حال اتصال..."}
        log(f"🔌 {name} متصل میشود (ظرفیت {remaining})")
        try:
            await sc.connect()
            # warm up
            try:
                async for _ in sc.app.get_dialogs(limit=200):
                    pass
                await asyncio.sleep(2)
            except:
                pass
            dash["workers"][phone]["state"] = "running"
            # Warmup: batch resolve users
            _warmup_ids = []
            _temp_q = asyncio.Queue()
            while True:
                try: _warmup_ids.append(queue.get_nowait())
                except asyncio.QueueEmpty: break
            for uid in _warmup_ids:
                await queue.put(uid)
            _valid_peers = {}
            if _warmup_ids:
                try:
                    _batch = _warmup_ids[:200]
                    _users = await sc.app.get_users(_batch)
                    for _u in _users:
                        if _u and _u.id and not getattr(_u, 'is_bot', False) and not getattr(_u, 'is_deleted', False):
                            try:
                                _valid_peers[_u.id] = await sc.app.resolve_peer(_u.id)
                            except: pass
                    log(f"🔥 {name}: warmup {len(_valid_peers)}/{len(_batch)} resolved")
                except Exception as _e:
                    log(f"⚠️ {name}: warmup: {_e}")
            dash["workers"][phone]["stage"] = "آماده"
            _ch_peer = await sc.app.resolve_peer(chat_id)
            while remaining > 0:
                try:
                    uid = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    async with sem:
                        from pyrogram.raw.functions.contacts import AddContact
                        from pyrogram.raw.functions.channels import InviteToChannel
                        if uid in _valid_peers:
                            user_peer = _valid_peers[uid]
                        else:
                            user_peer = await sc.app.resolve_peer(uid)
                        try:
                            await sc.app.invoke(AddContact(id=user_peer, first_name=str(uid)[:30], last_name="", phone="", add_phone_privacy_exception=False))
                            await asyncio.sleep(0.3)
                        except: pass
                        await sc.app.invoke(InviteToChannel(channel=_ch_peer, users=[user_peer]))
                    added += 1
                    remaining -= 1
                    mark_added(chat_id, dash["chat_title"], uid)
                    limits = adder_limits_load()
                    limits[phone] = {"added": already+added, "last_used": int(time.time())}
                    save_adder_limits_fn(limits)
                    dash["global"]["total_added"] += 1
                    dash["workers"][phone]["added"] = added
                    dash["workers"][phone]["remaining"] = remaining
                    dash["workers"][phone]["stage"] = f"آخرین: {uid}"
                    if added % 3 == 0 and progress_cb:
                        try:
                            await progress_cb(render_dashboard())
                        except:
                            pass
                    await asyncio.sleep(random.randint(10, 20))
                except (UserAlreadyParticipant,):
                    mark_added(chat_id, dash["chat_title"], uid)
                    dash["global"]["total_skipped"] += 1
                    await asyncio.sleep(1)
                except (UserPrivacyRestricted, UserNotMutualContact, UserBannedInChannel,
                        PeerIdInvalid, BadRequest, UsersTooMuch) as e:
                    errors += 1
                    dash["global"]["total_errors"] += 1
                    dash["workers"][phone]["errors"] = errors
                    await asyncio.sleep(random.randint(3,7))
                except ChatAdminRequired:
                    dash["workers"][phone]["state"] = "error"
                    dash["workers"][phone]["error"] = "ادمین نیست!"
                    log(f"❌ {name}: ادمین نیست")
                    break
                except FloodWait as e:
                    wait = e.value + random.randint(1,5)
                    log(f"⏱️ {name} فلود {wait}s")
                    dash["workers"][phone]["stage"] = f"فلود، صبر {wait}s"
                    if progress_cb:
                        try: await progress_cb(render_dashboard())
                        except: pass
                    await asyncio.sleep(wait)
                except Exception as e:
                    errors += 1
                    dash["global"]["total_errors"] += 1
                    dash["workers"][phone]["errors"] = errors
                    es = str(e).lower()
                    wt = 3
                    if "flood" in es or "too many" in es: wt = 20
                    elif "already" in es or "participant" in es:
                        mark_added(chat_id, dash["chat_title"], uid)
                    await asyncio.sleep(wt)
            dash["workers"][phone]["state"] = "done"
            dash["workers"][phone]["stage"] = "✅ پایان"
            log(f"✅ {name} تمام شد: {added} ادد، {errors} ارور")
        except (AuthKeyDuplicated, AuthKeyUnregistered) as e:
            dash["workers"][phone]["state"] = "auth_error"
            dash["workers"][phone]["error"] = f"سشن منقضی"
            log(f"❌ {name} سشن منقضی")
        except Exception as e:
            dash["workers"][phone]["state"] = "error"
            dash["workers"][phone]["error"] = str(e)[:100]
            log(f"❌ {name} ارور: {str(e)[:100]}")
        finally:
            try: await sc.disconnect()
            except: pass
            if progress_cb:
                try: await progress_cb(render_dashboard())
                except: pass

    try:
        chat = await sc_dummy_chat_info(chat_id, phones[0] if phones else None)
        if chat:
            dash["chat_title"] = getattr(chat, "title", "گروه مقصد") or "گروه مقصد"
    except:
        dash["chat_title"] = "گروه مقصد"

    tasks = [asyncio.create_task(add_worker(p)) for p in phones]
    await asyncio.gather(*tasks, return_exceptions=True)
    dash["running"] = False
    if progress_cb:
        try: await progress_cb(render_dashboard(final=True))
        except: pass


async def sc_dummy_chat_info(chat_id, phone):
    if not phone:
        return None
    sc, _ = make_scraper_for_phone(phone)
    if not sc:
        return None
    try:
        await sc.connect()
        chat = await sc.app.get_chat(chat_id)
        return chat
    except:
        return None
    finally:
        try: await sc.disconnect()
        except: pass


def render_dashboard(final=False):
    elapsed = int(time.time()-dash["started_at"]) if dash["started_at"] else 0
    mins = elapsed // 60
    secs = elapsed % 60
    mode_icon = "🚀" if dash["mode"] == "scrape" else "➕"
    mode_name = "اسکرپ موازی" if dash["mode"] == "scrape" else "ادد موازی"
    dot = "✅" if final else ["🟢","🟡","🟢","🔵"][int(elapsed/2)%4]
    txt = f"{dot} <b>داشبورد {mode_icon} {mode_name}</b>\n\n"
    txt += f"⏱️ زمان: {mins}m {secs}s\n"
    if dash["chat_title"]:
        txt += f"👥 گروه هدف: {dash['chat_title']}\n"
    if dash["mode"] == "scrape":
        txt += f"👤 مجموع پیدا شده: <b>{dash['global']['total_found']:,}</b>\n"
    else:
        done = dash['global']['total_added']+dash['global']['total_errors']+dash['global']['total_skipped']
        txt += f"✅ ادد موفق: <b>{dash['global']['total_added']:,}</b>\n"
        txt += f"❌ خطا: {dash['global']['total_errors']:,}\n"
        txt += f"🔁 رد شده (تکراری): {dash['global']['total_skipped']:,}\n"
    txt += "\n<b>🤖 وضعیت اکانت‌ها:</b>\n"
    for phone, w in dash["workers"].items():
        state_icon = {"connecting":"🟡","warming":"🟡","running":"🟢","done":"✅","error":"❌","auth_error":"🔐","flood":"⏱️"}.get(w.get("state"),"⚪")
        name = w.get("name", phone)
        if dash["mode"] == "scrape":
            txt += f"  {state_icon} {name}: {w.get('found',0):,} نفر"
            if w.get("speed"): txt += f" · ⚡{w['speed']}/min"
            stage = w.get("stage","")
            if stage and w.get("state") == "running": txt += f"\n     └ {stage[:45]}"
            if w.get("error"): txt += f"\n     └ ⚠️ {w['error'][:60]}"
        else:
            txt += f"  {state_icon} {name}: ✅{w.get('added',0)} ❌{w.get('errors',0)} ظرفیت:{w.get('remaining',0)}"
            if w.get("stage"): txt += f"\n     └ {w['stage'][:45]}"
            if w.get("error"): txt += f"\n     └ ⚠️ {w['error'][:60]}"
        txt += "\n"
    if final:
        txt += "\n✅ <b>عملیات تمام شد.</b>"
    else:
        txt += f"\n💡 در حال اجرا... صفحه به روز می‌شود."
    return txt
