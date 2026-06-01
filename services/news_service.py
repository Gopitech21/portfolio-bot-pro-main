import yfinance as yf
from services.market_data_utils import get_yfinance_session

positive_words = ["growth","profit","gain","strong","beat","expansion","upgrade"]
negative_words = ["loss","decline","drop","weak","fraud","downgrade","fall"]

def analyze_sentiment(headlines):
    score = 0
    for h in headlines:
        h = h.lower()
        for w in positive_words:
            if w in h: score += 1
        for w in negative_words:
            if w in h: score -= 1
    if score > 1: return "🟢 Positive"
    elif score < -1: return "🔴 Negative"
    else: return "🟡 Neutral"

def get_news(ticker):
    try:
        stock = yf.Ticker(ticker, session=get_yfinance_session())
        news = stock.news or []

        headlines = []

        for item in news[:3]:
            title = item.get('title')
            if title:
                headlines.append(title)

        return headlines, analyze_sentiment(headlines)

    except Exception:
        return [], "🟡 Neutral"
