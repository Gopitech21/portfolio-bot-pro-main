import pandas as pd

from services.risk_service import calculate_position_size
from services.varsity_logic_service import build_long_trade_context
from strategies.strategy import apply_strategy


def run_backtest(ticker, data, initial_capital=100000.0):
    if data is None or data.empty:
        return None

    df = apply_strategy(data.copy())

    cash = initial_capital
    shares = 0
    trades = []

    peak_portfolio_value = initial_capital
    max_drawdown = 0.0

    entry_price = 0.0
    current_stop = 0.0
    current_target = 0.0
    highest_price = 0.0
    winning_trades = 0
    total_closed_trades = 0

    for date in df.index:
        history = df.loc[:date]
        row = history.iloc[-1]

        price = row["Close"]
        if pd.isna(price):
            continue
        price = float(price)

        current_value = cash + (shares * price)
        if current_value > peak_portfolio_value:
            peak_portfolio_value = current_value

        drawdown = (peak_portfolio_value - current_value) / peak_portfolio_value * 100 if peak_portfolio_value else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown

        rsi = float(row["rsi"]) if not pd.isna(row["rsi"]) else 50.0
        macd = float(row["macd"]) if not pd.isna(row["macd"]) else 0.0
        signal = float(row["signal"]) if not pd.isna(row["signal"]) else 0.0
        ema_20 = float(row["ema_20"]) if not pd.isna(row.get("ema_20", price)) else price

        context = build_long_trade_context(history, min_rr=1.5)
        bullish_trend = context.get("trend") in {"bullish", "strong_bullish"}
        volume_confirmed = context.get("volume_confirmed", False)

        if shares > 0:
            highest_price = max(highest_price, price)
            trailing_stop = max(current_stop, highest_price * 0.95, ema_20 if ema_20 > 0 else current_stop)

            exit_reason = None
            if price <= trailing_stop:
                exit_reason = "STOP / TRAIL"
            elif current_target > 0 and price >= current_target:
                exit_reason = "TARGET"
            elif macd < signal and price < ema_20:
                exit_reason = "TREND BREAK"
            elif rsi >= 75:
                exit_reason = "OVERBOUGHT EXIT"

            if exit_reason:
                revenue = shares * price
                profit = revenue - (shares * entry_price)
                cash += revenue

                if profit > 0:
                    winning_trades += 1
                total_closed_trades += 1

                trades.append(
                    {
                        "date": date,
                        "action": "SELL",
                        "price": round(price, 2),
                        "shares": shares,
                        "value": round(revenue, 2),
                        "profit": round(profit, 2),
                        "reason": exit_reason,
                    }
                )
                shares = 0
                entry_price = 0.0
                current_stop = 0.0
                current_target = 0.0
                highest_price = 0.0
                continue

        buy_signal = (
            shares == 0
            and bullish_trend
            and volume_confirmed
            and context.get("passes_checklist")
            and context.get("rr_ratio", 0) >= 1.5
            and macd > signal
            and rsi <= 60
        )

        if buy_signal:
            stop_price = float(context.get("stop_price", 0.0))
            target_price = float(context.get("target_price", 0.0))
            sizing = calculate_position_size(
                capital=cash,
                entry_price=price,
                stop_loss=stop_price,
                risk_fraction=0.01,
                leverage=1.0,
            )

            affordable_shares = int(cash // price)
            quantity = min(sizing["quantity"], affordable_shares)
            if quantity <= 0:
                continue

            cash -= quantity * price
            shares = quantity
            entry_price = price
            current_stop = stop_price
            current_target = target_price
            highest_price = price

            trades.append(
                {
                    "date": date,
                    "action": "BUY",
                    "price": round(price, 2),
                    "shares": quantity,
                    "value": round(quantity * price, 2),
                    "stop_loss": round(stop_price, 2),
                    "target": round(target_price, 2),
                    "rr_ratio": context.get("rr_ratio", 0.0),
                    "reason": context.get("summary", ""),
                }
            )

    final_price = float(df.iloc[-1]["Close"])
    final_value = cash + (shares * final_price)
    total_return_pct = ((final_value - initial_capital) / initial_capital) * 100
    win_rate = (winning_trades / total_closed_trades * 100) if total_closed_trades > 0 else 0.0

    return {
        "ticker": ticker,
        "initial_capital": initial_capital,
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "total_closed_trades": total_closed_trades,
        "win_rate_pct": round(win_rate, 2),
        "trades": trades,
        "historical_data": df,
    }
