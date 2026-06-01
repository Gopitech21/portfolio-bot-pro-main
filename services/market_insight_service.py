from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from functools import lru_cache

import yfinance as yf

from services.market_data_utils import download_history, get_series, get_yfinance_session
from services.news_service import analyze_sentiment, get_news
from services.nse_service import get_all_stocks


DEFAULT_LIVE_UNIVERSE_LIMIT = 60
MAX_PROFILE_WORKERS = 6
MIN_SECTOR_STOCKS = 3

SEASONAL_THEMES = [
    {
        "name": "Summer Power & Cooling",
        "months": [3, 4, 5],
        "sector_filters": ["Utilities", "Industrials", "Consumer Cyclical"],
        "industry_keywords": [
            "regulated electric",
            "independent power producers",
            "electrical equipment",
            "appliances",
            "building products",
        ],
        "name_keywords": ["power", "electric", "cool", "ac"],
        "min_match_score": 2,
        "summary": "Peak summer can lift power demand and help cooling-related businesses like utilities, electrical equipment, and appliance-facing names.",
    },
    {
        "name": "Monsoon & Rural Demand",
        "months": [6, 7, 8, 9],
        "sector_filters": ["Basic Materials", "Industrials", "Consumer Defensive", "Consumer Cyclical"],
        "industry_keywords": [
            "agricultural inputs",
            "farm products",
            "specialty chemicals",
            "heavy construction machinery",
            "packaged foods",
        ],
        "name_keywords": ["fert", "aqua", "feed", "marine", "rural"],
        "min_match_score": 2,
        "summary": "Rainfall and sowing trends can support fertilizer, agro-chemical, rural machinery, feed, and selected aquaculture-linked names.",
    },
    {
        "name": "Festive Consumption",
        "months": [9, 10, 11],
        "sector_filters": ["Consumer Cyclical", "Consumer Defensive"],
        "industry_keywords": [
            "auto manufacturers",
            "specialty retail",
            "department stores",
            "luxury goods",
            "appliances",
            "household",
        ],
        "name_keywords": ["retail", "jewel", "paint", "fashion"],
        "min_match_score": 2,
        "summary": "Festive demand can support autos, retail, jewellery, paints, and discretionary consumption stocks.",
    },
    {
        "name": "Budget & Capex",
        "months": [1, 2, 3],
        "sector_filters": ["Industrials", "Energy", "Utilities", "Real Estate"],
        "industry_keywords": [
            "engineering & construction",
            "aerospace & defense",
            "specialty industrial machinery",
            "railroads",
            "building products",
            "construction",
            "infrastructure",
        ],
        "name_keywords": ["infra", "rail", "defence", "defense", "power"],
        "min_match_score": 2,
        "summary": "Budget expectations and capex cycles can support infra, rail, defence, power, and construction-oriented names.",
    },
]


def _clean_label(ticker):
    return str(ticker or "").replace(".NS", "").replace(".BO", "")


def _normalize_text(value):
    return str(value or "").strip().lower()


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _performance_label(avg_day_change, breadth_pct):
    if avg_day_change >= 1.0 and breadth_pct >= 60:
        return "🟢 Strong"
    if avg_day_change > 0:
        return "🟡 Positive"
    if avg_day_change <= -1.0 and breadth_pct <= 40:
        return "🔴 Weak"
    return "🟠 Mixed"


def _theme_bias_label(stage, avg_day_change, avg_week_change, breadth_pct, news_sentiment):
    if stage == "Upcoming Next Month":
        if avg_week_change > 1.5 or "Positive" in news_sentiment:
            return "🟢 Early Build-up"
        if avg_day_change < 0 and avg_week_change < 0:
            return "🟠 Watchlist Forming"
        return "🟡 Preparing"

    if avg_day_change >= 1.0 and breadth_pct >= 60:
        return "🟢 Active & Strong"
    if avg_day_change > 0 or avg_week_change > 1.0:
        return "🟡 Active Watch"
    if "Negative" in news_sentiment:
        return "🔴 News Pressure"
    return "🟠 Active but Mixed"


def _month_window(months):
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    ordered = sorted(set(int(month) for month in months))
    if not ordered:
        return "N/A"
    if len(ordered) == 1:
        return month_labels[ordered[0] - 1]
    return f"{month_labels[ordered[0] - 1]}-{month_labels[ordered[-1] - 1]}"


@lru_cache(maxsize=512)
def _fetch_company_profile(ticker):
    try:
        stock = yf.Ticker(ticker, session=get_yfinance_session())
        info = stock.info or {}

        sector = str(info.get("sector") or "").strip()
        industry = str(info.get("industry") or "").strip()
        name = str(info.get("shortName") or info.get("longName") or ticker).strip()
        market_cap = _safe_float(info.get("marketCap"), 0.0)

        if not sector:
            return None

        return {
            "sector": sector,
            "industry": industry,
            "name": name,
            "market_cap": market_cap,
        }
    except Exception:
        return None


