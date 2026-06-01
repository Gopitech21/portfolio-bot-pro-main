from functools import lru_cache

import pandas as pd
import yfinance as yf

from services.market_data_utils import get_yfinance_session
from services.risk_service import calculate_risk_reward


def _safe_float(value, default=0.0):
    if hasattr(value, "iloc"):
        value = value.iloc[0]

    try:
        if pd.isna(value):
            return default if default is None else float(default)
    except TypeError:
        pass

    try:
        return float(value)
    except (TypeError, ValueError):
        return default if default is None else float(default)


def _normalize_percentage(value):
    numeric = _safe_float(value, default=None)
    if numeric is None:
        return None

    if -1.0 <= numeric <= 1.0:
        numeric *= 100.0
    return float(numeric)


def _nearest_below(price, levels):
    candidates = [level for level in levels if level and 0 < level < price]
    if not candidates:
        return 0.0
    return float(max(candidates))


def _nearest_above(price, levels):
    candidates = [level for level in levels if level and level > price]
    if not candidates:
        return 0.0
    return float(min(candidates))


def _previous_average_volume(volume_series, lookback=10):
    volume_series = pd.to_numeric(volume_series, errors="coerce").dropna()
    if volume_series.empty:
        return 0.0

    if len(volume_series) >= lookback + 1:
        return float(volume_series.iloc[-(lookback + 1):-1].mean())

    if len(volume_series) > 1:
        return float(volume_series.iloc[:-1].mean())

    return float(volume_series.iloc[-1])


