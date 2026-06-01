from services.market_data_utils import download_history, get_series

# sample watchlist (expand later)
WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS",
    "HDFCBANK.NS", "ICICIBANK.NS",
    "ITC.NS", "LT.NS", "SBIN.NS",
    "AXISBANK.NS", "BHARTIARTL.NS"
]


def scan_market():
    results = []

    for ticker in WATCHLIST:
        try:
            df = download_history(ticker, period="3mo")

            if df.empty:
                continue

            close = get_series(df, "Close")
            if close.empty or len(close) < 20:
                continue

            price = float(close.iloc[-1])
            ma20 = float(close.rolling(20).mean().iloc[-1])

            score = 0

            if price > ma20:
                score += 1

            change = float((price - close.iloc[-5]) / close.iloc[-5] * 100)

            if change > 3:
                score += 1

            results.append({
                "ticker": ticker,
                "score": score,
                "change": round(change, 2)
            })

        except Exception:
            continue

    return sorted(results, key=lambda x: x['score'], reverse=True)
