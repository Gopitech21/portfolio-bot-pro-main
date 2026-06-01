from strategies.strategy import apply_strategy
from services.news_service import get_news
from services.varsity_logic_service import build_long_trade_context


def get_signal(data, ticker):
    data = apply_strategy(data)

    latest = data.iloc[-1]

    def safe_float(val):
        if hasattr(val, "iloc"):
            val = val.iloc[0]
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    rsi = safe_float(latest["rsi"])
    macd = safe_float(latest["macd"])
    signal = safe_float(latest["signal"])
    price = safe_float(latest["Close"])
    bb_upper = safe_float(latest.get("bb_upper", float("inf")))
    ema_20 = safe_float(latest.get("ema_20", price))

    pivot = safe_float(latest.get("pivot", 0))
    s1 = safe_float(latest.get("s1", 0))
    r1 = safe_float(latest.get("r1", 0))

    sr_status = ""
    if pivot > 0:
        if price < pivot and price < s1:
            sr_status = "[S/R: Bearish]"
        elif price > pivot and price > r1:
            sr_status = "[S/R: Bullish]"
        else:
            sr_status = "[S/R: Neutral]"

    news, sentiment = get_news(ticker)
    context = build_long_trade_context(data, min_rr=1.5)
    rr_ratio = context.get("rr_ratio", 0.0)
    trend = context.get("trend", "unknown")
    volume_confirmed = context.get("volume_confirmed", False)
    stop_price = context.get("stop_price", 0.0)

    if stop_price > 0 and price <= stop_price:
        action = f"🔴 SELL (Support / Stop Breakdown) {sr_status}"
    elif rsi >= 70 and (price >= bb_upper or macd < signal or trend in {"bearish", "strong_bearish"}):
        action = f"🔴 SELL (Overbought / Trend Weakness) {sr_status}"
    elif (
        rsi <= 35
        and macd > signal
        and volume_confirmed
        and rr_ratio >= 1.5
        and trend in {"bullish", "strong_bullish"}
    ):
        action = f"🟢 ADD MORE (Checklist Confirmed | RRR {rr_ratio:.2f}) {sr_status}"
    elif (
        rsi <= 45
        and macd > signal
        and price > ema_20
        and volume_confirmed
        and rr_ratio >= 1.5
        and trend != "bearish"
    ):
        action = f"🟢 ADD MORE (Recovery + Volume | RRR {rr_ratio:.2f}) {sr_status}"
    elif rsi <= 45 and macd > signal and not volume_confirmed:
        action = f"🟠 WAIT (Low Volume Setup) {sr_status}"
    elif rsi <= 45 and rr_ratio < 1.5:
        action = f"🟠 WAIT (Poor Risk/Reward) {sr_status}"
    elif trend in {"bullish", "strong_bullish"} and macd > signal:
        action = f"🟡 HOLD (Trend Positive | RRR {rr_ratio:.2f}) {sr_status}"
    else:
        action = f"🟡 HOLD {sr_status}"

    return action, news, sentiment, rsi
