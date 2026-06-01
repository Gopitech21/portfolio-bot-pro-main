import pandas as pd
import ta

from services.market_data_utils import download_history
from services.risk_service import calculate_position_size, calculate_risk_reward


def get_intraday_picks(watchlist):
    """
    Evaluates tickers for intraday setups using 15m data.
    Applies Varsity-style filters: liquidity, volatility, volume confirmation,
    and pre-defined risk/reward before surfacing a trade.
    """
    intraday_picks = []

    for ticker in watchlist:
        try:
            df = download_history(ticker, period="5d", interval="15m")
            if df is None or df.empty or len(df) < 20:
                continue

            daily_groups = df.groupby(df.index.date)
            daily_volumes = daily_groups["Volume"].sum()
            avg_daily_vol = daily_volumes.mean()

            daily_highs = daily_groups["High"].max()
            daily_lows = daily_groups["Low"].min()
            daily_closes = daily_groups["Close"].last()
            daily_ranges_pct = ((daily_highs - daily_lows) / daily_closes) * 100
            avg_volatility_pct = daily_ranges_pct.mean()

            if avg_daily_vol < 500000 or avg_volatility_pct < 1.5:
                continue

            last_date = df.index[-1].date()
            df_today = df[df.index.date == last_date]
            if df_today.empty:
                continue

            df["ema_9"] = ta.trend.EMAIndicator(df["Close"], window=9).ema_indicator()
            df["ema_20"] = ta.trend.EMAIndicator(df["Close"], window=20).ema_indicator()

            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            price = float(latest["Close"])

            latest_bar_volume = float(df_today["Volume"].iloc[-1])
            avg_intraday_bar_volume = float(avg_daily_vol / max(len(df_today), 1))
            volume_confirmed = latest_bar_volume >= avg_intraday_bar_volume if avg_intraday_bar_volume > 0 else False

            strategy_name = None
            entry_price = 0.0
            target_price = 0.0
            stop_loss = 0.0

            first_candle = df_today.iloc[0]
            orb_high = float(first_candle["High"])
            orb_low = float(first_candle["Low"])

            if price > orb_high and df_today.shape[0] > 1:
                strategy_name = "ORB Breakout"
                entry_price = price
                stop_loss = orb_low
                if entry_price <= stop_loss:
                    stop_loss = entry_price * 0.99
                target_price = entry_price + ((entry_price - stop_loss) * 2)

            df_yesterday = df[df.index.date < last_date]
            if not df_yesterday.empty and not strategy_name:
                yest_close = float(df_yesterday.iloc[-1]["Close"])
                today_open = float(first_candle["Open"])
                gap_pct = ((today_open - yest_close) / yest_close) * 100 if yest_close else 0.0

                if gap_pct > 1.0 and price >= today_open:
                    strategy_name = "Gap and Go"
                    entry_price = price
                    stop_loss = float(first_candle["Low"])
                    if entry_price <= stop_loss:
                        stop_loss = entry_price * 0.99
                    target_price = entry_price + ((entry_price - stop_loss) * 2)

            if not strategy_name:
                if prev["ema_9"] <= prev["ema_20"] and latest["ema_9"] > latest["ema_20"]:
                    strategy_name = "MA Crossover (9/20)"
                    entry_price = price
                    stop_loss = float(latest["ema_20"]) * 0.998
                    if entry_price <= stop_loss:
                        stop_loss = entry_price * 0.99
                    target_price = entry_price + ((entry_price - stop_loss) * 2)

            rr_ratio = calculate_risk_reward(entry_price, stop_loss, target_price)
            if not strategy_name or not volume_confirmed or rr_ratio < 1.5:
                continue

            sizing = calculate_position_size(
                capital=100000.0,
                entry_price=entry_price,
                stop_loss=stop_loss,
                risk_fraction=0.01,
                leverage=5.0,
            )

            intraday_picks.append(
                {
                    "ticker": ticker,
                    "price": round(float(price), 2),
                    "strategy": strategy_name,
                    "entry": round(entry_price, 2),
                    "exit": round(target_price, 2),
                    "target": round(target_price, 2),
                    "stop_loss": round(stop_loss, 2),
                    "capital_req": sizing["capital_required"],
                    "qty": sizing["quantity"],
                    "rr_ratio": rr_ratio,
                    "vol_confirmed": "Yes",
                }
            )

        except Exception as e:
            print(f"Intraday error for {ticker}: {e}")
            continue

    return intraday_picks
