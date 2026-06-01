from services.market_data_utils import download_history, get_series


def _analyze_index(symbols, label):
    if isinstance(symbols, str):
        symbols = [symbols]

    last_error = None

    for symbol in symbols:
        try:
            df = download_history(symbol, period="6mo")
            close = get_series(df, "Close")

            if close.empty or len(close) < 50:
                raise ValueError(f"Not enough data for {label}")

            latest = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            ma20 = float(close.rolling(20).mean().iloc[-1])
            ma50 = float(close.rolling(50).mean().iloc[-1])
            week_change = float((latest - close.iloc[-5]) / close.iloc[-5] * 100)
            day_change = float((latest - prev) / prev * 100)

            return {
                "label": label,
                "symbol": symbol,
                "latest": latest,
                "day_change": day_change,
                "week_change": week_change,
                "above_ma20": latest > ma20,
                "above_ma50": latest > ma50,
            }
        except Exception as exc:
            last_error = exc

    raise ValueError(str(last_error) if last_error else f"Not enough data for {label}")


def get_market_status():
    try:
        nifty = _analyze_index(["^NSEI", "NIFTYBEES.NS"], "Nifty 50")
        bank_nifty = _analyze_index(["^NSEBANK", "BANKBEES.NS"], "Bank Nifty")

        score = 0
        for index_data in (nifty, bank_nifty):
            if index_data["above_ma20"]:
                score += 1
            if index_data["above_ma50"]:
                score += 1

        if nifty["week_change"] > 1:
            score += 1
        elif nifty["week_change"] < -1:
            score -= 1

        if nifty["day_change"] < -2:
            regime = "BEARISH"
            emoji = "🔴"
            action_bias = "Defensive"
        elif score >= 4:
            regime = "BULLISH"
            emoji = "🟢"
            action_bias = "Buy on strength"
        elif score <= 1:
            regime = "CAUTIOUS"
            emoji = "🟠"
            action_bias = "Wait for clarity"
        else:
            regime = "NEUTRAL"
            emoji = "🟡"
            action_bias = "Stock specific"

        return {
            "regime": regime,
            "emoji": emoji,
            "action_bias": action_bias,
            "score": score,
            "nifty": nifty,
            "bank_nifty": bank_nifty,
            "summary": (
                f"{emoji} Market: {regime} | "
                f"Nifty: {nifty['day_change']:.2f}% today, {nifty['week_change']:.2f}% 5D | "
                f"Bank Nifty: {bank_nifty['day_change']:.2f}% today"
            ),
        }
    except Exception as e:
        print("Market status error:", e)
        return {
            "regime": "UNKNOWN",
            "emoji": "⚪",
            "action_bias": "Market data unavailable",
            "score": 0,
            "nifty": None,
            "bank_nifty": None,
            "summary": "⚪ Market: UNKNOWN | Market data unavailable",
        }


def check_market_crash(market=None):
    if market is None:
        market = get_market_status()

    nifty = market.get("nifty")

    if nifty and nifty["day_change"] < -2:
        return f"🚨 Market Crash: {round(nifty['day_change'], 2)}%"

    return None
