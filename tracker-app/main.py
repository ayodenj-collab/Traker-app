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
from urllib.parse import parse_qsl, quote_plus

import requests
from bs4 import BeautifulSoup

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
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
profiles_table = db.table("profiles")

app = FastAPI(title="Bewerbungs-Tracker")

STATUSES = ["gemerkt", "beworben", "antwort", "gespraech", "zusage", "absage"]


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
    ort: str = ""
    art: str = ""  # Lehre / Vollzeit / Teilzeit / Job
    status: str = "beworben"
    link: str = ""
    notizen: str = ""
    datum: str = ""  # Ab wann, z.B. "2026-09-01"


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


@app.get("/api/bot/applications")
def bot_list_applications(_=Depends(check_bot_secret)):
    """Bot holt alle Karten (fürs Mail-Matching)."""
    out = []
    for r in apps_table.all():
        d = dict(r)
        d["id"] = r.doc_id
        out.append(d)
    return out


class BotStatusUpdate(BaseModel):
    status: str = ""
    notiz_anhang: str = ""


@app.patch("/api/bot/applications/{app_id}")
def bot_update_application(app_id: int, data: BotStatusUpdate, _=Depends(check_bot_secret)):
    """Bot setzt Status und/oder hängt eine Notiz an (z.B. 'Mail erhalten')."""
    row = apps_table.get(doc_id=app_id)
    if not row:
        raise HTTPException(404, "Nicht gefunden")
    updates = {}
    if data.status:
        if data.status not in STATUSES:
            raise HTTPException(400, f"Status muss einer sein von: {STATUSES}")
        updates["status"] = data.status
    if data.notiz_anhang:
        alt = row.get("notizen", "")
        updates["notizen"] = (alt + "\n" if alt else "") + data.notiz_anhang
    if updates:
        apps_table.update(updates, doc_ids=[app_id])
    return {"ok": True}


class InviteIn(BaseModel):
    user_id: int


@app.post("/api/bot/invite")
def bot_invite(data: InviteIn, _=Depends(check_bot_secret)):
    """Bot fügt neue User zur Whitelist hinzu (/invite Command)."""
    W = Query()
    if not whitelist_table.contains(W.user_id == data.user_id):
        whitelist_table.insert({"user_id": data.user_id})
    return {"ok": True}



# ---------------------------------------------------------------- Jobsuche
# Mehrere Quellen parallel. Jede Quelle darf einzeln scheitern,
# ohne dass die ganze Suche kaputt ist.

