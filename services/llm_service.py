import os
from google import genai


MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None


def generate_text(prompt):
    if client is None:
        raise RuntimeError("Gemini API key is not configured")

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
    )
    return (response.text or "").strip()


def build_fallback_advice(ticker, price, rsi, action, news):
    tone = "stable"
    if rsi >= 70:
        tone = "overbought"
    elif rsi <= 35:
        tone = "oversold"

    news_text = ""
    if news:
        news_text = " Watch related news flow closely."

    action_text = str(action).upper()
    if "SELL" in action_text:
        return f"{ticker} looks {tone}; protect gains and review exit levels.{news_text}".strip()
    if "ADD MORE" in action_text:
        return f"{ticker} looks {tone}; accumulation can be considered on confirmation.{news_text}".strip()
    return f"{ticker} looks {tone}; hold and track price near {round(price, 2)} for the next move.{news_text}".strip()


def analyze_stock(ticker, price, rsi, action, news):
    try:
        prompt = f"""
Analyze stock:
{ticker}, price {price}, RSI {rsi}, signal {action}

Give short advice.
"""

        return generate_text(prompt)

    except Exception:
        return build_fallback_advice(ticker, price, rsi, action, news)
