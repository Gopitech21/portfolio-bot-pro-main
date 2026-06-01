import pandas as pd

from services.ai_ranking_service import ai_score_boost
from services.intraday_service import get_intraday_picks
from services.market_data_utils import download_history
from services.varsity_logic_service import build_long_trade_context, get_fundamental_snapshot
from strategies.strategy import apply_strategy


def _safe_number(value, default=0.0):
    if hasattr(value, "iloc"):
        value = value.iloc[0]

    if pd.isna(value):
        return float(default)

    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _round_price(value):
    return round(float(value), 2)


def _build_plan_payload(context):
    return {
        "price": _round_price(context.get("price", 0.0)),
        "entry": _round_price(context.get("entry_price", 0.0)),
        "exit": _round_price(context.get("target_price", 0.0)),
        "stop_loss": _round_price(context.get("stop_price", 0.0)),
        "rr_ratio": round(_safe_number(context.get("rr_ratio"), 0.0), 2),
        "support": _round_price(context.get("support_price", 0.0)),
        "resistance": _round_price(context.get("resistance_price", 0.0)),
        "vol_confirmed": "Yes" if context.get("volume_confirmed") else "No",
        "checklist": context.get("summary", ""),
    }


def get_screener_picks(watchlist):
    swing_picks = []
    long_term_picks = []
    breakout_picks = []

    for ticker in watchlist:
        try:
            df = download_history(ticker, period="1y")
            if df is None or df.empty or len(df) < 200:
                continue

            df = apply_strategy(df)
            latest = df.iloc[-1]
            price = _safe_number(latest.get("Close"), 0.0)
            if price <= 0:
                continue

            rsi = _safe_number(latest.get("rsi"), 50)
            macd = _safe_number(latest.get("macd"), 0)
            signal = _safe_number(latest.get("signal"), 0)
            adx = _safe_number(latest.get("adx"), 0)
            mfi = _safe_number(latest.get("mfi"), 50)
            cci = _safe_number(latest.get("cci"), 0)
            ema_20 = _safe_number(latest.get("ema_20"), price)
            ema_50 = _safe_number(latest.get("ema_50"), price)
            ema_200 = _safe_number(latest.get("ema_200"), price)
            bb_lower = _safe_number(latest.get("bb_lower"), 0)

            try:
                five_day_close = float(df["Close"].iloc[-5])
                change = float((price - five_day_close) / five_day_close * 100) if five_day_close else 0.0
            except Exception:
                change = 0.0

            swing_context = build_long_trade_context(df, min_rr=1.5)
            long_term_context = build_long_trade_context(
                df,
                min_rr=1.8,
                fallback_stop_pct=0.03,
                fallback_target_multiple=2.5,
            )

            volume_confirmed = swing_context.get("volume_confirmed", False)
            bullish_trend = swing_context.get("trend") in {"bullish", "strong_bullish"}

            swing_score = 0
            if adx > 25:
                swing_score += 2
            if 50 <= rsi <= 70:
                swing_score += 1
            if macd > signal:
                swing_score += 1
            if price > ema_20:
                swing_score += 1
            if mfi > 50:
                swing_score += 1
            if volume_confirmed:
                swing_score += 1
            if swing_context.get("rr_ratio", 0) >= 1.5:
                swing_score += 1

            if swing_score >= 5 and volume_confirmed and bullish_trend and swing_context.get("passes_checklist"):
                ai = ai_score_boost(ticker, change) * 0.5
                swing_picks.append(
                    {
                        "ticker": ticker,
                        "score": round(swing_score + ai, 2),
                        "change": round(change, 2),
                        "adx": round(adx, 1),
                        "rsi": round(rsi, 1),
                        "mfi": round(mfi, 1),
                        **_build_plan_payload(swing_context),
                    }
                )

            fundamental = get_fundamental_snapshot(ticker)
            lt_score = 0
            if rsi < 45:
                lt_score += 1
            if cci < -100:
                lt_score += 2
            if price <= bb_lower * 1.02 and bb_lower > 0:
                lt_score += 2
            if mfi < 40:
                lt_score += 1
            if fundamental.get("quality_pass"):
                lt_score += 2
            if long_term_context.get("rr_ratio", 0) >= 1.8:
                lt_score += 1

            if (
                lt_score >= 5
                and fundamental.get("quality_pass")
                and long_term_context.get("support_price", 0) > 0
                and long_term_context.get("rr_ratio", 0) >= 1.5
            ):
                ai = ai_score_boost(ticker, change) * 0.5
                long_term_picks.append(
                    {
                        "ticker": ticker,
                        "score": round(lt_score + ai, 2),
                        "change": round(change, 2),
                        "cci": round(cci, 1),
                        "rsi": round(rsi, 1),
                        "mfi": round(mfi, 1),
                        "fundamental_score": fundamental.get("score", 0),
                        "fundamental_quality": fundamental.get("quality", "Unavailable"),
                        "fundamental_note": fundamental.get("summary", ""),
                        **_build_plan_payload(long_term_context),
                    }
                )

            try:
                high_52w = float(df["High"].max())
                distance_to_high = (high_52w - price) / high_52w if high_52w else 0.0

                if "Volume" in df.columns:
                    vol_5d = df["Volume"].tail(5).mean()
                    vol_20d = df["Volume"].tail(20).mean()
                    volume_drying = vol_5d < vol_20d
                else:
                    volume_drying = False

                price_60d_ago = float(df["Close"].iloc[-60])
                positive_60d = price > price_60d_ago

                rsi_5d_ago = float(df["rsi"].iloc[-6])
                momentum_gain = rsi > rsi_5d_ago

                breakout_score = 0
                if distance_to_high <= 0.10:
                    breakout_score += 2
                if price > ema_20 > ema_50 > ema_200:
                    breakout_score += 2
                if 55 <= rsi <= 70:
                    breakout_score += 1
                if momentum_gain:
                    breakout_score += 1
                if volume_drying:
                    breakout_score += 1
                if positive_60d:
                    breakout_score += 1
                if volume_confirmed:
                    breakout_score += 2
                if swing_context.get("rr_ratio", 0) >= 1.5:
                    breakout_score += 1

                if (
                    breakout_score >= 6
                    and volume_confirmed
                    and bullish_trend
                    and swing_context.get("passes_checklist")
                ):
                    ai = ai_score_boost(ticker, change) * 0.5
                    breakout_picks.append(
                        {
                            "ticker": ticker,
                            "score": round(breakout_score + ai, 2),
                            "change": round(change, 2),
                            "dist_high": round(distance_to_high * 100, 1),
                            "rsi": round(rsi, 1),
                            "vol_drying": "Yes" if volume_drying else "No",
                            **_build_plan_payload(swing_context),
                        }
                    )
            except Exception:
                pass

        except Exception as e:
            print("Error screening:", ticker, e)
            continue

    return {
        "swing": sorted(swing_picks, key=lambda x: x["score"], reverse=True),
        "long_term": sorted(long_term_picks, key=lambda x: x["score"], reverse=True),
        "breakout": sorted(breakout_picks, key=lambda x: x["score"], reverse=True),
        "intraday": get_intraday_picks(watchlist),
    }
