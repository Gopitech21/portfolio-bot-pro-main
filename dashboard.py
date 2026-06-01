from datetime import datetime

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from notifier.telegram_bot import send_message
from services.alert_service import should_alert
from services.data_service import fetch_data
from services.exit_strategy import get_exit_signal
from services.fund_scanner_service import get_quarterly_results, scan_etfs, scan_mutual_funds
from services.market_data_utils import get_last_float, get_series
from services.market_insight_service import get_sector_pulse, get_seasonal_theme_watch
from services.market_service import get_market_status
from services.nse_service import get_all_stocks
from services.volume_delivery_service import get_volume_delivery_movers
from services.portfolio_loader import load_portfolio
from services.portfolio_service import calculate_metrics
from services.portfolio_signal_service import get_portfolio_status
from services.risk_service import risk_level
from services.signal_service import get_signal
from services.ticker_mapper import map_to_ticker
from services.varsity_logic_service import build_long_trade_context
from strategies.strategy import apply_strategy


st.set_page_config(page_title="Portfolio Dashboard", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    /* Metric styling */
    [data-testid="stMetricValue"] {
        font-size: 1.8rem;
        font-weight: 700;
    }
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 1rem;
    }
    .stTabs [data-baseweb="tab"] {
        padding-top: 1rem;
        padding-bottom: 1rem;
        font-weight: 600;
    }
    /* Hide top padding */
    .block-container {
        padding-top: 2rem;
    }
    .codex-table-wrap {
        width: 100%;
        overflow-x: auto;
        border: 1px solid rgba(250, 250, 250, 0.08);
        border-radius: 0.75rem;
        background: rgba(255, 255, 255, 0.02);
    }
    .codex-table {
        width: 100%;
        border-collapse: collapse;
        table-layout: auto;
    }
    .codex-table th,
    .codex-table td {
        text-align: center !important;
        vertical-align: middle !important;
        padding: 0.55rem 0.7rem;
        border-bottom: 1px solid rgba(250, 250, 250, 0.07);
        white-space: normal;
        word-break: break-word;
        font-size: 0.92rem;
    }
    .codex-table th {
        background: rgba(255, 255, 255, 0.04);
        font-weight: 600;
    }
    .codex-table tr:last-child td {
        border-bottom: none;
    }
    .codex-progress-cell {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 0.35rem;
    }
    .codex-progress-track {
        width: min(8.5rem, 100%);
        height: 0.45rem;
        background: rgba(255, 255, 255, 0.1);
        border-radius: 999px;
        overflow: hidden;
        margin: 0 auto;
    }
    .codex-progress-fill {
        height: 100%;
        border-radius: 999px;
        background: linear-gradient(90deg, #36cfc9 0%, #69c0ff 100%);
    }
</style>
""", unsafe_allow_html=True)

st.title("📊 My Portfolio Dashboard")

LIVE_INSIGHT_CACHE_VERSION = "2026-05-27-v3"

with st.sidebar:
    st.header("Dashboard Controls")
    live_mode = st.toggle("Live mode", value=False)
    refresh_seconds = st.slider("Refresh interval (seconds)", 15, 300, 60, disabled=not live_mode)
    notify_accumulate = st.toggle("Telegram accumulate alerts", value=True)
    if st.button("Refresh now", use_container_width=True):
        st.rerun()


def _clear_stale_live_insight_state():
    cached_version = st.session_state.get("live_insight_cache_version")
    if cached_version != LIVE_INSIGHT_CACHE_VERSION:
        for key in [
            "sector_pulse_data",
            "sector_pulse_updated_at",
            "seasonal_theme_data",
            "seasonal_theme_updated_at",
        ]:
            st.session_state.pop(key, None)
        st.session_state["live_insight_cache_version"] = LIVE_INSIGHT_CACHE_VERSION
        return

    sector_pulse = st.session_state.get("sector_pulse_data")
    if isinstance(sector_pulse, dict) and not sector_pulse.get("sectors"):
        st.session_state.pop("sector_pulse_data", None)
        st.session_state.pop("sector_pulse_updated_at", None)

    seasonal_theme = st.session_state.get("seasonal_theme_data")
    if isinstance(seasonal_theme, dict) and not seasonal_theme.get("active") and not seasonal_theme.get("upcoming"):
        st.session_state.pop("seasonal_theme_data", None)
        st.session_state.pop("seasonal_theme_updated_at", None)


_clear_stale_live_insight_state()


def _build_aligned_column_config(df, column_config=None):
    base_config = dict(column_config or {})
    aligned_config = {}

    for column in df.columns:
        config = base_config.get(column)

        if isinstance(config, str):
            aligned_config[column] = {"label": config, "alignment": "center"}
            continue

        if isinstance(config, dict):
            updated = dict(config)
            if updated.get("alignment") is None:
                updated["alignment"] = "center"
            aligned_config[column] = updated
            continue

        aligned_config[column] = {"label": column, "alignment": "center"}

    for column, config in base_config.items():
        if column not in aligned_config:
            aligned_config[column] = config

    return aligned_config


def build_centered_styler(df, *, style_fn=None, style_subset=None):
    """Return a pandas Styler with *style_fn* applied to *style_subset* columns."""
    valid_subset = [col for col in (style_subset or []) if col in df.columns]
    if style_fn and valid_subset:
        styler = df.style
        apply = getattr(styler, "map", None) or styler.applymap
        return apply(style_fn, subset=valid_subset)
    return df


def render_centered_dataframe(df, *, column_config=None, hide_index=True, use_container_width=True, style_fn=None, style_subset=None):
    if df is None:
        return

    data = df
    if style_fn is not None and style_subset is not None:
        data = build_centered_styler(df, style_fn=style_fn, style_subset=style_subset)

    st.dataframe(
        data,
        hide_index=hide_index,
        use_container_width=use_container_width,
        column_config=_build_aligned_column_config(df, column_config=column_config),
    )


def build_dashboard_rows():
    df = load_portfolio()
    market_status = get_market_status()

    total_value = 0.0
    total_invested = 0.0
    data_list = []
    history_map = {}

    for _, row in df.iterrows():
        ticker = row["ticker"]
        data = fetch_data(ticker)

        if data is None or data.empty:
            continue

        try:
            strategy_view = apply_strategy(data.copy())
            context = build_long_trade_context(strategy_view, min_rr=1.5)
            signal_data = get_signal(data, ticker)
            if not signal_data:
                continue

            action, _, _, rsi = signal_data
            price = get_last_float(data, "Close")
            highest_price = float(get_series(data, "Close").max())
            invested, current_value, profit, profit_pct = calculate_metrics(row, price)
            exit_signal = get_exit_signal(
                price,
                row["avg_price"],
                rsi,
                highest_price,
                stop_price=context.get("stop_price"),
                ema_20=float(strategy_view["ema_20"].iloc[-1]) if "ema_20" in strategy_view.columns else None,
                ema_50=float(strategy_view["ema_50"].iloc[-1]) if "ema_50" in strategy_view.columns else None,
            )
            status = get_portfolio_status(action, exit_signal, market_status, profit_pct, rsi)
            risk = risk_level(
                rsi,
                profit_pct,
                rr_ratio=context.get("rr_ratio"),
                volume_confirmed=context.get("volume_confirmed"),
                trend=context.get("trend"),
            )
        except Exception:
            continue

        total_value += current_value
        total_invested += invested
        history_map[ticker] = data.copy()

        data_list.append(
            {
                "Ticker": ticker,
                "Stock": row.get("stock_name", ticker),
                "Status": status,
                "Action": action,
                "Exit": exit_signal,
                "Price": round(price, 2),
                "Avg Price": round(row["avg_price"], 2),
                "Quantity": row["quantity"],
                "Value": round(current_value, 2),
                "P/L %": round(profit_pct, 2),
                "RSI": round(rsi, 1),
                "Risk": risk,
                "Source": row.get("source", ""),
            }
        )

    df_display = pd.DataFrame(data_list)
    if not df_display.empty:
        df_display = df_display.sort_values("Value", ascending=False).reset_index(drop=True)

    return market_status, total_invested, total_value, df_display, history_map


def build_distribution_frame(df_display, top_n=10):
    distribution = df_display[["Ticker", "Value"]].copy()
    distribution = distribution.groupby("Ticker", as_index=False)["Value"].sum()
    distribution = distribution.sort_values("Value", ascending=False).reset_index(drop=True)

    if len(distribution) > top_n:
        top = distribution.head(top_n).copy()
        other_value = distribution.iloc[top_n:]["Value"].sum()
        top.loc[len(top)] = {"Ticker": "Others", "Value": other_value}
        return top

    return distribution


def render_distribution_chart(df_display):
    if df_display.empty:
        st.info("No live holding values are available yet.")
        return

    distribution = build_distribution_frame(df_display, top_n=10)

    chart_col, table_col = st.columns([2.2, 1], gap="large")

    with chart_col:
        fig, ax = plt.subplots(figsize=(9, 6))
        fig.patch.set_alpha(0)  # Transparent background
        ax.patch.set_alpha(0)
        
        wedges, texts, autotexts = ax.pie(
            distribution["Value"],
            labels=distribution["Ticker"],
            autopct=lambda pct: f"{pct:.1f}%" if pct >= 4 else "",
            startangle=90,
            pctdistance=0.75,
            textprops={'color': "w", 'weight': 'bold'},
            wedgeprops={"width": 0.42, "edgecolor": "#1E1E1E", "linewidth": 2},
        )
        for text in texts:
            text.set_color('#E0E0E0') # Light gray for labels
            
        ax.set_title("Top Holdings Allocation", fontsize=16, color='white', pad=20)
        ax.axis("equal")
        st.pyplot(fig, clear_figure=True)

    with table_col:
        total_value = distribution["Value"].sum()
        summary = distribution.copy()
        summary["Weight %"] = (summary["Value"] / total_value * 100).round(2)
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.caption("Top holdings by current value")
        render_centered_dataframe(
            summary,
            column_config={
                "Ticker": "Ticker",
                "Value": st.column_config.NumberColumn("Value", format="₹ %.2f"),
                "Weight %": st.column_config.ProgressColumn("Weight", format="%.1f%%", min_value=0, max_value=100)
            },
        )


def render_action_tables(df_display):
    if df_display.empty:
        st.info("No holdings with live price data are available right now.")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    action_text = df_display["Action"].astype(str).str.upper()
    exit_text = df_display["Exit"].astype(str).str.upper()

    sell_mask = (
        (df_display["Status"] == "SELL")
        | action_text.str.contains("SELL", na=False)
        | exit_text.str.contains("BOOK PROFIT|TRAILING STOP HIT", na=False, regex=True)
    )
    accumulate_mask = (
        (df_display["Status"] == "BUY")
        & action_text.str.contains("ADD MORE", na=False)
        & ~sell_mask
        & ~df_display["Risk"].astype(str).str.contains("HIGH LOSS RISK", case=False, na=False)
    )
    hold_wait_mask = df_display["Status"].isin(["HOLD", "WAIT"])

    sell_df = df_display[sell_mask].copy()
    hold_wait_df = df_display[hold_wait_mask].copy()
    accumulate_df = df_display[accumulate_mask].copy()

    def display_portfolio_table(df, title, empty_msg):
        st.markdown(f"### {title}")
        if df.empty:
            st.caption(empty_msg)
            return

        def highlight_pl(val):
            if isinstance(val, (int, float)):
                color = '#00C07F' if val > 0 else '#FF4B4B' if val < 0 else 'inherit'
                return f'color: {color}; font-weight: bold;'
            return ''
            
        render_centered_dataframe(
            df,
            column_config={
                "Ticker": st.column_config.TextColumn("Ticker", width="medium"),
                "Stock": "Name",
                "Status": st.column_config.TextColumn("Status"),
                "Price": st.column_config.NumberColumn("Price (₹)", format="₹ %.2f"),
                "Avg Price": st.column_config.NumberColumn("Avg Price", format="₹ %.2f"),
                "Quantity": "Qty",
                "Value": st.column_config.NumberColumn("Value (₹)", format="₹ %.2f"),
                "P/L %": st.column_config.NumberColumn("P/L %", format="%.2f%%"),
                "RSI": st.column_config.ProgressColumn("RSI (Momentum)", format="%.1f", min_value=0, max_value=100),
            },
            style_fn=highlight_pl,
            style_subset=["P/L %"],
        )

    display_portfolio_table(sell_df, "🔴 Sell / Exit Holdings", "No sell/exit signals now.")
    st.markdown("<br>", unsafe_allow_html=True)
    display_portfolio_table(hold_wait_df, "🟡 Hold / Wait Holdings", "No hold/wait stocks now.")
    st.markdown("<br>", unsafe_allow_html=True)
    display_portfolio_table(accumulate_df, "🟢 Accumulate Holdings", "No accumulate signals now.")

    return sell_df, hold_wait_df, accumulate_df


def render_volume_mover_tables(df_display):
    st.caption("Institutional activity tracking based on Zerodha Varsity volume-price relationships.")

    with st.expander("📖 View Signal Classification Guide"):
        st.markdown("""
        **Volume-Price Relationship Rules:**
        - 🟢 **BULLISH (Smart Money Buying)**: Price ↑ + Volume ↑ (>10d avg) → Institutional buying
        - 🔴 **BEARISH (Smart Money Selling)**: Price ↓ + Volume ↑ (>10d avg) → Institutional selling
        - ⚠️ **CAUTION (Bull Trap)**: Price ↑ + Volume ↓ (<10d avg) → Weak hands buying (avoid)
        - ⚠️ **CAUTION (Bear Trap)**: Price ↓ + Volume ↓ (<10d avg) → Weak hands selling (avoid)
        """)

    if st.button("🔄 Refresh Volume Movers", key="refresh_volume_movers", use_container_width=True):
        st.session_state.pop("volume_movers_data", None)
        st.session_state.pop("volume_movers_updated_at", None)

    movers = st.session_state.get("volume_movers_data")
    if movers is None:
        try:
            watchlist = get_all_stocks(limit=200)
        except Exception:
            watchlist = df_display["Ticker"].dropna().unique().tolist() if not df_display.empty else []

        if not watchlist:
            st.info("Unable to fetch NSE/BSE stocks for volume analysis.")
            return

        with st.spinner("Refreshing live volume movers from the market..."):
            movers = get_volume_delivery_movers(watchlist, lookback_days=10, top_n=10)
        st.session_state["volume_movers_data"] = movers
        st.session_state["volume_movers_updated_at"] = datetime.now().strftime("%d %b %Y %I:%M:%S %p")

    last_refresh = st.session_state.get("volume_movers_updated_at")
    if last_refresh:
        st.caption(f"Last refreshed: {last_refresh}")

    swing_df = pd.DataFrame(movers.get("swing", []))
    long_term_df = pd.DataFrame(movers.get("long_term", []))
    all_analyzed = movers.get("all_analyzed", [])

    caution_stocks = [s for s in all_analyzed if s.get("warning")]
    if caution_stocks:
        st.warning(f"⚠️ **{len(caution_stocks)} CAUTION signals detected - Weak hands (bull/bear traps)**")
        for stock in caution_stocks[:5]:
            st.caption(f"**{stock['ticker']}**: {stock['warning']} | Signal: {stock['signal']} | Conf: {stock['confidence']}%")

    smart_money_stocks = [s for s in all_analyzed if s.get("category") in ("bullish_smart", "bearish_smart")]
    if smart_money_stocks:
        st.success(f"✅ **{len(smart_money_stocks)} SMART MONEY signals detected - Institutional activity**")

    col1, col2 = st.columns(2, gap="large")

    with col1:
        st.markdown("### ⚡ Swing Volume Movers (10D)")
        if swing_df.empty:
            st.caption("No swing volume movers detected.")
        else:
            display_swing = swing_df[[
                "ticker", "signal", "confidence", "price_change_10d", 
                "volume_ratio", "swing_score"
            ]].copy()
            render_centered_dataframe(
                display_swing,
                column_config={
                    "ticker": "Ticker",
                    "signal": "Signal",
                    "confidence": st.column_config.ProgressColumn("Confidence", format="%d%%", min_value=0, max_value=100),
                    "price_change_10d": st.column_config.NumberColumn("10D Change", format="%.2f%%"),
                    "volume_ratio": st.column_config.NumberColumn("Vol Ratio", format="x%.1f"),
                    "swing_score": "Score"
                },
            )

    with col2:
        st.markdown("### 🏛️ Long-Term Volume Movers (10D)")
        if long_term_df.empty:
            st.caption("No long-term volume movers detected.")
        else:
            display_long = long_term_df[[
                "ticker", "signal", "confidence", "price_change_10d", 
                "delivery_proxy_pct", "long_term_score"
            ]].copy()
            render_centered_dataframe(
                display_long,
                column_config={
                    "ticker": "Ticker",
                    "signal": "Signal",
                    "confidence": st.column_config.ProgressColumn("Confidence", format="%d%%", min_value=0, max_value=100),
                    "price_change_10d": st.column_config.NumberColumn("10D Change", format="%.2f%%"),
                    "delivery_proxy_pct": st.column_config.ProgressColumn("Delivery Proxy", format="%d%%", min_value=0, max_value=100),
                    "long_term_score": "Score"
                },
            )


def render_indicator_picks(df_display):
    st.caption("AI-assisted scans for intraday, breakout, swing, and long-term setups.")
    
    try:
        watchlist = get_all_stocks(limit=40)
    except Exception:
        watchlist = df_display["Ticker"].dropna().unique().tolist() if not df_display.empty else []

    if not watchlist:
        st.info("Unable to fetch stocks for screening.")
        return

    with st.expander("🔍 Scan Market for Swing & Long Term Picks (Click to Run)", expanded=True):
        if st.button("🚀 Run AI Indicator Scanner", key="scan_indicators", use_container_width=True, type="primary"):
            with st.spinner("Scanning market with AI & Technical Indicators (ADX, MFI, CCI, etc.)..."):
                from services.screener_service import get_screener_picks
                st.session_state["screener_picks"] = get_screener_picks(watchlist[:40]) 
        
        if "screener_picks" in st.session_state:
            picks = st.session_state["screener_picks"]
            swing = pd.DataFrame(picks.get("swing", []))
            lt = pd.DataFrame(picks.get("long_term", []))
            bo = pd.DataFrame(picks.get("breakout", []))
            intraday = pd.DataFrame(picks.get("intraday", []))
        
            st.markdown("### ⏱️ Intraday Setups (High Risk)")
            if not intraday.empty:
                display_intraday = intraday[["ticker", "price", "strategy", "entry", "exit", "stop_loss", "rr_ratio", "capital_req", "qty"]].copy()
                render_centered_dataframe(
                    display_intraday,
                    column_config={
                        "ticker": st.column_config.TextColumn("Ticker", width="medium"),
                        "price": st.column_config.NumberColumn("Current Price", format="₹ %.2f"),
                        "strategy": "Strategy",
                        "entry": st.column_config.NumberColumn("Entry", format="₹ %.2f"),
                        "exit": st.column_config.NumberColumn("Exit", format="₹ %.2f"),
                        "stop_loss": st.column_config.NumberColumn("Stop Loss", format="₹ %.2f"),
                        "rr_ratio": "RRR",
                        "capital_req": st.column_config.NumberColumn("Capital Req", format="₹ %.2f"),
                        "qty": "Quantity"
                    },
                )
            else:
                st.info("No intraday setups found.")
                
            st.markdown("<br>", unsafe_allow_html=True)
        
            st.markdown("### 🚀 Breakout Ready (Consolidation)")
            if not bo.empty:
                display_bo = bo[["ticker", "price", "entry", "exit", "stop_loss", "rr_ratio", "score", "change", "dist_high", "rsi", "vol_drying", "vol_confirmed"]].copy()
                render_centered_dataframe(
                    display_bo,
                    column_config={
                        "ticker": "Ticker",
                        "price": st.column_config.NumberColumn("Current Price", format="₹ %.2f"),
                        "entry": st.column_config.NumberColumn("Entry", format="₹ %.2f"),
                        "exit": st.column_config.NumberColumn("Exit", format="₹ %.2f"),
                        "stop_loss": st.column_config.NumberColumn("Stop Loss", format="₹ %.2f"),
                        "rr_ratio": "RRR",
                        "score": st.column_config.NumberColumn("AI Score"),
                        "change": st.column_config.NumberColumn("Change", format="%.2f%%"),
                        "dist_high": st.column_config.ProgressColumn("Dist to 52W High", format="%.1f%%", min_value=0, max_value=100),
                        "rsi": st.column_config.ProgressColumn("RSI", format="%.1f", min_value=0, max_value=100),
                        "vol_drying": "Volume Drying?",
                        "vol_confirmed": "Vol OK"
                    },
                )
            else:
                st.info("No breakout setups found.")
                
            st.markdown("<br>", unsafe_allow_html=True)
        
            col1, col2 = st.columns(2, gap="large")
            with col1:
                st.markdown("### ⚡ Swing Trades (Momentum)")
                if not swing.empty:
                    display_swing = swing[["ticker", "price", "entry", "exit", "stop_loss", "rr_ratio", "score", "change", "adx", "rsi", "mfi", "vol_confirmed"]].copy()
                    render_centered_dataframe(
                        display_swing,
                        column_config={
                            "ticker": "Ticker",
                            "price": st.column_config.NumberColumn("Current Price", format="₹ %.2f"),
                            "entry": st.column_config.NumberColumn("Entry", format="₹ %.2f"),
                            "exit": st.column_config.NumberColumn("Exit", format="₹ %.2f"),
                            "stop_loss": st.column_config.NumberColumn("Stop Loss", format="₹ %.2f"),
                            "rr_ratio": "RRR",
                            "score": "Score",
                            "change": st.column_config.NumberColumn("Change", format="%.2f%%"),
                            "rsi": st.column_config.ProgressColumn("RSI", format="%.1f", min_value=0, max_value=100),
                            "mfi": st.column_config.ProgressColumn("MFI", format="%.1f", min_value=0, max_value=100),
                            "adx": st.column_config.ProgressColumn("ADX (Trend)", format="%.1f", min_value=0, max_value=100),
                            "vol_confirmed": "Vol OK",
                        },
                    )
                else:
                    st.caption("No strong swing setups found.")
                    
            with col2:
                st.markdown("### 🏛️ Long Term (Quality + Support)")
                if not lt.empty:
                    display_lt = lt[["ticker", "price", "entry", "exit", "stop_loss", "rr_ratio", "score", "fundamental_quality", "fundamental_score", "cci", "rsi", "mfi"]].copy()
                    render_centered_dataframe(
                        display_lt,
                        column_config={
                            "ticker": "Ticker",
                            "price": st.column_config.NumberColumn("Current Price", format="₹ %.2f"),
                            "entry": st.column_config.NumberColumn("Entry", format="₹ %.2f"),
                            "exit": st.column_config.NumberColumn("Exit", format="₹ %.2f"),
                            "stop_loss": st.column_config.NumberColumn("Stop Loss", format="₹ %.2f"),
                            "rr_ratio": "RRR",
                            "score": "Score",
                            "fundamental_quality": "Quality",
                            "fundamental_score": "FA Score",
                            "rsi": st.column_config.ProgressColumn("RSI", format="%.1f", min_value=0, max_value=100),
                            "mfi": st.column_config.ProgressColumn("MFI", format="%.1f", min_value=0, max_value=100),
                            "cci": "CCI",
                        },
                    )
                else:
                    st.caption("No strong long-term value setups found.")


def render_sector_pulse():
    st.caption("Live sector strength built from current market metadata and live stock moves.")

    if st.button("🔄 Refresh Sector Pulse", key="refresh_sector_pulse", use_container_width=True):
        st.session_state.pop("sector_pulse_data", None)
        st.session_state.pop("sector_pulse_updated_at", None)

    pulse = st.session_state.get("sector_pulse_data")
    if pulse is None or not pulse.get("sectors"):
        with st.spinner("Scanning live sector strength..."):
            pulse = get_sector_pulse(max_sectors=None, top_stocks_per_sector=5, limit=60)
            if not pulse.get("sectors"):
                pulse = get_sector_pulse(max_sectors=None, top_stocks_per_sector=5, limit=40)
        st.session_state["sector_pulse_data"] = pulse
        st.session_state["sector_pulse_updated_at"] = pulse.get("updated_at")

    sectors = pulse.get("sectors", [])
    if not sectors:
        st.info("Unable to build live sector pulse right now. Please try Refresh Sector Pulse once more.")
        return

    last_refresh = st.session_state.get("sector_pulse_updated_at")
    if last_refresh:
        record_count = pulse.get("record_count")
        if record_count:
            st.caption(f"Last refreshed: {last_refresh} | Live stocks mapped: {record_count}")
        else:
            st.caption(f"Last refreshed: {last_refresh}")

    top_sectors = sectors[:3]
    summary_cols = st.columns(len(top_sectors))
    for col, sector in zip(summary_cols, top_sectors):
        with col:
            col.metric(
                sector["sector"],
                f"{sector['avg_day_change']:.2f}%",
                delta=f"Breadth {sector['breadth_pct']:.1f}%",
            )

    sector_frame = pd.DataFrame(
        [
            {
                "Sector": sector["sector"],
                "Today %": sector["avg_day_change"],
                "5D %": sector["avg_week_change"],
                "Breadth %": sector["breadth_pct"],
                "Status": sector["status"],
                "Leaders": sector["leaders"],
            }
            for sector in sectors
        ]
    )

    render_centered_dataframe(
        sector_frame,
        column_config={
            "Sector": st.column_config.TextColumn("Sector", width="medium"),
            "Today %": st.column_config.NumberColumn("Today %", format="%.2f%%"),
            "5D %": st.column_config.NumberColumn("5D %", format="%.2f%%"),
            "Breadth %": st.column_config.ProgressColumn("Breadth", format="%.1f%%", min_value=0, max_value=100),
            "Status": "Status",
            "Leaders": st.column_config.TextColumn("Top Stocks", width="large"),
        },
    )

    for sector in sectors:
        with st.expander(f"{sector['sector']} | {sector['status']} | {sector['avg_day_change']:.2f}% today", expanded=False):
            stocks = pd.DataFrame(sector.get("stocks", []))
            if stocks.empty:
                st.caption("No live stocks were available for this sector.")
                continue

            display_stocks = stocks[["symbol", "price", "day_change", "week_change", "vol_ratio", "above_ma20"]].copy()
            display_stocks["above_ma20"] = display_stocks["above_ma20"].map(lambda value: "Yes" if value else "No")

            render_centered_dataframe(
                display_stocks,
                column_config={
                    "symbol": st.column_config.TextColumn("Ticker", width="medium"),
                    "price": st.column_config.NumberColumn("Price", format="₹ %.2f"),
                    "day_change": st.column_config.NumberColumn("Today %", format="%.2f%%"),
                    "week_change": st.column_config.NumberColumn("5D %", format="%.2f%%"),
                    "vol_ratio": st.column_config.NumberColumn("Vol Ratio", format="x%.2f"),
                    "above_ma20": "Above MA20",
                },
            )


def render_seasonal_theme_watch():
    st.caption("Calendar-based theme watch that blends seasonal logic with live price action and simple news sentiment.")

    if st.button("🔄 Refresh Seasonal Themes", key="refresh_seasonal_themes", use_container_width=True):
        st.session_state.pop("seasonal_theme_data", None)
        st.session_state.pop("seasonal_theme_updated_at", None)

    theme_watch = st.session_state.get("seasonal_theme_data")
    if theme_watch is None or (not theme_watch.get("active") and not theme_watch.get("upcoming")):
        with st.spinner("Scanning seasonal themes from the live market..."):
            theme_watch = get_seasonal_theme_watch(top_stocks_per_theme=5, limit=80)
            if not theme_watch.get("active") and not theme_watch.get("upcoming"):
                theme_watch = get_seasonal_theme_watch(top_stocks_per_theme=5, limit=60)
            if not theme_watch.get("active") and not theme_watch.get("upcoming"):
                theme_watch = get_seasonal_theme_watch(top_stocks_per_theme=5, limit=40)
        st.session_state["seasonal_theme_data"] = theme_watch
        st.session_state["seasonal_theme_updated_at"] = theme_watch.get("updated_at")

    active = theme_watch.get("active", [])
    upcoming = theme_watch.get("upcoming", [])
    if not active and not upcoming:
        st.info("Unable to build seasonal themes right now. Please try Refresh Seasonal Themes once more.")
        return

    last_refresh = st.session_state.get("seasonal_theme_updated_at")
    if last_refresh:
        record_count = theme_watch.get("record_count")
        if record_count:
            st.caption(f"Last refreshed: {last_refresh} | Live stocks mapped: {record_count}")
        else:
            st.caption(f"Last refreshed: {last_refresh}")

    overview_rows = []
    for item in active + upcoming:
        overview_rows.append(
            {
                "Theme": item["theme"],
                "Stage": item["stage"],
                "Window": item["window"],
                "Today %": item["avg_day_change"],
                "5D %": item["avg_week_change"],
                "Breadth %": item["breadth_pct"],
                "Bias": item["bias"],
                "News": item["news_sentiment"],
                "Leaders": item["leaders"],
            }
        )

    overview_frame = pd.DataFrame(overview_rows)
    render_centered_dataframe(
        overview_frame,
        column_config={
            "Theme": st.column_config.TextColumn("Theme", width="large"),
            "Stage": "Stage",
            "Window": "Window",
            "Today %": st.column_config.NumberColumn("Today %", format="%.2f%%"),
            "5D %": st.column_config.NumberColumn("5D %", format="%.2f%%"),
            "Breadth %": st.column_config.ProgressColumn("Breadth", format="%.1f%%", min_value=0, max_value=100),
            "Bias": "Bias",
            "News": "News Sentiment",
            "Leaders": st.column_config.TextColumn("Top Stocks", width="large"),
        },
    )

    for title, items in [("🌿 Active Themes", active), ("🗓️ Upcoming Themes", upcoming)]:
        if not items:
            continue

        st.markdown(f"### {title}")
        for item in items:
            with st.expander(f"{item['theme']} | {item['stage']} | {item['bias']}", expanded=False):
                st.caption(item["summary"])
                info_cols = st.columns(4)
                info_cols[0].metric("Today", f"{item['avg_day_change']:.2f}%")
                info_cols[1].metric("5D", f"{item['avg_week_change']:.2f}%")
                info_cols[2].metric("Breadth", f"{item['breadth_pct']:.1f}%")
                info_cols[3].metric("News", item["news_sentiment"])
                st.caption(f"Related sectors: {item['related_sectors']}")

                stocks = pd.DataFrame(item.get("stocks", []))
                if stocks.empty:
                    st.caption("No live stock data available for this theme.")
                    continue

                display_stocks = stocks[["symbol", "price", "day_change", "week_change", "vol_ratio", "above_ma20"]].copy()
                display_stocks["above_ma20"] = display_stocks["above_ma20"].map(lambda value: "Yes" if value else "No")

                render_centered_dataframe(
                    display_stocks,
                    column_config={
                        "symbol": st.column_config.TextColumn("Ticker", width="medium"),
                        "price": st.column_config.NumberColumn("Price", format="₹ %.2f"),
                        "day_change": st.column_config.NumberColumn("Today %", format="%.2f%%"),
                        "week_change": st.column_config.NumberColumn("5D %", format="%.2f%%"),
                        "vol_ratio": st.column_config.NumberColumn("Vol Ratio", format="x%.2f"),
                        "above_ma20": "Above MA20",
                    },
                )


def format_compact_number(value):
    if value is None or pd.isna(value):
        return "N/A"

    absolute = abs(float(value))

    if absolute >= 1e7:
        return f"{float(value) / 1e7:,.1f} Cr"
    if absolute >= 1e5:
        return f"{float(value) / 1e5:,.1f} L"
    return f"{float(value):,.2f}"


@st.cache_data(ttl=1800, show_spinner=False)
def load_etf_scan_results():
    return scan_etfs()


@st.cache_data(ttl=1800, show_spinner=False)
def load_mutual_fund_scan_results():
    return scan_mutual_funds()


@st.cache_data(ttl=1800, show_spinner=False)
def load_quarterly_results(resolved_ticker):
    return get_quarterly_results(resolved_ticker)


def render_fund_summary_cards(frame, label_column, value_column):
    top_picks = frame.head(3)
    if top_picks.empty:
        st.info("No high-conviction candidates were found in the current scan.")
        return

    summary_cols = st.columns(len(top_picks))
    for column, (_, row) in zip(summary_cols, top_picks.iterrows()):
        with column:
            value = row.get(value_column)
            if pd.notna(value) and value_column in {"Price", "NAV"}:
                formatted_value = f"₹ {float(value):,.2f}"
            else:
                formatted_value = value
            st.metric(row[label_column][:32], f"{int(row['Score'])}/100", row["Verdict"])
            st.caption(f"{value_column}: {formatted_value}")
            st.caption(row["Analysis"])


def render_etf_scanner_tab():
    st.caption("Momentum and risk scanner for a curated ETF universe using 6M/1Y returns, trend, drawdown, and consistency.")

    with st.expander("How the ETF score works"):
        st.markdown("""
        - Stronger recent and 1Y returns improve the score.
        - Staying above 50DMA and keeping 50DMA above 200DMA helps confirm trend quality.
        - Lower drawdown, lower volatility, and more positive months lift conviction.
        - The scanner is best used to shortlist ideas, not replace position sizing or valuation checks.
        """)

    if st.button("Run ETF Scanner", key="run_etf_scanner", type="primary", use_container_width=True):
        load_etf_scan_results.clear()
        with st.spinner("Scanning ETF price trends..."):
            st.session_state["etf_scan_results"] = load_etf_scan_results()

    results = st.session_state.get("etf_scan_results")
    if not results:
        st.info("Run the ETF scanner to rank broad market, sector, commodity, PSU, and international ETFs.")
        return

    etf_frame = pd.DataFrame(results)
    categories = sorted(etf_frame["Category"].dropna().unique().tolist())
    selected_categories = st.multiselect(
        "ETF categories",
        categories,
        default=categories,
        key="etf_scanner_categories",
    )

    filtered = etf_frame[etf_frame["Category"].isin(selected_categories)].reset_index(drop=True)
    if filtered.empty:
        st.warning("No ETFs match the current filter.")
        return

    render_fund_summary_cards(filtered, "Ticker", "Price")

    render_centered_dataframe(
        filtered,
        column_config={
            "Name": st.column_config.TextColumn("ETF", width="large"),
            "Ticker": st.column_config.TextColumn("Ticker", width="medium"),
            "Category": "Category",
            "Category Rank": st.column_config.NumberColumn("Cat Rank", format="%d"),
            "Price": st.column_config.NumberColumn("Price", format="₹ %.2f"),
            "1M %": st.column_config.NumberColumn("1M %", format="%.2f%%"),
            "3M %": st.column_config.NumberColumn("3M %", format="%.2f%%"),
            "6M %": st.column_config.NumberColumn("6M %", format="%.2f%%"),
            "1Y %": st.column_config.NumberColumn("1Y %", format="%.2f%%"),
            "Max DD %": st.column_config.NumberColumn("Max DD", format="%.2f%%"),
            "Volatility %": st.column_config.NumberColumn("Volatility", format="%.2f%%"),
            "Positive Months %": st.column_config.NumberColumn("Positive Months", format="%.1f%%"),
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "Verdict": "Verdict",
            "Analysis": st.column_config.TextColumn("Why It Looks Good / Weak", width="large"),
        },
    )


def render_mutual_fund_scanner_tab():
    st.caption("NAV-based scanner for a curated mutual fund shortlist. It focuses on trend, returns, drawdown, and consistency.")

    with st.expander("How the mutual fund score works"):
        st.markdown("""
        - Recent NAV returns and 1Y return provide the performance backbone.
        - The trend check uses 50-day and 200-day NAV averages.
        - Lower drawdowns and steadier month-to-month gains improve the score.
        - Expense ratio, AUM, and fund-manager changes are not included in this version.
        """)

    if st.button("Run Mutual Fund Scanner", key="run_mutual_fund_scanner", type="primary", use_container_width=True):
        load_mutual_fund_scan_results.clear()
        with st.spinner("Scanning mutual fund NAV trends..."):
            st.session_state["mutual_fund_scan_results"] = load_mutual_fund_scan_results()

    results = st.session_state.get("mutual_fund_scan_results")
    if not results:
        st.info("Run the mutual fund scanner to compare a curated shortlist of direct-plan growth funds.")
        return

    fund_frame = pd.DataFrame(results)
    categories = sorted(fund_frame["Category"].dropna().unique().tolist())
    selected_categories = st.multiselect(
        "Mutual fund categories",
        categories,
        default=categories,
        key="mutual_fund_scanner_categories",
    )

    filtered = fund_frame[fund_frame["Category"].isin(selected_categories)].reset_index(drop=True)
    if filtered.empty:
        st.warning("No mutual funds match the current filter.")
        return

    render_fund_summary_cards(filtered, "Name", "NAV")

    render_centered_dataframe(
        filtered,
        column_config={
            "Name": st.column_config.TextColumn("Scheme", width="large"),
            "Fund House": st.column_config.TextColumn("Fund House", width="medium"),
            "Scheme Code": st.column_config.NumberColumn("Code", format="%d"),
            "Category": st.column_config.TextColumn("Category", width="large"),
            "Category Rank": st.column_config.NumberColumn("Cat Rank", format="%d"),
            "NAV": st.column_config.NumberColumn("NAV", format="₹ %.2f"),
            "1M %": st.column_config.NumberColumn("1M %", format="%.2f%%"),
            "3M %": st.column_config.NumberColumn("3M %", format="%.2f%%"),
            "6M %": st.column_config.NumberColumn("6M %", format="%.2f%%"),
            "1Y %": st.column_config.NumberColumn("1Y %", format="%.2f%%"),
            "Max DD %": st.column_config.NumberColumn("Max DD", format="%.2f%%"),
            "Volatility %": st.column_config.NumberColumn("Volatility", format="%.2f%%"),
            "Positive Months %": st.column_config.NumberColumn("Positive Months", format="%.1f%%"),
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "Verdict": "Verdict",
            "Analysis": st.column_config.TextColumn("Why It Looks Good / Weak", width="large"),
        },
    )


def render_quarterly_results_tab():
    st.caption("Quarterly result reader for listed companies. ETFs and mutual funds usually do not publish company-style quarterly P&L tables.")

    query = st.text_input(
        "Enter company ticker or name",
        value=st.session_state.get("quarterly_results_query", "RELIANCE.NS"),
        key="quarterly_results_query",
    )

    if st.button("Read Quarterly Results", key="read_quarterly_results", type="primary", use_container_width=True):
        resolved_ticker = map_to_ticker(query) or str(query).strip().upper()
        load_quarterly_results.clear()
        with st.spinner(f"Loading quarterly results for {resolved_ticker}..."):
            st.session_state["quarterly_results_symbol"] = resolved_ticker
            st.session_state["quarterly_results_data"] = load_quarterly_results(resolved_ticker)

    results = st.session_state.get("quarterly_results_data")
    resolved_ticker = st.session_state.get("quarterly_results_symbol")

    if not results or not resolved_ticker:
        st.info("Enter a listed stock ticker like RELIANCE.NS, TCS.NS, or HDFCBANK.NS to read quarterly numbers.")
        return

    st.markdown(f"### {resolved_ticker}")
    quarterly_table = results.get("quarterly_table", pd.DataFrame())

    if quarterly_table.empty:
        st.warning(results.get("notes", "Quarterly results are not available for this symbol."))
        st.caption("This usually happens for ETFs, mutual funds, and some instruments that do not expose company financial statements.")
        return

    summary = results.get("summary", {})
    latest_quarter = results.get("latest_quarter")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Latest Quarter", latest_quarter.strftime("%d %b %Y") if latest_quarter is not None else "N/A")
    m2.metric("Revenue", format_compact_number(summary.get("Revenue")))
    m3.metric("Net Income", format_compact_number(summary.get("Net Income")))
    m4.metric("EPS", f"{summary.get('EPS'):.2f}" if summary.get("EPS") is not None else "N/A")

    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Revenue QoQ", f"{summary.get('Revenue QoQ %'):.2f}%" if summary.get("Revenue QoQ %") is not None else "N/A")
    g2.metric("Revenue YoY", f"{summary.get('Revenue YoY %'):.2f}%" if summary.get("Revenue YoY %") is not None else "N/A")
    g3.metric("Net Income QoQ", f"{summary.get('Net Income QoQ %'):.2f}%" if summary.get("Net Income QoQ %") is not None else "N/A")
    g4.metric("Net Income YoY", f"{summary.get('Net Income YoY %'):.2f}%" if summary.get("Net Income YoY %") is not None else "N/A")

    chart_frame = quarterly_table.copy()
    chart_columns = [column for column in ["Revenue", "Net Income"] if column in chart_frame.columns]
    if chart_columns:
        chart_frame[chart_columns] = chart_frame[chart_columns] / 1e7
        st.line_chart(chart_frame[chart_columns], use_container_width=True)

    display_table = quarterly_table.copy().sort_index(ascending=False)
    display_table.index = display_table.index.strftime("%d-%b-%Y")

    rename_map = {}
    for column in ["Revenue", "Operating Income", "Net Income", "EBITDA"]:
        if column in display_table.columns:
            display_table[column] = display_table[column] / 1e7
            rename_map[column] = f"{column} (Cr)"

    display_table = display_table.rename(columns=rename_map)
    display_table = display_table.reset_index().rename(columns={"index": "Quarter End"})

    render_centered_dataframe(
        display_table,
        column_config={
            "Quarter End": "Quarter End",
            "Revenue (Cr)": st.column_config.NumberColumn("Revenue (Cr)", format="%.2f"),
            "Operating Income (Cr)": st.column_config.NumberColumn("Operating Income (Cr)", format="%.2f"),
            "Net Income (Cr)": st.column_config.NumberColumn("Net Income (Cr)", format="%.2f"),
            "EBITDA (Cr)": st.column_config.NumberColumn("EBITDA (Cr)", format="%.2f"),
            "EPS": st.column_config.NumberColumn("EPS", format="%.2f"),
        },
    )


def render_fund_scanner():
    fund_tabs = st.tabs(["📦 ETF Scanner", "🏦 Mutual Fund Scanner", "🧾 Quarterly Results"])

    with fund_tabs[0]:
        render_etf_scanner_tab()

    with fund_tabs[1]:
        render_mutual_fund_scanner_tab()

    with fund_tabs[2]:
        render_quarterly_results_tab()


def send_accumulate_alert_if_needed(df_display, accumulate_df, market_status):
    if df_display.empty:
        return

    if "Ticker" not in df_display.columns:
        return

    strong_candidates = pd.DataFrame()
    if not accumulate_df.empty:
        strong_candidates = accumulate_df[
            (accumulate_df["RSI"] <= 45)
            | accumulate_df["Action"].astype(str).str.contains("OVERSOLD|RECOVERY", case=False, na=False, regex=True)
        ].copy()

    strong_tickers = set(strong_candidates["Ticker"].tolist()) if not strong_candidates.empty else set()
    newly_triggered = []

    for _, row in df_display.iterrows():
        ticker = row.get("Ticker")
        if not ticker:
            continue

        state_key = f"{ticker}::ACCUMULATE"
        state_value = "ACTIVE" if ticker in strong_tickers else "INACTIVE"

        changed = should_alert(state_key, state_value)
        if changed and state_value == "ACTIVE":
            match = strong_candidates[strong_candidates["Ticker"] == ticker]
            if not match.empty:
                newly_triggered.append(match.iloc[0])

    if not newly_triggered:
        return

    lines = []
    for item in newly_triggered[:8]:
        lines.append(
            f"{item['Ticker']} | Price ₹{item['Price']} | RSI {item['RSI']} | P/L {item['P/L %']}% | {item['Action']}"
        )

    message = (
        "🟢 Accumulate Opportunity Alert\n\n"
        f"Market: {market_status.get('regime', 'UNKNOWN')}\n"
        "Good accumulate chances detected:\n\n"
        + "\n".join(lines)
    )

    try:
        send_message(message)
        st.success("Telegram alert sent for new accumulate opportunities.")
    except Exception as exc:
        st.warning(f"Telegram alert could not be sent: {exc}")


def build_daily_pnl_frame(price_df, quantity):
    close = get_series(price_df, "Close").dropna()

    if close.empty:
        return pd.DataFrame()

    last_month = close.tail(22).copy()
    day_change = last_month.diff().fillna(0.0)

    pnl_df = pd.DataFrame(
        {
            "Date": pd.to_datetime(last_month.index).strftime("%d-%b-%Y"),
            "Close": last_month.round(2).values,
            "Daily Change": day_change.round(2).values,
            "Daily Change %": (day_change / last_month.shift(1).replace(0, pd.NA) * 100).fillna(0.0).round(2).values,
            "Daily P/L (₹)": (day_change * float(quantity)).round(2).values,
        }
    )
    return pnl_df


def render_monthly_daily_pnl(df_display, history_map):
    st.caption("Click a stock name to expand and see day-wise gain/loss based on your held quantity.")

    if df_display.empty:
        st.info("No holdings available for daily P/L view.")
        return

    for _, row in df_display.iterrows():
        ticker = row["Ticker"]
        stock_name = row["Stock"]
        quantity = row["Quantity"]

        with st.expander(f"**{stock_name} ({ticker})**", expanded=False):
            history = history_map.get(ticker)

            if history is None or history.empty:
                st.warning("Price history unavailable for this stock.")
                continue

            pnl_df = build_daily_pnl_frame(history, quantity)

            if pnl_df.empty:
                st.warning("Not enough price data to compute daily P/L.")
                continue

            chart_frame = pnl_df.copy()
            chart_frame["Date"] = pd.to_datetime(chart_frame["Date"], format="%d-%b-%Y")
            chart_frame = chart_frame.set_index("Date")

            st.line_chart(chart_frame[["Daily P/L (₹)"]], use_container_width=True)
            
            def highlight_daily(val):
                if isinstance(val, (int, float)):
                    color = '#00C07F' if val > 0 else '#FF4B4B' if val < 0 else 'inherit'
                    return f'color: {color}; font-weight: bold;'
                return ''
                
            render_centered_dataframe(
                pnl_df,
                column_config={
                    "Date": "Date",
                    "Close": st.column_config.NumberColumn("Close", format="₹ %.2f"),
                    "Daily Change": st.column_config.NumberColumn("Daily Change", format="₹ %.2f"),
                    "Daily Change %": st.column_config.NumberColumn("Change %", format="%.2f%%"),
                    "Daily P/L (₹)": st.column_config.NumberColumn("Daily P/L", format="₹ %.2f"),
                },
                style_fn=highlight_daily,
                style_subset=["Daily Change %", "Daily P/L (₹)"],
            )


def render_dashboard():
    market_status, total_invested, total_value, df_display, history_map = build_dashboard_rows()
    last_updated = datetime.now().strftime("%d %b %Y %I:%M:%S %p")

    total_profit = total_value - total_invested
    profit_pct = (total_profit / total_invested * 100) if total_invested > 0 else 0

    st.markdown(f"**🌍 Market Status:** {market_status['summary']} | **Bias:** {market_status['action_bias']} | 🕒 {last_updated}")
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Invested", f"₹ {total_invested:,.2f}")
    m2.metric("Current Value", f"₹ {total_value:,.2f}")
    m3.metric("Overall Profit/Loss", f"₹ {total_profit:,.2f}", delta=f"{profit_pct:.2f}%")
    
    st.markdown("<br><br>", unsafe_allow_html=True)

    dash_tabs = st.tabs(["💼 My Portfolio", "🎯 Screener Picks", "🏭 Sector Pulse", "🌦️ Seasonal Themes", "🧺 Funds Scanner", "📊 Volume Movers", "📈 Daily P/L", "🥧 Allocation"])
    
    with dash_tabs[0]:
        _, _, accumulate_df = render_action_tables(df_display)
        if notify_accumulate:
            send_accumulate_alert_if_needed(df_display, accumulate_df, market_status)
            
    with dash_tabs[1]:
        render_indicator_picks(df_display)

    with dash_tabs[2]:
        render_sector_pulse()

    with dash_tabs[3]:
        render_seasonal_theme_watch()

    with dash_tabs[4]:
        render_fund_scanner()

    with dash_tabs[5]:
        render_volume_mover_tables(df_display)

    with dash_tabs[6]:
        render_monthly_daily_pnl(df_display, history_map)

    with dash_tabs[7]:
        render_distribution_chart(df_display)


@st.fragment(run_every=refresh_seconds if live_mode else None)
def live_dashboard_fragment():
    render_dashboard()


@st.fragment
def render_backtest_lab():
    st.header("🧪 Backtest Lab")
    st.write("Run historical simulations using the new technical indicators (BB, EMA, RSI, MACD).")

    try:
        live_tickers = get_all_stocks(limit=None)
    except Exception:
        live_tickers = []

    default_ticker = "RELIANCE.NS"
    if default_ticker not in live_tickers:
        live_tickers = [default_ticker] + [symbol for symbol in live_tickers if symbol != default_ticker]

    col1, col2 = st.columns([1, 2])
    with col1:
        if live_tickers:
            selected_ticker = st.session_state.get("backtest_ticker", default_ticker)
            default_index = live_tickers.index(selected_ticker) if selected_ticker in live_tickers else 0
            ticker = st.selectbox(
                "Select or Search Ticker",
                options=live_tickers,
                index=default_index,
                key="backtest_ticker",
                help="Type inside the dropdown to search the live market list.",
            )
        else:
            ticker = st.text_input("Enter Ticker (e.g., RELIANCE.NS, INFY.NS)", value=default_ticker)
        initial_capital = st.number_input("Initial Capital (₹)", value=100000, step=10000)
        period = st.selectbox("Historical Period", ["1y", "2y", "5y"], index=1)
        run_btn = st.button("Run Backtest", type="primary", use_container_width=True)

    with col2:
        st.info("The ticker dropdown uses the live market list. Type a few letters in the box to search quickly.")
        
    if run_btn and ticker:
        with st.spinner(f"Running backtest for {ticker}..."):
            from services.market_data_utils import download_history
            from services.backtest_service import run_backtest
            
            data = download_history(ticker, period=period)
            results = run_backtest(ticker, data, initial_capital)
            
            if results:
                st.success(f"Backtest complete for {ticker}!")
                
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Final Value", f"₹{results['final_value']:,.2f}")
                m2.metric("Total Return", f"{results['total_return_pct']}%")
                m3.metric("Win Rate", f"{results['win_rate_pct']}%")
                m4.metric("Max Drawdown", f"{results['max_drawdown_pct']}%")
                
                st.write(f"**Total Closed Trades:** {results['total_closed_trades']}")
                
                trades_df = pd.DataFrame(results['trades'])
                if not trades_df.empty:
                    display_trades = trades_df.copy()
                    if "date" in display_trades.columns:
                        display_trades["date"] = pd.to_datetime(display_trades["date"]).dt.strftime("%d-%b-%Y")

                    render_centered_dataframe(
                        display_trades,
                        column_config={
                            "date": "Date",
                            "action": "Action",
                            "price": st.column_config.NumberColumn("Price", format="₹ %.2f"),
                            "shares": st.column_config.NumberColumn("Shares", format="%d"),
                            "value": st.column_config.NumberColumn("Trade Value", format="₹ %.2f"),
                            "profit": st.column_config.NumberColumn("Profit", format="₹ %.2f"),
                        },
                    )
                    
                st.subheader("Price Action")
                st.line_chart(results['historical_data']['Close'], use_container_width=True)
            else:
                st.error("Could not run backtest. Please check the ticker symbol or try again.")


tabs = st.tabs(["📊 Live Dashboard", "🧪 Backtest Lab"])

with tabs[0]:
    live_dashboard_fragment()

with tabs[1]:
    render_backtest_lab()