def build_long_trade_context(
    df,
    min_rr=1.5,
    stop_buffer_pct=0.005,
    fallback_stop_pct=0.025,
    fallback_target_multiple=2.0,
):
    default_context = {
        "price": 0.0,
        "entry_price": 0.0,
        "stop_price": 0.0,
        "target_price": 0.0,
        "support_price": 0.0,
        "resistance_price": 0.0,
        "rr_ratio": 0.0,
        "trend": "unknown",
        "volume_confirmed": False,
        "avg_10d_volume": 0.0,
        "latest_volume": 0.0,
        "adx": 0.0,
        "macd_confirmed": False,
        "above_ema20": False,
        "above_ema50": False,
        "above_ema200": False,
        "support_gap_pct": None,
        "risk_pct": None,
        "passes_checklist": False,
        "checklist_score": 0,
        "summary": "Checklist unavailable",
    }

    if df is None or df.empty:
        return default_context

    latest = df.iloc[-1]
    price = _safe_float(latest.get("Close"), 0.0)
    if price <= 0:
        return default_context

    ema_20 = _safe_float(latest.get("ema_20"), price)
    ema_50 = _safe_float(latest.get("ema_50"), price)
    ema_200 = _safe_float(latest.get("ema_200"), price)
    bb_lower = _safe_float(latest.get("bb_lower"), 0.0)
    bb_upper = _safe_float(latest.get("bb_upper"), 0.0)
    pivot = _safe_float(latest.get("pivot"), 0.0)
    s1 = _safe_float(latest.get("s1"), 0.0)
    s2 = _safe_float(latest.get("s2"), 0.0)
    r1 = _safe_float(latest.get("r1"), 0.0)
    r2 = _safe_float(latest.get("r2"), 0.0)
    r3 = _safe_float(latest.get("r3"), 0.0)
    atr = abs(_safe_float(latest.get("atr"), price * fallback_stop_pct))
    adx = _safe_float(latest.get("adx"), 0.0)
    macd = _safe_float(latest.get("macd"), 0.0)
    signal = _safe_float(latest.get("signal"), 0.0)

    recent_high = 0.0
    recent_low = 0.0
    if "High" in df.columns:
        recent_high = _safe_float(pd.to_numeric(df["High"], errors="coerce").tail(20).max(), 0.0)
    if "Low" in df.columns:
        recent_low = _safe_float(pd.to_numeric(df["Low"], errors="coerce").tail(20).min(), 0.0)

    support_price = _nearest_below(
        price,
        [recent_low, ema_20, ema_50, ema_200, bb_lower, pivot, s1, s2],
    )
    resistance_price = _nearest_above(
        price,
        [recent_high, bb_upper, pivot, r1, r2, r3],
    )

    base_stop = price - max(atr, price * fallback_stop_pct, 0.01)
    stop_from_support = support_price * (1.0 - stop_buffer_pct) if support_price > 0 else 0.0
    stop_price = max(base_stop, stop_from_support) if stop_from_support > 0 else base_stop
    if stop_price >= price:
        stop_price = price - max(price * 0.01, atr * 0.5, 0.01)

    risk_per_share = max(price - stop_price, 0.01)
    fallback_target = price + max(risk_per_share * fallback_target_multiple, price * 0.05)
    if resistance_price > price:
        target_price = max(resistance_price, fallback_target)
    else:
        target_price = fallback_target
    if target_price <= price:
        target_price = fallback_target

    rr_ratio = calculate_risk_reward(price, stop_price, target_price)

    volume_series = (
        pd.to_numeric(df["Volume"], errors="coerce").dropna()
        if "Volume" in df.columns
        else pd.Series(dtype="float64")
    )
    avg_10d_volume = _previous_average_volume(volume_series)
    latest_volume = _safe_float(volume_series.iloc[-1], 0.0) if not volume_series.empty else 0.0
    volume_confirmed = bool(avg_10d_volume > 0 and latest_volume >= (avg_10d_volume * 0.6))

    above_ema20 = price > ema_20 > 0
    above_ema50 = price > ema_50 > 0
    above_ema200 = price > ema_200 > 0

    if above_ema20 and ema_20 > ema_50 > ema_200 > 0:
        trend = "strong_bullish"
    elif above_ema20 and ema_20 > ema_50 > 0:
        trend = "bullish"
    elif price < ema_20 < ema_50 < ema_200 and ema_20 > 0:
        trend = "strong_bearish"
    elif price < ema_20 < ema_50 and ema_20 > 0:
        trend = "bearish"
    else:
        trend = "sideways"

    support_gap_pct = ((price - support_price) / price * 100.0) if support_price > 0 else None
    risk_pct = (risk_per_share / price * 100.0) if price > 0 else None
    macd_confirmed = macd > signal

    checklist_score = sum(
        [
            int(volume_confirmed),
            int(trend in {"bullish", "strong_bullish"}),
            int(macd_confirmed),
            int(adx >= 20),
            int(rr_ratio >= min_rr),
            int(support_gap_pct is not None and support_gap_pct <= 5.0),
        ]
    )
    passes_checklist = (
        volume_confirmed
        and trend in {"bullish", "strong_bullish"}
        and macd_confirmed
        and rr_ratio >= min_rr
        and (support_gap_pct is None or support_gap_pct <= 5.0)
    )

    summary_bits = []
    summary_bits.append("vol ok" if volume_confirmed else "vol low")
    summary_bits.append(f"trend {trend.replace('_', ' ')}")
    summary_bits.append(f"RRR {rr_ratio:.2f}")
    if support_gap_pct is not None:
        summary_bits.append(f"support gap {support_gap_pct:.1f}%")

    return {
        "price": round(price, 2),
        "entry_price": round(price, 2),
        "stop_price": round(stop_price, 2),
        "target_price": round(target_price, 2),
        "support_price": round(support_price, 2) if support_price > 0 else 0.0,
        "resistance_price": round(resistance_price, 2) if resistance_price > 0 else 0.0,
        "rr_ratio": round(rr_ratio, 2),
        "trend": trend,
        "volume_confirmed": volume_confirmed,
        "avg_10d_volume": round(avg_10d_volume, 0),
        "latest_volume": round(latest_volume, 0),
        "adx": round(adx, 1),
        "macd_confirmed": macd_confirmed,
        "above_ema20": above_ema20,
        "above_ema50": above_ema50,
        "above_ema200": above_ema200,
        "support_gap_pct": round(support_gap_pct, 2) if support_gap_pct is not None else None,
        "risk_pct": round(risk_pct, 2) if risk_pct is not None else None,
        "passes_checklist": passes_checklist,
        "checklist_score": checklist_score,
        "summary": " | ".join(summary_bits),
    }


