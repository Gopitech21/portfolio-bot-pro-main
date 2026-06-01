from services.market_data_utils import download_history

def fetch_data(ticker):
    return download_history(ticker, period="1y", interval="1d")
