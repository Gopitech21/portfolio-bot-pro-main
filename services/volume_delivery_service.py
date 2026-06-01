from services.market_data_utils import download_history, get_series


def _safe_last(series):
    if series is None or series.empty:
        return None
    return float(series.iloc[-1])


def _classify_volume_price_action(price_change, vol_vs_avg_ratio, recent_volume, vol_10d_avg):
    """
    Apply Zerodha Varsity volume-price relationship rules:
    Rule 1: Price ↑ + Volume ↑ (>10d avg) = Bullish (smart money buying)
    Rule 2: Price ↑ + Volume ↓ (<10d avg) = Caution (weak hands, bull trap)
    Rule 3: Price ↓ + Volume ↑ (>10d avg) = Bearish (smart money selling)
    Rule 4: Price ↓ + Volume ↓ (<10d avg) = Caution (weak hands, bear trap)
    """
    price_up = price_change > 0.5  # threshold 0.5% to avoid noise
    high_vol = vol_vs_avg_ratio >= 1.0  # today's vol >= 10d average
    
    if price_up and high_vol:
        return {
            "signal": "🟢 BULLISH (Smart Money Buying)",
            "category": "bullish_smart",
            "confidence": min(100, 50 + (vol_vs_avg_ratio - 1.0) * 30 + max(0, price_change * 2)),
            "warning": None,
        }
    elif price_up and not high_vol:
        return {
            "signal": "⚠️ CAUTION (Bull Trap - Weak Hands)",
            "category": "caution_bull_trap",
            "confidence": 30 + min(20, price_change * 2),
            "warning": "Low volume on price increase - weak hands buying, avoid",
        }
    elif not price_up and high_vol:
        return {
            "signal": "🔴 BEARISH (Smart Money Selling)",
            "category": "bearish_smart",
            "confidence": min(100, 50 + (vol_vs_avg_ratio - 1.0) * 30 + abs(min(0, price_change * 2))),
            "warning": None,
        }
    else:  # price_down and low_vol
        return {
            "signal": "⚠️ CAUTION (Bear Trap - Weak Hands)",
            "category": "caution_bear_trap",
            "confidence": 30 + min(20, abs(price_change) * 2),
            "warning": "Low volume on price decrease - weak hands selling, avoid",
        }


def get_volume_delivery_movers(watchlist, lookback_days=10, top_n=5):
    """
    Analyze volume movers using Zerodha Varsity volume-price relationship rules.
    Uses 10-day average volume as baseline (industry standard).
    Classifies as smart money (bullish/bearish) or weak hands (caution traps).
    """
    rows = []

    for ticker in watchlist:
        try:
            df = download_history(ticker, period="3mo")
            if df is None or df.empty:
                continue

            close = get_series(df, "Close")
            open_ = get_series(df, "Open")
            volume = get_series(df, "Volume")

            if close.empty or open_.empty or volume.empty:
                continue

            if len(close) < 50 or len(volume) < 50:
                continue

            # Get last 10 days for analysis
            recent_close = close.tail(lookback_days)
            recent_open = open_.tail(lookback_days)
            recent_volume = volume.tail(lookback_days)

            if len(recent_volume) < lookback_days:
                continue

            # Calculate 10-day average volume (Zerodha standard)
            vol_10d_avg = float(recent_volume.mean())
            if vol_10d_avg <= 0:
                continue

            # Today's values
            today_close = float(recent_close.iloc[-1])
            today_open = float(recent_open.iloc[-1])
            today_volume = float(recent_volume.iloc[-1])
            today_high = float(df["High"].tail(1).iloc[0]) if "High" in df.columns else today_close
            today_low = float(df["Low"].tail(1).iloc[0]) if "Low" in df.columns else today_close

            # 10 days ago values
            ten_days_ago_close = float(recent_close.iloc[0])
            if ten_days_ago_close == 0:
                continue

            # Calculate metrics
            price_change_10d = ((today_close - ten_days_ago_close) / ten_days_ago_close) * 100
            vol_vs_avg_ratio = today_volume / vol_10d_avg

            # Classify using volume-price rules
            classification = _classify_volume_price_action(price_change_10d, vol_vs_avg_ratio, recent_volume, vol_10d_avg)

            # Calculate trend score
            ma20 = _safe_last(close.rolling(20).mean())
            ma50 = _safe_last(close.rolling(50).mean())
            trend_score = 0
            if ma20 is not None and today_close > ma20:
                trend_score += 1
            if ma50 is not None and today_close > ma50:
                trend_score += 1

            # Smart money score (high confidence for real smart money moves)
            if classification["category"] in ("bullish_smart", "bearish_smart"):
                smart_money_score = classification["confidence"] + trend_score * 15
            else:
                smart_money_score = max(0, classification["confidence"] - 30)  # Penalize weak hands

            # Swing score prioritizes volume and recent momentum
            swing_score = (
                classification["confidence"] * 0.6
                + (max(vol_vs_avg_ratio - 1.0, 0.0) * 25)
                + (max(price_change_10d, 0.0) * 1.5)
                + (trend_score * 8)
            )

            # Long-term score prioritizes delivery proxy and trend
            delivery_proxy_pct = (float(((recent_close > recent_open) & (recent_volume > vol_10d_avg)).sum()) / float(lookback_days)) * 100
            long_term_score = (
                (delivery_proxy_pct * 0.5)
                + (trend_score * 20)
                + (max(price_change_10d, 0.0) * 1.0)
                + (smart_money_score * 0.3)
            )

            rows.append(
                {
                    "ticker": ticker,
                    "price_change_10d": round(price_change_10d, 2),
                    "volume_ratio": round(vol_vs_avg_ratio, 2),
                    "vol_10d_avg": round(vol_10d_avg, 0),
                    "today_volume": round(today_volume, 0),
                    "delivery_proxy_pct": round(delivery_proxy_pct, 1),
                    "signal": classification["signal"],
                    "confidence": round(classification["confidence"], 1),
                    "warning": classification["warning"],
                    "category": classification["category"],
                    "trend_score": trend_score,
                    "swing_score": round(swing_score, 2),
                    "long_term_score": round(long_term_score, 2),
                }
            )
        except Exception:
            continue

    swing = sorted(rows, key=lambda x: x["swing_score"], reverse=True)[:top_n]
    long_term = sorted(rows, key=lambda x: x["long_term_score"], reverse=True)[:top_n]
    
    return {
        "swing": swing,
        "long_term": long_term,
        "all_analyzed": rows,  # For dashboard warnings
    }
