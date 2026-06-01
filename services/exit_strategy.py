def _safe_float(value, default=0.0):
    if hasattr(value, "iloc"):
        value = value.iloc[0]

    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def get_exit_signal(price, avg_price, rsi, highest_price, stop_price=None, ema_20=None, ema_50=None):
    price = _safe_float(price, 0.0)
    avg_price = _safe_float(avg_price, 0.0)
    rsi = _safe_float(rsi, 50.0)
    highest_price = max(_safe_float(highest_price, price), price)
    stop_price = _safe_float(stop_price, 0.0) if stop_price is not None else 0.0
    ema_20 = _safe_float(ema_20, 0.0) if ema_20 is not None else 0.0
    ema_50 = _safe_float(ema_50, 0.0) if ema_50 is not None else 0.0

    if avg_price <= 0 or price <= 0:
        return "⚪ Hold"

    profit_pct = ((price - avg_price) / avg_price) * 100
    trailing_sl = highest_price * 0.95

    if stop_price > 0 and price <= stop_price:
        return "🚨 Stop-Loss Hit"

    if price <= trailing_sl and highest_price > avg_price * 1.03:
        return "🔻 Trailing Stop Hit"

    if ema_20 > 0 and ema_50 > 0 and ema_20 < ema_50 and profit_pct > 0:
        return "⚠️ Trend Weakening"

    if rsi >= 75 and profit_pct >= 8:
        return "💰 Book Profit"

    if rsi >= 68 and profit_pct >= 4:
        return "⚠️ Partial Profit"

    if profit_pct <= -8:
        return "⚠️ Capital Protection"

    return "🟢 Hold"
