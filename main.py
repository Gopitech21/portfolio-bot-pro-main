import argparse

from services.data_provider import fetch_data
from services.signal_service import get_signal
from services.portfolio_service import calculate_metrics
from services.portfolio_loader import load_portfolio
from services.risk_service import calculate_stop_loss, risk_level
from services.market_data_utils import get_last_float, get_series
from services.market_insight_service import get_sector_pulse, get_seasonal_theme_watch

from services.nse_service import get_all_stocks
from services.screener_service import get_screener_picks
from services.ai_ranking_service import get_confidence, get_entry_signal
from services.exit_strategy import get_exit_signal

from services.llm_service import analyze_stock
from services.alert_service import should_alert
from services.market_service import check_market_crash, get_market_status
from services.portfolio_signal_service import get_portfolio_status
from services.volume_delivery_service import get_volume_delivery_movers
from services.backtest_service import run_backtest
from services.varsity_logic_service import build_long_trade_context
from strategies.strategy import apply_strategy

from notifier.telegram_bot import get_updates, send_message


FULL_PORTFOLIO_COMMANDS = {"/portfolio", "/full", "/summary", "portfolio", "full", "complete portfolio"}
SECTOR_COMMANDS = {"/sectors", "/sector", "sectors", "sector"}
THEME_COMMANDS = {"/themes", "/theme", "themes", "theme"}
MARKET_PULSE_COMMANDS = {"/pulse", "/marketpulse", "pulse", "market pulse"}
SCHEDULED_SESSION_TITLES = {
    "open": "🔔 Market Open Update (09:15 IST)",
    "open_plus_1h": "⏰ One Hour Update (10:15 IST)",
    "close": "🔒 Market Close Update (15:30 IST)",
}


def prepend_session_header(message, session=None):
    title = SCHEDULED_SESSION_TITLES.get(session)
    if not title:
        return message
    return f"{title}\n\n{message}"


def build_market_section(market_status, screener_data):
    market_lines = [market_status["summary"], f"Bias: {market_status['action_bias']}"]

    if market_status.get("nifty"):
        nifty = market_status["nifty"]
        bank_nifty = market_status["bank_nifty"]
        market_lines.append(
            f"Nifty vs MA20/MA50: {'Above' if nifty['above_ma20'] else 'Below'}/"
            f"{'Above' if nifty['above_ma50'] else 'Below'}"
        )
        if bank_nifty:
            market_lines.append(
                f"Bank Nifty vs MA20/MA50: {'Above' if bank_nifty['above_ma20'] else 'Below'}/"
                f"{'Above' if bank_nifty['above_ma50'] else 'Below'}"
            )

    swing_lines = []
    if isinstance(screener_data, dict) and "swing" in screener_data:
        for stock in screener_data["swing"][:3]:
            ticker = stock['ticker']
            score = stock['score']
            adx = stock.get('adx', 0)
            rr_ratio = stock.get('rr_ratio', 0)
            swing_lines.append(f"⚡ {ticker} | Score: {score} | ADX: {adx} | RRR: {rr_ratio}")

    lt_lines = []
    if isinstance(screener_data, dict) and "long_term" in screener_data:
        for stock in screener_data["long_term"][:3]:
            ticker = stock['ticker']
            score = stock['score']
            quality = stock.get('fundamental_quality', 'NA')
            rr_ratio = stock.get('rr_ratio', 0)
            lt_lines.append(f"🏛️ {ticker} | Score: {score} | Quality: {quality} | RRR: {rr_ratio}")
            
    bo_lines = []
    if isinstance(screener_data, dict) and "breakout" in screener_data:
        for stock in screener_data["breakout"][:3]:
            ticker = stock['ticker']
            score = stock['score']
            dist = stock.get('dist_high', 0)
            rr_ratio = stock.get('rr_ratio', 0)
            bo_lines.append(f"🚀 {ticker} | Score: {score} | Dist 52W: {dist}% | RRR: {rr_ratio}")

    intraday_lines = []
    if isinstance(screener_data, dict) and "intraday" in screener_data:
        for stock in screener_data["intraday"][:3]:
            ticker = stock['ticker']
            strat = stock['strategy']
            entry = stock['entry']
            tgt = stock.get('exit', stock.get('target'))
            sl = stock['stop_loss']
            cap = stock['capital_req']
            rr_ratio = stock.get('rr_ratio', 0)
            intraday_lines.append(f"⏱️ {ticker} | {strat} | Entry: ₹{entry} | Tgt: ₹{tgt} | SL: ₹{sl} | RRR: {rr_ratio} | Cap Req: ₹{cap}")

    section = "🌍 Market Status\n\n" + "\n".join(market_lines)
    if swing_lines or lt_lines or bo_lines or intraday_lines or (isinstance(screener_data, dict) and "intraday" in screener_data):
        section += "\n\n🎯 Indicator-Based Picks\n\n"
        
        section += "*⏱️ Intraday Setups (High Risk)*\n"
        if intraday_lines:
            section += "\n".join(intraday_lines) + "\n\n"
        else:
            section += "_No high-probability setups triggered right now._\n\n"
            
        if bo_lines:
            section += "*Breakout Ready (Consolidation)*\n" + "\n".join(bo_lines) + "\n\n"
        if swing_lines:
            section += "*Swing Trades (Momentum)*\n" + "\n".join(swing_lines) + "\n\n"
        if lt_lines:
            section += "*Long Term (Value/Support)*\n" + "\n".join(lt_lines)
    return section


