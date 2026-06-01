from functools import lru_cache
from math import sqrt

import pandas as pd
import requests
import urllib3
import yfinance as yf

from services.market_data_utils import download_history, get_series, get_yfinance_session


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MUTUAL_FUND_API_BASE = "https://api.mfapi.in/mf"

ETF_UNIVERSE = [
    {"name": "Nippon India ETF Nifty 50 BeES", "ticker": "NIFTYBEES.NS", "category": "Broad Market"},
    {"name": "UTI Nifty Next 50 ETF", "ticker": "NEXT50BETA.NS", "category": "Broad Market"},
    {"name": "Nippon India ETF Nifty Bank BeES", "ticker": "BANKBEES.NS", "category": "Sector"},
    {"name": "CPSE ETF", "ticker": "CPSEETF.NS", "category": "PSU"},
    {"name": "Bharat 22 ETF", "ticker": "ICICIB22.NS", "category": "PSU"},
    {"name": "Motilal Oswal Nasdaq 100 ETF", "ticker": "MON100.NS", "category": "International"},
    {"name": "Motilal Oswal Nasdaq Q 50 ETF", "ticker": "MONQ50.NS", "category": "International"},
    {"name": "Nippon India ETF Gold BeES", "ticker": "GOLDBEES.NS", "category": "Commodity"},
    {"name": "Nippon India ETF Silver BeES", "ticker": "SILVERBEES.NS", "category": "Commodity"},
    {"name": "Aditya Birla Sun Life Nifty Next 50 ETF", "ticker": "ABSLNN50ET.NS", "category": "Broad Market"},
]

MUTUAL_FUND_UNIVERSE = [
    {"scheme_code": 122639, "label": "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"},
    {"scheme_code": 118955, "label": "HDFC Flexi Cap Fund - Direct Plan - Growth"},
    {"scheme_code": 120586, "label": "ICICI Prudential Large Cap Fund - Direct Plan - Growth"},
    {"scheme_code": 118825, "label": "Mirae Asset Large Cap Fund - Direct Plan - Growth"},
    {"scheme_code": 127042, "label": "Motilal Oswal Midcap Fund - Direct Plan - Growth"},
    {"scheme_code": 125497, "label": "SBI Small Cap Fund - Direct Plan - Growth"},
    {"scheme_code": 118778, "label": "Nippon India Small Cap Fund - Direct Plan - Growth"},
    {"scheme_code": 125354, "label": "Axis Small Cap Fund - Direct Plan - Growth"},
    {"scheme_code": 119063, "label": "HDFC Nifty 50 Index Fund - Direct Plan"},
    {"scheme_code": 119827, "label": "SBI Nifty Index Fund - Direct Plan - Growth"},
    {"scheme_code": 118741, "label": "Nippon India Index Fund - Nifty 50 Plan - Direct Plan - Growth"},
]

QUARTERLY_ROW_CANDIDATES = {
    "Revenue": ["Total Revenue", "Operating Revenue", "Revenue"],
    "Operating Income": ["Operating Income", "EBIT", "Normalized EBITDA"],
    "Net Income": [
        "Net Income",
        "Net Income Common Stockholders",
        "Net Income Including Noncontrolling Interests",
    ],
    "EBITDA": ["EBITDA", "Normalized EBITDA"],
    "EPS": ["Diluted EPS", "Basic EPS"],
}


def _safe_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_pct_change(current, previous):
    current_value = _safe_number(current)
    previous_value = _safe_number(previous)

    if current_value is None or previous_value in (None, 0):
        return None

    return (current_value - previous_value) / abs(previous_value) * 100


def _return_for_offset(series, offset):
    if len(series) <= offset:
        return None

    latest = _safe_number(series.iloc[-1])
    prior = _safe_number(series.iloc[-(offset + 1)])
    return _safe_pct_change(latest, prior)


def _positive_months_pct(series):
    if len(series) < 60:
        return None

    monthly = series.resample("ME").last().pct_change().dropna()
    if monthly.empty:
        return None

    return float((monthly > 0).mean() * 100)


def _max_drawdown_pct(series):
    if series.empty:
        return None

    rolling_peak = series.cummax()
    drawdown = (series / rolling_peak - 1.0).min()
    if pd.isna(drawdown):
        return None

    return float(drawdown * 100)


def _annualized_volatility_pct(series):
    returns = series.pct_change().dropna()
    if returns.empty:
        return None

    return float(returns.std() * sqrt(252) * 100)


def _prepare_series(series):
    cleaned = pd.Series(series).dropna().astype("float64")
    cleaned.index = pd.to_datetime(cleaned.index)
    return cleaned.sort_index()


