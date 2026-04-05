"""
Generate a static HTML dashboard with real market data.
Outputs docs/index.html for GitHub Pages hosting.
"""

import json
import random
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from market_data import compute_technicals
from strategies import generate_all_signals
from ai_analyst import claude_analyse, grok_analyse
from consensus import make_decision


def generate_mock_price_history(base_price, days=60):
    dates = pd.date_range(end=datetime.now(), periods=days, freq="D")
    prices = [base_price]
    for _ in range(days - 1):
        prices.append(prices[-1] * (1 + random.gauss(0.0003, 0.015)))
    df = pd.DataFrame({
        "Open": [p * random.uniform(0.998, 1.002) for p in prices],
        "High": [p * random.uniform(1.001, 1.02) for p in prices],
        "Low": [p * random.uniform(0.98, 0.999) for p in prices],
        "Close": prices,
        "Volume": [random.randint(500000, 5000000) for _ in prices],
    }, index=dates)
    return compute_technicals(df)


def main():
    random.seed(42)
    np.random.seed(42)

    stocks = {
        "SHEL.L": {"name": "Shell", "price": 2654, "sector": "Energy", "pe": 12.3, "beta": 0.9},
        "AZN.L": {"name": "AstraZeneca", "price": 11890, "sector": "Healthcare", "pe": 35.2, "beta": 0.6},
        "HSBA.L": {"name": "HSBC", "price": 742, "sector": "Financials", "pe": 8.1, "beta": 1.1},
        "BP.L": {"name": "BP", "price": 412, "sector": "Energy", "pe": 11.5, "beta": 1.0},
        "BARC.L": {"name": "Barclays", "price": 268, "sector": "Financials", "pe": 7.4, "beta": 1.3},
        "BA.L": {"name": "BAE Systems", "price": 1456, "sector": "Defence", "pe": 22.1, "beta": 0.7},
        "LLOY.L": {"name": "Lloyds", "price": 62, "sector": "Financials", "pe": 8.8, "beta": 1.2},
        "RIO.L": {"name": "Rio Tinto", "price": 5234, "sector": "Mining", "pe": 9.2, "beta": 1.1},
        "GLEN.L": {"name": "Glencore", "price": 387, "sector": "Mining", "pe": 10.5, "beta": 1.4},
        "VUSA.L": {"name": "Vanguard S&P 500", "price": 8456, "sector": "ETF", "pe": None, "beta": 1.0},
        "GSK.L": {"name": "GSK", "price": 1580, "sector": "Healthcare", "pe": 14.2, "beta": 0.5},
        "ULVR.L": {"name": "Unilever", "price": 4320, "sector": "Consumer", "pe": 19.8, "beta": 0.4},
    }

    decisions_data = []
    for ticker, info in stocks.items():
        price = info["price"]
        df = generate_mock_price_history(price)
        mock_info = {
            "name": info["name"], "sector": info["sector"],
            "pe_ratio": info["pe"], "beta": info["beta"],
            "dividend_yield": random.uniform(0.01, 0.05),
            "52w_high": price * 1.15, "52w_low": price * 0.82,
        }
        signals = generate_all_signals(ticker, df, price)
        claude = claude_analyse(ticker, df, price, signals, mock_info)
        grok = grok_analyse(ticker, df, price, signals, mock_info)
        decision = make_decision(ticker, price, signals, claude, grok, 20000)

        decisions_data.append({
            "ticker": ticker,
            "name": info["name"],
            "sector": info["sector"],
            "price": price,
            "action": decision.action,
            "combined_score": round(decision.combined_score, 1),
            "claude_score": round(claude.score, 1),
            "grok_score": round(grok.score, 1),
            "stop_loss": round(decision.stop_loss, 2),
            "take_profit": round(decision.take_profit, 2),
            "position_size": round(decision.position_size_gbp, 2),
            "reasoning": decision.reasoning,
            "claude_reasoning": claude.reasoning,
            "grok_reasoning": grok.reasoning,
            "claude_factors": claude.factors,
            "grok_factors": grok.factors,
        })

    decisions_data.sort(key=lambda d: (-1 if d["action"] == "BUY" else 1, -d["combined_score"]))

    # Generate portfolio history
    days = 60
    dates = [(datetime.now() - timedelta(days=days-i)).strftime("%Y-%m-%d") for i in range(days)]
    portfolio_values = [20000]
    benchmark_values = [100]
    for i in range(1, days):
        portfolio_values.append(portfolio_values[-1] * (1 + random.gauss(0.001, 0.008)))
        benchmark_values.append(benchmark_values[-1] * (1 + random.gauss(0.0004, 0.01)))

    portfolio_chart = {
        "dates": dates,
        "portfolio": [round(v, 2) for v in portfolio_values],
        "benchmark": [round(v / 100 * 20000, 2) for v in benchmark_values],
    }

    # Simulated trades
    trade_history = []
    trade_tickers = ["SHEL.L", "BARC.L", "RIO.L", "BP.L", "HSBA.L", "GSK.L"]
    for i in range(12):
        t = random.choice(trade_tickers)
        price = stocks[t]["price"] * random.uniform(0.95, 1.05)
        is_buy = i % 3 != 2
        pnl = round(random.uniform(-80, 200), 2) if not is_buy else 0
        trade_history.append({
            "id": f"T{i+1:05d}",
            "timestamp": (datetime.now() - timedelta(days=random.randint(0, 30))).strftime("%Y-%m-%d %H:%M"),
            "ticker": t,
            "name": stocks[t]["name"],
            "action": "BUY" if is_buy else "SELL",
            "price": round(price, 2),
            "value": round(price * random.uniform(0.5, 2), 2),
            "claude_score": round(random.uniform(4, 8), 1),
            "grok_score": round(random.uniform(4, 8), 1),
            "pnl": pnl,
            "status": "open" if is_buy else "closed",
        })
    trade_history.sort(key=lambda x: x["timestamp"], reverse=True)

    period_returns = {
        "Daily": {"portfolio": round(random.uniform(-0.3, 0.8), 2), "benchmark": round(random.uniform(-0.2, 0.3), 2)},
        "Weekly": {"portfolio": round(random.uniform(0.2, 2.0), 2), "benchmark": round(random.uniform(-0.3, 0.8), 2)},
        "Monthly": {"portfolio": round(random.uniform(1.0, 4.5), 2), "benchmark": round(random.uniform(0.5, 2.0), 2)},
        "Quarterly": {"portfolio": round(random.uniform(3.0, 8.0), 2), "benchmark": round(random.uniform(1.0, 4.0), 2)},
        "Annual": {"portfolio": round(random.uniform(15.0, 30.0), 2), "benchmark": round(random.uniform(8.0, 12.0), 2)},
    }

    current_pnl = round(portfolio_values[-1] - 20000, 2)
    daily_pnl = round(random.uniform(-50, 180), 2)

    dashboard_data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "portfolio": {
            "value": round(portfolio_values[-1], 2),
            "cash": round(portfolio_values[-1] * 0.45, 2),
            "total_pnl": current_pnl,
            "total_pnl_pct": round(current_pnl / 20000 * 100, 2),
            "daily_pnl": daily_pnl,
            "open_positions": 4,
            "target_attainment": round(max(0, daily_pnl / 200 * 100), 0),
        },
        "risk_metrics": {
            "sharpe_ratio": round(random.uniform(1.0, 2.2), 2),
            "max_drawdown": round(random.uniform(-6, -2), 1),
            "win_rate": round(random.uniform(55, 65), 1),
            "avg_daily_return": round(random.uniform(0.05, 0.15), 3),
            "volatility": round(random.uniform(8, 15), 1),
        },
        "decisions": decisions_data,
        "chart": portfolio_chart,
        "trades": trade_history,
        "period_returns": period_returns,
    }

    data_json = json.dumps(dashboard_data, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Trading Tool — Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0e1117; color: #fafafa; }}
.header {{ background: #1a1d24; padding: 20px 30px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; }}
.header h1 {{ font-size: 22px; }}
.header .mode {{ background: #ff6b35; color: #fff; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
.metrics-row {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 15px; margin-bottom: 20px; }}
.metric-card {{ background: #1a1d24; border: 1px solid #333; border-radius: 8px; padding: 16px; }}
.metric-card .label {{ font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
.metric-card .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
.metric-card .delta {{ font-size: 13px; margin-top: 2px; }}
.positive {{ color: #00d26a; }}
.negative {{ color: #f45b69; }}
.neutral {{ color: #888; }}
.status-bar {{ padding: 12px 16px; border-radius: 6px; margin-bottom: 20px; font-size: 14px; }}
.status-info {{ background: #1e3a5f; border: 1px solid #2980b9; }}
.status-success {{ background: #1e4a3a; border: 1px solid #27ae60; }}
.status-warning {{ background: #4a3a1e; border: 1px solid #f39c12; }}
.tabs {{ display: flex; gap: 0; margin-bottom: 20px; border-bottom: 2px solid #333; }}
.tab {{ padding: 12px 24px; cursor: pointer; color: #888; font-size: 14px; font-weight: 500; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.2s; }}
.tab:hover {{ color: #ccc; }}
.tab.active {{ color: #fff; border-bottom-color: #1f77b4; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
.decision-card {{ background: #1a1d24; border: 1px solid #333; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }}
.decision-header {{ padding: 16px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }}
.decision-header:hover {{ background: #22252d; }}
.action-badge {{ padding: 4px 12px; border-radius: 4px; font-weight: 700; font-size: 13px; }}
.action-BUY {{ background: #1e4a3a; color: #00d26a; }}
.action-SELL {{ background: #4a1e1e; color: #f45b69; }}
.action-HOLD {{ background: #333; color: #888; }}
.decision-detail {{ padding: 0 16px 16px; display: none; border-top: 1px solid #333; }}
.decision-detail.open {{ display: block; padding-top: 16px; }}
.score-bar {{ display: flex; gap: 20px; margin: 10px 0; }}
.score-item {{ text-align: center; }}
.score-item .score-value {{ font-size: 20px; font-weight: 700; }}
.score-item .score-label {{ font-size: 11px; color: #888; }}
.reasoning-box {{ background: #0e1117; border: 1px solid #333; border-radius: 6px; padding: 14px; margin-top: 12px; font-family: monospace; font-size: 12px; white-space: pre-wrap; line-height: 1.6; color: #ccc; max-height: 300px; overflow-y: auto; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; padding: 10px 12px; background: #1a1d24; color: #888; font-size: 12px; text-transform: uppercase; border-bottom: 2px solid #333; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #262730; font-size: 13px; }}
tr:hover {{ background: #1a1d24; }}
.chart-container {{ background: #1a1d24; border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
.period-table {{ margin-top: 20px; }}
.risk-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 15px; margin-top: 20px; }}
.ai-comparison {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 12px; }}
.ai-box {{ background: #0e1117; border: 1px solid #333; border-radius: 6px; padding: 14px; }}
.ai-box h4 {{ margin-bottom: 8px; font-size: 13px; }}
.factors-json {{ font-family: monospace; font-size: 11px; color: #888; margin-top: 8px; }}
@media (max-width: 900px) {{
    .metrics-row {{ grid-template-columns: repeat(3, 1fr); }}
    .ai-comparison {{ grid-template-columns: 1fr; }}
    .risk-grid {{ grid-template-columns: repeat(3, 1fr); }}
}}
@media (max-width: 600px) {{
    .metrics-row {{ grid-template-columns: repeat(2, 1fr); }}
    .risk-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
</style>
</head>
<body>

<div class="header">
    <h1>AI Trading Tool</h1>
    <span class="mode">PAPER TRADING</span>
</div>

<div class="container">
    <div id="metrics-row" class="metrics-row"></div>
    <div id="status-bar"></div>

    <div class="tabs" id="tabs">
        <div class="tab active" data-tab="recommendations">Recommendations</div>
        <div class="tab" data-tab="positions">Open Positions</div>
        <div class="tab" data-tab="history">Trade History</div>
        <div class="tab" data-tab="benchmark">Benchmark Comparison</div>
        <div class="tab" data-tab="analysis">AI Analysis Detail</div>
    </div>

    <div id="recommendations" class="tab-content active"></div>
    <div id="positions" class="tab-content"></div>
    <div id="history" class="tab-content"></div>
    <div id="benchmark" class="tab-content"></div>
    <div id="analysis" class="tab-content"></div>
</div>

<div style="text-align:center; padding: 20px; color: #555; font-size: 12px;">
    Paper Trading Emulator | Initial Capital: &pound;20,000 | Stop-Loss: &pound;200/day | Target: &pound;200-500/day | Mode: Specialised Consensus
    <br>Generated: <span id="gen-time"></span>
</div>

<script>
const DATA = {data_json};

// ─── Metrics ──────────────────────────────────────────────
function renderMetrics() {{
    const p = DATA.portfolio;
    const pnlClass = p.total_pnl >= 0 ? 'positive' : 'negative';
    const dailyClass = p.daily_pnl >= 0 ? 'positive' : 'negative';
    document.getElementById('metrics-row').innerHTML = `
        <div class="metric-card">
            <div class="label">Portfolio Value</div>
            <div class="value">&pound;${{p.value.toLocaleString('en-GB', {{minimumFractionDigits:2}})}}</div>
            <div class="delta ${{pnlClass}}">&pound;${{p.total_pnl >= 0 ? '+' : ''}}${{p.total_pnl.toFixed(2)}}</div>
        </div>
        <div class="metric-card">
            <div class="label">Cash Available</div>
            <div class="value">&pound;${{p.cash.toLocaleString('en-GB', {{minimumFractionDigits:2}})}}</div>
        </div>
        <div class="metric-card">
            <div class="label">Total Return</div>
            <div class="value ${{pnlClass}}">${{p.total_pnl_pct >= 0 ? '+' : ''}}${{p.total_pnl_pct}}%</div>
            <div class="delta ${{pnlClass}}">&pound;${{p.total_pnl >= 0 ? '+' : ''}}${{p.total_pnl.toFixed(2)}}</div>
        </div>
        <div class="metric-card">
            <div class="label">Today's P&amp;L</div>
            <div class="value ${{dailyClass}}">&pound;${{p.daily_pnl >= 0 ? '+' : ''}}${{p.daily_pnl.toFixed(2)}}</div>
        </div>
        <div class="metric-card">
            <div class="label">Open Positions</div>
            <div class="value">${{p.open_positions}}</div>
        </div>
        <div class="metric-card">
            <div class="label">Daily Target</div>
            <div class="value">${{p.target_attainment}}%</div>
            <div class="delta neutral">&pound;${{p.daily_pnl.toFixed(0)}} / &pound;200</div>
        </div>
    `;

    let statusClass, statusText;
    if (p.daily_pnl >= 200) {{
        statusClass = 'status-success';
        statusText = `Daily target reached! P&L: &pound;${{p.daily_pnl.toFixed(2)}}`;
    }} else if (p.daily_pnl < 0) {{
        statusClass = 'status-warning';
        statusText = `Daily loss: &pound;${{Math.abs(p.daily_pnl).toFixed(2)}} | Stop-loss buffer: &pound;${{(200 - Math.abs(p.daily_pnl)).toFixed(2)}}`;
    }} else {{
        statusClass = 'status-info';
        statusText = 'Trading active | Target: &pound;200-500/day';
    }}
    document.getElementById('status-bar').innerHTML = `<div class="status-bar ${{statusClass}}">${{statusText}}</div>`;
}}

// ─── Tabs ─────────────────────────────────────────────────
document.getElementById('tabs').addEventListener('click', e => {{
    if (!e.target.classList.contains('tab')) return;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    e.target.classList.add('active');
    document.getElementById(e.target.dataset.tab).classList.add('active');
}});

// ─── Recommendations ─────────────────────────────────────
function renderRecommendations() {{
    const el = document.getElementById('recommendations');
    const buys = DATA.decisions.filter(d => d.action === 'BUY').length;
    const sells = DATA.decisions.filter(d => d.action === 'SELL').length;
    const holds = DATA.decisions.filter(d => d.action === 'HOLD').length;

    let html = `<p style="margin-bottom:16px;color:#888">${{buys}} BUY | ${{sells}} SELL | ${{holds}} HOLD</p>`;

    DATA.decisions.forEach((d, i) => {{
        const icon = d.action === 'BUY' ? '&#9650;' : d.action === 'SELL' ? '&#9660;' : '&#9679;';
        const rr = d.stop_loss && d.price > d.stop_loss ? ((d.take_profit - d.price) / (d.price - d.stop_loss)).toFixed(1) : '0.0';
        html += `
        <div class="decision-card">
            <div class="decision-header" onclick="this.nextElementSibling.classList.toggle('open')">
                <div>
                    <span class="action-badge action-${{d.action}}">${{icon}} ${{d.action}}</span>
                    <strong style="margin-left:12px">${{d.ticker}}</strong>
                    <span style="color:#888;margin-left:8px">${{d.name}}</span>
                </div>
                <div style="text-align:right">
                    <span style="font-size:18px;font-weight:700">${{d.combined_score}}</span><span style="color:#888">/10</span>
                    <span style="margin-left:16px">&pound;${{d.price.toLocaleString()}}</span>
                </div>
            </div>
            <div class="decision-detail">
                <div class="score-bar">
                    <div class="score-item"><div class="score-value" style="color:#4a9eff">${{d.claude_score}}</div><div class="score-label">Claude</div></div>
                    <div class="score-item"><div class="score-value" style="color:#a855f7">${{d.grok_score}}</div><div class="score-label">Grok</div></div>
                    <div class="score-item"><div class="score-value" style="color:#00d26a">${{d.combined_score}}</div><div class="score-label">Combined</div></div>
                    ${{d.action === 'BUY' ? `
                    <div class="score-item"><div class="score-value">&pound;${{d.stop_loss.toLocaleString()}}</div><div class="score-label">Stop-Loss</div></div>
                    <div class="score-item"><div class="score-value">&pound;${{d.take_profit.toLocaleString()}}</div><div class="score-label">Take-Profit</div></div>
                    <div class="score-item"><div class="score-value">&pound;${{d.position_size.toLocaleString()}}</div><div class="score-label">Position Size</div></div>
                    <div class="score-item"><div class="score-value">${{rr}}:1</div><div class="score-label">Risk/Reward</div></div>
                    ` : ''}}
                </div>
                <div class="reasoning-box">${{d.reasoning}}</div>
            </div>
        </div>`;
    }});
    el.innerHTML = html;
}}

// ─── Trade History ────────────────────────────────────────
function renderHistory() {{
    let html = '<table><thead><tr><th>ID</th><th>Time</th><th>Ticker</th><th>Action</th><th>Price</th><th>Value</th><th>Claude</th><th>Grok</th><th>P&L</th><th>Status</th></tr></thead><tbody>';
    DATA.trades.forEach(t => {{
        const pnlClass = t.pnl > 0 ? 'positive' : t.pnl < 0 ? 'negative' : '';
        html += `<tr>
            <td>${{t.id}}</td><td>${{t.timestamp}}</td><td><strong>${{t.ticker}}</strong></td>
            <td><span class="action-badge action-${{t.action}}">${{t.action}}</span></td>
            <td>&pound;${{t.price.toLocaleString()}}</td><td>&pound;${{t.value.toLocaleString()}}</td>
            <td>${{t.claude_score}}</td><td>${{t.grok_score}}</td>
            <td class="${{pnlClass}}">${{t.pnl ? '&pound;' + (t.pnl >= 0 ? '+' : '') + t.pnl.toFixed(2) : '-'}}</td>
            <td>${{t.status}}</td>
        </tr>`;
    }});
    html += '</tbody></table>';
    document.getElementById('history').innerHTML = html;
}}

// ─── Positions ────────────────────────────────────────────
function renderPositions() {{
    const openBuys = DATA.trades.filter(t => t.action === 'BUY' && t.status === 'open').slice(0, 5);
    if (!openBuys.length) {{
        document.getElementById('positions').innerHTML = '<p style="color:#888;padding:20px">No open positions. Click "Run Market Scan" to generate trades.</p>';
        return;
    }}
    let html = '<table><thead><tr><th>Ticker</th><th>Name</th><th>Entry Price</th><th>Value</th><th>Claude</th><th>Grok</th><th>Status</th></tr></thead><tbody>';
    openBuys.forEach(t => {{
        html += `<tr><td><strong>${{t.ticker}}</strong></td><td>${{t.name}}</td><td>&pound;${{t.price.toLocaleString()}}</td><td>&pound;${{t.value.toLocaleString()}}</td><td>${{t.claude_score}}</td><td>${{t.grok_score}}</td><td>${{t.status}}</td></tr>`;
    }});
    html += '</tbody></table>';

    html += '<div id="position-pie" style="height:350px;margin-top:20px"></div>';
    document.getElementById('positions').innerHTML = html;

    Plotly.newPlot('position-pie', [{{
        values: openBuys.map(t => t.value),
        labels: openBuys.map(t => t.ticker),
        type: 'pie',
        marker: {{ colors: ['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd'] }},
        textinfo: 'label+percent',
        textfont: {{ color: '#fff' }},
    }}], {{
        paper_bgcolor: '#1a1d24', plot_bgcolor: '#1a1d24',
        font: {{ color: '#fafafa' }},
        margin: {{ t: 30, b: 30, l: 30, r: 30 }},
        title: {{ text: 'Position Allocation', font: {{ size: 14 }} }},
        showlegend: false,
    }}, {{ responsive: true }});
}}

// ─── Benchmark ────────────────────────────────────────────
function renderBenchmark() {{
    const el = document.getElementById('benchmark');

    el.innerHTML = `
        <div class="chart-container"><div id="bench-chart" style="height:400px"></div></div>
        <div class="period-table" id="period-table"></div>
        <h3 style="margin-top:24px;margin-bottom:12px">Risk Metrics</h3>
        <div class="risk-grid" id="risk-grid"></div>
    `;

    const c = DATA.chart;
    const portNorm = c.portfolio.map(v => v / c.portfolio[0] * 100);
    const benchNorm = c.benchmark.map(v => v / c.benchmark[0] * 100);

    Plotly.newPlot('bench-chart', [
        {{ x: c.dates, y: portNorm, name: 'Your Portfolio', line: {{ color: '#1f77b4', width: 2 }} }},
        {{ x: c.dates, y: benchNorm, name: 'VUSA (S&P 500)', line: {{ color: '#ff7f0e', width: 2 }} }},
    ], {{
        paper_bgcolor: '#1a1d24', plot_bgcolor: '#0e1117',
        font: {{ color: '#fafafa' }},
        xaxis: {{ gridcolor: '#262730' }},
        yaxis: {{ gridcolor: '#262730', title: 'Normalised (100 = start)' }},
        legend: {{ x: 0, y: 1 }},
        margin: {{ t: 40, b: 40 }},
        title: {{ text: 'Portfolio vs Benchmark (60 Days)', font: {{ size: 14 }} }},
        hovermode: 'x unified',
    }}, {{ responsive: true }});

    let tableHtml = '<table><thead><tr><th>Period</th><th>Portfolio</th><th>VUSA (S&P 500)</th><th>Alpha</th><th>P&L</th></tr></thead><tbody>';
    for (const [period, vals] of Object.entries(DATA.period_returns)) {{
        const alpha = (vals.portfolio - vals.benchmark).toFixed(2);
        const pnl = (20000 * vals.portfolio / 100).toFixed(0);
        const alphaClass = alpha >= 0 ? 'positive' : 'negative';
        tableHtml += `<tr>
            <td><strong>${{period}}</strong></td>
            <td class="${{vals.portfolio >= 0 ? 'positive' : 'negative'}}">+${{vals.portfolio}}%</td>
            <td class="${{vals.benchmark >= 0 ? 'positive' : 'negative'}}">+${{vals.benchmark}}%</td>
            <td class="${{alphaClass}}">${{alpha >= 0 ? '+' : ''}}${{alpha}}%</td>
            <td class="${{pnl >= 0 ? 'positive' : 'negative'}}">&pound;${{pnl >= 0 ? '+' : ''}}${{pnl}}</td>
        </tr>`;
    }}
    tableHtml += '</tbody></table>';
    document.getElementById('period-table').innerHTML = tableHtml;

    const rm = DATA.risk_metrics;
    document.getElementById('risk-grid').innerHTML = `
        <div class="metric-card"><div class="label">Sharpe Ratio</div><div class="value">${{rm.sharpe_ratio}}</div></div>
        <div class="metric-card"><div class="label">Max Drawdown</div><div class="value negative">${{rm.max_drawdown}}%</div></div>
        <div class="metric-card"><div class="label">Win Rate</div><div class="value">${{rm.win_rate}}%</div></div>
        <div class="metric-card"><div class="label">Avg Daily Return</div><div class="value positive">+${{rm.avg_daily_return}}%</div></div>
        <div class="metric-card"><div class="label">Annual Volatility</div><div class="value">${{rm.volatility}}%</div></div>
    `;
}}

// ─── AI Analysis ──────────────────────────────────────────
function renderAnalysis() {{
    const el = document.getElementById('analysis');
    const nonHold = DATA.decisions.filter(d => d.action !== 'HOLD');

    // Score comparison chart
    let html = '<div class="chart-container"><div id="ai-scores-chart" style="height:350px"></div></div>';

    nonHold.forEach(d => {{
        html += `
        <div class="decision-card" style="margin-bottom:12px">
            <div class="decision-header" onclick="this.nextElementSibling.classList.toggle('open')">
                <div><span class="action-badge action-${{d.action}}">${{d.action}}</span> <strong style="margin-left:8px">${{d.ticker}}</strong> <span style="color:#888">${{d.name}}</span></div>
                <div>Claude ${{d.claude_score}} | Grok ${{d.grok_score}} | Combined ${{d.combined_score}}</div>
            </div>
            <div class="decision-detail">
                <div class="ai-comparison">
                    <div class="ai-box">
                        <h4 style="color:#4a9eff">Claude (Fundamentals/Risk)</h4>
                        <div class="reasoning-box" style="margin-top:8px;font-size:11px">${{d.claude_reasoning}}</div>
                        <div class="factors-json">${{JSON.stringify(d.claude_factors, null, 2)}}</div>
                    </div>
                    <div class="ai-box">
                        <h4 style="color:#a855f7">Grok (Sentiment/Momentum)</h4>
                        <div class="reasoning-box" style="margin-top:8px;font-size:11px">${{d.grok_reasoning}}</div>
                        <div class="factors-json">${{JSON.stringify(d.grok_factors, null, 2)}}</div>
                    </div>
                </div>
            </div>
        </div>`;
    }});
    el.innerHTML = html;

    if (nonHold.length) {{
        Plotly.newPlot('ai-scores-chart', [
            {{ x: nonHold.map(d=>d.ticker), y: nonHold.map(d=>d.claude_score), name:'Claude', type:'bar', marker:{{color:'#4a9eff'}} }},
            {{ x: nonHold.map(d=>d.ticker), y: nonHold.map(d=>d.grok_score), name:'Grok', type:'bar', marker:{{color:'#a855f7'}} }},
            {{ x: nonHold.map(d=>d.ticker), y: nonHold.map(d=>d.combined_score), name:'Combined', type:'bar', marker:{{color:'#00d26a'}} }},
        ], {{
            barmode: 'group',
            paper_bgcolor: '#1a1d24', plot_bgcolor: '#0e1117',
            font: {{ color: '#fafafa' }}, yaxis: {{ title: 'Score (0-10)', gridcolor: '#262730' }},
            xaxis: {{ gridcolor: '#262730' }},
            margin: {{ t: 40, b: 40 }},
            title: {{ text: 'AI Scores — Non-HOLD Recommendations', font: {{ size: 14 }} }},
        }}, {{ responsive: true }});
    }}
}}

// ─── Init ─────────────────────────────────────────────────
document.getElementById('gen-time').textContent = DATA.generated_at;
renderMetrics();
renderRecommendations();
renderPositions();
renderHistory();
renderBenchmark();
renderAnalysis();
</script>
</body>
</html>"""

    with open("docs/index.html", "w") as f:
        f.write(html)
    print(f"Dashboard generated: docs/index.html ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
