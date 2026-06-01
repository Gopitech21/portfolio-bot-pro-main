from services.llm_service import generate_text


MAX_SCORE = 5.5


def calculate_base_score(price, ma20, ma50, change, volume_spike):
    score = 0

    if price > ma20:
        score += 1
    if price > ma50:
        score += 1
    if change > 2:
        score += 1
    if volume_spike:
        score += 1

    return score


def ai_score_boost(ticker, change):
    try:
        prompt = f"""
Stock: {ticker}
Momentum: {change}%

Return score between 0 and 1 only.
"""

        val = float(generate_text(prompt))

        return max(0, min(1, val))

    except Exception:
        return 0.5


def final_score(base, ai):
    return round(base + ai, 2)


def get_confidence(score):
    if MAX_SCORE <= 0:
        return 0

    confidence = int(round((score / MAX_SCORE) * 100))
    return max(0, min(100, confidence))


def get_entry_signal(rsi, change):
    if rsi < 40 and change > 0:
        return "🟢 Buy Now"
    elif rsi < 60:
        return "🟡 Wait"
    else:
        return "🔴 Avoid"
