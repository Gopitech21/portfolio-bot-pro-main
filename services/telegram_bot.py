import os
import requests


def send_message(msg):
    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    requests.post(url, json={
        "chat_id": chat,
        "text": msg
    })
