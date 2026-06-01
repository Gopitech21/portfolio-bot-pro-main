def _safe_float(value, default=0.0):
    if hasattr(value, "iloc"):
        value = value.iloc[0]

    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_risk_reward(entry_price, stop_loss, target_price):
    entry = _safe_float(entry_price, 0.0)
    stop = _safe_float(stop_loss, 0.0)
    target = _safe_float(target_price, 0.0)

    risk = entry - stop
    reward = target - entry

    if entry <= 0 or risk <= 0 or reward <= 0:
        return 0.0

    return round(reward / risk, 2)


def calculate_position_size(capital, entry_price, stop_loss, risk_fraction=0.01, leverage=1.0):
    capital = max(_safe_float(capital, 0.0), 0.0)
    entry = _safe_float(entry_price, 0.0)
    stop = _safe_float(stop_loss, 0.0)
    leverage = max(_safe_float(leverage, 1.0), 1.0)
    risk_fraction = max(_safe_float(risk_fraction, 0.01), 0.0)

    if capital <= 0 or entry <= 0:
        return {
            "risk_budget": 0.0,
            "risk_per_share": 0.0,
            "quantity": 0,
            "capital_required": 0.0,
        }

    risk_budget = capital * risk_fraction
    risk_per_share = max(entry - stop, entry * 0.01, 0.01)
    quantity = int(risk_budget / risk_per_share)

    if quantity <= 0:
        quantity = 1

    total_value = quantity * entry
    capital_required = total_value / leverage

    return {
        "risk_budget": round(risk_budget, 2),
        "risk_per_share": round(risk_per_share, 2),
        "quantity": quantity,
        "capital_required": round(capital_required, 2),
    }


def calculate_stop_loss(avg_price, current_price, stop_price=None):
    avg = _safe_float(avg_price, 0.0)
    current = _safe_float(current_price, 0.0)
    derived_stop = _safe_float(stop_price, 0.0)

    if derived_stop <= 0 and avg > 0:
        derived_stop = avg * 0.92

    if derived_stop <= 0:
        return "⚪ Stop unavailable"

    if current <= derived_stop:
        return f"🚨 Stop-Loss Hit (₹{derived_stop:.2f})"

    cushion_pct = ((current - derived_stop) / derived_stop) * 100 if derived_stop else 0.0
    if cushion_pct <= 2:
        return f"⚠️ Near Stop-Loss (₹{derived_stop:.2f})"

    return f"✅ Protected above ₹{derived_stop:.2f}"


def risk_level(rsi, profit_pct, rr_ratio=None, volume_confirmed=None, trend=None):
    rsi = _safe_float(rsi, 50.0)
    profit_pct = _safe_float(profit_pct, 0.0)
    rr_ratio = _safe_float(rr_ratio, 0.0) if rr_ratio is not None else None

    if profit_pct <= -12:
        return "🔴 High Loss Risk"
    if rsi >= 75:
        return "🔴 High Risk (Overbought)"
    if rr_ratio is not None and 0 < rr_ratio < 1.0 and profit_pct <= 0:
        return "🔴 Poor Risk/Reward"
    if trend == "sideways":
        return "🟠 Sideways / Low Edge"
    if volume_confirmed is False:
        return "🟠 Low Volume Risk"
    if rsi <= 35 and (rr_ratio is None or rr_ratio >= 1.5):
        return "🟢 Opportunity Zone"
    return "🟡 Moderate"
