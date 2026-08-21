"""
Cheeworker Bewerbungs-Tracker – Telegram Mini App Backend
FastAPI + TinyDB, läuft auf Railway.

Env-Variablen:
  BOT_TOKEN    – Token deines Telegram-Bots (gleicher wie Cheeworker)
  ALLOWED_IDS  – Komma-getrennte Telegram-IDs, z.B. "123456789,987654321"
  API_SECRET   – Beliebiger geheimer String, damit dein Bot Karten anlegen darf
"""

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from tinydb import Query, TinyDB

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_SECRET = os.environ.get("API_SECRET", "")
DB_PATH = os.environ.get("DB_PATH", "tracker_db.json")

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
db = TinyDB(DB_PATH)
apps_table = db.table("applications")
whitelist_table = db.table("whitelist")

app = FastAPI(title="Bewerbungs-Tracker")

STATUSES = ["beworben", "antwort", "gespraech", "zusage", "absage"]


# ---------------------------------------------------------------- Whitelist

def seed_whitelist() -> None:
    """Beim Start: IDs aus ALLOWED_IDS in die DB übernehmen (falls neu)."""
    raw = os.environ.get("ALLOWED_IDS", "")
    W = Query()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and not whitelist_table.contains(W.user_id == int(part)):
            whitelist_table.insert({"user_id": int(part)})


seed_whitelist()


def is_allowed(user_id: int) -> bool:
    W = Query()
    return whitelist_table.contains(W.user_id == user_id)


# ------------------------------------------------- Telegram initData prüfen

def validate_init_data(init_data: str) -> dict:
    """Prüft die Signatur der Telegram-WebApp-initData. Gibt User-Dict zurück."""
    if not init_data:
        raise HTTPException(401, "initData fehlt")

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "hash fehlt")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, received_hash):
        raise HTTPException(401, "Ungültige Signatur")

    # optional: initData nicht älter als 24h akzeptieren
    auth_date = int(parsed.get("auth_date", "0"))
    if auth_date and time.time() - auth_date > 86400:
        raise HTTPException(401, "Login abgelaufen – App neu öffnen")

    try:
        user = json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(401, "user-Daten kaputt")

    if "id" not in user:
        raise HTTPException(401, "Keine User-ID")
    return user


def current_user(x_init_data: str = Header(default="")) -> dict:
    user = validate_init_data(x_init_data)
    if not is_allowed(int(user["id"])):
        raise HTTPException(403, "Kein Zugang – frag den Admin")
    return user


# ------------------------------------------------------------------ Models

class ApplicationIn(BaseModel):
    firma: str
    position: str = ""
    status: str = "beworben"
    kontakt: str = ""
    notizen: str = ""
    datum: str = ""  # z.B. "2026-08-21"


class StatusUpdate(BaseModel):
    status: str


# ---------------------------------------------------------------- API

@app.get("/api/applications")
def list_applications(user: dict = Depends(current_user)):
    A = Query()
    rows = apps_table.search(A.owner_id == int(user["id"]))
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = r.doc_id
        out.append(d)
    out.sort(key=lambda x: x.get("datum", ""), reverse=True)
    return out


@app.post("/api/applications")
def create_application(data: ApplicationIn, user: dict = Depends(current_user)):
    if data.status not in STATUSES:
        raise HTTPException(400, f"Status muss einer sein von: {STATUSES}")
    doc = data.model_dump()
    doc["owner_id"] = int(user["id"])
    if not doc["datum"]:
        doc["datum"] = time.strftime("%Y-%m-%d")
    doc_id = apps_table.insert(doc)
    return {"id": doc_id, **doc}


@app.patch("/api/applications/{app_id}/status")
def update_status(app_id: int, data: StatusUpdate, user: dict = Depends(current_user)):
    if data.status not in STATUSES:
        raise HTTPException(400, f"Status muss einer sein von: {STATUSES}")
    row = apps_table.get(doc_id=app_id)
    if not row or row.get("owner_id") != int(user["id"]):
        raise HTTPException(404, "Nicht gefunden")
    apps_table.update({"status": data.status}, doc_ids=[app_id])
    return {"ok": True}


@app.put("/api/applications/{app_id}")
def update_application(app_id: int, data: ApplicationIn, user: dict = Depends(current_user)):
    row = apps_table.get(doc_id=app_id)
    if not row or row.get("owner_id") != int(user["id"]):
        raise HTTPException(404, "Nicht gefunden")
    apps_table.update(data.model_dump(), doc_ids=[app_id])
    return {"ok": True}


@app.delete("/api/applications/{app_id}")
def delete_application(app_id: int, user: dict = Depends(current_user)):
    row = apps_table.get(doc_id=app_id)
    if not row or row.get("owner_id") != int(user["id"]):
        raise HTTPException(404, "Nicht gefunden")
    apps_table.remove(doc_ids=[app_id])
    return {"ok": True}


# --------------------------------------- Bot-Endpunkte (mit API_SECRET)

class BotApplicationIn(ApplicationIn):
    owner_id: int


def check_bot_secret(x_api_secret: str = Header(default="")):
    if not API_SECRET or not hmac.compare_digest(x_api_secret, API_SECRET):
        raise HTTPException(401, "Falscher API-Secret")


@app.post("/api/bot/applications")
def bot_create_application(data: BotApplicationIn, _=Depends(check_bot_secret)):
    """Dein Cheeworker-Bot legt hiermit automatisch Karten an."""
    doc = data.model_dump()
    if not doc["datum"]:
        doc["datum"] = time.strftime("%Y-%m-%d")
    doc_id = apps_table.insert(doc)
    return {"id": doc_id}


class InviteIn(BaseModel):
    user_id: int


@app.post("/api/bot/invite")
def bot_invite(data: InviteIn, _=Depends(check_bot_secret)):
    """Bot fügt neue User zur Whitelist hinzu (/invite Command)."""
    W = Query()
    if not whitelist_table.contains(W.user_id == data.user_id):
        whitelist_table.insert({"user_id": data.user_id})
    return {"ok": True}


# ---------------------------------------------------------------- Frontend

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
