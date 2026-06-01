import re
from difflib import SequenceMatcher
from functools import lru_cache

import requests
import yfinance as yf

from services.market_data_utils import get_yfinance_session


YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0"}
NOISE_TOKENS = {
    "LIMITED",
    "LTD",
    "LIMITED.",
    "LTD.",
    "CORPORATION",
    "CORP",
    "COMPANY",
    "CO",
    "PVT",
    "PRIVATE",
    "THE",
    "OF",
}

KNOWN = {
    "ACI INFOCOM": "517356.BO",
    "ACI INFOCOM LTD": "517356.BO",
    "ADANI POWER": "ADANIPOWER.NS",
    "AXIS BANK": "AXISBANK.NS",
    "BAJAJ HOUSING FINANCE": "BAJAJHFL.NS",
    "BHARAT COKING COAL": "BHARATCOAL.NS",
    "BHARAT ELECTRONICS": "BEL.NS",
    "CENTRAL BANK INDIA": "CENTRALBK.NS",
    "CPSE ETF": "CPSEETF.NS",
    "DEEPAK FERTILIZERS AND PETR": "DEEPAKFERT.NS",
    "DEEPAK FERTILISERS PETROCHEMICALS": "DEEPAKFERT.NS",
    "EMPOWER INDIA": "504351.BO",
    "EMPOWER INDIA LTD": "504351.BO",
    "GAIL INDIA": "GAIL.NS",
    "G G ENGINEERING": "540614.BO",
    "G G ENGINEERING LIMITED": "540614.BO",
    "GARDEN REACH SHIP AND ENG": "GRSE.NS",
    "GARDEN REACH SHIPBUILDERS ENGINEERS": "GRSE.NS",
    "INDIAN RAILWAY FIN L": "IRFC.NS",
    "INDIAN RAILWAY FINANCE": "IRFC.NS",
    "INDITRADE CAPITAL": "532745.BO",
    "ITC": "ITC.NS",
    "JAIPRAKASH POWER VEN": "JPPOWER.NS",
    "JAIPRAKASH POWER VENTURES": "JPPOWER.NS",
    "MAZAGON DOCK SHIPBUIL": "MAZDOCK.NS",
    "MAZAGON DOCK SHIPBUILDERS": "MAZDOCK.NS",
    "MOTILAL OS NASDAQ100 ETF": "MON100.NS",
    "MOTILAL OSWAL NASDAQ 100 ETF": "MON100.NS",
    "MOTILALAMC MONQ50": "MONQ50.NS",
    "MOTILAL OSWAL NASDAQ Q 50 ETF": "MONQ50.NS",
    "NIP IND ETF BANK BEES": "BANKBEES.NS",
    "NIPPON INDIA ETF BANK BEES": "BANKBEES.NS",
    "NIP IND ETF GOLD BEES": "GOLDBEES.NS",
    "NIPPON INDIA ETF GOLD BEES": "GOLDBEES.NS",
    "NIP IND ETF NIFTY BEES": "NIFTYBEES.NS",
    "NIPPON INDIA ETF NIFTY BEES": "NIFTYBEES.NS",
    "NIPPONAMC NETFSILVER": "SILVERBEES.NS",
    "NIPPON INDIA ETF SILVER": "SILVERBEES.NS",
    "NTPC GREEN ENERGY": "NTPCGREEN.NS",
    "ONESOURCE INDUSTRIES AND VENTU": "530805.BO",
    "PC JEWELLER": "PCJEWELLER.NS",
    "NTPC": "NTPC.NS",
    "RAIL VIKAS NIGAM": "RVNL.NS",
    "SAMVRDHNA MTHRSN INTL": "MOTHERSON.NS",
    "SAMVARDHANA MOTHERSON INTERNATIONAL": "MOTHERSON.NS",
    "SEACOAST SHIPPING SERVICES LIM": "542753.BO",
    "SHAH METACORP": "SHAH.NS",
    "SHIPPING INDIA LT": "SCILAL.NS",
    "SHIPPING INDIA LAND ASSETS": "SCILAL.NS",
    "STATE BANK INDIA": "SBIN.NS",
    "UJJIVAN SMALL FINANC BANK": "UJJIVANSFB.NS",
    "UJJIVAN SMALL FINANCE BANK": "UJJIVANSFB.NS",
    "UTIAMC NEXT50BETA": "NEXT50BETA.NS",
    "UTI NIFTY NEXT 50 ETF": "NEXT50BETA.NS",
}


def normalize_name(name):
    text = str(name or "").upper()
    text = text.replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    parts = [part for part in text.split() if part and part not in NOISE_TOKENS]
    return " ".join(parts)


def _normalize_symbol(symbol):
    return str(symbol or "").upper().strip()


def _clean_text(value):
    if value is None:
        return ""

    text = str(value).strip()

    if not text or text.upper() in {"NAN", "NONE", "NULL"}:
        return ""

    return text


def _is_existing_symbol(value):
    symbol = _normalize_symbol(value)

    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return True

    if re.fullmatch(r"\d{6}", symbol):
        return True

    return bool(re.fullmatch(r"[A-Z0-9&-]{1,12}", symbol))


