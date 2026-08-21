# Cheeworker Bewerbungs-Tracker (Telegram Mini App)

Private Mini App direkt in Telegram. Pipeline: Beworben → Antwort → Gespräch → Zusage/Absage.
Zugang nur für Telegram-IDs auf der Whitelist.

## Dateien

- `main.py` – FastAPI-Server (Auth, Whitelist, Bewerbungs-API)
- `static/index.html` – die Mini App selbst
- `bot_snippet.py` – zum Einbauen in deinen Cheeworker-Bot
- `requirements.txt`, `Procfile` – für Railway

## Setup (ca. 15 min)

### 1. Neues Railway-Service

Neuen Service im gleichen Railway-Projekt anlegen (eigenes Repo oder Ordner im Monorepo).
Diese Dateien reinkopieren, pushen.

Env-Variablen setzen:

| Variable | Wert |
|---|---|
| `BOT_TOKEN` | Token vom Cheeworker-Bot (gleicher wie im Bot-Service) |
| `ALLOWED_IDS` | Deine Telegram-ID, z.B. `123456789` (mehrere mit Komma) |
| `API_SECRET` | Irgendein langer zufälliger String |

Deine Telegram-ID kriegst du z.B. von @userinfobot.

**Wichtig:** Railway-Volume für die DB mounten (sonst ist alles nach jedem Deploy weg):
Service → Settings → Volumes → Mount Path z.B. `/data`, dann Env-Variable `DB_PATH=/data/tracker_db.json` setzen.

### 2. Domain holen

Railway → Settings → Networking → "Generate Domain".
Die URL merken, z.B. `https://tracker-production-xxxx.up.railway.app`

### 3. Bot verbinden

In `bot_snippet.py`:
- `ADMIN_ID` auf deine Telegram-ID setzen
- Die zwei Handler bei deinen anderen registrieren

Im Bot-Service auf Railway zwei Env-Variablen dazu:
- `TRACKER_URL` = deine Domain von Schritt 2
- `API_SECRET` = gleicher Wert wie beim Tracker

### 4. Testen

Bot anschreiben: `/tracker` → Button → App geht in Telegram auf. Fertig.

## Freundin (oder wen auch immer) einladen

```
/invite 987654321
```
(nur du als Admin kannst das)

## Bot legt automatisch Karten an

In deinem Bot dort, wo ein Bewerbungsschreiben fertig generiert wurde:

```python
create_tracker_card(owner_id=user_id, firma="Elektro Ekici", position="Lehre Elektrotechnik")
```

→ Karte erscheint automatisch unter "Beworben".

## Hinweise

- Jeder sieht nur die eigenen Bewerbungen (getrennt pro Telegram-ID)
- Ohne Whitelist-Eintrag: "Kein Zugang", auch wenn wer die URL kennt
- Design übernimmt automatisch dein Telegram-Theme (hell/dunkel)
- Optional: Bei BotFather → Bot Settings → Menu Button die URL eintragen,
  dann ist die App als Menü-Button unten links im Chat
