import json
import os

FILE = "state.json"


def load():
    if os.path.exists(FILE):
        return json.load(open(FILE))
    return {}


def save(data):
    json.dump(data, open(FILE, "w"))


def should_alert(ticker, signal):
    state = load()

    if state.get(ticker) != signal:
        state[ticker] = signal
        save(state)
        return True

    return False
