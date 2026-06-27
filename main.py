import asyncio
import aiohttp
import json
import os
import re
import sqlite3
import time
import traceback
import logging
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    BotCommand,
)

# ═══════════════════════════════════════════
#  ⚙️  CONFIGURATION
# ═══════════════════════════════════════════

BOT_TOKEN = "8673180473:AAFikpIzaqbKgUGQ4Ksfl9pmH66pIuyJqMU"
OWNER_ID  = 6770872906

SESSION_TIMEOUT_HOURS = 24
RATE_LIMIT_ACTIONS    = 10
RATE_LIMIT_SECONDS    = 60

WELCOME_PHOTO = "https://i.ibb.co/4ZKxKMV/cpm-banner.jpg"

# ═══════════════════════════════════════════
#  📊 LOGGING
# ═══════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)s │ %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("CPM")

# ═══════════════════════════════════════════
#  🗄️  PERSISTENT STORE
# ═══════════════════════════════════════════

STORE_PATH = Path("cpm_store.json")

DEFAULT_STORE: Dict[str, Any] = {
    "allowed_users": [],
    "vip_users": [],
    "admins": {},
    "pending": {},
    "banned": [],
    "expiry": {},
    "stats": {"total_logins": 0, "total_actions": 0, "total_unlocks": 0},
    "admin_log": [],
    "users": {},
    "daily_stats": {},
    "notes": {},
    "warnings": {},
    "maintenance": False,
    "broadcast_history": [],
}


def load_store() -> Dict[str, Any]:
    try:
        if STORE_PATH.exists():
            with STORE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in DEFAULT_STORE.items():
                if k not in data:
                    data[k] = v
            data["admins"] = {str(k): v for k, v in data.get("admins", {}).items()}
            data["allowed_users"] = list({int(x) for x in data.get("allowed_users", [])})
            data["vip_users"] = list({int(x) for x in data.get("vip_users", [])})
            data["banned"] = list({int(x) for x in data.get("banned", [])})
            return data
        save_store(DEFAULT_STORE)
        return dict(DEFAULT_STORE)
    except Exception:
        save_store(DEFAULT_STORE)
        return dict(DEFAULT_STORE)


def save_store(data: Dict[str, Any]) -> None:
    try:
        tmp = STORE_PATH.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(STORE_PATH)
    except Exception as e:
        log.error(f"Save store error: {e}")


STORE = load_store()

if OWNER_ID not in STORE["allowed_users"]:
    STORE["allowed_users"].append(OWNER_ID)
if str(OWNER_ID) not in STORE["admins"]:
    STORE["admins"][str(OWNER_ID)] = "owner"
save_store(STORE)

ALLOWED_USERS: List[int] = list(STORE.get("allowed_users", []))
VIP_USERS: List[int] = list(STORE.get("vip_users", []))
ADMINS: Dict[int, str] = {int(k): v for k, v in STORE.get("admins", {}).items()}
BANNED: List[int] = list(STORE.get("banned", []))
PENDING: Dict[str, Any] = STORE.get("pending", {})
EXPIRY: Dict[str, Any] = STORE.get("expiry", {})
RATE_DATA: Dict[str, Any] = {}

ADMIN_LEVELS = {"owner": 100, "superadmin": 50, "admin": 10, "moderator": 5}


def is_allowed(uid: int) -> bool:
    return uid in ALLOWED_USERS

def is_banned(uid: int) -> bool:
    return uid in BANNED

def is_pending(uid: int) -> bool:
    return str(uid) in PENDING

def is_vip(uid: int) -> bool:
    return uid in VIP_USERS

def is_expired(uid: int) -> bool:
    exp = EXPIRY.get(str(uid))
    if not exp:
        return False
    try:
        return datetime.now() > datetime.fromisoformat(exp)
    except:
        return False

def is_maintenance() -> bool:
    return STORE.get("maintenance", False)

def admin_level(uid: int) -> int:
    role = ADMINS.get(uid, "")
    return ADMIN_LEVELS.get(role, 0)

def admin_role(uid: int) -> str:
    return ADMINS.get(uid, "")

def has_admin(uid: int, required: str = "admin") -> bool:
    return admin_level(uid) >= ADMIN_LEVELS.get(required, 10)

def check_rate_limit(uid: int) -> Tuple[bool, int]:
    now = time.time()
    key = str(uid)
    data = RATE_DATA.get(key, {"count": 0, "reset": now + RATE_LIMIT_SECONDS})
    if now > data["reset"]:
        data = {"count": 0, "reset": now + RATE_LIMIT_SECONDS}
    if data["count"] >= RATE_LIMIT_ACTIONS:
        wait = int(data["reset"] - now)
        return False, wait
    data["count"] += 1
    RATE_DATA[key] = data
    return True, 0


def store_allow(uid: int, name: str = "") -> bool:
    global ALLOWED_USERS, STORE
    uid = int(uid)
    if uid in ALLOWED_USERS:
        return False
    ALLOWED_USERS.append(uid)
    STORE["allowed_users"] = list(ALLOWED_USERS)
    save_store(STORE)
    return True

def store_ban(uid: int) -> bool:
    global BANNED, ALLOWED_USERS, STORE
    uid = int(uid)
    if uid in BANNED:
        return False
    BANNED.append(uid)
    if uid in ALLOWED_USERS:
        ALLOWED_USERS.remove(uid)
    STORE["banned"] = list(BANNED)
    STORE["allowed_users"] = list(ALLOWED_USERS)
    save_store(STORE)
    return True

def store_unban(uid: int) -> bool:
    global BANNED, STORE
    uid = int(uid)
    if uid not in BANNED:
        return False
    BANNED.remove(uid)
    STORE["banned"] = list(BANNED)
    save_store(STORE)
    return True

def store_remove_user(uid: int) -> bool:
    global ALLOWED_USERS, STORE
    uid = int(uid)
    if uid not in ALLOWED_USERS:
        return False
    ALLOWED_USERS = [x for x in ALLOWED_USERS if x != uid]
    STORE["allowed_users"] = list(ALLOWED_USERS)
    save_store(STORE)
    return True

def store_add_admin(uid: int, role: str = "admin") -> bool:
    global ADMINS, STORE
    uid = int(uid)
    if role not in ADMIN_LEVELS:
        role = "admin"
    store_allow(uid)
    ADMINS[uid] = role
    STORE["admins"] = {str(k): v for k, v in ADMINS.items()}
    save_store(STORE)
    return True

def store_remove_admin(uid: int) -> bool:
    global ADMINS, STORE
    uid = int(uid)
    if uid not in ADMINS:
        return False
    ADMINS.pop(uid, None)
    STORE["admins"] = {str(k): v for k, v in ADMINS.items()}
    save_store(STORE)
    return True

def store_add_pending(uid: int, name: str, username: str = "") -> bool:
    global PENDING, STORE
    uid_str = str(uid)
    if uid_str in PENDING:
        return False
    PENDING[uid_str] = {
        "name": name, "username": username,
        "time": datetime.now().isoformat()
    }
    STORE["pending"] = PENDING
    save_store(STORE)
    return True

def store_remove_pending(uid: int):
    global PENDING, STORE
    PENDING.pop(str(uid), None)
    STORE["pending"] = PENDING
    save_store(STORE)

def store_add_vip(uid: int) -> bool:
    global VIP_USERS, STORE
    uid = int(uid)
    if uid in VIP_USERS:
        return False
    VIP_USERS.append(uid)
    store_allow(uid)
    STORE["vip_users"] = list(VIP_USERS)
    save_store(STORE)
    return True

def store_remove_vip(uid: int) -> bool:
    global VIP_USERS, STORE
    uid = int(uid)
    if uid not in VIP_USERS:
        return False
    VIP_USERS.remove(uid)
    STORE["vip_users"] = list(VIP_USERS)
    save_store(STORE)
    return True

def store_set_expiry(uid: int, days: int):
    global EXPIRY, STORE
    exp = (datetime.now() + timedelta(days=days)).isoformat()
    EXPIRY[str(uid)] = exp
    STORE["expiry"] = EXPIRY
    save_store(STORE)

def store_remove_expiry(uid: int):
    global EXPIRY, STORE
    EXPIRY.pop(str(uid), None)
    STORE["expiry"] = EXPIRY
    save_store(STORE)

def store_add_warning(uid: int, reason: str) -> int:
    warns = STORE.setdefault("warnings", {})
    user_warns = warns.setdefault(str(uid), [])
    user_warns.append({"reason": reason, "time": datetime.now().isoformat()})
    STORE["warnings"] = warns
    save_store(STORE)
    return len(user_warns)

def store_get_warnings(uid: int) -> List[Dict]:
    return STORE.get("warnings", {}).get(str(uid), [])

def store_clear_warnings(uid: int):
    warns = STORE.get("warnings", {})
    warns.pop(str(uid), None)
    STORE["warnings"] = warns
    save_store(STORE)

def store_set_note(uid: int, note: str):
    notes = STORE.setdefault("notes", {})
    notes[str(uid)] = note
    STORE["notes"] = notes
    save_store(STORE)

def store_get_note(uid: int) -> str:
    return STORE.get("notes", {}).get(str(uid), "")

def admin_log(actor_id: int, action: str, target: str = ""):
    entry = {
        "time": datetime.now().isoformat(),
        "actor": actor_id,
        "action": action,
        "target": target,
    }
    STORE.setdefault("admin_log", []).insert(0, entry)
    STORE["admin_log"] = STORE["admin_log"][:200]
    save_store(STORE)

def add_broadcast_history(actor: int, msg_type: str, text: str, sent: int, failed: int):
    bh = STORE.setdefault("broadcast_history", [])
    bh.insert(0, {
        "time": datetime.now().isoformat(),
        "actor": actor, "type": msg_type,
        "text": text[:50], "sent": sent, "failed": failed,
    })
    STORE["broadcast_history"] = bh[:20]
    save_store(STORE)

def update_daily_stats(key: str = "actions"):
    today = datetime.now().strftime("%Y-%m-%d")
    ds = STORE.setdefault("daily_stats", {})
    td = ds.setdefault(today, {"actions": 0, "logins": 0, "unlocks": 0})
    td[key] = td.get(key, 0) + 1
    STORE["daily_stats"] = ds
    save_store(STORE)


# ═══════════════════════════════════════════
#  🎮 CPM NUKER — FIXED
# ═══════════════════════════════════════════

FK = "AIzaSyBW1ZbMiUeDZHYUO2bY8Bfnf5rRgrQGPTM"
SU = "https://europe-west1-cp-multiplayer.cloudfunctions.net/SavePlayerRecordsIOS1"
LU = "https://europe-west1-cp-multiplayer.cloudfunctions.net/GetPlayerRecordsIOS1"
RU = "https://us-central1-cp-multiplayer.cloudfunctions.net/SetUserRating4"
MAX_MONEY = 50_000_000
MAX_COIN  = 500_000