from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def _quelle_karriere(q: str, ort: str) -> list:
    if q and ort:
        url = f"https://www.karriere.at/jobs/{quote_plus(q)}/{quote_plus(ort)}"
    elif q:
        url = f"https://www.karriere.at/jobs/{quote_plus(q)}"
    else:
        url = f"https://www.karriere.at/jobs/{quote_plus(ort)}"
    r = requests.get(url, headers=UA, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []
    for item in soup.select("[class*='jobsListItem'], [class*='jobs-item'], article"):
        a = item.select_one("a[href*='/jobs/']") or item.select_one("h2 a, h3 a")
        if not a:
            continue
        titel = a.get_text(strip=True)
        link = a.get("href", "")
        if link.startswith("/"):
            link = "https://www.karriere.at" + link
        if not titel:
            continue
        firma_el = item.select_one("[class*='company']")
        ort_el = item.select_one("[class*='location']")
        jobs.append({
            "titel": titel,
            "firma": firma_el.get_text(strip=True) if firma_el else "",
            "ort": ort_el.get_text(" ", strip=True) if ort_el else "",
            "link": link,
            "quelle": "karriere.at",
        })
    return jobs


def _quelle_ams(q: str, ort: str) -> list:
    """AMS 'alle jobs' (jobs.ams.at/public/emps) - mehrere API-Varianten probieren."""
    suchtext = " ".join(x for x in [q, ort] if x).strip() or "lehre"

    def _extract(items):
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            titel = str(it.get("title") or it.get("titel") or
                        it.get("occupation") or it.get("beruf") or "")
            jid = str(it.get("id") or it.get("jobId") or it.get("uuid") or "")
            link = (f"https://jobs.ams.at/public/emps/jobs/{jid}" if jid
                    else str(it.get("url") or it.get("link") or ""))
            if not titel or not link.startswith("http"):
                continue
            firma = str(it.get("company") or it.get("companyName") or
                        it.get("arbeitgeber") or it.get("employer") or "")
            ortx = str(it.get("location") or it.get("workplace") or
                       it.get("ort") or it.get("city") or ort or "")
            out.append({
                "titel": titel[:150], "firma": firma[:100], "ort": ortx[:80],
                "link": link, "quelle": "AMS",
            })
        return out

    def _find_items(data):
        """Job-Liste im JSON finden, egal wie sie heisst."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("items", "jobs", "content", "results", "hits", "advertList", "jobAdverts"):
                v = data.get(k)
                if isinstance(v, list) and v:
                    return v
                if isinstance(v, dict):
                    inner = _find_items(v)
                    if inner:
                        return inner
        return []

    basis_params = {
        "query": suchtext,
        "sortField": "_SCORE",
        "sortOrder": "desc",
        "page": 0,
        "size": 30,
    }
    versuche = [
        # exakt wie die AMS-Webseite selbst sucht (inkl. WKO-Lehrstellen)
        ("GET", "https://jobs.ams.at/public/emps/api/jobs", basis_params),
        ("GET", "https://jobs.ams.at/public/emps/api/jobs/search", basis_params),
        ("POST", "https://jobs.ams.at/public/emps/api/jobs/search", basis_params),
        ("GET", "https://jobs.ams.at/public/emps/api/search", basis_params),
        ("POST", "https://jobs.ams.at/public/emps/api/search",
         {"query": suchtext, "page": 0, "pageSize": 30}),
    ]
    for methode, url, params in versuche:
        try:
            if methode == "POST":
                r = requests.post(url, json=params,
                                  headers={**UA, "Content-Type": "application/json",
                                           "Accept": "application/json"},
                                  timeout=10)
            else:
                r = requests.get(url, params=params,
                                 headers={**UA, "Accept": "application/json"},
                                 timeout=10)
            if not r.ok:
                continue
            jobs = _extract(_find_items(r.json()))
            if jobs:
                return jobs
        except Exception:
            continue
    return []


def _quelle_hokify(q: str, ort: str) -> list:
    r = requests.get(
        "https://hokify.at/jobs",
        params={"searchTerm": q or "", "location": ort or ""},
        headers=UA, timeout=10,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []
    for a in soup.select("a[href*='/job/'], a[href*='/jobs/'], [data-cy*='job'] a, article a"):
        link = a.get("href", "")
        if "/job" not in link:
            continue
        if link.startswith("/"):
            link = "https://hokify.at" + link
        titel = a.get_text(" ", strip=True) or a.get("title", "") or a.get("aria-label", "")
        if not titel or len(titel) < 4:
            continue
        jobs.append({
            "titel": titel[:120], "firma": "", "ort": "",
            "link": link, "quelle": "hokify",
        })
    return jobs


def _quelle_jobsat(q: str, ort: str) -> list:
    if q and ort:
        url = f"https://www.jobs.at/j/{quote_plus(q)}/{quote_plus(ort)}"
    else:
        url = f"https://www.jobs.at/j/{quote_plus(q or ort)}"
    r = requests.get(url, headers=UA, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []
    for item in soup.select("[class*='job-item'], [class*='jobitem'], article"):
        a = item.select_one("a[href*='/job/'], h2 a, h3 a")
        if not a:
            continue
        titel = a.get_text(strip=True)
        link = a.get("href", "")
        if link.startswith("/"):
            link = "https://www.jobs.at" + link
        if not titel:
            continue
        firma_el = item.select_one("[class*='company']")
        ort_el = item.select_one("[class*='location']")
        jobs.append({
            "titel": titel,
            "firma": firma_el.get_text(strip=True) if firma_el else "",
            "ort": ort_el.get_text(" ", strip=True) if ort_el else "",
            "link": link,
            "quelle": "jobs.at",
        })
    return jobs


def _quelle_lehrberuf(q: str, ort: str) -> list:
    """lehrberuf.info - spezialisiert auf Lehrstellen in Oesterreich."""
    r = requests.get(
        "https://www.lehrberuf.info/suchergebnisse",
        params={"what": q or "lehre", "where": ort or ""},
        headers=UA, timeout=10,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []
    for item in soup.select("[class*='job'], [class*='result'], article, li"):
        a = item.select_one("a[href*='lehrstelle'], a[href*='/job'], h2 a, h3 a")
        if not a:
            continue
        titel = a.get_text(" ", strip=True)
        link = a.get("href", "")
        if link.startswith("/"):
            link = "https://www.lehrberuf.info" + link
        if not titel or len(titel) < 4 or not link.startswith("http"):
            continue
        firma_el = item.select_one("[class*='company'], [class*='firma']")
        ort_el = item.select_one("[class*='location'], [class*='ort']")
        jobs.append({
            "titel": titel[:150],
            "firma": firma_el.get_text(strip=True)[:100] if firma_el else "",
            "ort": ort_el.get_text(" ", strip=True)[:80] if ort_el else "",
            "link": link,
            "quelle": "lehrberuf.info",
        })
    return jobs


QUELLEN = {
    "karriere.at": _quelle_karriere,
    "AMS": _quelle_ams,
    "hokify": _quelle_hokify,
    "jobs.at": _quelle_jobsat,
    "lehrberuf.info": _quelle_lehrberuf,
}


@app.get("/api/jobs")
def search_jobs(q: str = "", ort: str = "", user: dict = Depends(current_user)):
    """Fragt alle Quellen parallel ab. Gibt Jobs + Status je Quelle zurueck."""
    q = q.strip()
    ort = ort.strip()
    if not q and not ort:
        raise HTTPException(400, "Beruf oder Ort eingeben")
    results = {}

    def run(name, fn):
        try:
            return name, fn(q, ort)[:40]
        except Exception:
            return name, None  # Quelle kaputt/nicht erreichbar

    with ThreadPoolExecutor(max_workers=5) as ex:
        for name, jobs in ex.map(lambda kv: run(*kv), QUELLEN.items()):
            results[name] = jobs

    alle = []
    seen = set()
    quellen_status = {}
    for name, jobs in results.items():
        if jobs is None:
            quellen_status[name] = "fehler"
            continue
        quellen_status[name] = str(len(jobs))
        for j in jobs:
            key = j["link"] or (j["titel"] + j["firma"])
            if key in seen:
                continue
            seen.add(key)
            alle.append(j)

    return {"jobs": alle[:120], "quellen": quellen_status}



# ------------------------------------------------ KI: Claude-API-Helfer

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-6"


def _claude(prompt: str, max_tokens: int = 2500, web: bool = False) -> str:
    if not ANTHROPIC_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY fehlt in den Railway-Variablen")
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if web:
        payload["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=180 if web else 90,
    )
    r.raise_for_status()
    return "".join(
        b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text"
    )


def _parse_json(text: str):
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("[") or p.startswith("{"):
                text = p
                break
    start = min([i for i in [text.find("["), text.find("{")] if i >= 0], default=0)
    return json.loads(text[start:])


# ------------------------------------------------ KI-Tiefensuche

@app.get("/api/jobs/deep")
def deep_jobs(q: str = "", ort: str = "", user: dict = Depends(current_user)):
    q, ort = q.strip(), ort.strip()
    if not q and not ort:
        raise HTTPException(400, "Beruf oder Ort eingeben")
    prompt = f"""Suche im Web nach aktuellen Stellenanzeigen in Oesterreich.
Beruf/Stichwort: {q or "alle Berufe"}. Ort/Region: {ort or "Oesterreich"}.
Durchsuche breit: Zeitungs-Jobportale (derStandard, Kurier, Heute), willhaben,
Firmen-Karriereseiten, Gemeinde-Seiten, Lehrstellenboersen (WKO, AMS) - nicht nur karriere.at.
Antworte NUR mit einem JSON-Array (kein anderer Text), maximal 25 Eintraege:
[{{"titel":"...","firma":"...","ort":"...","link":"https://...","quelle":"seitenname.at"}}]
Verwende nur echte Links aus den Suchergebnissen. Keine erfundenen Links."""
    try:
        text = _claude(prompt, max_tokens=4000, web=True)
        jobs = _parse_json(text)
        assert isinstance(jobs, list)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "KI-Suche fehlgeschlagen - nochmal probieren")
    sauber = []
    for j in jobs[:25]:
        if not isinstance(j, dict) or not j.get("titel"):
            continue
        link = str(j.get("link", ""))
        if not link.startswith("http"):
            continue
        sauber.append({
            "titel": str(j.get("titel", ""))[:150],
            "firma": str(j.get("firma", ""))[:100],
            "ort": str(j.get("ort", ""))[:80],
            "link": link,
            "quelle": ("KI: " + str(j.get("quelle", "Web")))[:40],
        })
    return {"jobs": sauber}


# ------------------------------------------------ Job-Details

@app.get("/api/jobs/details")
def job_details(link: str, user: dict = Depends(current_user)):
    if not link.startswith("http"):
        raise HTTPException(400, "Kein gueltiger Link")
    try:
        r = requests.get(link, headers=UA, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        page_text = soup.get_text(" ", strip=True)[:13000]
    except Exception:
        raise HTTPException(502, "Inserat konnte nicht geladen werden")

    prompt = f"""Das ist der Text einer Stellenanzeige. Fasse auf Deutsch zusammen - so viel Info wie moeglich, aber kompakt pro Punkt. Punkte ohne Info im Text einfach weglassen:
- Firma: wer sind die, was machen die, wie gross
- Aufgaben: was macht man in dem Job konkret
- Anforderungen: Ausbildung, Erfahrung, Faehigkeiten, Fuehrerschein
- Gehalt: Betrag und ob brutto/monatlich, Ueberzahlung
- Arbeitszeit: Vollzeit/Teilzeit, Stunden, Schichten
- Beginn: ab wann
- Vorteile: Benefits, Zulagen, Aufstiegsmoeglichkeiten
- Kontakt: Ansprechperson, Telefon
- Bewerbung: wie bewirbt man sich (Mail, Formular, ...)
Extrahiere ausserdem die Bewerbungs-E-Mail-Adresse, falls eine im Text steht.
Antworte NUR mit JSON: {{"zusammenfassung":"...(mit \\n zwischen Punkten)","email":"...oder leer"}}

Text der Anzeige:
{page_text}"""
    try:
        data = _parse_json(_claude(prompt, max_tokens=1500))
        return {
            "zusammenfassung": str(data.get("zusammenfassung", ""))[:6000],
            "email": str(data.get("email", ""))[:100],
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Analyse fehlgeschlagen")


# ------------------------------------------------ Profil / Lebenslauf

def _extract_text(filename: str, data: bytes) -> str:
    import io
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    if name.endswith(".docx"):
        from docx import Document
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    return data.decode("utf-8", errors="replace")


class ProfileDataIn(BaseModel):
    name: str = ""
    adresse: str = ""
    telefon: str = ""
    email: str = ""
    geburtsdatum: str = ""
    info: str = ""  # Ausbildung, Faehigkeiten, sonstiges


@app.get("/api/profile")
def get_profile(user: dict = Depends(current_user)):
    P = Query()
    row = profiles_table.get(P.user_id == int(user["id"]))
    if not row:
        return {}
    return {
        "dateiname": row.get("dateiname", ""),
        "datum": row.get("datum", ""),
        "analyse": row.get("analyse", ""),
        "daten": row.get("daten", {}),
    }


@app.post("/api/profile")
def save_profile(data: ProfileDataIn, user: dict = Depends(current_user)):
    """Persoenliche Daten speichern (werden fuer Bewerbungen verwendet)."""
    P = Query()
    uid = int(user["id"])
    row = profiles_table.get(P.user_id == uid) or {"user_id": uid}
    row["daten"] = data.model_dump()
    profiles_table.upsert(row, P.user_id == uid)
    return {"ok": True}


@app.delete("/api/profile/cv")
def delete_cv(user: dict = Depends(current_user)):
    """Nur Lebenslauf + Analyse loeschen, Daten bleiben."""
    P = Query()
    uid = int(user["id"])
    row = profiles_table.get(P.user_id == uid)
    if row:
        pfad = row.get("cv_pfad", "")
        if pfad and os.path.exists(pfad):
            try:
                os.remove(pfad)
            except OSError:
                pass
        for k in ("cv_text", "analyse", "dateiname", "datum", "cv_pfad"):
            row.pop(k, None)
        profiles_table.upsert(row, P.user_id == uid)
    return {"ok": True}


@app.delete("/api/profile")
def delete_profile(user: dict = Depends(current_user)):
    """Alles loeschen: Daten + Lebenslauf + Analyse."""
    P = Query()
    profiles_table.remove(P.user_id == int(user["id"]))
    return {"ok": True}


@app.post("/api/profile/cv")
async def upload_cv(file: UploadFile = File(...), user: dict = Depends(current_user)):
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "Datei zu gross (max 5 MB)")
    try:
        text = _extract_text(file.filename, data)[:15000]
    except Exception:
        raise HTTPException(400, "Datei konnte nicht gelesen werden (PDF oder DOCX verwenden)")
    if len(text.strip()) < 50:
        raise HTTPException(400, "Kaum Text gefunden - ist das ein Scan? PDF mit echtem Text verwenden")

    prompt = f"""Analysiere diesen Lebenslauf fuer Bewerbungen in Oesterreich (Lehre/Job).
Antworte auf Deutsch, direkt und ehrlich, mit kurzen Absaetzen:
1) Staerken - was kommt gut an
2) Schwaechen/Luecken - was faellt negativ auf
3) Konkrete Verbesserungen - was sofort aendern
4) Passende Jobs - welche Stellen zu dem Profil passen

