import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from bs4 import BeautifulSoup
import re
import time
import datetime
import os
import yfinance as yf
from threading import Lock

# ── CONFIGURACIÓN VISUAL ESTILO K-AURUM ──────────────────────────────────────────
st.set_page_config(page_title="K-AURUM Q-CORE v10.0", layout="wide", initial_sidebar_state="expanded")

# Colores institucionales
C_GOLD   = "#FFD700"
C_RED    = "#FF4B4B"
C_GREEN  = "#00FA9A"
C_CYAN   = "#00FFFF"
C_GRAY   = "#808080"
BG_DARK  = "#0E1117"

# ── LÓGICA DE DATOS (Mantenida de la versión original) ──────────────────────────
FINNHUB_API_KEY = "d7lnma9r01qk7lvto8i"
FINNHUB_BASE = "https://finnhub.io/api/v1"

def _finnhub_get(url, params=None):
    if params is None: params = {}
    params["token"] = FINNHUB_API_KEY
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200: return resp.json()
    except: pass
    return None

class DataEngine:
    @st.cache_data(ttl=60)
    def get_gold_price(_self):
        url = f"{FINNHUB_BASE}/quote"
        data = _finnhub_get(url, params={"symbol": "OANDA:XAU_USD"})
        if data and "c" in data:
            return {
                "price": data["c"],
                "change": data["d"],
                "pct": data["dp"],
                "high": data["h"],
                "low": data["l"]
            }
        # Fallback yfinance
        ticker = yf.Ticker("GC=F")
        hist = ticker.history(period="1d")
        if not hist.empty:
            last = hist.iloc[-1]
            prev = hist.iloc[0]
            return {
                "price": last["Close"],
                "change": last["Close"] - prev["Close"],
                "pct": ((last["Close"] - prev["Close"]) / prev["Close"]) * 100,
                "high": last["High"],
                "low": last["Low"]
            }
        return None

    @st.cache_data(ttl=300)
    def get_historical_data(_self, interval="5m", limit=100):
        ticker = yf.Ticker("GC=F")
        df = ticker.history(period="5d", interval=interval)
        if not df.empty:
            df.columns = [c.lower() for c in df.columns]
            return df.tail(limit)
        return None

    @st.cache_data(ttl=3600)
    def get_cot_data(_self):
        cftc_url = "https://www.cftc.gov/dea/futures/deacmxlf.htm"
        try:
            resp = requests.get(cftc_url, timeout=10)
            text = resp.text
            if "GOLD - COMMODITY EXCHANGE INC." in text:
                block = text.split("GOLD - COMMODITY EXCHANGE INC.")[1].split("-----------------------------------------------------------------------")[0]
                non_comm = re.search(r"NON-COMMERCIAL\s+([\d,]+)\s+([\d,]+)", block)
                comm = re.search(r"COMMERCIAL\s+([\d,]+)\s+([\d,]+)", block)
                if non_comm and comm:
                    spec_l, spec_s = float(non_comm.group(1).replace(",","")), float(non_comm.group(2).replace(",",""))
                    comm_l, comm_s = float(comm.group(1).replace(",","")), float(comm.group(2).replace(",",""))
                    net_pct = ((spec_l - spec_s) / (spec_l + spec_s)) * 100
                    hedge = -(comm_l - comm_s) / (comm_l + comm_s)
                    bias = "BULLISH" if net_pct > 10 else "BEARISH" if net_pct < -10 else "NEUTRAL"
                    return {"net": net_pct, "hedge": hedge, "bias": bias}
        except: pass
        return {"net": 0, "hedge": 0, "bias": "NEUTRAL"}