def _build_metrics(series):
    history = _prepare_series(series)
    if len(history) < 120:
        return None

    last_year = history.tail(252)
    ma50 = history.rolling(50).mean().iloc[-1] if len(history) >= 50 else None
    ma200 = history.rolling(200).mean().iloc[-1] if len(history) >= 200 else None

    return {
        "latest_value": float(history.iloc[-1]),
        "return_1m": _return_for_offset(history, 21),
        "return_3m": _return_for_offset(history, 63),
        "return_6m": _return_for_offset(history, 126),
        "return_1y": _return_for_offset(history, 252),
        "ma50": _safe_number(ma50),
        "ma200": _safe_number(ma200),
        "max_drawdown_pct": _max_drawdown_pct(last_year),
        "volatility_pct": _annualized_volatility_pct(last_year),
        "positive_months_pct": _positive_months_pct(history),
    }


def _score_metrics(metrics):
    score = 0
    strengths = []
    cautions = []

    return_1y = metrics.get("return_1y")
    return_6m = metrics.get("return_6m")
    latest_value = metrics.get("latest_value")
    ma50 = metrics.get("ma50")
    ma200 = metrics.get("ma200")
    positive_months_pct = metrics.get("positive_months_pct")
    max_drawdown_pct = metrics.get("max_drawdown_pct")
    volatility_pct = metrics.get("volatility_pct")

    if return_1y is not None:
        if return_1y >= 20:
            score += 25
            strengths.append("strong 1Y return")
        elif return_1y >= 12:
            score += 20
            strengths.append("healthy 1Y return")
        elif return_1y >= 6:
            score += 12
        elif return_1y >= 0:
            score += 6
        else:
            cautions.append("negative 1Y return")

    if return_6m is not None:
        if return_6m >= 10:
            score += 15
            strengths.append("solid 6M momentum")
        elif return_6m >= 5:
            score += 10
        elif return_6m >= 0:
            score += 5
        else:
            cautions.append("weak recent momentum")

    if latest_value is not None and ma50 is not None:
        if latest_value > ma50:
            score += 10
            strengths.append("above 50DMA/NAV trend")
        else:
            cautions.append("below 50DMA/NAV trend")

    if ma50 is not None and ma200 is not None:
        if ma50 > ma200:
            score += 10
            strengths.append("50DMA above 200DMA")
        else:
            cautions.append("long trend still soft")

    if positive_months_pct is not None:
        if positive_months_pct >= 65:
            score += 15
            strengths.append("good monthly consistency")
        elif positive_months_pct >= 55:
            score += 10
        elif positive_months_pct >= 45:
            score += 5

    if max_drawdown_pct is not None:
        if max_drawdown_pct >= -10:
            score += 15
            strengths.append("drawdown kept contained")
        elif max_drawdown_pct >= -18:
            score += 10
        elif max_drawdown_pct >= -25:
            score += 5
        else:
            cautions.append("deep drawdown over the last year")

    if volatility_pct is not None:
        if volatility_pct <= 18:
            score += 10
        elif volatility_pct <= 25:
            score += 6
        elif volatility_pct <= 35:
            score += 3
        else:
            cautions.append("high volatility")

    if score >= 70:
        verdict = "Strong"
    elif score >= 55:
        verdict = "Good"
    elif score >= 40:
        verdict = "Watch"
    else:
        verdict = "Weak"

    analysis_bits = strengths[:2]
    if cautions:
        analysis_bits.append(cautions[0])

    analysis = "; ".join(analysis_bits) if analysis_bits else "Mixed setup with limited edge right now."
    return score, verdict, analysis


def _rank_results(results):
    if not results:
        return []

    ranked = []
    for category in sorted({item["Category"] for item in results}):
        category_rows = [row for row in results if row["Category"] == category]
        category_rows.sort(
            key=lambda row: (
                row["Score"],
                row.get("1Y %") if row.get("1Y %") is not None else float("-inf"),
            ),
            reverse=True,
        )
        for index, row in enumerate(category_rows, start=1):
            row["Category Rank"] = index
            ranked.append(row)

    ranked.sort(
        key=lambda row: (
            row["Score"],
            row.get("1Y %") if row.get("1Y %") is not None else float("-inf"),
        ),
        reverse=True,
    )
    return ranked


def scan_etfs():
    results = []

    for item in ETF_UNIVERSE:
        try:
            history = download_history(item["ticker"], period="2y")
            close = get_series(history, "Close").dropna()
            metrics = _build_metrics(close)
            if not metrics:
                continue

            score, verdict, analysis = _score_metrics(metrics)
            results.append(
                {
                    "Name": item["name"],
                    "Ticker": item["ticker"],
                    "Category": item["category"],
                    "Price": round(metrics["latest_value"], 2),
                    "1M %": round(metrics["return_1m"], 2) if metrics["return_1m"] is not None else None,
                    "3M %": round(metrics["return_3m"], 2) if metrics["return_3m"] is not None else None,
                    "6M %": round(metrics["return_6m"], 2) if metrics["return_6m"] is not None else None,
                    "1Y %": round(metrics["return_1y"], 2) if metrics["return_1y"] is not None else None,
                    "Max DD %": round(metrics["max_drawdown_pct"], 2) if metrics["max_drawdown_pct"] is not None else None,
                    "Volatility %": round(metrics["volatility_pct"], 2) if metrics["volatility_pct"] is not None else None,
                    "Positive Months %": round(metrics["positive_months_pct"], 1) if metrics["positive_months_pct"] is not None else None,
                    "Score": score,
                    "Verdict": verdict,
                    "Analysis": analysis,
                }
            )
        except Exception:
            continue

    return _rank_results(results)