Lebenslauf:
{text}"""
    analyse = _claude(prompt, max_tokens=2000)
    # Originaldatei speichern, damit sie bei Bewerbungen angehaengt werden kann
    uid = int(user["id"])
    cv_dir = os.path.dirname(DB_PATH) or "."
    ext = ".pdf" if file.filename.lower().endswith(".pdf") else ".docx"
    cv_pfad = os.path.join(cv_dir, f"cv_{uid}{ext}")
    with open(cv_pfad, "wb") as f:
        f.write(data)
    P = Query()
    row = profiles_table.get(P.user_id == uid) or {"user_id": uid}
    row.update({
        "dateiname": file.filename,
        "cv_text": text,
        "cv_pfad": cv_pfad,
        "analyse": analyse,
        "datum": time.strftime("%Y-%m-%d"),
    })
    profiles_table.upsert(row, P.user_id == uid)
    return {"analyse": analyse, "dateiname": file.filename, "datum": time.strftime("%Y-%m-%d")}



# ------------------------------------------------ Direkt bewerben

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


class EntwurfIn(BaseModel):
    titel: str
    firma: str = ""
    link: str = ""


class SendenIn(BaseModel):
    an: str
    betreff: str
    text: str
    titel: str = ""
    firma: str = ""
    ort: str = ""
    link: str = ""


@app.post("/api/bewerbung/entwurf")
def bewerbung_entwurf(data: EntwurfIn, user: dict = Depends(current_user)):
    """KI schreibt einen Anschreiben-Entwurf aus Profil + Lebenslauf."""
    P = Query()
    row = profiles_table.get(P.user_id == int(user["id"])) or {}
    daten = row.get("daten", {})
    cv = (row.get("cv_text", "") or "")[:4000]

    prompt = f"""Schreibe eine kurze Bewerbungs-E-Mail auf Deutsch (Oesterreich).
