from services.market_data_utils import download_history


def fetch_data(ticker):
    try:
        df = download_history(ticker, period="1y")

        if df is None or df.empty:
            return None

        return df

    except Exception as e:
        print("Data error:", ticker, e)
        return None