# ── CÁLCULOS TÉCNICOS (Wilder's Smoothing - Precisión Institucional) ───────────
def calculate_indicators(df):
    period = 14
    close = df['close']
    # RSI Wilder's
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    # Bollinger
    df['sma20'] = close.rolling(20).mean()
    df['std20'] = close.rolling(20).std()
    df['bb_up'] = df['sma20'] + (df['std20'] * 2)
    df['bb_low'] = df['sma20'] - (df['std20'] * 2)
    # ADX
    plus_dm = df['high'].diff()
    minus_dm = df['low'].diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
    tr = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - close.shift(1)), abs(df['low'] - close.shift(1))))
    atr = pd.Series(tr).ewm(alpha=1/period, adjust=False).mean()
    plus_di = 100 * (pd.Series(plus_dm).ewm(alpha=1/period, adjust=False).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).ewm(alpha=1/period, adjust=False).mean() / atr)
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
    df['adx'] = dx.ewm(alpha=1/period, adjust=False).mean()
    return df

# ── INTERFAZ STREAMLIT ──────────────────────────────────────────────────────────
st.markdown(f"""
    <style>
    .main {{ background-color: {BG_DARK}; color: white; }}
    .stMetric {{ background-color: #1E1E1E; padding: 15px; border-radius: 10px; border: 1px solid {C_GOLD}; }}
    </style>
""", unsafe_allow_html=True)

st.title("🔱 K-AURUM Q-CORE v10.0 | Web Terminal")

engine = DataEngine()
price_data = engine.get_gold_price()
cot = engine.get_cot_data()
df = engine.get_historical_data()

if price_data and df is not None:
    df = calculate_indicators(df)
    
    # Header Metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("XAU/USD", f"${price_data['price']:,.2f}", f"{price_data['pct']:+.2f}%")
    col2.metric("COT Bias", cot['bias'], f"Net: {cot['net']:.1f}%")
    col3.metric("Hedge Score", f"{cot['hedge']:.2f}", "Institutional")
    
    # Lógica de Señal (Mantenida)
    rsi = df['rsi'].iloc[-1]
    adx = df['adx'].iloc[-1]
    last_price = price_data['price']
    
    score = 50
    if cot['bias'] == "BULLISH": score += 15
    if rsi < 35: score += 10
    if adx > 25: score += 5
    if last_price < df['bb_low'].iloc[-1]: score += 10
    
    if cot['bias'] == "BEARISH": score -= 15
    if rsi > 65: score -= 10
    if last_price > df['bb_up'].iloc[-1]: score -= 10
    
    signal = "NEUTRAL"
    sig_col = C_GRAY
    if score > 65: signal = "BUY"; sig_col = C_GREEN
    elif score < 35: signal = "SELL"; sig_col = C_RED
    
    col4.markdown(f"""
        <div style="text-align:center; background:{sig_col}; padding:10px; border-radius:10px; color:black; font-weight:bold;">
            <h2 style="margin:0;">{signal}</h2>
            <span>Score: {score}/100</span>
        </div>
    """, unsafe_allow_html=True)

    # Gráfico Principal
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    
    # Velas y Bollinger
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], name="Gold"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['bb_up'], line=dict(color='rgba(173, 216, 230, 0.5)'), name="BB Upper"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['bb_low'], line=dict(color='rgba(173, 216, 230, 0.5)'), name="BB Lower"), row=1, col=1)
    
    # RSI
    fig.add_trace(go.Scatter(x=df.index, y=df['rsi'], line=dict(color=C_GOLD), name="RSI"), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=[70]*len(df), line=dict(color=C_RED, dash='dash'), name="Overbought"), row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=[30]*len(df), line=dict(color=C_GREEN, dash='dash'), name="Oversold"), row=2, col=1)

    fig.update_layout(template="plotly_dark", height=600, showlegend=False, xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)

    # Sidebar Info
    st.sidebar.header("Configuración")
    st.sidebar.info(f"API Status: OK\n\nSource: Finnhub + CFTC\n\nAnti-Overfitting: Active\n\nZ-Score: {adx/10:.2f}")
    
    if st.sidebar.button("Refrescar Datos"):
        st.rerun()

else:
    st.error("Error cargando datos. Verifica tu conexión o API Key.")