def build_daily_signal_line(item):
    return (
        f"{item['ticker']} | ₹{item['price']} | "
        f"P/L {item['profit_pct']}% | RSI {item['rsi']} | {item['reason']}"
    )


def build_accumulate_line(item):
    return (
        f"{item['ticker']} | ₹{item['price']} | P/L {item['profit_pct']}% | "
        f"RSI {item['rsi']} | Rank {item['accumulate_rank']}"
    )


def build_volume_line(item):
    return (
        f"{item['ticker']} | {item['signal']} | Conf: {item['confidence']}% | "
        f"10D: {item['price_change_10d']}% | Vol x{item['volume_ratio']}"
    )


def build_sector_section(sector_pulse, max_sectors=4, max_stocks=3):
    if not sector_pulse or not sector_pulse.get("sectors"):
        return ""

    lines = ["🏭 Sector Pulse Today"]

    for sector in sector_pulse["sectors"][:max_sectors]:
        leaders = sector.get("stocks", [])[:max_stocks]
        leader_text = ", ".join(
            f"{stock['symbol']} {stock['day_change']:+.2f}%"
            for stock in leaders
        )
        lines.append(
            f"{sector['sector']} | {sector['status']} | Today {sector['avg_day_change']:+.2f}% | "
            f"Breadth {sector['breadth_pct']:.1f}%"
        )
        if leader_text:
            lines.append(f"Leaders: {leader_text}")

    return "\n".join(lines)


def build_theme_section(theme_watch, max_active=2, max_upcoming=1, max_stocks=3):
    if not theme_watch:
        return ""

    lines = []
    active = theme_watch.get("active", [])
    upcoming = theme_watch.get("upcoming", [])

    if active or upcoming:
        lines.append("🌦️ Seasonal Theme Watch")

    for theme in active[:max_active]:
        leaders = ", ".join(
            f"{stock['symbol']} {stock['day_change']:+.2f}%"
            for stock in theme.get("stocks", [])[:max_stocks]
        )
        lines.append(
            f"{theme['theme']} | {theme['stage']} | {theme['bias']} | "
            f"Today {theme['avg_day_change']:+.2f}% | News {theme['news_sentiment']}"
        )
        if leaders:
            lines.append(f"Top stocks: {leaders}")

    for theme in upcoming[:max_upcoming]:
        leaders = ", ".join(
            f"{stock['symbol']} {stock['day_change']:+.2f}%"
            for stock in theme.get("stocks", [])[:max_stocks]
        )
        lines.append(
            f"{theme['theme']} | {theme['stage']} | {theme['bias']} | "
            f"5D {theme['avg_week_change']:+.2f}% | News {theme['news_sentiment']}"
        )
        if leaders:
            lines.append(f"Watchlist: {leaders}")

    return "\n".join(lines)


def build_sector_report_message():
    sector_pulse = get_sector_pulse(max_sectors=6, top_stocks_per_sector=4)
    section = build_sector_section(sector_pulse, max_sectors=6, max_stocks=4)
    if section:
        return section + "\n\n💬 Commands: /themes, /pulse, /portfolio"
    return "Sector pulse is unavailable right now."


def build_theme_report_message():
    theme_watch = get_seasonal_theme_watch(top_stocks_per_theme=4)
    section = build_theme_section(theme_watch, max_active=3, max_upcoming=2, max_stocks=4)
    if section:
        return section + "\n\n💬 Commands: /sectors, /pulse, /portfolio"
    return "Seasonal theme watch is unavailable right now."