@lru_cache(maxsize=64)
def get_mutual_fund_history(scheme_code):
    response = requests.get(f"{MUTUAL_FUND_API_BASE}/{scheme_code}", timeout=30, verify=False)
    response.raise_for_status()
    payload = response.json()

    meta = payload.get("meta", {})
    data = pd.DataFrame(payload.get("data", []))
    if data.empty:
        return meta, pd.Series(dtype="float64")

    data["date"] = pd.to_datetime(data["date"], format="%d-%m-%Y", errors="coerce")
    data["nav"] = pd.to_numeric(data["nav"], errors="coerce")
    data = data.dropna(subset=["date", "nav"]).sort_values("date")
    nav_series = data.set_index("date")["nav"]
    return meta, nav_series


def scan_mutual_funds():
    results = []

    for fund in MUTUAL_FUND_UNIVERSE:
        try:
            meta, nav_series = get_mutual_fund_history(fund["scheme_code"])
            metrics = _build_metrics(nav_series)
            if not metrics:
                continue

            score, verdict, analysis = _score_metrics(metrics)
            category = meta.get("scheme_category") or meta.get("scheme_type") or "Mutual Fund"
            results.append(
                {
                    "Name": meta.get("scheme_name") or fund["label"],
                    "Fund House": meta.get("fund_house") or "",
                    "Scheme Code": fund["scheme_code"],
                    "Category": category,
                    "NAV": round(metrics["latest_value"], 2),
                    "1M %": round(metrics["return_1m"], 2) if metrics["return_1m"] is not None else None,
                    "3M %": round(metrics["return_3m"], 2) if metrics["return_3m"] is not None else None,
                    "6M %": round(metrics["return_6m"], 2) if metrics["return_6m"] is not None else None,
                    "1Y %": round(metrics["return_1y"], 2) if metrics["return_1y"] is not None else None,
                    "Max DD %": round(metrics["max_drawdown_pct"], 2) if metrics["max_drawdown_pct"] is not None else None,
                    "Volatility %": round(metrics["volatility_pct"], 2) if metrics["volatility_pct"] is not None else None,
                    "Positive Months %": round(metrics["positive_months_pct"], 1) if metrics["positive_months_pct"] is not None else None,
                    "Score": score,
                    "Verdict": verdict,
                    "Analysis": analysis,
                }
            )
        except Exception:
            continue

    return _rank_results(results)


def _pick_first_row(frame, candidates):
    for candidate in candidates:
        if candidate in frame.index:
            row = pd.to_numeric(frame.loc[candidate], errors="coerce").dropna()
            if not row.empty:
                return row
    return pd.Series(dtype="float64")


def get_quarterly_results(ticker):
    stock = yf.Ticker(ticker, session=get_yfinance_session())

    income_frame = stock.quarterly_income_stmt
    if income_frame is None or income_frame.empty:
        income_frame = stock.quarterly_financials

    if income_frame is None or income_frame.empty:
        return {
            "ticker": ticker,
            "quarterly_table": pd.DataFrame(),
            "latest_quarter": None,
            "summary": {},
            "notes": "Quarterly financial statements are not available for this symbol.",
        }

    selected_rows = {}
    for label, candidates in QUARTERLY_ROW_CANDIDATES.items():
        row = _pick_first_row(income_frame, candidates)
        if not row.empty:
            selected_rows[label] = row

    quarterly_table = pd.DataFrame(selected_rows)
    if quarterly_table.empty:
        return {
            "ticker": ticker,
            "quarterly_table": pd.DataFrame(),
            "latest_quarter": None,
            "summary": {},
            "notes": "Quarterly data exists but the key income statement rows could not be mapped.",
        }

    quarterly_table.index = pd.to_datetime(quarterly_table.index)
    quarterly_table = quarterly_table.sort_index()

    latest_row = quarterly_table.iloc[-1]
    previous_row = quarterly_table.iloc[-2] if len(quarterly_table) >= 2 else pd.Series(dtype="float64")
    year_ago_row = quarterly_table.iloc[-5] if len(quarterly_table) >= 5 else pd.Series(dtype="float64")

    summary = {}
    for metric in ["Revenue", "Operating Income", "Net Income", "EBITDA", "EPS"]:
        if metric in quarterly_table.columns:
            summary[metric] = _safe_number(latest_row.get(metric))
            summary[f"{metric} QoQ %"] = _safe_pct_change(latest_row.get(metric), previous_row.get(metric))
            summary[f"{metric} YoY %"] = _safe_pct_change(latest_row.get(metric), year_ago_row.get(metric))

    notes = "Quarterly income statement values are available."
    return {
        "ticker": ticker,
        "quarterly_table": quarterly_table,
        "latest_quarter": quarterly_table.index[-1],
        "summary": summary,
        "notes": notes,
    }
