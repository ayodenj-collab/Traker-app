"""
Das hier in deinen Cheeworker-Bot einbauen (python-telegram-bot).

Env-Variablen im Bot-Service:
  TRACKER_URL  – z.B. https://dein-tracker.up.railway.app
  API_SECRET   – gleicher Wert wie beim Tracker-Service
"""

import os

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import CommandHandler, ContextTypes

TRACKER_URL = os.environ.get("TRACKER_URL", "")
API_SECRET = os.environ.get("API_SECRET", "")

ADMIN_ID = 123456789  # <-- deine Telegram-ID eintragen


# ---------------------------------------------------- /tracker – App öffnen

async def tracker_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📋 Bewerbungen öffnen", web_app=WebAppInfo(url=TRACKER_URL))
    ]])
    await update.message.reply_text("Dein Bewerbungs-Tracker:", reply_markup=keyboard)


# ------------------------------------- /invite <telegram_id> – User freischalten

async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Nur der Admin kann einladen.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("So: /invite 123456789")
        return
    user_id = int(context.args[0])
    r = requests.post(
        f"{TRACKER_URL}/api/bot/invite",
        json={"user_id": user_id},
        headers={"X-Api-Secret": API_SECRET},
        timeout=10,
    )
    if r.ok:
        await update.message.reply_text(f"✅ {user_id} freigeschaltet.")
    else:
        await update.message.reply_text(f"Fehler: {r.status_code} {r.text}")


# ------------------------------ Karte automatisch anlegen (nach Bewerbung generieren)

def create_tracker_card(owner_id: int, firma: str, position: str = "", notizen: str = ""):
    """
    Diese Funktion dort aufrufen, wo dein Bot ein Bewerbungsschreiben fertig generiert hat.
    Legt automatisch eine Karte mit Status 'beworben' an.
    """
    requests.post(
        f"{TRACKER_URL}/api/bot/applications",
        json={
            "owner_id": owner_id,
            "firma": firma,
            "position": position,
            "status": "beworben",
            "notizen": notizen,
            "kontakt": "",
            "datum": "",  # leer = heute
        },
        headers={"X-Api-Secret": API_SECRET},
        timeout=10,
    )


# -------------------------------------------------- Handler registrieren
# Bei deinen anderen Handlern dazu:
#
#   application.add_handler(CommandHandler("tracker", tracker_cmd))
#   application.add_handler(CommandHandler("invite", invite_cmd))