def build_market_pulse_message():
    sector_message = build_sector_report_message()
    theme_message = build_theme_report_message()
    return sector_message + "\n\n" + theme_message


def build_daily_signal_message(
    sell_lines,
    accumulate_lines,
    market_status,
    mover_snapshot=None,
    screener_data=None,
    sector_pulse=None,
    theme_watch=None,
):
    message_parts = [
        "Portfolio tracker:",
        f"🌍 Market: {market_status['regime']}",
        "📌 Daily actions only",
    ]

    if sell_lines:
        message_parts.append("\n🔴 SELL / Exit\n" + "\n".join(sell_lines))

    if accumulate_lines:
        message_parts.append("\n🟢 Accumulate More\n" + "\n".join(accumulate_lines))

    if not sell_lines and not accumulate_lines:
        message_parts.append("\n✅ No SELL or Accumulate More signals today.")

    if mover_snapshot and mover_snapshot.get("swing"):
        top = mover_snapshot["swing"][:3]
        lines = [f"{item['ticker']} (Vol x{item['volume_ratio']}, 10D {item['price_change_10d']}%)" for item in top]
        message_parts.append("\n📈 10D Volume Movers (Swing)\n" + "\n".join(lines))

    if screener_data and isinstance(screener_data, dict) and "intraday" in screener_data and screener_data["intraday"]:
        intraday_lines = []
        for stock in screener_data["intraday"][:3]:
            ticker = stock['ticker']
            strat = stock['strategy']
            entry = stock['entry']
            tgt = stock.get('exit', stock.get('target'))
            sl = stock['stop_loss']
            cap = stock['capital_req']
            rr_ratio = stock.get('rr_ratio', 0)
            intraday_lines.append(f"⏱️ {ticker} | {strat} | Entry: ₹{entry} | Tgt: ₹{tgt} | SL: ₹{sl} | RRR: {rr_ratio} | Cap Req: ₹{cap}")
        if intraday_lines:
            message_parts.append("\n🎯 Intraday Setups (High Risk)\n" + "\n".join(intraday_lines))

    sector_section = build_sector_section(sector_pulse, max_sectors=3, max_stocks=2)
    if sector_section:
        message_parts.append("\n" + sector_section)

    theme_section = build_theme_section(theme_watch, max_active=1, max_upcoming=1, max_stocks=2)
    if theme_section:
        message_parts.append("\n" + theme_section)

    message_parts.append("\n💬 Send /portfolio, /sectors, /themes, or /pulse in Telegram for more detail.")
    return "\n".join(message_parts)