Stelle: {data.titel}
Firma: {data.firma or "unbekannt"}
Bewerber: {daten.get("name", "")} | {daten.get("adresse", "")} | Tel: {daten.get("telefon", "")} | Mail: {daten.get("email", "")}
Ausbildung/Faehigkeiten: {daten.get("info", "")}
Lebenslauf-Auszug: {cv or "keiner vorhanden"}

Regeln:
- Klassisches oesterreichisches Format: Anrede "Sehr geehrte Damen und Herren", 3 kurze Absaetze, "Mit freundlichen Gruessen" + Name
- Beginne mit Bezug zur Firma, dann zum Bewerber
- Konkret und ehrlich, nichts erfinden was nicht in den Daten steht
- Erwaehne am Ende: "Meinen Lebenslauf finden Sie im Anhang." (nur wenn Lebenslauf vorhanden: {"ja" if row.get("cv_pfad") else "nein"})
- Keine Platzhalter wie [Name] - wenn eine Info fehlt, Satz weglassen
Antworte NUR mit JSON: {{"betreff":"Bewerbung: ...","text":"..."}}"""
    try:
        d = _parse_json(_claude(prompt, max_tokens=1200))
        return {
            "betreff": str(d.get("betreff", f"Bewerbung: {data.titel}"))[:150],
            "text": str(d.get("text", ""))[:5000],
            "anhang": bool(row.get("cv_pfad")),
            "absender": GMAIL_USER,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Entwurf fehlgeschlagen - nochmal probieren")


@app.post("/api/bewerbung/senden")
def bewerbung_senden(data: SendenIn, user: dict = Depends(current_user)):
    """Schickt die Bewerbung per Gmail ab und legt die Tracker-Karte an."""
    if not (GMAIL_USER and GMAIL_APP_PASSWORD):
        raise HTTPException(503, "GMAIL_USER / GMAIL_APP_PASSWORD fehlen in den Railway-Variablen")
    if "@" not in data.an:
        raise HTTPException(400, "Keine gueltige Empfaenger-Adresse")
    if len(data.text.strip()) < 30:
        raise HTTPException(400, "Text zu kurz")

    uid = int(user["id"])
    P = Query()
    row = profiles_table.get(P.user_id == uid) or {}

    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = data.an
    msg["Subject"] = data.betreff[:150]
    msg.attach(MIMEText(data.text, "plain", "utf-8"))

    pfad = row.get("cv_pfad", "")
    if pfad and os.path.exists(pfad):
        with open(pfad, "rb") as f:
            teil = MIMEApplication(f.read())
        name = row.get("dateiname", "Lebenslauf.pdf")
        teil.add_header("Content-Disposition", "attachment", filename=name)
        msg.attach(teil)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
    except Exception:
        raise HTTPException(502, "Mail-Versand fehlgeschlagen - Gmail-Zugangsdaten checken")

    apps_table.insert({
        "owner_id": uid,
        "firma": data.firma or data.titel,
        "ort": data.ort,
        "art": "",
        "status": "beworben",
        "link": data.link,
        "notizen": f"Beworben per Mail an {data.an} am {time.strftime('%d.%m.')}\n{data.titel}",
        "datum": time.strftime("%Y-%m-%d"),
    })
    return {"ok": True}


# ---------------------------------------------------------------- Frontend

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