@lru_cache(maxsize=256)
def get_fundamental_snapshot(ticker):
    empty_result = {
        "available": False,
        "score": 0,
        "quality": "Unavailable",
        "quality_pass": False,
        "summary": "Fundamental data unavailable",
        "roe_pct": None,
        "profit_margin_pct": None,
        "operating_margin_pct": None,
        "debt_to_equity": None,
        "current_ratio": None,
        "revenue_growth_pct": None,
        "earnings_growth_pct": None,
    }

    try:
        stock = yf.Ticker(ticker, session=get_yfinance_session())
        info = stock.info or {}
    except Exception:
        return empty_result

    if not info:
        return empty_result

    roe_pct = _normalize_percentage(info.get("returnOnEquity"))
    profit_margin_pct = _normalize_percentage(info.get("profitMargins"))
    operating_margin_pct = _normalize_percentage(info.get("operatingMargins"))
    debt_to_equity = _safe_float(info.get("debtToEquity"), default=None)
    current_ratio = _safe_float(info.get("currentRatio"), default=None)
    revenue_growth_pct = _normalize_percentage(info.get("revenueGrowth"))
    earnings_growth_pct = _normalize_percentage(info.get("earningsGrowth"))

    score = 0
    strengths = []
    cautions = []

    if roe_pct is not None:
        if roe_pct >= 15:
            score += 2
            strengths.append("healthy ROE")
        elif roe_pct < 8:
            cautions.append("low ROE")

    if profit_margin_pct is not None:
        if profit_margin_pct >= 8:
            score += 1
            strengths.append("good PAT margin")
        elif profit_margin_pct < 4:
            cautions.append("thin profit margin")

    if operating_margin_pct is not None:
        if operating_margin_pct >= 10:
            score += 1
            strengths.append("healthy operating margin")
        elif operating_margin_pct < 6:
            cautions.append("weak operating margin")

    if debt_to_equity is not None:
        if debt_to_equity <= 100:
            score += 1
        else:
            cautions.append("high leverage")

    if current_ratio is not None:
        if current_ratio >= 1.0:
            score += 1
        else:
            cautions.append("tight liquidity")

    if revenue_growth_pct is not None:
        if revenue_growth_pct > 0:
            score += 1
            strengths.append("positive revenue growth")
        else:
            cautions.append("negative revenue growth")

    if earnings_growth_pct is not None:
        if earnings_growth_pct > 0:
            score += 1
            strengths.append("positive earnings growth")
        else:
            cautions.append("negative earnings growth")

    if score >= 6:
        quality = "Strong"
    elif score >= 4:
        quality = "Good"
    elif score >= 2:
        quality = "Mixed"
    else:
        quality = "Weak"

    summary_bits = strengths[:2]
    if cautions:
        summary_bits.append(cautions[0])

    return {
        "available": True,
        "score": score,
        "quality": quality,
        "quality_pass": score >= 4,
        "summary": "; ".join(summary_bits) if summary_bits else "Limited published edge",
        "roe_pct": round(roe_pct, 2) if roe_pct is not None else None,
        "profit_margin_pct": round(profit_margin_pct, 2) if profit_margin_pct is not None else None,
        "operating_margin_pct": round(operating_margin_pct, 2) if operating_margin_pct is not None else None,
        "debt_to_equity": round(debt_to_equity, 2) if debt_to_equity is not None else None,
        "current_ratio": round(current_ratio, 2) if current_ratio is not None else None,
        "revenue_growth_pct": round(revenue_growth_pct, 2) if revenue_growth_pct is not None else None,
        "earnings_growth_pct": round(earnings_growth_pct, 2) if earnings_growth_pct is not None else None,
    }