def build_reports(include_alerts=False, session=None):
    print("🚀 AI Trading System Started")

    # 📊 Load portfolio
    df = load_portfolio()
    portfolio_tickers = df['ticker'].dropna().unique().tolist()

    # 🔥 Fetch NSE stocks
    print("📡 Fetching NSE stocks...")
    nse_stocks = get_all_stocks(limit=120)
    market_status = get_market_status()

    # 🔥 Build watchlist
    watchlist = list(set(nse_stocks + portfolio_tickers))

    insight_cache = {}

    # 🔥 Run screener
    print("📊 Running Screener...")
    screener_data = get_screener_picks(watchlist)

    # ✅ FIX: fallback BEFORE using screener_data
    if not screener_data:
        print("⚠️ Screener empty")
        screener_data = {"swing": [], "long_term": [], "breakout": [], "intraday": []}
    else:
        # Ensure all required keys exist to avoid KeyError later
        for key in ["swing", "long_term", "breakout", "intraday"]:
            if key not in screener_data:
                screener_data[key] = []

    print("🏭 Building sector pulse...")
    sector_pulse = get_sector_pulse(
        max_sectors=6,
        top_stocks_per_sector=4,
        snapshot_cache=insight_cache,
        tickers=watchlist,
    )

    print("🌦️ Building seasonal theme watch...")
    theme_watch = get_seasonal_theme_watch(
        top_stocks_per_theme=4,
        snapshot_cache=insight_cache,
        tickers=watchlist,
    )

    market_section = build_market_section(market_status, screener_data)

    # 🔥 PORTFOLIO ANALYSIS
    actionable_lines = []
    sell_lines = []
    accumulate_candidates = []
    alert_messages = []
    total_value = 0
    total_invested = 0

    for _, row in df.iterrows():
        ticker = row['ticker']

        data = fetch_data(ticker)

        if data is None or data.empty:
            continue

        try:
            strategy_view = apply_strategy(data.copy())
            context = build_long_trade_context(strategy_view, min_rr=1.5)
            result = get_signal(data, ticker)

            if not result:
                continue

            action, _, sentiment, rsi = result

            price = get_last_float(data, "Close")
            highest_price = float(get_series(data, "Close").max())

            invested, current_value, profit, profit_pct = calculate_metrics(row, price)

            total_value += current_value
            total_invested += invested

            stop = calculate_stop_loss(row['avg_price'], price, stop_price=context.get("stop_price"))
            risk = risk_level(
                rsi,
                profit_pct,
                rr_ratio=context.get("rr_ratio"),
                volume_confirmed=context.get("volume_confirmed"),
                trend=context.get("trend"),
            )

            exit_signal = get_exit_signal(
                price,
                row['avg_price'],
                rsi,
                highest_price,
                stop_price=context.get("stop_price"),
                ema_20=float(strategy_view["ema_20"].iloc[-1]) if "ema_20" in strategy_view.columns else None,
                ema_50=float(strategy_view["ema_50"].iloc[-1]) if "ema_50" in strategy_view.columns else None,
            )
            status = get_portfolio_status(action, exit_signal, market_status, profit_pct, rsi)

            signal_item = {
                "ticker": ticker,
                "price": round(price, 2),
                "profit_pct": round(profit_pct, 2),
                "rsi": round(rsi, 1),
                "reason": action,
            }
            action_text = str(action).upper()

            if "SELL" in action_text:
                sell_lines.append(build_daily_signal_line(signal_item))
            elif "ADD MORE" in action_text and status == "BUY":
                # Higher rank means stronger add-more candidate.
                accumulate_rank = round(
                    (max(0, 60 - rsi) * 0.6)
                    + (max(0, -profit_pct) * 0.4)
                    + (context.get("rr_ratio", 0) * 6)
                    + (6 if context.get("volume_confirmed") else 0),
                    2,
                )
                accumulate_item = {
                    "ticker": ticker,
                    "price": round(price, 2),
                    "profit_pct": round(profit_pct, 2),
                    "rsi": round(rsi, 1),
                    "accumulate_rank": accumulate_rank,
                }
                accumulate_candidates.append(accumulate_item)

            # Only include actionable stocks (SELL or BUY/accumulate) in full actionable section
            if status not in ("SELL", "BUY"):
                continue

            actionable_lines.append(
                f"{ticker} | {status} | Price: {round(price, 2)} | P/L: {round(profit_pct, 2)}% | RSI: {round(rsi, 1)}"
            )

            if include_alerts:
                ai_text = analyze_stock(ticker, price, rsi, action, [])

                msg = f"""
{ticker}
Price: {round(price, 2)}

Action: {action}
Exit: {exit_signal}

🧠 AI: {ai_text}

P/L: {round(profit, 2)} ({round(profit_pct, 2)}%)

Risk: {risk}
StopLoss: {stop}
Market: {market_status['regime']}
"""

                if should_alert(ticker, status):
                    alert_messages.append(msg)

        except Exception as e:
            print("Portfolio error:", ticker, e)

    # 📊 SUMMARY
    summary = f"""
📊 Portfolio Summary

Invested: {round(total_invested, 2)}
Current: {round(total_value, 2)}
Profit: {round(total_value - total_invested, 2)}
"""

    full_msg = market_section + "\n\n" + summary

    if actionable_lines:
        portfolio_section = "📌 Actionable Stocks\n\n" + "\n".join(actionable_lines)
        full_msg += "\n\n" + portfolio_section
    else:
        full_msg += "\n\n✅ No actionable signals — all holdings are HOLD/WAIT."

    if alert_messages:
        full_msg += "\n\n🔔 Changed Signals\n\n" + "\n\n".join(alert_messages)

    crash = check_market_crash(market_status)
    if crash:
        full_msg = crash + "\n\n" + full_msg

    accumulate_candidates = sorted(accumulate_candidates, key=lambda x: x["accumulate_rank"], reverse=True)
    accumulate_lines = [build_accumulate_line(item) for item in accumulate_candidates[:5]]

    movers = get_volume_delivery_movers(watchlist, lookback_days=10, top_n=5)
    long_term_lines = [build_volume_line(item) for item in movers.get("long_term", [])]
    swing_lines = [build_volume_line(item) for item in movers.get("swing", [])]

    # Extract caution warnings for weak hands/traps
    all_analyzed = movers.get("all_analyzed", [])
    caution_stocks = [s for s in all_analyzed if s.get("warning")]

    if caution_stocks:
        caution_text = "\n".join([f"{s['ticker']}: {s['warning']}" for s in caution_stocks[:3]])
        full_msg += "\n\n⚠️ CAUTION (Weak Hands / Bull-Bear Traps)\n\n" + caution_text

    if long_term_lines:
        full_msg += "\n\n📊 Smart Money Movers (Long Term - 10D)\n\n" + "\n".join(long_term_lines)

    if swing_lines:
        full_msg += "\n\n⚡ Smart Money Movers (Swing - 10D)\n\n" + "\n".join(swing_lines)

    sector_section_full = build_sector_section(sector_pulse, max_sectors=5, max_stocks=3)
    if sector_section_full:
        full_msg += "\n\n" + sector_section_full

    theme_section_full = build_theme_section(theme_watch, max_active=3, max_upcoming=2, max_stocks=3)
    if theme_section_full:
        full_msg += "\n\n" + theme_section_full

    daily_msg = build_daily_signal_message(
        sell_lines,
        accumulate_lines,
        market_status,
        mover_snapshot=movers,
        screener_data=screener_data,
        sector_pulse=sector_pulse,
        theme_watch=theme_watch,
    )
    if crash:
        daily_msg = crash + "\n\n" + daily_msg

    daily_msg = prepend_session_header(daily_msg, session=session)
    full_msg = prepend_session_header(full_msg, session=session)

    return daily_msg, full_msg, sector_pulse, theme_watch