class CPMNuker:
    def __init__(self):
        self.db_path = "cpm_tokens.db"
        self.user_data_cache: Dict[str, Dict] = {}
        self.base_headers = {
            "User-Agent": "okhttp/3.12.13",
            "Content-Type": "application/json",
        }
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    user_id          INTEGER PRIMARY KEY,
                    auth_token       TEXT,
                    email            TEXT,
                    password         TEXT,
                    refresh_token    TEXT,
                    token_expires_at REAL,
                    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS user_data (
                    cache_key  TEXT PRIMARY KEY,
                    email      TEXT,
                    data_json  TEXT,
                    saved_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS backups (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER,
                    label      TEXT,
                    data_json  TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.commit()

    def _cache_key(self, uid: int, email: str = None) -> str:
        if email:
            return f"{uid}_{email}"
        td = self.get_token_data(uid)
        if td and td.get("email"):
            return f"{uid}_{td['email']}"
        return str(uid)

    # ── Token management ──────────────────────────────────────
    def save_token(self, uid: int, auth: str, email: str, pw: str = None, rt: str = None):
        exp = time.time() + 3600
        with sqlite3.connect(self.db_path) as c:
            c.execute("""
                INSERT OR REPLACE INTO tokens
                (user_id, auth_token, email, password, refresh_token, token_expires_at)
                VALUES (?,?,?,?,?,?)
            """, (uid, auth, email, pw, rt, exp))
            c.commit()

    def get_token_data(self, uid: int) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as c:
            row = c.execute("""
                SELECT auth_token, email, password, refresh_token, token_expires_at
                FROM tokens WHERE user_id = ?
            """, (uid,)).fetchone()
        if row:
            return {
                "auth_token": row[0], "email": row[1],
                "password": row[2], "refresh_token": row[3],
                "token_expires_at": row[4]
            }
        return None

    def get_token(self, uid: int) -> Optional[Dict]:
        td = self.get_token_data(uid)
        if td:
            return {"auth_token": td["auth_token"], "email": td["email"]}
        return None

    def get_auth_token(self, uid: int) -> Optional[str]:
        td = self.get_token_data(uid)
        return td["auth_token"] if td else None

    def update_token(self, uid: int, auth: str, rt: str = None):
        exp = time.time() + 3600
        with sqlite3.connect(self.db_path) as c:
            if rt:
                c.execute("""
                    UPDATE tokens SET auth_token=?, refresh_token=?, token_expires_at=?
                    WHERE user_id=?
                """, (auth, rt, exp, uid))
            else:
                c.execute("""
                    UPDATE tokens SET auth_token=?, token_expires_at=?
                    WHERE user_id=?
                """, (auth, exp, uid))
            c.commit()

    def delete_token(self, uid: int):
        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM tokens WHERE user_id=?", (uid,))
            c.commit()
        for k in [k for k in self.user_data_cache if k.startswith(str(uid))]:
            del self.user_data_cache[k]

    def is_token_expired(self, uid: int) -> bool:
        td = self.get_token_data(uid)
        if not td or not td.get("token_expires_at"):
            return True
        return td["token_expires_at"] < time.time()

    # ── User data ──────────────────────────────────────────────
    def get_user_template(self, uid: int, email: str = None) -> Dict:
        ck = self._cache_key(uid, email)
        if ck not in self.user_data_cache:
            saved = self._load_user_data(ck)
            if saved:
                self.user_data_cache[ck] = saved
            else:
                # Empty safe template — no zeros that overwrite real data
                self.user_data_cache[ck] = {
                    "Name": "",
                    "localID": "",
                    "money": 0,
                    "coin": 0,
                    "floats": [],
                    "integers": [],
                    "wheels": [],
                    "animations": [],
                    "personEquipmentsMale": {},
                    "personEquipmentsFemale": {},
                    "carIDnStatus": {},
                    "LevelsDoneTime": []
                }
        return self.user_data_cache[ck]

    def save_user_template(self, uid: int, data: Dict, email: str = None):
        ck = self._cache_key(uid, email)
        self.user_data_cache[ck] = data
        self._save_user_data(ck, email, data)

    def _save_user_data(self, ck: str, email: str, data: Dict):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""
                INSERT OR REPLACE INTO user_data (cache_key, email, data_json)
                VALUES (?,?,?)
            """, (ck, email, json.dumps(data)))
            c.commit()

    def _load_user_data(self, ck: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as c:
            row = c.execute("""
                SELECT data_json FROM user_data WHERE cache_key=?
            """, (ck,)).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                pass
        return None

    def save_backup(self, uid: int, label: str, data: Dict):
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                "INSERT INTO backups (user_id,label,data_json) VALUES (?,?,?)",
                (uid, label, json.dumps(data))
            )
            c.commit()

    def get_backups(self, uid: int) -> List[Dict]:
        with sqlite3.connect(self.db_path) as c:
            rows = c.execute(
                "SELECT id,label,created_at FROM backups WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
                (uid,)
            ).fetchall()
        return [{"id": r[0], "label": r[1], "time": r[2]} for r in rows]

    def restore_backup(self, uid: int, backup_id: int) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT data_json FROM backups WHERE id=? AND user_id=?",
                (backup_id, uid)
            ).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                pass
        return None

    # ── HTTP ──────────────────────────────────────────────────
    async def _request(self, url: str, payload: Dict = None,
                       headers: Dict = None, params: Dict = None) -> Optional[Dict]:
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(ssl=False)
            h = {**self.base_headers, **(headers or {})}
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as s:
                async with s.post(url, json=payload or {}, headers=h, params=params or {}) as r:
                    text = await r.text()
                    try:
                        return json.loads(text)
                    except Exception:
                        return {"raw": text}
        except Exception as e:
            log.error(f"HTTP error: {e}")
            return None

    # ── Token refresh ─────────────────────────────────────────
    async def _refresh_token(self, uid: int) -> Tuple[bool, str]:
        td = self.get_token_data(uid)
        if not td:
            return False, "NO_TOKEN"
        rt = td.get("refresh_token")
        em = td.get("email")
        pw = td.get("password")

        if rt:
            r = await self._request(
                f"https://securetoken.googleapis.com/v1/token?key={FK}",
                {"grant_type": "refresh_token", "refresh_token": rt}
            )
            if r and r.get("id_token"):
                self.update_token(uid, r["id_token"], r.get("refresh_token", rt))
                return True, "OK"

        if em and pw:
            res = await self.account_login(em, pw)
            if res.get("ok"):
                self.update_token(uid, res["auth"], res.get("refresh_token", ""))
                return True, "OK"

        return False, "REFRESH_FAILED"

    async def get_valid_token(self, uid: int) -> Tuple[bool, str, str]:
        if self.is_token_expired(uid):
            ok, msg = await self._refresh_token(uid)
            if not ok:
                return False, msg, ""
        td = self.get_token_data(uid)
        if td and td.get("auth_token"):
            return True, "OK", td["auth_token"]
        return False, "NO_TOKEN", ""

    # ── Login ─────────────────────────────────────────────────
    async def account_login(self, email: str, password: str) -> Dict[str, Any]:
        url = f"https://www.googleapis.com/identitytoolkit/v3/relyingparty/verifyPassword?key={FK}"
        r = await self._request(url, {
            "email": email, "password": password,
            "returnSecureToken": True,
            "clientType": "CLIENT_TYPE_ANDROID"
        }, headers={
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; SM-A025F Build/SP1A.210812.016)"
        })

        if not r:
            return {"ok": False, "message": "NETWORK_ERROR"}
        if "idToken" in r:
            return {
                "ok": True, "message": "OK",
                "auth": r["idToken"],
                "refresh_token": r.get("refreshToken", "")
            }
        err = r.get("error", {}).get("message", "").upper()
        for key in ["EMAIL_NOT_FOUND", "INVALID_PASSWORD", "TOO_MANY_ATTEMPTS",
                    "USER_DISABLED", "INVALID_EMAIL", "INVALID_LOGIN_CREDENTIALS"]:
            if key in err:
                return {"ok": False, "message": key}
        return {"ok": False, "message": "LOGIN_FAILED"}

    # ── LOAD REAL ACCOUNT DATA FROM SERVER ────────────────────
    async def load_account(self, uid: int, force: bool = False) -> bool:
        """
        Fetch real player data from CPM servers.
        Returns True if successful, False if failed.
        CRITICAL: Must be called before any modification.
        """
        ck = self._cache_key(uid)

        # If already cached and not forced, skip loading
        if not force and ck in self.user_data_cache:
            saved = self._load_user_data(ck)
            if saved:
                return True

        ok, msg, auth = await self.get_valid_token(uid)
        if not ok:
            log.warning(f"load_account: No valid token for {uid}: {msg}")
            return False

        try:
            r = await self._request(
                LU,
                {"data": json.dumps({})},
                {"Authorization": f"Bearer {auth}"}
            )

            if not r:
                log.warning(f"load_account: No response for {uid}")
                return False

            # Try to parse the result
            real_data = None

            # Case 1: result is a JSON string
            if "result" in r:
                try:
                    real_data = json.loads(r["result"])
                except Exception:
                    real_data = r["result"]

            # Case 2: data key
            elif "data" in r:
                try:
                    real_data = json.loads(r["data"])
                except Exception:
                    real_data = r["data"]

            # Case 3: response itself is the data
            elif isinstance(r, dict) and "Name" in r:
                real_data = r

            if real_data and isinstance(real_data, dict):
                # Validate it has real game fields
                has_game_data = any(
                    k in real_data for k in
                    ["Name", "localID", "money", "coin", "floats", "integers"]
                )
                if has_game_data:
                    td = self.get_token_data(uid)
                    em = td.get("email") if td else None
                    self.save_user_template(uid, real_data, em)
                    log.info(f"load_account: Loaded real data for {uid} ✔")
                    return True

            log.warning(f"load_account: Could not parse real data for {uid}")
            return False

        except Exception as e:
            log.error(f"load_account error for {uid}: {e}")
            return False

    # ── Save full data ────────────────────────────────────────
    async def _send_data(self, auth: str, data: Dict) -> Tuple[bool, str]:
        # Clean data before sending — remove any extra keys not needed
        safe_keys = {
            "Name", "localID", "money", "coin",
            "floats", "integers", "wheels", "animations",
            "personEquipmentsMale", "personEquipmentsFemale",
            "carIDnStatus", "LevelsDoneTime"
        }
        clean_data = {k: v for k, v in data.items() if k in safe_keys}

        r = await self._request(
            SU,
            {"data": json.dumps(clean_data)},
            {"Authorization": f"Bearer {auth}"}
        )
        if r:
            rs = str(r)
            if '"result":1' in rs or "'result': 1" in rs or "1" in rs:
                return True, "OK"
        return False, "SAVE_FAILED"

    async def _save(self, uid: int, data: Dict) -> Dict[str, Any]:
        ok, msg, auth = await self.get_valid_token(uid)
        if not ok:
            return {"ok": False, "message": msg}
        success, msg2 = await self._send_data(auth, data)
        if success:
            td = self.get_token_data(uid)
            self.save_user_template(uid, data, td.get("email") if td else None)
            STORE["stats"]["total_actions"] = STORE["stats"].get("total_actions", 0) + 1
            save_store(STORE)
            update_daily_stats("actions")
            return {"ok": True}
        return {"ok": False, "message": msg2}

    async def _get_real_data(self, uid: int) -> Dict:
        """
        Always returns real account data.
        Loads from server if not cached.
        """
        loaded = await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        return self.get_user_template(uid, em)

    async def _modify(self, uid: int, mods: Dict[str, Any]) -> Dict[str, Any]:
        """Fixed: Always load real data first before modifying"""
        # Load real data from server first
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)

        for k, v in mods.items():
            if k == "money":
                v = min(v, MAX_MONEY)
            if k == "coin":
                v = min(v, MAX_COIN)
            d[k] = v

        return await self._save(uid, d)

    # ── Game operations ───────────────────────────────────────
    async def set_money(self, uid: int, amount: int) -> Dict[str, Any]:
        return await self._modify(uid, {"money": min(amount, MAX_MONEY)})

    async def set_coin(self, uid: int, amount: int) -> Dict[str, Any]:
        return await self._modify(uid, {"coin": min(amount, MAX_COIN)})

    async def set_player_name(self, uid: int, name: str) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        d["Name"] = name
        return await self._save(uid, d)

    async def set_player_id(self, uid: int, pid: str) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        d["localID"] = pid.upper()
        return await self._save(uid, d)

    async def set_race_wins(self, uid: int, amount: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        fl = d.get("floats", [])
        while len(fl) < 9:
            fl.append(0.0)
        fl[8] = float(amount)
        d["floats"] = fl
        return await self._save(uid, d)

    async def set_race_loses(self, uid: int, amount: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        fl = d.get("floats", [])
        while len(fl) < 10:
            fl.append(0.0)
        fl[9] = float(amount)
        d["floats"] = fl
        return await self._save(uid, d)

    async def unlock_w16(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        fl = d.get("floats", [])
        while len(fl) < 33:
            fl.append(0.0)
        fl[32] = 1.0
        d["floats"] = fl
        return await self._save(uid, d)

    async def unlock_horns(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        fl = d.get("floats", [])
        while len(fl) < 32:
            fl.append(0.0)
        for i in [27, 28, 29, 30, 31]:
            fl[i] = 1.0
        d["floats"] = fl
        return await self._save(uid, d)

    async def disable_damage(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        fl = d.get("floats", [])
        while len(fl) < 35:
            fl.append(0.0)
        fl[34] = 1.0
        d["floats"] = fl
        return await self._save(uid, d)

    async def unlimited_fuel(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        fl = d.get("floats", [])
        while len(fl) < 4:
            fl.append(0.0)
        fl[3] = 1.0
        d["floats"] = fl
        return await self._save(uid, d)

    async def unlock_smoke(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        fl = d.get("floats", [])
        while len(fl) < 34:
            fl.append(0.0)
        fl[33] = 1.0
        d["floats"] = fl
        return await self._save(uid, d)

    async def unlock_animations(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        existing = d.get("animations", [])
        d["animations"] = list(set(existing + list(range(301))))
        return await self._save(uid, d)

    async def unlock_wheels(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        existing_wheels = d.get("wheels", [])
        new_wheels = list(range(73, 221))
        d["wheels"] = list(set(existing_wheels + new_wheels))
        it = d.get("integers", [])
        while len(it) < 113:
            it.append(0)
        for i in [0, 1, 2, 3, 4, 5, 110, 111, 112]:
            it[i] = 1
        d["integers"] = it
        return await self._save(uid, d)

    async def unlock_houses(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        it = d.get("integers", [])
        while len(it) < 113:
            it.append(0)
        for i in [8, 110, 111, 112]:
            it[i] = 1
        d["integers"] = it
        return await self._save(uid, d)

    async def complete_all_levels(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        lvl = [0] + [120 if i == 43 else 1 for i in range(1, 110)]
        d["LevelsDoneTime"] = lvl
        return await self._save(uid, d)

    async def unlock_equipments_male(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        eq = {
            "Gender": 0,
            "bag": list(range(101)),
            "beard": list(range(6, 21)) + [100],
            "cap": list(range(3, 64)),
            "face": [0, 1, 2, 100],
            "glasses": list(range(10)) + [100],
            "gloves": list(range(6)) + [100],
            "hair": list(range(3, 20)) + [100],
            "mask": list(range(3, 9)) + [100],
            "pants": list(range(26)),
            "shoes": list(range(31)),
            "top": list(range(2, 109)),
            "SelectedEquipments": [-1, 10, 19, 41, 100, 4, 20, 9, 22, 21, 74]
        }
        d["personEquipmentsMale"] = eq
        return await self._save(uid, d)

    async def unlock_equipments_female(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)
        eq = {
            "Gender": 1,
            "bag": list(range(6)),
            "beard": [],
            "cap": list(range(3, 41)),
            "face": [0],
            "glasses": list(range(10)),
            "gloves": [1],
            "hair": [0, 7, 8, 9, 10],
            "mask": list(range(3, 8)),
            "pants": list(range(12)),
            "shoes": list(range(3, 15)),
            "top": list(range(5, 80)),
            "SelectedEquipments": [0, 0, -1, -1, -1, -1, -1, -1, 0, -1, -1]
        }
        d["personEquipmentsFemale"] = eq
        return await self._save(uid, d)

    async def set_rank(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        ok, msg, auth = await self.get_valid_token(uid)
        if not ok:
            return {"ok": False, "message": msg}
        rd = {"RatingData": {
            "time": 1e22, "cars": 1e16, "car_fix": 1e13,
            "car_collided": 1e12, "car_exchange": 1e13,
            "car_trade": 1e13, "car_wash": 1e13,
            "slicer_cut": 1e13, "drift_max": 1e14,
            "drift": 1e14, "cargo": 1e5, "delivery": 1e5,
            "race_win": 3e20, "taxi": 1e10,
            "levels": 10000990000, "gifts": 1e9,
            "fuel": 1e10, "offroad": 1e10,
            "speed_banner": 1e9, "reactions": 1e17,
            "run": 1e9, "real_estate": 1e9,
            "t_distance": 1e10, "treasure": 1e10,
            "block_post": 1e10, "push_ups": 1e12,
            "burnt_tire": 1e10, "passanger_distance": 1e8
        }}
        r = await self._request(
            RU,
            {"data": json.dumps(rd)},
            {"Authorization": f"Bearer {auth}"}
        )
        if r and "1" in str(r):
            STORE["stats"]["total_unlocks"] = STORE["stats"].get("total_unlocks", 0) + 1
            save_store(STORE)
            return {"ok": True}
        return {"ok": False, "message": "RANK_FAILED"}

    async def fix_account_data(self, uid: int) -> Dict[str, Any]:
        await self.load_account(uid)
        td = self.get_token_data(uid)
        em = td.get("email") if td else None
        d = self.get_user_template(uid, em)

        fl = (d.get("floats", []))[:54]
        while len(fl) < 54:
            fl.append(0.0)
        bugs = 0
        fixed_fl = []
        for v in fl:
            if v == 1 or v == 1.0:
                fixed_fl.append(1.0)
            elif isinstance(v, (int, float)) and v > 1:
                bugs += 1
                fixed_fl.append(0.0)
            else:
                fixed_fl.append(float(v) if v else 0.0)

        it = (d.get("integers", []))[:120]
        while len(it) < 120:
            it.append(0)
        fixed_it = []
        for v in it:
            if v == 1:
                fixed_it.append(1)
            elif isinstance(v, (int, float)) and v > 1:
                bugs += 1
                fixed_it.append(0)
            else:
                fixed_it.append(int(v) if v else 0)

        d["floats"] = fixed_fl
        d["integers"] = fixed_it
        result = await self._save(uid, d)
        if result.get("ok"):
            return {"ok": True, "bugs_fixed": bugs}
        return {"ok": False, "message": "FIX_FAILED"}


nuker = CPMNuker()

# ═══════════════════════════════════════════
#  🎨 THEME
# ═══════════════════════════════════════════

class T:
    H = "◈━━━━━━━━━━━━━━━━━━━━━━━━◈"

    @staticmethod
    def hdr(icon, title):
        return f"{T.H}\n  {icon}  {title}\n{T.H}"

    @staticmethod
    def welcome_approved(name, username, uid):
        now = datetime.now()
        date_str = now.strftime("%d %b %Y")
        time_str = now.strftime("%I:%M %p")
        badge = "💎 VIP" if is_vip(uid) else "✦ Member"
        exp = ""
        if str(uid) in EXPIRY:
            try:
                exp_dt = datetime.fromisoformat(EXPIRY[str(uid)])
                days = (exp_dt - now).days
                exp = f"\n  │ ⏰ Access: {days}d left"
            except:
                pass
        return (
            f"{T.H}\n"
            f"  🎮  𝗖𝗣𝗠 𝗧𝗢𝗢𝗟 𝗕𝗢𝗫  𝘃𝟯.𝟱\n"
            f"{T.H}\n\n"
            f"  ╭─── 𝗪𝗘𝗟𝗖𝗢𝗠𝗘 ───╮\n"
            f"  │\n"
            f"  │ 👤 {name}\n"
            f"  │ 📱 @{username or 'N/A'}\n"
            f"  │ 🆔 <code>{uid}</code>\n"
            f"  │ 🏷 {badge}\n"
            f"  │\n"
            f"  │ 📅 {date_str}\n"
            f"  │ 🕐 {time_str}{exp}\n"
            f"  │\n"
            f"  ╰──────────────╯\n\n"
            f"  ▸ Sign in with your CPM\n"
            f"    game credentials below"
        )

    @staticmethod
    def no_access():
        return (
            f"{T.H}\n  🔒  𝗔𝗖𝗖𝗘𝗦𝗦 𝗗𝗘𝗡𝗜𝗘𝗗\n{T.H}\n\n"
            "  ▸ You don't have access.\n"
            "  ▸ Request below to join."
        )

    @staticmethod
    def banned():
        return f"{T.H}\n  🚫  𝗕𝗔𝗡𝗡𝗘𝗗\n{T.H}\n\n  ✗ Access revoked."

    @staticmethod
    def maintenance():
        return (
            f"{T.H}\n  🔧  𝗠𝗔𝗜𝗡𝗧𝗘𝗡𝗔𝗡𝗖𝗘\n{T.H}\n\n"
            "  ▸ Bot under maintenance.\n"
            "  ▸ Try again later."
        )

    @staticmethod
    def get_access_menu():
        return (
            f"{T.H}\n  🔑  𝗚𝗘𝗧 𝗔𝗖𝗖𝗘𝗦𝗦\n{T.H}\n\n"
            "  ▸ Send Request — Notify admin\n"
            "  ▸ Message Admin — Direct chat"
        )

    @staticmethod
    def request_sent():
        return (
            f"{T.H}\n  📩  𝗥𝗘𝗤𝗨𝗘𝗦𝗧 𝗦𝗘𝗡𝗧\n{T.H}\n\n"
            "  ✔ Sent to admins.\n"
            "  ▸ You'll be notified."
        )

    @staticmethod
    def already_pending():
        return (
            f"{T.H}\n  ⏳  𝗣𝗘𝗡𝗗𝗜𝗡𝗚\n{T.H}\n\n"
            "  ▸ Already pending.\n"
            "  ▸ Wait for admin."
        )

    @staticmethod
    def login_email():
        return (
            f"{T.H}\n  📧  𝗘𝗡𝗧𝗘𝗥 𝗘𝗠𝗔𝗜𝗟\n{T.H}\n\n"
            "  ✏ Type your CPM email:"
        )

    @staticmethod
    def login_pass():
        return (
            f"{T.H}\n  🔑  𝗣𝗔𝗦𝗦𝗪𝗢𝗥𝗗\n{T.H}\n\n"
            "  ✏ Type your CPM password:\n"
            "  🔒 Deleted from chat"
        )

    @staticmethod
    def login_fail(reason):
        err_map = {
            "EMAIL_NOT_FOUND": "✗ Email not found",
            "INVALID_PASSWORD": "✗ Wrong password",
            "INVALID_LOGIN_CREDENTIALS": "✗ Invalid credentials",
            "TOO_MANY_ATTEMPTS": "✗ Too many attempts",
            "USER_DISABLED": "✗ Account disabled",
            "INVALID_EMAIL": "✗ Invalid email",
            "NETWORK_ERROR": "✗ Network error",
        }
        return (
            f"{T.H}\n  ❌  𝗟𝗢𝗚𝗜𝗡 𝗙𝗔𝗜𝗟𝗘𝗗\n{T.H}\n\n"
            f"  {err_map.get(reason, reason)}\n\n"
            "  ▸ Tap Login to retry"
        )

    @staticmethod
    def home(td, uid=0):
        email = td.get("email", "—") if td else "—"
        badge = " 💎" if is_vip(uid) else ""
        exp_txt = ""
        if str(uid) in EXPIRY:
            try:
                exp_dt = datetime.fromisoformat(EXPIRY[str(uid)])
                days = (exp_dt - datetime.now()).days
                exp_txt = f"\n  ⏰ Expires: {days}d"
            except:
                pass
        return (
            f"{T.H}\n  🏠  𝗗𝗔𝗦𝗛𝗕𝗢𝗔𝗥𝗗{badge}\n{T.H}\n\n"
            f"  ◆ {email}{exp_txt}\n\n"
            "  ▸ Select option below:"
        )

    @staticmethod
    def money_menu():
        return f"{T.H}\n  💰  𝗠𝗢𝗡𝗘𝗬\n{T.H}\n\n  Max: $50,000,000"

    @staticmethod
    def coins_menu():
        return f"{T.H}\n  🪙  𝗖𝗢𝗜𝗡𝗦\n{T.H}\n\n  Max: 500,000"

    @staticmethod
    def features_menu():
        return f"{T.H}\n  ⚡  𝗙𝗘𝗔𝗧𝗨𝗥𝗘𝗦\n{T.H}\n\n  ▸ Select or UNLOCK ALL"

    @staticmethod
    def settings_menu():
        return f"{T.H}\n  🔧  𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦\n{T.H}\n\n  ▸ Modify account:"

    @staticmethod
    def admin_panel(uid):
        role = admin_role(uid)
        badge = {
            "owner": "👑 Owner", "superadmin": "⭐ Super",
            "admin": "🛡 Admin", "moderator": "👮 Mod"
        }.get(role, "❓")
        t = len(ALLOWED_USERS)
        bar = "▰" * min(int(t / 2), 15) + "▱" * (15 - min(int(t / 2), 15))
        maint = "🔴 ON" if is_maintenance() else "🟢 OFF"
        return (
            f"{T.H}\n  👑  𝗔𝗗𝗠𝗜𝗡 𝗣𝗔𝗡𝗘𝗟\n{T.H}\n\n"
            f"  ◆ Role: {badge}\n"
            f"  ◆ Maint: {maint}\n\n"
            f"  [{bar}] {t} users\n"
            f"  ◈ Pending {len(PENDING)}\n"
            f"  ◈ Banned  {len(BANNED)}\n"
            f"  ◈ VIP     {len(VIP_USERS)}\n"
            f"  ◈ Admins  {len(ADMINS)}\n\n"
            "  ▸ Select action:"
        )

    @staticmethod
    def stats():
        s = STORE.get("stats", {})
        ds = STORE.get("daily_stats", {})
        today = datetime.now().strftime("%Y-%m-%d")
        td = ds.get(today, {})
        return (
            f"{T.H}\n  📊  𝗦𝗧𝗔𝗧𝗦\n{T.H}\n\n"
            f"  ◆ Users     {len(ALLOWED_USERS)}\n"
            f"  ◆ VIP       {len(VIP_USERS)}\n"
            f"  ◆ Pending   {len(PENDING)}\n"
            f"  ◆ Banned    {len(BANNED)}\n\n"
            f"  ◈ Actions   {s.get('total_actions', 0)}\n"
            f"  ◈ Unlocks   {s.get('total_unlocks', 0)}\n"
            f"  ◈ Logins    {s.get('total_logins', 0)}\n\n"
            f"  ◆ Today     {td.get('actions', 0)} acts"
        )


# ═══════════════════════════════════════════
#  ⌨️  KEYBOARDS
# ═══════════════════════════════════════════

class K:
    @staticmethod
    def no_access():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔑 Get Access", callback_data="get_access")],
        ])

    @staticmethod
    def access_menu():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📩 Send Request", callback_data="send_request")],
            [InlineKeyboardButton(text="💬 Message Admin", callback_data="msg_admin")],
            [InlineKeyboardButton(text="◂ Back", callback_data="back_start")],
        ])

    @staticmethod
    def after_request():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Check Status", callback_data="check_status")],
            [InlineKeyboardButton(text="💬 Message Admin", callback_data="msg_admin")],
        ])

    @staticmethod
    def login():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔐 Sign In", callback_data="login")],
        ])

    @staticmethod
    def cancel():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✗ Cancel", callback_data="cancel")],
        ])

    @staticmethod
    def home(uid=0):
        rows = [
            [InlineKeyboardButton(text="💰 Money", callback_data="menu_money"),
             InlineKeyboardButton(text="🪙 Coins", callback_data="menu_coins")],
            [InlineKeyboardButton(text="⚡ Features", callback_data="menu_feat"),
             InlineKeyboardButton(text="🔧 Settings", callback_data="menu_set")],
            [InlineKeyboardButton(text="💾 Backup", callback_data="menu_backup")],
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="refresh")],
        ]
        if has_admin(uid, "moderator"):
            rows.append([InlineKeyboardButton(text="👑 Admin Panel", callback_data="admin_menu")])
        rows.append([InlineKeyboardButton(text="🚪 Sign Out", callback_data="logout")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def money():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="$1M", callback_data="m_1000000"),
             InlineKeyboardButton(text="$5M", callback_data="m_5000000"),
             InlineKeyboardButton(text="$10M", callback_data="m_10000000")],
            [InlineKeyboardButton(text="$25M", callback_data="m_25000000"),
             InlineKeyboardButton(text="$50M ◆", callback_data="m_50000000")],
            [InlineKeyboardButton(text="✏ Custom", callback_data="m_custom")],
            [InlineKeyboardButton(text="◂ Back", callback_data="back_home")],
        ])

    @staticmethod
    def coins():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="100K", callback_data="c_100000"),
             InlineKeyboardButton(text="250K", callback_data="c_250000"),
             InlineKeyboardButton(text="500K ◆", callback_data="c_500000")],
            [InlineKeyboardButton(text="✏ Custom", callback_data="c_custom")],
            [InlineKeyboardButton(text="◂ Back", callback_data="back_home")],
        ])

    @staticmethod
    def feat():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚗 W16", callback_data="f_w16"),
             InlineKeyboardButton(text="🔊 Horns", callback_data="f_horns"),
             InlineKeyboardButton(text="🛡 NoDmg", callback_data="f_damage")],
            [InlineKeyboardButton(text="⛽ Fuel", callback_data="f_fuel"),
             InlineKeyboardButton(text="💨 Smoke", callback_data="f_smoke"),
             InlineKeyboardButton(text="🎭 Anims", callback_data="f_anims")],
            [InlineKeyboardButton(text="🛞 Wheels", callback_data="f_wheels"),
             InlineKeyboardButton(text="🏠 Houses", callback_data="f_houses"),
             InlineKeyboardButton(text="🎮 Levels", callback_data="f_levels")],
            [InlineKeyboardButton(text="👕 Male", callback_data="f_male"),
             InlineKeyboardButton(text="👚 Female", callback_data="f_female"),
             InlineKeyboardButton(text="🏅 Rank", callback_data="f_rank")],
            [InlineKeyboardButton(text="🎯 Presets", callback_data="menu_preset")],
            [InlineKeyboardButton(text="🚀 ◆ UNLOCK ALL ◆", callback_data="f_all")],
            [InlineKeyboardButton(text="◂ Back", callback_data="back_home")],
        ])

    @staticmethod
    def preset():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌱 Starter", callback_data="preset_starter"),
             InlineKeyboardButton(text="⚡ Pro", callback_data="preset_pro"),
             InlineKeyboardButton(text="💎 Max", callback_data="preset_max")],
            [InlineKeyboardButton(text="◂ Back", callback_data="menu_feat")],
        ])

    @staticmethod
    def sett():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏ Name", callback_data="s_name")],
            [InlineKeyboardButton(text="🆔 Player ID", callback_data="s_pid")],
            [InlineKeyboardButton(text="🏆 Wins", callback_data="s_wins"),
             InlineKeyboardButton(text="😞 Loses", callback_data="s_loses")],
            [InlineKeyboardButton(text="🔧 Fix Account", callback_data="s_fix")],
            [InlineKeyboardButton(text="◂ Back", callback_data="back_home")],
        ])

    @staticmethod
    def backup_menu():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💾 Save", callback_data="b_save")],
            [InlineKeyboardButton(text="📂 Restore", callback_data="b_restore")],
            [InlineKeyboardButton(text="◂ Back", callback_data="back_home")],
        ])

    @staticmethod
    def admin(uid):
        lvl = admin_level(uid)
        b = []
        if lvl >= 5:
            b.append([
                InlineKeyboardButton(text="📊 Stats", callback_data="a_stats"),
                InlineKeyboardButton(text="👥 Users", callback_data="a_users")
            ])
            b.append([InlineKeyboardButton(text="📋 Log", callback_data="a_log")])
        if lvl >= 10:
            b.append([InlineKeyboardButton(text="⏳ Pending", callback_data="a_pend")])
            b.append([
                InlineKeyboardButton(text="✅ Approve", callback_data="a_approve"),
                InlineKeyboardButton(text="🚫 Ban", callback_data="a_ban"),
                InlineKeyboardButton(text="🔓 Unban", callback_data="a_unban")
            ])
            b.append([
                InlineKeyboardButton(text="➕ Add", callback_data="a_adduser"),
                InlineKeyboardButton(text="🗑 Remove", callback_data="a_rmuser"),
                InlineKeyboardButton(text="👢 Kick", callback_data="a_kick")
            ])
            b.append([
                InlineKeyboardButton(text="🔍 Search", callback_data="a_search"),
                InlineKeyboardButton(text="⏰ Expiry", callback_data="a_expiry")
            ])
            b.append([
                InlineKeyboardButton(text="⚠ Warn", callback_data="a_warn"),
                InlineKeyboardButton(text="📝 Note", callback_data="a_note"),
                InlineKeyboardButton(text="ℹ Profile", callback_data="a_profile")
            ])
        if lvl >= 50:
            b.append([
                InlineKeyboardButton(text="💎 +VIP", callback_data="a_addvip"),
                InlineKeyboardButton(text="💎 -VIP", callback_data="a_rmvip")
            ])
            b.append([InlineKeyboardButton(text="📢 Broadcast", callback_data="a_bcast_menu")])
            b.append([
                InlineKeyboardButton(text="📋 Allowed", callback_data="a_list"),
                InlineKeyboardButton(text="📋 Admins", callback_data="a_admins")
            ])
            b.append([InlineKeyboardButton(text="📜 Bcast Log", callback_data="a_bcast_hist")])
        if lvl >= 100:
            b.append([
                InlineKeyboardButton(text="➕ +Admin", callback_data="a_addadm"),
                InlineKeyboardButton(text="➖ -Admin", callback_data="a_rmadm")
            ])
            b.append([
                InlineKeyboardButton(text="🔧 Maint", callback_data="a_maint"),
                InlineKeyboardButton(text="🔄 Reset", callback_data="a_reset")
            ])
        b.append([InlineKeyboardButton(text="◂ Home", callback_data="back_home")])
        return InlineKeyboardMarkup(inline_keyboard=b)

    @staticmethod
    def broadcast_menu():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Text Only", callback_data="bcast_text")],
            [InlineKeyboardButton(text="🖼 Photo+Text", callback_data="bcast_photo")],
            [InlineKeyboardButton(text="📢 All Users", callback_data="bcast_all")],
            [InlineKeyboardButton(text="💎 VIP Only", callback_data="bcast_vip")],
            [InlineKeyboardButton(text="📜 History", callback_data="a_bcast_hist")],
            [InlineKeyboardButton(text="◂ Back", callback_data="admin_menu")],
        ])

    @staticmethod
    def back_admin():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◂ Admin", callback_data="admin_menu")],
        ])

    @staticmethod
    def back_home():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◂ Home", callback_data="back_home")],
        ])

    @staticmethod
    def confirm_logout():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✔ Yes", callback_data="do_logout"),
             InlineKeyboardButton(text="✗ No", callback_data="back_home")],
        ])

    @staticmethod
    def pending_list():
        b = []
        for uid_str, info in list(PENDING.items())[:15]:
            uid = int(uid_str)
            name = info.get("name", f"User {uid}")[:14]
            b.append([
                InlineKeyboardButton(text=f"✔ {name}", callback_data=f"ap_{uid}"),
                InlineKeyboardButton(text="✗", callback_data=f"bn_{uid}")
            ])
        b.append([InlineKeyboardButton(text="◂ Back", callback_data="admin_menu")])
        return InlineKeyboardMarkup(inline_keyboard=b)


# ═══════════════════════════════════════════
#  📋 FSM STATES
# ═══════════════════════════════════════════

class S_Login(StatesGroup):
    email = State()
    password = State()

class S_Money(StatesGroup):
    amount = State()

class S_Coins(StatesGroup):
    amount = State()

class S_Name(StatesGroup):
    name = State()

class S_PID(StatesGroup):
    pid = State()

class S_Wins(StatesGroup):
    val = State()

class S_Loses(StatesGroup):
    val = State()

class S_Admin(StatesGroup):
    approve = State()
    ban = State()
    unban = State()
    bcast_text = State()
    bcast_photo = State()
    bcast_photo_cap = State()
    adduser = State()
    addadm_id = State()
    addadm_lv = State()
    rmadm = State()
    rmuser = State()
    kick = State()
    search = State()
    expiry_id = State()
    expiry_dy = State()
    addvip = State()
    rmvip = State()
    backup_lb = State()
    warn_id = State()
    warn_reason = State()
    note_id = State()
    note_text = State()
    profile_id = State()
    bcast_target = State()


# ═══════════════════════════════════════════
#  🤖 BOT
# ═══════════════════════════════════════════

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())
rt = Router()
dp.include_router(rt)
START_TIME = time.time()


def fmt(n):
    return f"{int(n):,}"


async def notify_admins(text):
    for uid, role in ADMINS.items():
        if ADMIN_LEVELS.get(role, 0) >= 10:
            try:
                await bot.send_message(uid, text)
            except Exception:
                pass


async def anim_result(msg, ok, title, detail="", kb=None):
    frames = ["⬜", "🟩", "✅"] if ok else ["⬜", "🟥", "❌"]
    icon = "✅" if ok else "❌"
    for f in frames:
        try:
            await msg.edit_text(f"  {f} {title}")
        except Exception:
            pass
        await asyncio.sleep(0.2)
    final = f"{T.H}\n  {icon}  {title}\n{T.H}"
    if detail:
        final += f"\n\n  {detail}"
    try:
        await msg.edit_text(final, reply_markup=kb)
    except Exception:
        pass


# ═══════════════════════════════════════════
#  🚀 /start
# ═══════════════════════════════════════════

@rt.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    name = msg.from_user.full_name
    un = msg.from_user.username or ""

    STORE.setdefault("users", {})[str(uid)] = {
        "name": name, "username": un,
        "last_seen": datetime.now().isoformat()
    }
    save_store(STORE)

    if is_banned(uid):
        await msg.answer(T.banned())
        return

    if is_maintenance() and not has_admin(uid, "moderator"):
        await msg.answer(T.maintenance())
        return

    if is_expired(uid):
        store_remove_user(uid)
        await msg.answer(
            f"{T.H}\n  ⏰  𝗘𝗫𝗣𝗜𝗥𝗘𝗗\n{T.H}\n\n"
            "  ▸ Access expired.\n  ▸ Contact admin.",
            reply_markup=K.no_access()
        )
        return

    if not is_allowed(uid):
        await msg.answer(T.no_access(), reply_markup=K.no_access())
        return

    td = nuker.get_token_data(uid)
    if td:
        await msg.answer(T.home(td, uid), reply_markup=K.home(uid))
    else:
        welcome_txt = T.welcome_approved(name, un, uid)
        try:
            await msg.answer_photo(
                photo=WELCOME_PHOTO,
                caption=welcome_txt,
                reply_markup=K.login()
            )
        except:
            await msg.answer(welcome_txt, reply_markup=K.login())


# ═══════════════════════════════════════════
#  🔑 ACCESS FLOW
# ═══════════════════════════════════════════

@rt.callback_query(F.data == "get_access")
async def cb_get_access(cb: CallbackQuery):
    if is_banned(cb.from_user.id):
        await cb.message.edit_text(T.banned())
        await cb.answer()
        return
    await cb.message.edit_text(T.get_access_menu(), reply_markup=K.access_menu())
    await cb.answer()


@rt.callback_query(F.data == "send_request")
async def cb_send_request(cb: CallbackQuery):
    uid = cb.from_user.id
    name = cb.from_user.full_name
    un = cb.from_user.username or ""

    if is_allowed(uid):
        await cb.answer("✅ Already approved!")
        return

    if is_pending(uid):
        await cb.message.edit_text(T.already_pending(), reply_markup=K.after_request())
        await cb.answer("⏳ Pending")
        return

    store_add_pending(uid, name, un)
    await notify_admins(
        f"{T.H}\n  🔔  𝗡𝗘𝗪 𝗥𝗘𝗤𝗨𝗘𝗦𝗧\n{T.H}\n\n"
        f"  👤 {name}\n  🆔 <code>{uid}</code>\n"
        f"  📱 @{un or 'none'}\n\n  ▸ /admin → Pending"
    )
    await cb.message.edit_text(T.request_sent(), reply_markup=K.after_request())
    await cb.answer("📩 Sent!")


@rt.callback_query(F.data == "msg_admin")
async def cb_msg_admin(cb: CallbackQuery):
    try:
        owner = await bot.get_chat(OWNER_ID)
        un = owner.username
        txt = (
            f"{T.H}\n  💬  𝗖𝗢𝗡𝗧𝗔𝗖𝗧\n{T.H}\n\n"
            f"  ▸ @{un}\n"
            f"  ▸ ID: <code>{cb.from_user.id}</code>"
        )
        await cb.message.edit_text(txt, reply_markup=K.after_request())
    except:
        await cb.message.edit_text(
            f"  ▸ ID: <code>{cb.from_user.id}</code>",
            reply_markup=K.after_request()
        )
    await cb.answer()


@rt.callback_query(F.data == "check_status")
async def cb_check_status(cb: CallbackQuery):
    uid = cb.from_user.id
    if is_allowed(uid):
        td = nuker.get_token_data(uid)
        name = cb.from_user.full_name
        un = cb.from_user.username or ""
        if td:
            await cb.message.edit_text(T.home(td, uid), reply_markup=K.home(uid))
        else:
            await cb.message.edit_text(
                T.welcome_approved(name, un, uid), reply_markup=K.login()
            )
        await cb.answer("✅ Approved!")
    elif is_banned(uid):
        await cb.message.edit_text(T.banned())
        await cb.answer("🚫")
    else:
        await cb.message.edit_text(T.already_pending(), reply_markup=K.after_request())
        await cb.answer("⏳")


@rt.callback_query(F.data == "back_start")
async def cb_back_start(cb: CallbackQuery):
    uid = cb.from_user.id
    if is_allowed(uid):
        td = nuker.get_token_data(uid)
        name = cb.from_user.full_name
        un = cb.from_user.username or ""
        if td:
            await cb.message.edit_text(T.home(td, uid), reply_markup=K.home(uid))
        else:
            await cb.message.edit_text(
                T.welcome_approved(name, un, uid), reply_markup=K.login()
            )
    else:
        await cb.message.edit_text(T.no_access(), reply_markup=K.no_access())
    await cb.answer()


# ═══════════════════════════════════════════
#  🔐 LOGIN
# ═══════════════════════════════════════════

@rt.callback_query(F.data == "login")
async def cb_login(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    if is_banned(uid):
        await cb.message.edit_text(T.banned())
        await cb.answer()
        return
    if not is_allowed(uid):
        await cb.message.edit_text(T.no_access(), reply_markup=K.no_access())
        await cb.answer()
        return
    if is_maintenance() and not has_admin(uid, "moderator"):
        await cb.message.edit_text(T.maintenance())
        await cb.answer()
        return
    await state.set_state(S_Login.email)
    await cb.message.edit_text(T.login_email(), reply_markup=K.cancel())
    await cb.answer()


@rt.message(S_Login.email)
async def p_email(msg: Message, state: FSMContext):
    em = msg.text.strip()
    if "@" not in em or "." not in em:
        await msg.answer("  ✗ Invalid email.", reply_markup=K.cancel())
        return
    await state.update_data(email=em)
    await state.set_state(S_Login.password)
    await msg.answer(T.login_pass(), reply_markup=K.cancel())


@rt.message(S_Login.password)
async def p_pass(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    pw = msg.text.strip()
    try:
        await msg.delete()
    except Exception:
        pass

    data = await state.get_data()
    em = data["email"]
    ld = await msg.answer("  ⏳ Signing in...")

    try:
        r = await nuker.account_login(em, pw)
        if r.get("ok"):
            nuker.save_token(uid, r["auth"], em, pw, r.get("refresh_token", ""))

            # CRITICAL FIX: Load real account data right after login
            loading_msg = await msg.answer("  ⏳ Loading your account data...")
            loaded = await nuker.load_account(uid, force=True)
            try:
                await loading_msg.delete()
            except:
                pass

            STORE["stats"]["total_logins"] = STORE["stats"].get("total_logins", 0) + 1
            save_store(STORE)
            update_daily_stats("logins")
            await state.clear()

            if loaded:
                await anim_result(
                    ld, True, "𝗟𝗢𝗚𝗜𝗡 𝗦𝗨𝗖𝗖𝗘𝗦𝗦",
                    f"📧 {em}\n  ✔ Account data loaded",
                    K.home(uid)
                )
            else:
                await anim_result(
                    ld, True, "𝗟𝗢𝗚𝗜𝗡 𝗦𝗨𝗖𝗖𝗘𝗦𝗦",
                    f"📧 {em}",
                    K.home(uid)
                )

            td = nuker.get_token_data(uid)
            name = msg.from_user.full_name
            try:
                await bot.send_message(
                    OWNER_ID,
                    f"◈ 🔔 Login\n  👤 {name}\n"
                    f"  🆔 <code>{uid}</code>\n  📧 {em}"
                )
            except:
                pass
        else:
            await state.clear()
            await ld.edit_text(
                T.login_fail(r.get("message", "LOGIN_FAILED")),
                reply_markup=K.login()
            )
    except Exception:
        await state.clear()
        await ld.edit_text(T.login_fail("NETWORK_ERROR"), reply_markup=K.login())


# ═══════════════════════════════════════════
#  🏠 NAVIGATION
# ═══════════════════════════════════════════

@rt.callback_query(F.data == "back_home")
async def cb_back_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = cb.from_user.id
    td = nuker.get_token_data(uid)
    name = cb.from_user.full_name
    un = cb.from_user.username or ""
    if not td:
        await cb.message.edit_text(
            T.welcome_approved(name, un, uid), reply_markup=K.login()
        )
    else:
        await cb.message.edit_text(T.home(td, uid), reply_markup=K.home(uid))
    await cb.answer()


@rt.callback_query(F.data == "refresh")
async def cb_refresh(cb: CallbackQuery):
    uid = cb.from_user.id
    td = nuker.get_token_data(uid)
    name = cb.from_user.full_name
    un = cb.from_user.username or ""
    if not td:
        await cb.message.edit_text(
            T.welcome_approved(name, un, uid), reply_markup=K.login()
        )
    else:
        await cb.message.edit_text(T.home(td, uid), reply_markup=K.home(uid))
    await cb.answer("🔄")


@rt.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = cb.from_user.id
    td = nuker.get_token_data(uid)
    name = cb.from_user.full_name
    un = cb.from_user.username or ""
    if td:
        await cb.message.edit_text(T.home(td, uid), reply_markup=K.home(uid))
    else:
        await cb.message.edit_text(
            T.welcome_approved(name, un, uid), reply_markup=K.login()
        )
    await cb.answer("✗")


# ═══════════════════════════════════════════
#  🚪 LOGOUT
# ═══════════════════════════════════════════

@rt.callback_query(F.data == "logout")
async def cb_logout(cb: CallbackQuery):
    await cb.message.edit_text(
        T.hdr("🚪", "𝗦𝗜𝗚𝗡 𝗢𝗨𝗧?") + "\n\n  ▸ Confirm?",
        reply_markup=K.confirm_logout()
    )
    await cb.answer()


@rt.callback_query(F.data == "do_logout")
async def cb_do_logout(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    nuker.delete_token(cb.from_user.id)
    await cb.message.edit_text(
        T.hdr("✅", "𝗦𝗜𝗚𝗡𝗘𝗗 𝗢𝗨𝗧") + "\n\n  ▸ Done.",
        reply_markup=K.login()
    )
    await cb.answer("✅")


# ═══════════════════════════════════════════
#  💰 MONEY
# ═══════════════════════════════════════════

@rt.callback_query(F.data == "menu_money")
async def cb_money_menu(cb: CallbackQuery):
    if not nuker.get_token(cb.from_user.id):
        await cb.answer("✗ Login first!", show_alert=True)
        return
    ok, w = check_rate_limit(cb.from_user.id)
    if not ok:
        await cb.answer(f"⏳ Wait {w}s", show_alert=True)
        return
    await cb.message.edit_text(T.money_menu(), reply_markup=K.money())
    await cb.answer()


@rt.callback_query(F.data.startswith("m_"))
async def cb_money(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    v = cb.data[2:]
    if v == "custom":
        await state.set_state(S_Money.amount)
        await cb.message.edit_text(
            T.hdr("💰", "𝗖𝗨𝗦𝗧𝗢𝗠") + f"\n\n  ✏ Enter (1-{fmt(MAX_MONEY)}):",
            reply_markup=K.cancel()
        )
        await cb.answer()
        return
    m = await cb.message.edit_text(f"  ⏳ Loading account & setting ${fmt(int(v))}...")
    await cb.answer()
    r = await nuker.set_money(uid, int(v))
    await anim_result(
        m, r.get("ok"),
        "𝗠𝗢𝗡𝗘𝗬 𝗦𝗘𝗧" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        f"💰 ${fmt(int(v))}" if r.get("ok") else r.get("message", ""),
        K.back_home()
    )


@rt.message(S_Money.amount)
async def p_money(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    try:
        a = int(msg.text.strip().replace(",", ""))
        if a < 1 or a > MAX_MONEY:
            raise ValueError
    except Exception:
        await msg.answer(f"  ✗ Enter 1-{fmt(MAX_MONEY)}", reply_markup=K.cancel())
        return
    await state.clear()
    ld = await msg.answer(f"  ⏳ Loading account & setting ${fmt(a)}...")
    r = await nuker.set_money(uid, a)
    await anim_result(
        ld, r.get("ok"),
        "𝗠𝗢𝗡𝗘𝗬 𝗦𝗘𝗧" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        f"💰 ${fmt(a)}" if r.get("ok") else "",
        K.back_home()
    )


# ═══════════════════════════════════════════
#  🪙 COINS
# ═══════════════════════════════════════════

@rt.callback_query(F.data == "menu_coins")
async def cb_coins_menu(cb: CallbackQuery):
    if not nuker.get_token(cb.from_user.id):
        await cb.answer("✗ Login first!", show_alert=True)
        return
    await cb.message.edit_text(T.coins_menu(), reply_markup=K.coins())
    await cb.answer()


@rt.callback_query(F.data.startswith("c_"))
async def cb_coins(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    v = cb.data[2:]
    if v == "custom":
        await state.set_state(S_Coins.amount)
        await cb.message.edit_text(
            T.hdr("🪙", "𝗖𝗨𝗦𝗧𝗢𝗠") + f"\n\n  ✏ Enter (1-{fmt(MAX_COIN)}):",
            reply_markup=K.cancel()
        )
        await cb.answer()
        return
    m = await cb.message.edit_text(f"  ⏳ Loading account & setting {fmt(int(v))} coins...")
    await cb.answer()
    r = await nuker.set_coin(uid, int(v))
    await anim_result(
        m, r.get("ok"),
        "𝗖𝗢𝗜𝗡𝗦 𝗦𝗘𝗧" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        f"🪙 {fmt(int(v))}" if r.get("ok") else "",
        K.back_home()
    )


@rt.message(S_Coins.amount)
async def p_coins(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    try:
        a = int(msg.text.strip().replace(",", ""))
        if a < 1 or a > MAX_COIN:
            raise ValueError
    except Exception:
        await msg.answer(f"  ✗ Enter 1-{fmt(MAX_COIN)}", reply_markup=K.cancel())
        return
    await state.clear()
    ld = await msg.answer(f"  ⏳ Loading account & setting {fmt(a)} coins...")
    r = await nuker.set_coin(uid, a)
    await anim_result(
        ld, r.get("ok"),
        "𝗖𝗢𝗜𝗡𝗦 𝗦𝗘𝗧" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        f"🪙 {fmt(a)}" if r.get("ok") else "",
        K.back_home()
    )


# ═══════════════════════════════════════════
#  ⚡ FEATURES
# ═══════════════════════════════════════════

FEAT_MAP = {
    "f_w16":   ("🚗 W16",    nuker.unlock_w16),
    "f_horns": ("🔊 Horns",  nuker.unlock_horns),
    "f_damage":("🛡 NoDmg",  nuker.disable_damage),
    "f_fuel":  ("⛽ Fuel",   nuker.unlimited_fuel),
    "f_smoke": ("💨 Smoke",  nuker.unlock_smoke),
    "f_anims": ("🎭 Anims",  nuker.unlock_animations),
    "f_wheels":("🛞 Wheels", nuker.unlock_wheels),
    "f_houses":("🏠 Houses", nuker.unlock_houses),
    "f_levels":("🎮 Levels", nuker.complete_all_levels),
    "f_male":  ("👕 Male",   nuker.unlock_equipments_male),
    "f_female":("👚 Female", nuker.unlock_equipments_female),
    "f_rank":  ("🏅 Rank",   nuker.set_rank),
}


@rt.callback_query(F.data == "menu_feat")
async def cb_feat_menu(cb: CallbackQuery):
    if not nuker.get_token(cb.from_user.id):
        await cb.answer("✗ Login first!", show_alert=True)
        return
    await cb.message.edit_text(T.features_menu(), reply_markup=K.feat())
    await cb.answer()


@rt.callback_query(F.data == "menu_preset")
async def cb_preset_menu(cb: CallbackQuery):
    if not nuker.get_token(cb.from_user.id):
        await cb.answer("✗ Login first!", show_alert=True)
        return
    await cb.message.edit_text(
        f"{T.H}\n  🎯  𝗣𝗥𝗘𝗦𝗘𝗧𝗦\n{T.H}\n\n"
        "  🌱 Starter — $1M + 100K\n"
        "  ⚡ Pro — $25M + 250K + Rank\n"
        "  💎 Max — $50M + 500K + All",
        reply_markup=K.preset()
    )
    await cb.answer()


@rt.callback_query(F.data.in_({"preset_starter", "preset_pro", "preset_max"}))
async def cb_preset(cb: CallbackQuery):
    uid = cb.from_user.id
    p = cb.data.split("_")[1]
    m = await cb.message.edit_text(f"  ⏳ Loading account & applying {p}...")
    await cb.answer()
    ok = True
    if p == "starter":
        r1 = await nuker.set_money(uid, 1_000_000)
        r2 = await nuker.set_coin(uid, 100_000)
        ok = r1.get("ok") and r2.get("ok")
    elif p == "pro":
        r1 = await nuker.set_money(uid, 25_000_000)
        r2 = await nuker.set_coin(uid, 250_000)
        r3 = await nuker.set_rank(uid)
        ok = r1.get("ok") and r2.get("ok")
    elif p == "max":
        r1 = await nuker.set_money(uid, MAX_MONEY)
        r2 = await nuker.set_coin(uid, MAX_COIN)
        r3 = await nuker.set_rank(uid)
        r4 = await nuker.unlock_w16(uid)
        r5 = await nuker.disable_damage(uid)
        ok = r1.get("ok") and r2.get("ok")
    await anim_result(
        m, ok,
        f"𝗣𝗥𝗘𝗦𝗘𝗧 {p.upper()}" if ok else "𝗙𝗔𝗜𝗟𝗘𝗗",
        "", K.back_home()
    )


@rt.callback_query(F.data.in_(set(FEAT_MAP.keys())))
async def cb_feat(cb: CallbackQuery):
    uid = cb.from_user.id
    fname, fn = FEAT_MAP[cb.data]
    m = await cb.message.edit_text(f"  ⏳ Loading account & applying {fname}...")
    await cb.answer()
    r = await fn(uid)
    if r.get("ok"):
        STORE["stats"]["total_unlocks"] = STORE["stats"].get("total_unlocks", 0) + 1
        save_store(STORE)
        update_daily_stats("unlocks")
    await anim_result(
        m, r.get("ok"),
        f"{fname} ✔" if r.get("ok") else f"{fname} ✗",
        "", K.feat()
    )


@rt.callback_query(F.data == "f_all")
async def cb_feat_all(cb: CallbackQuery):
    uid = cb.from_user.id
    if not nuker.get_token(uid):
        await cb.answer("✗ Login first!", show_alert=True)
        return
    await cb.answer()

    m = await cb.message.edit_text(
        f"{T.H}\n  🚀  𝗨𝗡𝗟𝗢𝗖𝗞𝗜𝗡𝗚 𝗔𝗟𝗟\n{T.H}\n\n"
        "  ⏳ Loading account first..."
    )

    # Load account ONCE before all operations
    await nuker.load_account(uid, force=True)

    ALL_FEATS = list(FEAT_MAP.values())
    total = len(ALL_FEATS)
    done = 0
    failed = 0
    results = []

    for i, (name, fn) in enumerate(ALL_FEATS):
        pct = int((i / total) * 100)
        filled = int(pct / 7)
        bar = "▰" * filled + "▱" * (15 - filled)
        try:
            await m.edit_text(
                f"{T.H}\n  🚀  𝗨𝗡𝗟𝗢𝗖𝗞𝗜𝗡𝗚 𝗔𝗟𝗟\n{T.H}\n\n"
                f"  [{bar}] {pct}%\n"
                f"  ✔{done} ✗{failed} ▸{i}/{total}\n\n"
                f"  ⏳ {name}\n\n"
                + "\n".join(results[-3:])
            )
        except Exception:
            pass

        r = await fn(uid)
        if r.get("ok"):
            done += 1
            results.append(f"  ✔ {name}")
            STORE["stats"]["total_unlocks"] = STORE["stats"].get("total_unlocks", 0) + 1
        else:
            failed += 1
            results.append(f"  ✗ {name}")
        await asyncio.sleep(0.3)

    save_store(STORE)
    try:
        await m.edit_text(
            f"{T.H}\n  🎉  𝗗𝗢𝗡𝗘\n{T.H}\n\n"
            f"  [▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰] 100%\n\n"
            f"  ✔ {done}/{total}  ✗ {failed}/{total}\n\n"
            + "\n".join(results),
            reply_markup=K.back_home()
        )
    except Exception:
        await cb.message.answer("  ✔ Done.", reply_markup=K.back_home())


# ═══════════════════════════════════════════
#  🔧 SETTINGS
# ═══════════════════════════════════════════

@rt.callback_query(F.data == "menu_set")
async def cb_set_menu(cb: CallbackQuery):
    if not nuker.get_token(cb.from_user.id):
        await cb.answer("✗ Login first!", show_alert=True)
        return
    await cb.message.edit_text(T.settings_menu(), reply_markup=K.sett())
    await cb.answer()


@rt.callback_query(F.data == "s_name")
async def cb_s_name(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S_Name.name)
    await cb.message.edit_text(
        T.hdr("✏", "𝗡𝗔𝗠𝗘") + "\n\n  ✏ Enter new name:",
        reply_markup=K.cancel()
    )
    await cb.answer()


@rt.message(S_Name.name)
async def p_name(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    name = msg.text.strip()
    if not name or len(name) > 100:
        await msg.answer("  ✗ 1-100 chars.", reply_markup=K.cancel())
        return
    await state.clear()
    ld = await msg.answer("  ⏳ Loading account & setting name...")
    r = await nuker.set_player_name(uid, name)
    await anim_result(
        ld, r.get("ok"),
        "𝗡𝗔𝗠𝗘 𝗦𝗘𝗧" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        f"✔ {name}" if r.get("ok") else "",
        K.back_home()
    )


@rt.callback_query(F.data == "s_pid")
async def cb_s_pid(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S_PID.pid)
    await cb.message.edit_text(
        T.hdr("🆔", "𝗣𝗟𝗔𝗬𝗘𝗥 𝗜𝗗") + "\n\n  ✏ Enter ID (4-100):",
        reply_markup=K.cancel()
    )
    await cb.answer()


@rt.message(S_PID.pid)
async def p_pid(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    pid = msg.text.strip()
    clean = re.sub(r'\[\w+\]', '', pid)
    if not clean or len(clean) < 4 or len(clean) > 100 or not clean.isalnum():
        await msg.answer("  ✗ 4-100 alphanumeric.", reply_markup=K.cancel())
        return
    await state.clear()
    ld = await msg.answer("  ⏳ Loading account & setting ID...")
    r = await nuker.set_player_id(uid, pid)
    await anim_result(
        ld, r.get("ok"),
        "𝗜𝗗 𝗦𝗘𝗧" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        f"✔ {pid.upper()}" if r.get("ok") else "",
        K.back_home()
    )


@rt.callback_query(F.data == "s_wins")
async def cb_s_wins(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S_Wins.val)
    await cb.message.edit_text(
        T.hdr("🏆", "𝗪𝗜𝗡𝗦") + "\n\n  ✏ Enter wins:",
        reply_markup=K.cancel()
    )
    await cb.answer()


@rt.message(S_Wins.val)
async def p_wins(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    try:
        v = int(msg.text.strip())
        assert v >= 0
    except Exception:
        await msg.answer("  ✗ Invalid.", reply_markup=K.cancel())
        return
    await state.clear()
    ld = await msg.answer("  ⏳ Loading account & setting wins...")
    r = await nuker.set_race_wins(uid, v)
    await anim_result(
        ld, r.get("ok"),
        "𝗪𝗜𝗡𝗦 𝗦𝗘𝗧" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        f"✔ {fmt(v)}" if r.get("ok") else "",
        K.back_home()
    )


@rt.callback_query(F.data == "s_loses")
async def cb_s_loses(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S_Loses.val)
    await cb.message.edit_text(
        T.hdr("😞", "𝗟𝗢𝗦𝗘𝗦") + "\n\n  ✏ Enter loses:",
        reply_markup=K.cancel()
    )
    await cb.answer()


@rt.message(S_Loses.val)
async def p_loses(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    try:
        v = int(msg.text.strip())
        assert v >= 0
    except Exception:
        await msg.answer("  ✗ Invalid.", reply_markup=K.cancel())
        return
    await state.clear()
    ld = await msg.answer("  ⏳ Loading account & setting loses...")
    r = await nuker.set_race_loses(uid, v)
    await anim_result(
        ld, r.get("ok"),
        "𝗟𝗢𝗦𝗘𝗦 𝗦𝗘𝗧" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        f"✔ {fmt(v)}" if r.get("ok") else "",
        K.back_home()
    )


@rt.callback_query(F.data == "s_fix")
async def cb_s_fix(cb: CallbackQuery):
    uid = cb.from_user.id
    m = await cb.message.edit_text("  ⏳ Loading account & fixing...")
    await cb.answer()
    r = await nuker.fix_account_data(uid)
    await anim_result(
        m, r.get("ok"),
        "𝗙𝗜𝗫𝗘𝗗" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        f"✔ Bugs: {r.get('bugs_fixed', 0)}" if r.get("ok") else "",
        K.back_home()
    )


# ═══════════════════════════════════════════
#  💾 BACKUP
# ═══════════════════════════════════════════

@rt.callback_query(F.data == "menu_backup")
async def cb_backup_menu(cb: CallbackQuery):
    if not nuker.get_token(cb.from_user.id):
        await cb.answer("✗ Login first!", show_alert=True)
        return
    await cb.message.edit_text(
        f"{T.H}\n  💾  𝗕𝗔𝗖𝗞𝗨𝗣\n{T.H}\n\n"
        "  ▸ Save or restore\n  ▸ Max 5 backups",
        reply_markup=K.backup_menu()
    )
    await cb.answer()


@rt.callback_query(F.data == "b_save")
async def cb_b_save(cb: CallbackQuery, state: FSMContext):
    await state.set_state(S_Admin.backup_lb)
    await cb.message.edit_text(
        T.hdr("💾", "𝗦𝗔𝗩𝗘") + "\n\n  ✏ Label:",
        reply_markup=K.cancel()
    )
    await cb.answer()


@rt.message(S_Admin.backup_lb)
async def p_backup_lb(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    label = msg.text.strip()[:30]
    await state.clear()
    # Load real data before saving backup
    await nuker.load_account(uid)
    td = nuker.get_token_data(uid)
    em = td.get("email") if td else None
    d = nuker.get_user_template(uid, em)
    nuker.save_backup(uid, label, d)
    await msg.answer(
        T.hdr("✅", "𝗦𝗔𝗩𝗘𝗗") + f"\n\n  ✔ {label}",
        reply_markup=K.back_home()
    )


@rt.callback_query(F.data == "b_restore")
async def cb_b_restore(cb: CallbackQuery):
    uid = cb.from_user.id
    bkps = nuker.get_backups(uid)
    if not bkps:
        await cb.message.edit_text(
            T.hdr("💾", "𝗡𝗢𝗡𝗘") + "\n\n  ▸ No backups.",
            reply_markup=K.back_home()
        )
        await cb.answer()
        return
    rows = []
    for b in bkps:
        rows.append([InlineKeyboardButton(
            text=f"📂 {b['label']} ({b['time'][:10]})",
            callback_data=f"rb_{b['id']}"
        )])
    rows.append([InlineKeyboardButton(text="◂ Back", callback_data="menu_backup")])
    await cb.message.edit_text(
        T.hdr("📂", "𝗥𝗘𝗦𝗧𝗢𝗥𝗘") + "\n\n  ▸ Select:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await cb.answer()


@rt.callback_query(F.data.startswith("rb_"))
async def cb_rb(cb: CallbackQuery):
    uid = cb.from_user.id
    bid = int(cb.data[3:])
    m = await cb.message.edit_text("  ⏳ Restoring...")
    await cb.answer()
    data = nuker.restore_backup(uid, bid)
    if not data:
        await anim_result(m, False, "𝗙𝗔𝗜𝗟𝗘𝗗", "", K.back_home())
        return
    r = await nuker._save(uid, data)
    await anim_result(
        m, r.get("ok"),
        "𝗥𝗘𝗦𝗧𝗢𝗥𝗘𝗗" if r.get("ok") else "𝗙𝗔𝗜𝗟𝗘𝗗",
        "", K.back_home()
    )


# ═══════════════════════════════════════════
#  👑 ADMIN PANEL
# ═══════════════════════════════════════════

@rt.message(Command("admin"))
async def cmd_admin(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    if not has_admin(uid, "moderator"):
        await msg.answer("  ✗ No admin access.")
        return
    await msg.answer(T.admin_panel(uid), reply_markup=K.admin(uid))


@rt.callback_query(F.data == "admin_menu")
async def cb_admin_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = cb.from_user.id
    if not has_admin(uid, "moderator"):
        await cb.answer("✗ No access!", show_alert=True)
        return
    await cb.message.edit_text(T.admin_panel(uid), reply_markup=K.admin(uid))
    await cb.answer()


@rt.callback_query(F.data == "a_stats")
async def cb_a_stats(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "moderator"):
        await cb.answer("✗", show_alert=True)
        return
    await cb.message.edit_text(T.stats(), reply_markup=K.back_admin())
    await cb.answer()


@rt.callback_query(F.data == "a_log")
async def cb_a_log(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "moderator"):
        await cb.answer("✗", show_alert=True)
        return
    logs = STORE.get("admin_log", [])[:10]
    txt = f"{T.H}\n  📋  𝗟𝗢𝗚\n{T.H}\n\n"
    if not logs:
        txt += "  ▸ No activity."
    for e in logs:
        t = e.get("time", "")[:16].replace("T", " ")
        txt += f"  ◆ {t}\n  ▸ {e.get('action', '')} {e.get('target', '')}\n\n"
    await cb.message.edit_text(txt, reply_markup=K.back_admin())
    await cb.answer()


@rt.callback_query(F.data == "a_users")
async def cb_a_users(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "moderator"):
        await cb.answer("✗", show_alert=True)
        return
    users = STORE.get("users", {})
    lb = {"owner": "👑", "superadmin": "⭐", "admin": "🛡", "moderator": "👮", "": ""}
    txt = f"{T.H}\n  👥  𝗨𝗦𝗘𝗥𝗦\n{T.H}\n\n"
    for uid in sorted(ALLOWED_USERS)[:20]:
        info = users.get(str(uid), {})
        name = info.get("name", f"User {uid}")[:14]
        role = ADMINS.get(uid, "")
        badge = lb.get(role, "")
        vip = "💎" if uid in VIP_USERS else ""
        txt += f"  {badge}{vip} {name}  <code>{uid}</code>\n"
    if len(ALLOWED_USERS) > 20:
        txt += f"\n  ▸ +{len(ALLOWED_USERS) - 20} more"
    await cb.message.edit_text(txt, reply_markup=K.back_admin())
    await cb.answer()


@rt.callback_query(F.data == "a_pend")
async def cb_a_pend(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    if not PENDING:
        await cb.message.edit_text(
            T.hdr("✅", "𝗡𝗢 𝗣𝗘𝗡𝗗𝗜𝗡𝗚") + "\n\n  ▸ Empty.",
            reply_markup=K.back_admin()
        )
    else:
        await cb.message.edit_text(
            T.hdr("⏳", "𝗣𝗘𝗡𝗗𝗜𝗡𝗚") + f"\n\n  ▸ {len(PENDING)} requests:",
            reply_markup=K.pending_list()
        )
    await cb.answer()


@rt.callback_query(F.data.startswith("ap_"))
async def cb_do_ap(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    uid = int(cb.data[3:])
    name = PENDING.get(str(uid), {}).get("name", f"User {uid}")
    un = PENDING.get(str(uid), {}).get("username", "")
    store_allow(uid, name)
    store_remove_pending(uid)
    admin_log(cb.from_user.id, "APPROVED", str(uid))
    welcome = T.welcome_approved(name, un, uid)
    try:
        await bot.send_photo(uid, photo=WELCOME_PHOTO, caption=welcome, reply_markup=K.login())
    except:
        try:
            await bot.send_message(uid, welcome, reply_markup=K.login())
        except:
            pass
    await cb.answer(f"✔ {uid}")
    if not PENDING:
        await cb.message.edit_text("  ✔ All done!", reply_markup=K.back_admin())
    else:
        await cb.message.edit_text(
            T.hdr("⏳", "𝗣𝗘𝗡𝗗𝗜𝗡𝗚") + f"\n\n  ▸ {len(PENDING)}",
            reply_markup=K.pending_list()
        )


@rt.callback_query(F.data.startswith("bn_"))
async def cb_do_bn(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    uid = int(cb.data[3:])
    store_ban(uid)
    store_remove_pending(uid)
    admin_log(cb.from_user.id, "BANNED", str(uid))
    try:
        await bot.send_message(uid, T.banned())
    except:
        pass
    await cb.answer(f"✗ {uid}")
    if not PENDING:
        await cb.message.edit_text("  ✔ All done!", reply_markup=K.back_admin())
    else:
        await cb.message.edit_text(
            T.hdr("⏳", "𝗣𝗘𝗡𝗗𝗜𝗡𝗚") + f"\n\n  ▸ {len(PENDING)}",
            reply_markup=K.pending_list()
        )


@rt.callback_query(F.data == "a_approve")
async def cb_a_approve(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.approve)
    await cb.message.edit_text(
        T.hdr("✅", "𝗔𝗣𝗣𝗥𝗢𝗩𝗘") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.approve)
async def p_approve(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    store_allow(uid)
    store_remove_pending(uid)
    admin_log(msg.from_user.id, "APPROVED", str(uid))
    name = STORE.get("users", {}).get(str(uid), {}).get("name", "User")
    un = STORE.get("users", {}).get(str(uid), {}).get("username", "")
    try:
        await bot.send_photo(uid, photo=WELCOME_PHOTO,
                             caption=T.welcome_approved(name, un, uid),
                             reply_markup=K.login())
    except:
        try:
            await bot.send_message(uid, "  ✔ Approved! /start", reply_markup=K.login())
        except:
            pass
    await msg.answer(f"  ✔ <code>{uid}</code> approved!", reply_markup=K.back_admin())


@rt.callback_query(F.data == "a_ban")
async def cb_a_ban(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.ban)
    await cb.message.edit_text(
        T.hdr("🚫", "𝗕𝗔𝗡") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.ban)
async def p_ban(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    store_ban(uid)
    nuker.delete_token(uid)
    admin_log(msg.from_user.id, "BANNED", str(uid))
    await msg.answer(f"  🚫 <code>{uid}</code> banned!", reply_markup=K.back_admin())


@rt.callback_query(F.data == "a_unban")
async def cb_a_unban(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.unban)
    await cb.message.edit_text(
        T.hdr("🔓", "𝗨𝗡𝗕𝗔𝗡") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.unban)
async def p_unban(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    store_unban(uid)
    admin_log(msg.from_user.id, "UNBANNED", str(uid))
    await msg.answer(f"  🔓 <code>{uid}</code> unbanned!", reply_markup=K.back_admin())


@rt.callback_query(F.data == "a_adduser")
async def cb_a_adduser(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.adduser)
    await cb.message.edit_text(
        T.hdr("➕", "𝗔𝗗𝗗 𝗨𝗦𝗘𝗥") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.adduser)
async def p_adduser(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    store_allow(uid)
    store_remove_pending(uid)
    admin_log(msg.from_user.id, "ADDED", str(uid))
    name = STORE.get("users", {}).get(str(uid), {}).get("name", "User")
    un = STORE.get("users", {}).get(str(uid), {}).get("username", "")
    try:
        await bot.send_photo(uid, photo=WELCOME_PHOTO,
                             caption=T.welcome_approved(name, un, uid),
                             reply_markup=K.login())
    except:
        try:
            await bot.send_message(uid, "  ✔ Added! /start", reply_markup=K.login())
        except:
            pass
    await msg.answer(f"  ✔ <code>{uid}</code> added!", reply_markup=K.back_admin())


@rt.callback_query(F.data == "a_kick")
async def cb_a_kick(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.kick)
    await cb.message.edit_text(
        T.hdr("👢", "𝗞𝗜𝗖𝗞") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.kick)
async def p_kick(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    if uid == OWNER_ID:
        await msg.answer("  ✗ Can't kick owner!", reply_markup=K.back_admin())
        return
    store_remove_user(uid)
    nuker.delete_token(uid)
    admin_log(msg.from_user.id, "KICKED", str(uid))
    try:
        await bot.send_message(
            uid, T.hdr("👢", "𝗞𝗜𝗖𝗞𝗘𝗗") + "\n\n  ▸ Access removed."
        )
    except:
        pass
    await msg.answer(f"  👢 <code>{uid}</code> kicked!", reply_markup=K.back_admin())


@rt.callback_query(F.data == "a_search")
async def cb_a_search(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.search)
    await cb.message.edit_text(
        T.hdr("🔍", "𝗦𝗘𝗔𝗥𝗖𝗛") + "\n\n  ✏ ID or name:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.search)
async def p_search(msg: Message, state: FSMContext):
    await state.clear()
    q = msg.text.strip().lower()
    users = STORE.get("users", {})
    found = []
    for uid_str, info in users.items():
        name = info.get("name", "").lower()
        uid = int(uid_str)
        if q in uid_str or q in name:
            role = ADMINS.get(uid, "user")
            vip = "💎" if uid in VIP_USERS else ""
            st = "🚫" if uid in BANNED else ("✔" if uid in ALLOWED_USERS else "⏳")
            warns = len(store_get_warnings(uid))
            found.append(
                f"  {st}{vip} {info.get('name', '?')}\n"
                f"  ◆ <code>{uid}</code> {role} ⚠{warns}\n"
            )
    txt = f"{T.H}\n  🔍  𝗥𝗘𝗦𝗨𝗟𝗧𝗦\n{T.H}\n\n"
    txt += "\n".join(found[:5]) if found else "  ▸ No results."
    await msg.answer(txt, reply_markup=K.back_admin())


@rt.callback_query(F.data == "a_expiry")
async def cb_a_expiry(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.expiry_id)
    await cb.message.edit_text(
        T.hdr("⏰", "𝗘𝗫𝗣𝗜𝗥𝗬") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.expiry_id)
async def p_expiry_id(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.update_data(target=uid)
    await state.set_state(S_Admin.expiry_dy)
    await msg.answer(
        f"  ✏ Days for <code>{uid}</code>:\n  ▸ 0 = remove",
        reply_markup=K.back_admin()
    )


@rt.message(S_Admin.expiry_dy)
async def p_expiry_dy(msg: Message, state: FSMContext):
    try:
        days = int(msg.text.strip())
        assert days >= 0
    except:
        await msg.answer("  ✗ Invalid.")
        return
    d = await state.get_data()
    uid = d.get("target")
    await state.clear()
    if days == 0:
        store_remove_expiry(uid)
        await msg.answer(
            f"  ✔ Expiry removed <code>{uid}</code>",
            reply_markup=K.back_admin()
        )
    else:
        store_set_expiry(uid, days)
        admin_log(msg.from_user.id, f"EXPIRY_{days}d", str(uid))
        await msg.answer(
            f"  ✔ <code>{uid}</code> expires in {days}d",
            reply_markup=K.back_admin()
        )
        try:
            await bot.send_message(uid, f"  ⏰ Access expires in {days} days.")
        except:
            pass


# ── Warn ──────────────────────────────────

@rt.callback_query(F.data == "a_warn")
async def cb_a_warn(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.warn_id)
    await cb.message.edit_text(
        T.hdr("⚠", "𝗪𝗔𝗥𝗡") + "\n\n  ✏ Enter user ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.warn_id)
async def p_warn_id(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.update_data(target=uid)
    await state.set_state(S_Admin.warn_reason)
    await msg.answer(
        f"  ✏ Reason for <code>{uid}</code>:",
        reply_markup=K.back_admin()
    )


@rt.message(S_Admin.warn_reason)
async def p_warn_reason(msg: Message, state: FSMContext):
    d = await state.get_data()
    uid = d.get("target")
    await state.clear()
    reason = msg.text.strip()[:100]
    count = store_add_warning(uid, reason)
    admin_log(msg.from_user.id, f"WARN_{count}", str(uid))
    try:
        await bot.send_message(uid, f"  ⚠ Warning ({count}/3): {reason}")
    except:
        pass
    if count >= 3:
        store_ban(uid)
        nuker.delete_token(uid)
        admin_log(msg.from_user.id, "AUTO_BAN_3WARNS", str(uid))
        try:
            await bot.send_message(uid, T.banned())
        except:
            pass
        await msg.answer(
            f"  ⚠ <code>{uid}</code> warned ({count})\n  🚫 Auto-banned (3 warns)",
            reply_markup=K.back_admin()
        )
    else:
        await msg.answer(
            f"  ⚠ <code>{uid}</code> warned ({count}/3)",
            reply_markup=K.back_admin()
        )


# ── Note ──────────────────────────────────

@rt.callback_query(F.data == "a_note")
async def cb_a_note(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.note_id)
    await cb.message.edit_text(
        T.hdr("📝", "𝗡𝗢𝗧𝗘") + "\n\n  ✏ Enter user ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.note_id)
async def p_note_id(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.update_data(target=uid)
    await state.set_state(S_Admin.note_text)
    current = store_get_note(uid)
    txt = f"  ✏ Note for <code>{uid}</code>:"
    if current:
        txt += f"\n  ▸ Current: {current}"
    await msg.answer(txt, reply_markup=K.back_admin())


@rt.message(S_Admin.note_text)
async def p_note_text(msg: Message, state: FSMContext):
    d = await state.get_data()
    uid = d.get("target")
    await state.clear()
    note = msg.text.strip()[:200]
    store_set_note(uid, note)
    admin_log(msg.from_user.id, "SET_NOTE", str(uid))
    await msg.answer(
        f"  📝 Note saved for <code>{uid}</code>",
        reply_markup=K.back_admin()
    )


# ── Profile ───────────────────────────────

@rt.callback_query(F.data == "a_profile")
async def cb_a_profile(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "admin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.profile_id)
    await cb.message.edit_text(
        T.hdr("ℹ", "𝗣𝗥𝗢𝗙𝗜𝗟𝗘") + "\n\n  ✏ Enter user ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.profile_id)
async def p_profile_id(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    info = STORE.get("users", {}).get(str(uid), {})
    name = info.get("name", "Unknown")
    un = info.get("username", "N/A")
    last = info.get("last_seen", "N/A")[:16].replace("T", " ")
    role = ADMINS.get(uid, "user")
    vip = "💎 Yes" if uid in VIP_USERS else "No"
    st = "🚫 Banned" if uid in BANNED else (
        "✔ Allowed" if uid in ALLOWED_USERS else "⏳ Pending"
    )
    warns = store_get_warnings(uid)
    note = store_get_note(uid)
    exp = EXPIRY.get(str(uid), "None")
    if exp != "None":
        try:
            exp = datetime.fromisoformat(exp).strftime("%d %b %Y")
        except:
            pass
    txt = (
        f"{T.H}\n  ℹ  𝗣𝗥𝗢𝗙𝗜𝗟𝗘\n{T.H}\n\n"
        f"  ◆ Name     {name}\n"
        f"  ◆ User     @{un}\n"
        f"  ◆ ID       <code>{uid}</code>\n"
        f"  ◆ Status   {st}\n"
        f"  ◆ Role     {role}\n"
        f"  ◆ VIP      {vip}\n"
        f"  ◆ Expiry   {exp}\n"
        f"  ◆ Last     {last}\n"
        f"  ◆ Warns    {len(warns)}/3\n"
    )
    if warns:
        txt += "\n  ◈ Warnings:\n"
        for w in warns[-3:]:
            txt += f"  ▸ {w['reason'][:30]}\n"
    if note:
        txt += f"\n  📝 {note}"
    await msg.answer(txt, reply_markup=K.back_admin())


# ── VIP ───────────────────────────────────

@rt.callback_query(F.data == "a_addvip")
async def cb_a_addvip(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.addvip)
    await cb.message.edit_text(
        T.hdr("💎", "𝗔𝗗𝗗 𝗩𝗜𝗣") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.addvip)
async def p_addvip(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    store_add_vip(uid)
    admin_log(msg.from_user.id, "ADD_VIP", str(uid))
    try:
        await bot.send_message(uid, "  💎 You're now VIP!")
    except:
        pass
    await msg.answer(f"  💎 <code>{uid}</code> VIP!", reply_markup=K.back_admin())


@rt.callback_query(F.data == "a_rmvip")
async def cb_a_rmvip(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.set_state(S_Admin.rmvip)
    await cb.message.edit_text(
        T.hdr("💎", "𝗥𝗘𝗠 𝗩𝗜𝗣") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.rmvip)
async def p_rmvip(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    store_remove_vip(uid)
    await msg.answer(
        f"  ✔ VIP removed <code>{uid}</code>",
        reply_markup=K.back_admin()
    )


# ═══════════════════════════════════════════
#  📢 BROADCAST
# ═══════════════════════════════════════════

@rt.callback_query(F.data == "a_bcast_menu")
async def cb_bcast_menu(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    await cb.message.edit_text(
        f"{T.H}\n  📢  𝗕𝗥𝗢𝗔𝗗𝗖𝗔𝗦𝗧\n{T.H}\n\n"
        f"  ◆ Users: {len(ALLOWED_USERS)}\n"
        f"  ◆ VIP:   {len(VIP_USERS)}",
        reply_markup=K.broadcast_menu()
    )
    await cb.answer()


@rt.callback_query(F.data == "bcast_text")
async def cb_bcast_text(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.update_data(bcast_target="all", bcast_type="text")
    await state.set_state(S_Admin.bcast_text)
    await cb.message.edit_text(
        T.hdr("📢", "𝗧𝗘𝗫𝗧") + "\n\n  ✏ Enter message:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.callback_query(F.data == "bcast_all")
async def cb_bcast_all(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.update_data(bcast_target="all", bcast_type="text")
    await state.set_state(S_Admin.bcast_text)
    await cb.message.edit_text(
        T.hdr("📢", "𝗔𝗟𝗟 𝗨𝗦𝗘𝗥𝗦") + f"\n\n  ✏ Message for {len(ALLOWED_USERS)}:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.callback_query(F.data == "bcast_vip")
async def cb_bcast_vip(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.update_data(bcast_target="vip", bcast_type="text")
    await state.set_state(S_Admin.bcast_text)
    await cb.message.edit_text(
        T.hdr("💎", "𝗩𝗜𝗣 𝗢𝗡𝗟𝗬") + f"\n\n  ✏ Message for {len(VIP_USERS)} VIPs:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.callback_query(F.data == "bcast_photo")
async def cb_bcast_photo(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    await state.update_data(bcast_target="all", bcast_type="photo")
    await state.set_state(S_Admin.bcast_photo)
    await cb.message.edit_text(
        T.hdr("🖼", "𝗣𝗛𝗢𝗧𝗢") + "\n\n  📸 Send photo or URL:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.bcast_photo, F.photo)
async def p_bcast_photo_file(msg: Message, state: FSMContext):
    photo_id = msg.photo[-1].file_id
    await state.update_data(bcast_photo_id=photo_id)
    await state.set_state(S_Admin.bcast_photo_cap)
    await msg.answer(
        "  ✔ Photo received!\n  ✏ Enter caption:",
        reply_markup=K.back_admin()
    )


@rt.message(S_Admin.bcast_photo, F.text)
async def p_bcast_photo_url(msg: Message, state: FSMContext):
    url = msg.text.strip()
    await state.update_data(bcast_photo_id=url)
    await state.set_state(S_Admin.bcast_photo_cap)
    await msg.answer(
        "  ✔ URL set!\n  ✏ Enter caption:",
        reply_markup=K.back_admin()
    )


@rt.message(S_Admin.bcast_photo_cap)
async def p_bcast_photo_cap(msg: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    caption = msg.text.strip()
    photo = d.get("bcast_photo_id", "")
    target = d.get("bcast_target", "all")
    targets = VIP_USERS if target == "vip" else ALLOWED_USERS
    bc_cap = (
        f"{T.H}\n  📢  𝗕𝗥𝗢𝗔𝗗𝗖𝗔𝗦𝗧\n{T.H}\n\n"
        f"  {caption}\n\n  ◈ From Admin"
    )
    s = 0
    f = 0
    for uid in targets:
        try:
            await bot.send_photo(uid, photo=photo, caption=bc_cap)
            s += 1
        except:
            try:
                await bot.send_message(uid, bc_cap)
                s += 1
            except:
                f += 1
        await asyncio.sleep(0.05)
    admin_log(msg.from_user.id, f"BCAST_PHOTO_{target} s={s} f={f}")
    add_broadcast_history(msg.from_user.id, f"photo_{target}", caption, s, f)
    await msg.answer(
        f"  📢 Photo sent!\n  ✔ {s}  ✗ {f}",
        reply_markup=K.back_admin()
    )


@rt.message(S_Admin.bcast_text)
async def p_bcast_text(msg: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    txt = msg.text.strip()
    target = d.get("bcast_target", "all")
    targets = VIP_USERS if target == "vip" else ALLOWED_USERS
    bc = (
        f"{T.H}\n  📢  𝗕𝗥𝗢𝗔𝗗𝗖𝗔𝗦𝗧\n{T.H}\n\n"
        f"  {txt}\n\n  ◈ From Admin"
    )
    s = 0
    f = 0
    for uid in targets:
        try:
            await bot.send_message(uid, bc)
            s += 1
        except:
            f += 1
        await asyncio.sleep(0.05)
    admin_log(msg.from_user.id, f"BCAST_TEXT_{target} s={s} f={f}")
    add_broadcast_history(msg.from_user.id, f"text_{target}", txt, s, f)
    await msg.answer(
        f"  📢 Sent!\n  ✔ {s}  ✗ {f}",
        reply_markup=K.back_admin()
    )


@rt.callback_query(F.data == "a_bcast_hist")
async def cb_bcast_hist(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    bh = STORE.get("broadcast_history", [])[:8]
    txt = f"{T.H}\n  📜  𝗕𝗖𝗔𝗦𝗧 𝗟𝗢𝗚\n{T.H}\n\n"
    if not bh:
        txt += "  ▸ No broadcasts."
    for b in bh:
        t = b.get("time", "")[:16].replace("T", " ")
        txt += (
            f"  ◆ {t}\n"
            f"  ▸ {b.get('type', '?')} ✔{b.get('sent', 0)} ✗{b.get('failed', 0)}\n"
            f"  ▸ {b.get('text', '')[:30]}\n\n"
        )
    await cb.message.edit_text(txt, reply_markup=K.back_admin())
    await cb.answer()


# ── Lists ─────────────────────────────────

@rt.callback_query(F.data == "a_list")
async def cb_a_list(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    txt = f"{T.H}\n  📋  𝗔𝗟𝗟𝗢𝗪𝗘𝗗\n{T.H}\n\n"
    for uid in sorted(ALLOWED_USERS):
        role = ADMINS.get(uid, "user")
        vip = "💎" if uid in VIP_USERS else ""
        txt += f"  ◆{vip} <code>{uid}</code> — {role}\n"
    txt += f"\n  ▸ Total: {len(ALLOWED_USERS)}"
    await cb.message.edit_text(txt, reply_markup=K.back_admin())
    await cb.answer()


@rt.callback_query(F.data == "a_admins")
async def cb_a_admins(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "superadmin"):
        await cb.answer("✗", show_alert=True)
        return
    lb = {"owner": "👑", "superadmin": "⭐", "admin": "🛡", "moderator": "👮"}
    txt = f"{T.H}\n  👑  𝗔𝗗𝗠𝗜𝗡𝗦\n{T.H}\n\n"
    for uid, role in sorted(
        ADMINS.items(),
        key=lambda x: ADMIN_LEVELS.get(x[1], 0),
        reverse=True
    ):
        txt += f"  {lb.get(role, '?')} <code>{uid}</code> — {role}\n"
    await cb.message.edit_text(txt, reply_markup=K.back_admin())
    await cb.answer()


# ── Owner only ────────────────────────────

@rt.callback_query(F.data == "a_addadm")
async def cb_a_addadm(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "owner"):
        await cb.answer("✗ Owner only!", show_alert=True)
        return
    await state.set_state(S_Admin.addadm_id)
    await cb.message.edit_text(
        T.hdr("➕", "𝗔𝗗𝗗 𝗔𝗗𝗠𝗜𝗡") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.addadm_id)
async def p_addadm_id(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.update_data(target=uid)
    await state.set_state(S_Admin.addadm_lv)
    await msg.answer(
        f"  ▸ Role for <code>{uid}</code>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👮 Mod", callback_data="sl_moderator")],
            [InlineKeyboardButton(text="🛡 Admin", callback_data="sl_admin")],
            [InlineKeyboardButton(text="⭐ Super", callback_data="sl_superadmin")],
            [InlineKeyboardButton(text="◂ Cancel", callback_data="admin_menu")],
        ])
    )


@rt.callback_query(F.data.startswith("sl_"))
async def cb_sl(cb: CallbackQuery, state: FSMContext):
    role = cb.data[3:]
    d = await state.get_data()
    t = d.get("target")
    if not t:
        await cb.answer("✗")
        await state.clear()
        return
    await state.clear()
    store_add_admin(t, role)
    admin_log(cb.from_user.id, f"ADD_ADMIN_{role}", str(t))
    await cb.message.edit_text(
        f"  ✔ <code>{t}</code> → {role}",
        reply_markup=K.back_admin()
    )
    try:
        await bot.send_message(t, f"  🎉 Promoted to {role}! /admin")
    except:
        pass
    await cb.answer()


@rt.callback_query(F.data == "a_rmadm")
async def cb_a_rmadm(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "owner"):
        await cb.answer("✗ Owner only!", show_alert=True)
        return
    await state.set_state(S_Admin.rmadm)
    await cb.message.edit_text(
        T.hdr("➖", "𝗥𝗘𝗠 𝗔𝗗𝗠𝗜𝗡") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.rmadm)
async def p_rmadm(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    if uid == OWNER_ID:
        await msg.answer("  ✗ Can't remove owner!", reply_markup=K.back_admin())
        return
    store_remove_admin(uid)
    admin_log(msg.from_user.id, "REM_ADMIN", str(uid))
    await msg.answer(f"  ✔ <code>{uid}</code> demoted!", reply_markup=K.back_admin())


@rt.callback_query(F.data == "a_rmuser")
async def cb_a_rmuser(cb: CallbackQuery, state: FSMContext):
    if not has_admin(cb.from_user.id, "owner"):
        await cb.answer("✗ Owner only!", show_alert=True)
        return
    await state.set_state(S_Admin.rmuser)
    await cb.message.edit_text(
        T.hdr("🗑", "𝗥𝗘𝗠𝗢𝗩𝗘") + "\n\n  ✏ Enter ID:",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.message(S_Admin.rmuser)
async def p_rmuser(msg: Message, state: FSMContext):
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer("  ✗ Invalid ID.")
        return
    await state.clear()
    if uid == OWNER_ID:
        await msg.answer("  ✗ Can't remove owner!", reply_markup=K.back_admin())
        return
    store_remove_user(uid)
    store_remove_admin(uid)
    admin_log(msg.from_user.id, "REMOVED", str(uid))
    await msg.answer(f"  ✔ <code>{uid}</code> removed!", reply_markup=K.back_admin())


# ── Maintenance & Reset ───────────────────

@rt.callback_query(F.data == "a_maint")
async def cb_a_maint(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "owner"):
        await cb.answer("✗ Owner only!", show_alert=True)
        return
    STORE["maintenance"] = not STORE.get("maintenance", False)
    save_store(STORE)
    st = "🔴 ON" if STORE["maintenance"] else "🟢 OFF"
    admin_log(cb.from_user.id, f"MAINTENANCE_{st}")
    await cb.message.edit_text(
        f"  🔧 Maintenance: {st}",
        reply_markup=K.back_admin()
    )
    await cb.answer()


@rt.callback_query(F.data == "a_reset")
async def cb_a_reset(cb: CallbackQuery):
    if not has_admin(cb.from_user.id, "owner"):
        await cb.answer("✗ Owner only!", show_alert=True)
        return
    STORE["stats"] = {"total_logins": 0, "total_actions": 0, "total_unlocks": 0}
    STORE["daily_stats"] = {}
    save_store(STORE)
    admin_log(cb.from_user.id, "RESET_STATS")
    await cb.message.edit_text("  ✔ Stats reset!", reply_markup=K.back_admin())
    await cb.answer()


# ═══════════════════════════════════════════
#  📋 COMMANDS
# ═══════════════════════════════════════════

@rt.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(
        f"{T.H}\n  ❓  𝗛𝗘𝗟𝗣\n{T.H}\n\n"
        "  /start  — Start\n"
        "  /admin  — Admin panel\n"
        "  /help   — Help\n"
        "  /status — Status\n"
        "  /ping   — Latency\n\n"
        "  ◈ Made with ❤ by AADIBRAND"
    )


@rt.message(Command("status"))
async def cmd_status(msg: Message):
    uid = msg.from_user.id
    td = nuker.get_token_data(uid)
    up = time.strftime('%H:%M:%S', time.gmtime(time.time() - START_TIME))
    maint = "🔴" if is_maintenance() else "🟢"
    txt = f"{T.H}\n  🤖  𝗦𝗧𝗔𝗧𝗨𝗦\n{T.H}\n\n"
    txt += f"  ◆ Logged   {'✔' if td else '✗'}\n"
    if td:
        txt += f"  ◆ Email    {td.get('email', '—')}\n"
    txt += (
        f"  ◆ Users    {len(ALLOWED_USERS)}\n"
        f"  ◆ Maint    {maint}\n"
        f"  ◆ Uptime   {up}"
    )
    await msg.answer(txt)


@rt.message(Command("ping"))
async def cmd_ping(msg: Message):
    t = time.time()
    m = await msg.answer("  🏓...")
    ms = (time.time() - t) * 1000
    await m.edit_text(f"  🏓 {ms:.0f}ms {'✔' if ms < 1000 else '✗'}")


# ═══════════════════════════════════════════
#  ⏰ DAILY STATS TASK
# ═══════════════════════════════════════════

async def daily_stats_task():
    while True:
        now = datetime.now()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        await asyncio.sleep((midnight - now).total_seconds())
        yesterday = now.strftime("%Y-%m-%d")
        ds = STORE.get("daily_stats", {}).get(yesterday, {})
        txt = (
            f"{T.H}\n  📊  𝗗𝗔𝗜𝗟𝗬 {yesterday}\n{T.H}\n\n"
            f"  ◆ Actions  {ds.get('actions', 0)}\n"
            f"  ◆ Logins   {ds.get('logins', 0)}\n"
            f"  ◆ Unlocks  {ds.get('unlocks', 0)}\n"
            f"  ◆ Users    {len(ALLOWED_USERS)}\n"
            f"  ◆ VIP      {len(VIP_USERS)}"
        )
        try:
            await bot.send_message(OWNER_ID, txt)
        except:
            pass


# ═══════════════════════════════════════════
#  🚀 MAIN
# ═══════════════════════════════════════════

async def main():
    global START_TIME
    START_TIME = time.time()

    log.info("━" * 35)
    log.info("  🎮 CPM Tool Box")
    log.info("  👑 By AADIBRAND")
    log.info(f"  ◆ Owner:  {OWNER_ID}")
    log.info(f"  ◆ Users:  {len(ALLOWED_USERS)}")
    log.info(f"  ◆ Admins: {len(ADMINS)}")
    log.info("━" * 35)

    await bot.set_my_commands([
        BotCommand(command="start",  description="🎮 Start"),
        BotCommand(command="admin",  description="👑 Admin"),
        BotCommand(command="help",   description="❓ Help"),
        BotCommand(command="status", description="📊 Status"),
        BotCommand(command="ping",   description="🏓 Ping"),
    ])

    asyncio.create_task(daily_stats_task())
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("  ✗ Stopped")
    except Exception as e:
        log.error(f"  ✗ {e}")
        log.error(traceback.format_exc())