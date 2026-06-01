import csv
import io
import re

import requests
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LIVE_EQUITY_URLS = (
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
)
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/csv,application/octet-stream;q=0.9,*/*;q=0.8",
}


def _clean_symbol(value):
    symbol = str(value or "").strip().upper()
    if not symbol or symbol == "SYMBOL":
        return None

    if not re.fullmatch(r"[A-Z0-9&-]+", symbol):
        return None

    return f"{symbol}.NS"


def _normalize_row(row):
    return {str(key or "").strip().upper(): value for key, value in row.items()}


def _fetch_live_equity_symbols():
    errors = []

    for url in LIVE_EQUITY_URLS:
        try:
            response = requests.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=20,
                verify=False,
            )
            response.raise_for_status()

            reader = csv.DictReader(io.StringIO(response.text))
            symbols = []
            seen = set()

            for raw_row in reader:
                row = _normalize_row(raw_row)
                series = str(row.get("SERIES", "") or "").strip().upper()
                if series and series != "EQ":
                    continue

                symbol = _clean_symbol(row.get("SYMBOL"))
                if symbol and symbol not in seen:
                    seen.add(symbol)
                    symbols.append(symbol)

            if symbols:
                return symbols
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    raise RuntimeError(" | ".join(errors) if errors else "Unable to fetch live NSE equity list")


def _spread_symbols(symbols, limit):
    if limit is None:
        return symbols

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return symbols

    if limit <= 0 or len(symbols) <= limit:
        return symbols

    if limit == 1:
        return [symbols[len(symbols) // 2]]

    selected = []
    seen = set()
    max_index = len(symbols) - 1
    step = max_index / float(limit - 1)

    for i in range(limit):
        index = min(max_index, int(round(i * step)))
        symbol = symbols[index]
        if symbol not in seen:
            seen.add(symbol)
            selected.append(symbol)

    if len(selected) < limit:
        for symbol in symbols:
            if symbol in seen:
                continue
            seen.add(symbol)
            selected.append(symbol)
            if len(selected) >= limit:
                break

    return selected[:limit]


def get_all_stocks(limit=150):
    try:
        symbols = _fetch_live_equity_symbols()
        return _spread_symbols(symbols, limit)
    except Exception as e:
        print("NSE error:", e)
        return []