def handle_telegram_commands():
    updates = get_updates()
    handled = 0

    for update in updates:
        message = update.get("message") or update.get("edited_message") or {}
        text = str(message.get("text", "")).strip().lower()
        chat_id = (message.get("chat") or {}).get("id")

        if not chat_id:
            continue

        if text in FULL_PORTFOLIO_COMMANDS:
            _, full_msg, _, _ = build_reports(include_alerts=False)
            send_message(full_msg, chat_id=chat_id)
        elif text in SECTOR_COMMANDS:
            send_message(build_sector_report_message(), chat_id=chat_id)
        elif text in THEME_COMMANDS:
            send_message(build_theme_report_message(), chat_id=chat_id)
        elif text in MARKET_PULSE_COMMANDS:
            send_message(build_market_pulse_message(), chat_id=chat_id)
        else:
            continue

        handled += 1

    print(f"✅ Handled {handled} Telegram command(s)")


def run(full=False, session=None):
    daily_msg, full_msg, _, _ = build_reports(include_alerts=full, session=session)
    send_message(full_msg if full else daily_msg)
    if session:
        print(f"✅ Run completed for session: {session}")
    else:
        print("✅ Run completed")


def parse_args():
    parser = argparse.ArgumentParser(description="Send portfolio tracker Telegram updates.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Send the complete portfolio report instead of the short daily SELL/Accumulate report.",
    )
    parser.add_argument(
        "--handle-telegram-commands",
        action="store_true",
        help="Reply with the complete portfolio report when Telegram receives /portfolio or /full.",
    )
    parser.add_argument(
        "--session",
        choices=sorted(SCHEDULED_SESSION_TITLES.keys()),
        help="Label a scheduled Telegram update as market open, one-hour update, or market close.",
    )
    parser.add_argument(
        "--backtest",
        type=str,
        help="Run a backtest on a specific ticker (e.g. RELIANCE.NS) and print the results.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.backtest:
        ticker = args.backtest
        print(f"🚀 Running backtest for {ticker} over the last 2 years...")
        # Since backtest needs more data, we'll download directly to get a longer period
        from services.market_data_utils import download_history
        data = download_history(ticker, period="2y")
        
        results = run_backtest(ticker, data)
        if results:
            print("\n📊 Backtest Results:")
            print(f"Initial Capital: ₹{results['initial_capital']}")
            print(f"Final Value:     ₹{results['final_value']}")
            print(f"Total Return:    {results['total_return_pct']}%")
            print(f"Win Rate:        {results['win_rate_pct']}%")
            print(f"Total Trades:    {results['total_closed_trades']}")
            print(f"Max Drawdown:    {results['max_drawdown_pct']}%\n")
        else:
            print(f"⚠️ Failed to fetch data or run backtest for {ticker}.")
            
    elif args.handle_telegram_commands:
        handle_telegram_commands()
    else:
        run(full=args.full, session=args.session)