def _format_yahoo_symbol(symbol, quote):
    raw_symbol = _normalize_symbol(symbol)

    if not raw_symbol:
        return None

    if raw_symbol.endswith(".NS") or raw_symbol.endswith(".BO"):
        return raw_symbol

    if re.fullmatch(r"\d{6}", raw_symbol):
        return f"{raw_symbol}.BO"

    exchange_text = " ".join(
        str(quote.get(key, "") or "")
        for key in ("exchange", "fullExchangeName", "exchDisp", "exchangeDispTxt")
    ).upper()

    if "NSE" in exchange_text or "NATIONAL STOCK EXCHANGE" in exchange_text:
        return f"{raw_symbol}.NS"

    if "BSE" in exchange_text or "BOMBAY" in exchange_text:
        return f"{raw_symbol}.BO"

    return raw_symbol


def _is_indian_quote(quote):
    symbol = _normalize_symbol(quote.get("symbol"))
    exchange_text = " ".join(
        str(quote.get(key, "") or "")
        for key in ("exchange", "fullExchangeName", "exchDisp", "exchangeDispTxt")
    ).upper()

    return (
        symbol.endswith(".NS")
        or symbol.endswith(".BO")
        or "NSE" in exchange_text
        or "BSE" in exchange_text
        or "BOMBAY" in exchange_text
        or "INDIA" in exchange_text
    )


def _quote_name(quote):
    return normalize_name(
        quote.get("longname")
        or quote.get("shortname")
        or quote.get("name")
        or quote.get("description")
    )


def _score_quote(quote, target_name, preferred_exchange):
    if not _is_indian_quote(quote):
        return -1

    target = normalize_name(target_name)
    quote_name = _quote_name(quote)
    score = 0

    symbol = _format_yahoo_symbol(quote.get("symbol"), quote)
    if symbol:
        if preferred_exchange == "NSE" and symbol.endswith(".NS"):
            score += 25
        elif preferred_exchange == "BSE" and symbol.endswith(".BO"):
            score += 25
        else:
            score += 10

    quote_type = str(quote.get("quoteType", "") or "").upper()
    if quote_type in {"EQUITY", "ETF", "MUTUALFUND"}:
        score += 10

    if target and quote_name:
        if target == quote_name:
            score += 60

        target_tokens = set(target.split())
        quote_tokens = set(quote_name.split())

        if target_tokens:
            score += int(25 * (len(target_tokens & quote_tokens) / len(target_tokens)))

        score += int(SequenceMatcher(None, target, quote_name).ratio() * 25)

    return score


def _search_with_yfinance(query):
    try:
        search_cls = getattr(yf, "Search", None)
        if search_cls is None:
            return []

        search = search_cls(
            query=query,
            max_results=10,
            news_count=0,
            lists_count=0,
            include_nav_links=False,
            include_research=False,
            include_cultural_assets=False,
            enable_fuzzy_query=True,
            raise_errors=False,
            session=get_yfinance_session(),
        )
        quotes = getattr(search, "quotes", [])
        return quotes if isinstance(quotes, list) else []
    except Exception:
        return []


def _search_with_http(query):
    try:
        response = requests.get(
            YAHOO_SEARCH_URL,
            params={
                "q": query,
                "quotesCount": 10,
                "newsCount": 0,
                "listsCount": 0,
                "enableFuzzyQuery": "true",
            },
            headers=YAHOO_HEADERS,
            timeout=10,
            verify=False,
        )
        response.raise_for_status()
        payload = response.json()
        quotes = payload.get("quotes", [])
        return quotes if isinstance(quotes, list) else []
    except Exception:
        return []


def _search_quotes(query):
    quotes = _search_with_yfinance(query)

    if quotes:
        return quotes

    return _search_with_http(query)


def _pick_best_symbol(quotes, target_name, preferred_exchange):
    scored = []

    for quote in quotes:
        symbol = _format_yahoo_symbol(quote.get("symbol"), quote)
        if not symbol:
            continue

        score = _score_quote(quote, target_name, preferred_exchange)
        if score >= 0:
            scored.append((score, symbol))

    if not scored:
        return None

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


@lru_cache(maxsize=512)
def _resolve_symbol(name, isin=None, preferred_exchange="NSE"):
    if _is_existing_symbol(name):
        symbol = _normalize_symbol(name)
        if symbol.endswith(".NS") or symbol.endswith(".BO"):
            return symbol
        if re.fullmatch(r"\d{6}", symbol):
            return f"{symbol}.BO"
        return f"{symbol}.NS"

    normalized_name = normalize_name(name)
    if not normalized_name:
        return None

    if normalized_name in KNOWN:
        return KNOWN[normalized_name]

    if isin:
        symbol = _pick_best_symbol(_search_quotes(str(isin).strip()), name, preferred_exchange)
        if symbol:
            return symbol

    symbol = _pick_best_symbol(_search_quotes(str(name).strip()), name, preferred_exchange)
    if symbol:
        return symbol

    return None


def map_to_ticker(name, isin=None, preferred_exchange="NSE"):
    return _resolve_symbol(_clean_text(name), _clean_text(isin), preferred_exchange)
