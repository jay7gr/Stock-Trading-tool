"""
Streamlit Dashboard — visual interface for the trading tool.

Shows: portfolio overview, daily recommendations, trade history,
P&L tracking, benchmark comparison, and AI reasoning.

Run with: streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, timedelta

import config
from trading_engine import TradingEngine
from portfolio import (
    get_benchmark_returns, calculate_portfolio_history,
    performance_vs_benchmark, compute_period_returns, compute_risk_metrics,
)


# ─── Page Config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Trading Tool",
    page_icon="$",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Session State ────────────────────────────────────────────────────
if "engine" not in st.session_state:
    st.session_state.engine = TradingEngine()

engine: TradingEngine = st.session_state.engine


# ─── Sidebar ──────────────────────────────────────────────────────────
with st.sidebar:
    st.title("AI Trading Tool")
    st.caption("Paper Trading Mode" if config.EMULATOR_MODE else "LIVE Trading")

    st.divider()

    if st.button("Run Market Scan", type="primary", use_container_width=True):
        with st.spinner("Scanning markets..."):
            engine.run_scan()
        st.success("Scan complete!")

    st.divider()

    # Settings
    st.subheader("Settings")
    config.CONSENSUS_MODE = st.selectbox(
        "Consensus Mode",
        ["specialised", "weighted", "agreement"],
        index=["specialised", "weighted", "agreement"].index(config.CONSENSUS_MODE),
    )
    config.MIN_CONSENSUS_SCORE = st.slider(
        "Min Consensus Score", 5.0, 9.0, config.MIN_CONSENSUS_SCORE, 0.5
    )
    config.DAILY_STOP_LOSS = st.number_input(
        "Daily Stop-Loss (GBP)", value=config.DAILY_STOP_LOSS, step=50.0
    )

    st.divider()
    st.caption(f"Last scan: {engine.last_scan_time or 'Never'}")
    st.caption(f"Universe: {len(config.STOCK_UNIVERSE)} stocks")


# ─── Main Content ─────────────────────────────────────────────────────
summary = engine.emulator.get_summary()
risk = engine.risk.get_status()

# ─── Row 1: Key Metrics ──────────────────────────────────────────────
st.header("Portfolio Overview")
cols = st.columns(6)

with cols[0]:
    st.metric("Portfolio Value", f"£{summary['portfolio_value']:,.2f}",
              delta=f"£{summary['total_pnl']:,.2f}")
with cols[1]:
    st.metric("Cash Available", f"£{summary['cash']:,.2f}")
with cols[2]:
    st.metric("Total Return", f"{summary['total_pnl_pct']:.2f}%",
              delta=f"£{summary['total_pnl']:,.2f}")
with cols[3]:
    st.metric("Today's P&L", f"£{risk['daily_pnl']:,.2f}",
              delta=None)
with cols[4]:
    st.metric("Open Positions", summary['open_positions'])
with cols[5]:
    target_pct = risk['attainment_pct']
    st.metric("Daily Target", f"{target_pct:.0f}%",
              delta=f"£{risk['daily_pnl']:,.2f} / £{config.DAILY_PROFIT_TARGET_MIN}")

# Risk status bar
if risk['is_halted']:
    st.error(f"TRADING HALTED: {risk['halt_reason']}")
elif risk['daily_pnl'] < 0:
    remaining = abs(config.DAILY_STOP_LOSS) - abs(risk['daily_pnl'])
    st.warning(f"Daily loss: £{abs(risk['daily_pnl']):.2f} | Stop-loss buffer: £{remaining:.2f}")
elif risk['daily_pnl'] >= config.DAILY_PROFIT_TARGET_MIN:
    st.success(f"Daily target reached! P&L: £{risk['daily_pnl']:.2f}")
else:
    st.info(f"Trading active | Target: £{config.DAILY_PROFIT_TARGET_MIN}-{config.DAILY_PROFIT_TARGET_MAX}/day")

st.divider()

# ─── Row 2: Tabs ──────────────────────────────────────────────────────
tab_recs, tab_positions, tab_history, tab_benchmark, tab_analysis = st.tabs([
    "Recommendations", "Open Positions", "Trade History",
    "Benchmark Comparison", "AI Analysis Detail",
])

# ─── Tab: Recommendations ────────────────────────────────────────────
with tab_recs:
    st.subheader("Latest Recommendations")

    if not engine.last_scan_results:
        st.info("No scan results yet. Click 'Run Market Scan' in the sidebar.")
    else:
        # Filter controls
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            action_filter = st.multiselect(
                "Filter by action", ["BUY", "SELL", "HOLD"],
                default=["BUY", "SELL"],
            )
        with filter_col2:
            min_score = st.slider("Min combined score", 0.0, 10.0, 0.0, 0.5)

        decisions = [
            d for d in engine.last_scan_results
            if d.action in action_filter and d.combined_score >= min_score
        ]

        for d in decisions:
            action_color = {"BUY": "green", "SELL": "red", "HOLD": "gray"}[d.action]
            with st.expander(
                f"{'🟢' if d.action == 'BUY' else '🔴' if d.action == 'SELL' else '⚪'} "
                f"**{d.ticker}** — {d.action} | Score: {d.combined_score:.1f}/10 | "
                f"Price: £{d.entry_price:.2f}",
                expanded=(d.action == "BUY"),
            ):
                mc1, mc2, mc3, mc4 = st.columns(4)
                with mc1:
                    st.metric("Claude Score", f"{d.claude_analysis.score:.1f}/10")
                with mc2:
                    st.metric("Grok Score", f"{d.grok_analysis.score:.1f}/10")
                with mc3:
                    st.metric("Stop-Loss", f"£{d.stop_loss:.2f}" if d.stop_loss else "N/A")
                with mc4:
                    st.metric("Take-Profit", f"£{d.take_profit:.2f}" if d.take_profit else "N/A")

                if d.action == "BUY":
                    risk_reward = (
                        (d.take_profit - d.entry_price) / (d.entry_price - d.stop_loss)
                        if d.stop_loss and d.entry_price > d.stop_loss else 0
                    )
                    st.write(
                        f"**Position Size:** £{d.position_size_gbp:.2f} | "
                        f"**Risk/Reward:** {risk_reward:.1f}:1"
                    )

                st.markdown("---")
                st.markdown("**Full Reasoning:**")
                st.text(d.reasoning)

# ─── Tab: Open Positions ─────────────────────────────────────────────
with tab_positions:
    st.subheader("Open Positions")

    if not engine.emulator.positions:
        st.info("No open positions.")
    else:
        pos_data = []
        for ticker, pos in engine.emulator.positions.items():
            pnl_color = "green" if pos.unrealised_pnl >= 0 else "red"
            pos_data.append({
                "Ticker": ticker,
                "Qty": f"{pos.quantity:.2f}",
                "Entry": f"£{pos.avg_entry_price:.2f}",
                "Current": f"£{pos.current_price:.2f}",
                "Value": f"£{pos.value_gbp:.2f}",
                "P&L": f"£{pos.unrealised_pnl:+.2f}",
                "P&L %": f"{pos.unrealised_pnl_pct:+.1f}%",
                "Stop": f"£{pos.stop_loss:.2f}",
                "Target": f"£{pos.take_profit:.2f}",
            })
        st.dataframe(pd.DataFrame(pos_data), use_container_width=True, hide_index=True)

        # Position pie chart
        fig = px.pie(
            values=[p.value_gbp for p in engine.emulator.positions.values()],
            names=list(engine.emulator.positions.keys()),
            title="Position Allocation",
        )
        st.plotly_chart(fig, use_container_width=True)

# ─── Tab: Trade History ──────────────────────────────────────────────
with tab_history:
    st.subheader("Trade History")

    trades = engine.emulator.trade_history
    if not trades:
        st.info("No trades executed yet.")
    else:
        trade_data = []
        for t in reversed(trades):
            trade_data.append({
                "ID": t.id,
                "Time": t.timestamp[:19],
                "Ticker": t.ticker,
                "Action": t.action,
                "Price": f"£{t.price:.2f}",
                "Value": f"£{t.value_gbp:.2f}",
                "Claude": f"{t.claude_score:.1f}",
                "Grok": f"{t.grok_score:.1f}",
                "Combined": f"{t.combined_score:.1f}",
                "P&L": f"£{t.pnl:+.2f}" if t.pnl else "-",
                "Status": t.status,
            })
        st.dataframe(pd.DataFrame(trade_data), use_container_width=True, hide_index=True)

        # Trade detail expander
        selected_trade = st.selectbox(
            "View trade reasoning",
            [f"{t.id} — {t.ticker} {t.action}" for t in reversed(trades)],
        )
        if selected_trade:
            trade_id = selected_trade.split(" — ")[0]
            trade = next((t for t in trades if t.id == trade_id), None)
            if trade:
                st.text(trade.reasoning)

# ─── Tab: Benchmark Comparison ───────────────────────────────────────
with tab_benchmark:
    st.subheader(f"Performance vs {config.BENCHMARK_TICKER} (S&P 500)")

    # Time period selector
    period_view = st.radio(
        "View", ["Daily", "Monthly", "Quarterly", "Annual", "All Time"],
        horizontal=True,
    )

    period_map = {
        "Daily": "5d", "Monthly": "1mo", "Quarterly": "3mo",
        "Annual": "1y", "All Time": "1y",
    }

    benchmark_df = get_benchmark_returns(period_map[period_view])
    portfolio_df = calculate_portfolio_history(
        engine.emulator.trade_history, config.INITIAL_CAPITAL
    )

    if not portfolio_df.empty and not benchmark_df.empty:
        merged = performance_vs_benchmark(portfolio_df, benchmark_df)

        if not merged.empty:
            # Normalise both to 100 for comparison
            fig = go.Figure()

            if "portfolio_value" in merged.columns:
                port_normalised = merged["portfolio_value"] / merged["portfolio_value"].iloc[0] * 100
                fig.add_trace(go.Scatter(
                    x=merged.index, y=port_normalised,
                    name="Your Portfolio", line=dict(color="blue", width=2),
                ))

            if "benchmark_price" in merged.columns:
                bench_normalised = merged["benchmark_price"] / merged["benchmark_price"].iloc[0] * 100
                fig.add_trace(go.Scatter(
                    x=merged.index, y=bench_normalised,
                    name=f"VUSA (S&P 500)", line=dict(color="orange", width=2),
                ))

            fig.update_layout(
                title=f"Portfolio vs Benchmark ({period_view})",
                yaxis_title="Normalised Value (100 = start)",
                xaxis_title="Date",
                hovermode="x unified",
            )
            st.plotly_chart(fig, use_container_width=True)

        # Period returns table
        period_returns = compute_period_returns(portfolio_df, benchmark_df)
        returns_data = []
        for period_name, vals in period_returns.items():
            returns_data.append({
                "Period": period_name.replace("_", " ").title(),
                "Portfolio": f"{vals['portfolio_return']:+.2f}%",
                "VUSA (S&P 500)": f"{vals['benchmark_return']:+.2f}%",
                "Alpha": f"{vals['alpha']:+.2f}%",
                "P&L": f"£{vals['portfolio_pnl']:+.2f}",
            })
        st.dataframe(pd.DataFrame(returns_data), use_container_width=True, hide_index=True)

        # Risk metrics
        st.markdown("### Risk Metrics")
        metrics = compute_risk_metrics(portfolio_df)
        mcols = st.columns(5)
        with mcols[0]:
            st.metric("Sharpe Ratio", metrics["sharpe_ratio"])
        with mcols[1]:
            st.metric("Max Drawdown", f"{metrics['max_drawdown']:.1f}%")
        with mcols[2]:
            st.metric("Win Rate", f"{metrics['win_rate']:.0f}%")
        with mcols[3]:
            st.metric("Avg Daily Return", f"{metrics['avg_daily_return']:.3f}%")
        with mcols[4]:
            st.metric("Annual Volatility", f"{metrics.get('volatility_annual', 0):.1f}%")
    else:
        st.info("Run a market scan to generate portfolio data for comparison.")

# ─── Tab: AI Analysis Detail ─────────────────────────────────────────
with tab_analysis:
    st.subheader("AI Analysis Breakdown")

    if not engine.last_scan_results:
        st.info("No scan results yet.")
    else:
        # Score comparison chart
        buy_decisions = [d for d in engine.last_scan_results if d.action == "BUY"]
        if buy_decisions:
            chart_data = pd.DataFrame([
                {
                    "Ticker": d.ticker,
                    "Claude Score": d.claude_analysis.score,
                    "Grok Score": d.grok_analysis.score,
                    "Combined": d.combined_score,
                }
                for d in buy_decisions
            ])
            fig = go.Figure()
            fig.add_trace(go.Bar(name="Claude", x=chart_data["Ticker"],
                                 y=chart_data["Claude Score"], marker_color="blue"))
            fig.add_trace(go.Bar(name="Grok", x=chart_data["Ticker"],
                                 y=chart_data["Grok Score"], marker_color="purple"))
            fig.add_trace(go.Bar(name="Combined", x=chart_data["Ticker"],
                                 y=chart_data["Combined"], marker_color="green"))
            fig.update_layout(barmode="group", title="AI Scores — BUY Recommendations",
                              yaxis_title="Score (0-10)")
            st.plotly_chart(fig, use_container_width=True)

        # Detailed reasoning for each model
        for d in engine.last_scan_results:
            if d.action == "HOLD":
                continue
            with st.expander(f"{d.ticker} — {d.action}", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Claude (Fundamentals/Risk)**")
                    st.text(d.claude_analysis.reasoning)
                    if d.claude_analysis.factors:
                        st.json(d.claude_analysis.factors)
                with col2:
                    st.markdown("**Grok (Sentiment/Momentum)**")
                    st.text(d.grok_analysis.reasoning)
                    if d.grok_analysis.factors:
                        st.json(d.grok_analysis.factors)

# ─── Footer ──────────────────────────────────────────────────────────
st.divider()
st.caption(
    f"Paper Trading Emulator | Initial Capital: £{config.INITIAL_CAPITAL:,.0f} | "
    f"Stop-Loss: £{abs(config.DAILY_STOP_LOSS):.0f}/day | "
    f"Target: £{config.DAILY_PROFIT_TARGET_MIN:.0f}-{config.DAILY_PROFIT_TARGET_MAX:.0f}/day | "
    f"Mode: {config.CONSENSUS_MODE}"
)
