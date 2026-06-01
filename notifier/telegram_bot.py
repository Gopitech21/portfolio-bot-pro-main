import requests
from config import TELEGRAM_TOKEN, CHAT_ID


MAX_MESSAGE_LEN = 3900


def _split_long_message(message, limit=MAX_MESSAGE_LEN):
    chunks = []
    current = ""

    for paragraph in message.split("\n\n"):
        candidate = paragraph if not current else current + "\n\n" + paragraph

        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(paragraph) <= limit:
            current = paragraph
            continue

        for line in paragraph.splitlines():
            line_candidate = line if not current else current + "\n" + line

            if len(line_candidate) <= limit:
                current = line_candidate
                continue

            if current:
                chunks.append(current)

            if len(line) <= limit:
                current = line
                continue

            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            current = ""

    if current:
        chunks.append(current)

    return chunks or [message]


def _telegram_url(method):
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"


def send_message(msg, chat_id=None):
    url = _telegram_url("sendMessage")
    destination_chat_id = chat_id or CHAT_ID
    chunks = _split_long_message(msg)

    print(f"📝 Telegram message preview ({len(chunks)} chunk(s))")

    for index, chunk in enumerate(chunks, start=1):
        print(f"--- TELEGRAM CHUNK {index}/{len(chunks)} START ---")
        print(chunk)
        print(f"--- TELEGRAM CHUNK {index}/{len(chunks)} END ---")

        response = requests.post(url, json={
            "chat_id": destination_chat_id,
            "text": chunk
        })

        if response.status_code != 200:
            print(f"❌ Telegram error: {response.status_code} - {response.text}")
            raise Exception(f"Failed to send message: {response.text}")

    print("✅ Message sent successfully")


def get_updates(offset=None, timeout=0):
    url = _telegram_url("getUpdates")
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset

    response = requests.get(url, params=params)

    if response.status_code != 200:
        print(f"❌ Telegram error: {response.status_code} - {response.text}")
        raise Exception(f"Failed to fetch updates: {response.text}")

    payload = response.json()
    if not payload.get("ok"):
        raise Exception(f"Failed to fetch updates: {payload}")

    updates = payload.get("result", [])
    if updates:
        last_update_id = updates[-1].get("update_id")
        if last_update_id is not None:
            requests.get(url, params={"offset": last_update_id + 1, "timeout": 0})

    return updates
