from functools import lru_cache

import pandas as pd
import yfinance as yf
import urllib3
from curl_cffi import requests as curl_requests


PRICE_COLUMNS = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@lru_cache(maxsize=1)
def get_yfinance_session():
    session = curl_requests.Session(impersonate="chrome")
    session.verify = False
    return session


def normalize_download_frame(df):
    if df is None or df.empty:
        return df

    if isinstance(df.columns, pd.MultiIndex):
        first_level = {str(value) for value in df.columns.get_level_values(0)}
        second_level = {str(value) for value in df.columns.get_level_values(1)}

        if PRICE_COLUMNS.intersection(first_level):
            df.columns = df.columns.get_level_values(0)
        elif PRICE_COLUMNS.intersection(second_level):
            df.columns = df.columns.get_level_values(1)

    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    return df


def download_history(ticker, period="6mo", interval="1d"):
    df = yf.download(
        ticker,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
        session=get_yfinance_session(),
    )
    return normalize_download_frame(df)


def get_series(df, column):
    if df is None or df.empty or column not in df.columns:
        return pd.Series(dtype="float64")

    series = df[column]

    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]

    return series


def get_last_float(df, column):
    series = get_series(df, column)

    if series.empty:
        raise ValueError(f"{column} data is unavailable")

    return float(series.iloc[-1])
