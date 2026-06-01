def get_portfolio_status(action, exit_signal, market_status, profit_pct, rsi):
    action_text = str(action).upper()
    exit_text = str(exit_signal).upper()
    regime = market_status.get("regime", "UNKNOWN")

    if any(token in action_text for token in ("SELL",)):
        return "SELL"

    if any(token in exit_text for token in ("BOOK PROFIT", "TRAILING STOP HIT", "STOP-LOSS HIT", "CAPITAL PROTECTION")):
        return "SELL"

    if "ADD MORE" in action_text:
        if profit_pct <= -12:
            return "WAIT"
        if regime in {"BULLISH", "NEUTRAL"} and rsi <= 55:
            return "BUY"
        return "WAIT"

    if regime == "BEARISH" and profit_pct < 0:
        return "WAIT"

    if regime in {"CAUTIOUS", "BEARISH"} and rsi > 60:
        return "WAIT"

    if "HOLD" in action_text:
        return "HOLD"

    if "WAIT" in action_text:
        return "WAIT"

    return "WAIT"