def _fetch_price_snapshot(ticker):
    try:
        df = download_history(ticker, period="1mo")
        close = get_series(df, "Close").dropna()
        volume = get_series(df, "Volume").dropna()

        if len(close) < 6:
            return None

        latest = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        week_ref = float(close.iloc[-6])
        ma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else latest
        day_change = ((latest - prev) / prev * 100) if prev else 0.0
        week_change = ((latest - week_ref) / week_ref * 100) if week_ref else 0.0

        vol_ratio = 0.0
        if len(volume) >= 6:
            avg_volume = float(volume.tail(10).mean())
            latest_volume = float(volume.iloc[-1])
            vol_ratio = (latest_volume / avg_volume) if avg_volume else 0.0

        return {
            "ticker": ticker,
            "symbol": _clean_label(ticker),
            "price": round(latest, 2),
            "day_change": round(day_change, 2),
            "week_change": round(week_change, 2),
            "above_ma20": latest >= ma20,
            "vol_ratio": round(vol_ratio, 2),
        }
    except Exception:
        return None


def _build_live_record(ticker):
    snapshot = _fetch_price_snapshot(ticker)
    profile = _fetch_company_profile(ticker)

    if not snapshot or not profile:
        return None

    return {
        **snapshot,
        **profile,
    }


def _resolve_live_tickers(tickers=None, limit=DEFAULT_LIVE_UNIVERSE_LIMIT):
    if tickers:
        ordered = []
        seen = set()
        for ticker in tickers:
            symbol = str(ticker or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            ordered.append(symbol)
        return ordered

    return get_all_stocks(limit=limit)


def _collect_live_records(tickers=None, limit=DEFAULT_LIVE_UNIVERSE_LIMIT, record_cache=None):
    resolved_tickers = _resolve_live_tickers(tickers=tickers, limit=limit)
    records = []
    pending = []

    for ticker in resolved_tickers:
        if record_cache is not None and ticker in record_cache:
            cached = record_cache[ticker]
            if cached:
                records.append(cached)
            continue
        pending.append(ticker)

    if pending:
        with ThreadPoolExecutor(max_workers=MAX_PROFILE_WORKERS) as executor:
            future_map = {executor.submit(_build_live_record, ticker): ticker for ticker in pending}

            for future in as_completed(future_map):
                ticker = future_map[future]
                try:
                    record = future.result()
                except Exception:
                    record = None

                if record_cache is not None:
                    record_cache[ticker] = record

                if record:
                    records.append(record)

    records.sort(
        key=lambda item: (
            item.get("day_change", 0.0),
            item.get("week_change", 0.0),
            item.get("market_cap", 0.0),
        ),
        reverse=True,
    )
    return records


def _build_sector_groups(records, top_stocks_per_sector, min_sector_stocks):
    sector_map = {}

    for record in records:
        sector_name = str(record.get("sector") or "").strip()
        if not sector_name:
            continue
        sector_map.setdefault(sector_name, []).append(record)

    sectors = []
    for sector_name, stocks in sector_map.items():
        if len(stocks) < min_sector_stocks:
            continue

        stocks = sorted(
            stocks,
            key=lambda item: (
                item.get("day_change", 0.0),
                item.get("week_change", 0.0),
                item.get("market_cap", 0.0),
            ),
            reverse=True,
        )

        avg_day_change = sum(item["day_change"] for item in stocks) / len(stocks)
        avg_week_change = sum(item["week_change"] for item in stocks) / len(stocks)
        breadth_pct = (sum(1 for item in stocks if item["day_change"] > 0) / len(stocks)) * 100

        sectors.append(
            {
                "sector": sector_name,
                "avg_day_change": round(avg_day_change, 2),
                "avg_week_change": round(avg_week_change, 2),
                "breadth_pct": round(breadth_pct, 1),
                "status": _performance_label(avg_day_change, breadth_pct),
                "leader": stocks[0]["symbol"],
                "leaders": ", ".join(item["symbol"] for item in stocks[:min(3, len(stocks))]),
                "stocks": stocks[:top_stocks_per_sector],
                "stock_count": len(stocks),
            }
        )

    sectors.sort(key=lambda item: (item["avg_day_change"], item["avg_week_change"], item["breadth_pct"]), reverse=True)
    return sectors


def _theme_match_score(record, theme):
    sector_text = _normalize_text(record.get("sector"))
    industry_text = _normalize_text(record.get("industry"))
    name_text = _normalize_text(record.get("name"))

    score = 0

    normalized_sector_filters = {_normalize_text(value) for value in theme.get("sector_filters", [])}
    if sector_text in normalized_sector_filters:
        score += 1

    for keyword in theme.get("industry_keywords", []):
        if _normalize_text(keyword) in industry_text:
            score += 2
            break

    for keyword in theme.get("name_keywords", []):
        if _normalize_text(keyword) in name_text:
            score += 1
            break

    return score


def _match_theme_stocks(records, theme, min_match_score):
    matched_stocks = []

    for record in records:
        match_score = _theme_match_score(record, theme)
        if match_score >= int(min_match_score):
            matched_stocks.append({**record, "theme_match_score": match_score})

    matched_stocks.sort(
        key=lambda item: (
            item.get("theme_match_score", 0),
            item.get("day_change", 0.0),
            item.get("week_change", 0.0),
            item.get("market_cap", 0.0),
        ),
        reverse=True,
    )
    return matched_stocks


def get_sector_pulse(max_sectors=None, top_stocks_per_sector=4, snapshot_cache=None, tickers=None, limit=DEFAULT_LIVE_UNIVERSE_LIMIT):
    records = _collect_live_records(tickers=tickers, limit=limit, record_cache=snapshot_cache)
    sectors = _build_sector_groups(records, top_stocks_per_sector=top_stocks_per_sector, min_sector_stocks=MIN_SECTOR_STOCKS)

    if not sectors:
        sectors = _build_sector_groups(records, top_stocks_per_sector=top_stocks_per_sector, min_sector_stocks=2)

    if not sectors:
        sectors = _build_sector_groups(records, top_stocks_per_sector=top_stocks_per_sector, min_sector_stocks=1)

    if max_sectors is not None:
        sectors = sectors[:max_sectors]

    return {
        "updated_at": datetime.now().strftime("%d %b %Y %I:%M:%S %p"),
        "record_count": len(records),
        "sectors": sectors,
    }


def get_seasonal_theme_watch(reference_date=None, top_stocks_per_theme=4, snapshot_cache=None, tickers=None, limit=DEFAULT_LIVE_UNIVERSE_LIMIT):
    current_date = reference_date or date.today()
    current_month = current_date.month
    next_month = 1 if current_month == 12 else current_month + 1
    include_upcoming = current_date.day >= 20

    records = _collect_live_records(tickers=tickers, limit=limit, record_cache=snapshot_cache)
    active = []
    upcoming = []

    for theme in SEASONAL_THEMES:
        months = theme.get("months", [])
        stage = None

        if current_month in months:
            stage = "Active Now"
        elif include_upcoming and next_month in months:
            stage = "Upcoming Next Month"
        else:
            continue

        matched_stocks = _match_theme_stocks(records, theme, min_match_score=theme.get("min_match_score", 2))

        if not matched_stocks:
            matched_stocks = _match_theme_stocks(records, theme, min_match_score=1)

        if not matched_stocks:
            continue

        avg_day_change = sum(item["day_change"] for item in matched_stocks) / len(matched_stocks)
        avg_week_change = sum(item["week_change"] for item in matched_stocks) / len(matched_stocks)
        breadth_pct = (sum(1 for item in matched_stocks if item["day_change"] > 0) / len(matched_stocks)) * 100

        headlines = []
        for stock in matched_stocks[:2]:
            stock_headlines, _ = get_news(stock["ticker"])
            headlines.extend(stock_headlines[:2])

        news_sentiment = analyze_sentiment(headlines) if headlines else "🟡 Neutral"

        item = {
            "theme": theme["name"],
            "stage": stage,
            "window": _month_window(months),
            "avg_day_change": round(avg_day_change, 2),
            "avg_week_change": round(avg_week_change, 2),
            "breadth_pct": round(breadth_pct, 1),
            "bias": _theme_bias_label(stage, avg_day_change, avg_week_change, breadth_pct, news_sentiment),
            "news_sentiment": news_sentiment,
            "related_sectors": ", ".join(theme.get("sector_filters", [])),
            "summary": theme.get("summary", ""),
            "leaders": ", ".join(stock["symbol"] for stock in matched_stocks[:min(3, len(matched_stocks))]),
            "stocks": matched_stocks[:top_stocks_per_theme],
        }

        if stage == "Active Now":
            active.append(item)
        else:
            upcoming.append(item)

    active.sort(key=lambda item: (item["avg_day_change"], item["avg_week_change"], item["breadth_pct"]), reverse=True)
    upcoming.sort(key=lambda item: (item["avg_week_change"], item["avg_day_change"], item["breadth_pct"]), reverse=True)

    return {
        "updated_at": datetime.now().strftime("%d %b %Y %I:%M:%S %p"),
        "record_count": len(records),
        "active": active,
        "upcoming": upcoming,
    }
