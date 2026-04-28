#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# K-AURUM Q-CORE v10.1 — Streamlit Web Edition
# Deploy en: https://streamlit.io/cloud

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import time

# ══════════════════════════════════════════════════════════════
#  CONFIG & CONSTANTES
# ══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title='K-AURUM Q-CORE v10.1',
    layout='wide',
    initial_sidebar_state='expanded',
    menu_items={'About': 'K-AURUM Q-CORE v10.1 — Sistema de análisis técnico XAU/USD'}
)

C_GOLD  = '#FFD700'
C_RED   = '#FF4B4B'
C_GREEN = '#00FA9A'
C_BLUE  = '#4B9FFF'
C_GRAY  = '#808080'
BG      = '#0E1117'

# ── API KEY: usa st.secrets en producción ──────────────────────
# En Streamlit Cloud: Settings → Secrets → FINNHUB_API_KEY = "tu_key"
# Localmente: crea .streamlit/secrets.toml con esa línea
try:
    FINNHUB_API_KEY = st.secrets["FINNHUB_API_KEY"]
except Exception:
    FINNHUB_API_KEY = "d7lnma9r01qk7lvto8i0d7lnma9r01qk7lvto8ig"  # fallback dev

FINNHUB_BASE = "https://finnhub.io/api/v1"

# ══════════════════════════════════════════════════════════════
#  ESTILOS CSS
# ══════════════════════════════════════════════════════════════
st.markdown(f"""
<style>
  .main {{ background:{BG}; color:white; }}
  .block-container {{ padding-top: 1rem; }}
  div[data-testid="metric-container"] {{
    background: #1A1E2E;
    border: 1px solid #2A2E3E;
    border-radius: 10px;
    padding: 10px 15px;
  }}
  div[data-testid="metric-container"] label {{
    color: {C_GRAY};
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: 0.05em;
  }}
  .signal-box {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 14px;
    border-radius: 12px;
    font-weight: bold;
    font-size: 1.05rem;
    letter-spacing: 0.08em;
  }}
  .info-card {{
    background:#1A1E2E;
    border:1px solid #2A2E3E;
    border-radius:10px;
    padding:14px 18px;
    margin-bottom:10px;
  }}
  .stAlert {{ border-radius: 10px; }}
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
#  FUNCIONES DE DATOS  (cache correcto fuera de clase)
# ══════════════════════════════════════════════════════════════

def _finnhub_get(endpoint: str, params: dict = None):
    """Petición GET a Finnhub con manejo de errores."""
    params = params or {}
    params['token'] = FINNHUB_API_KEY
    try:
        r = requests.get(f'{FINNHUB_BASE}/{endpoint}', params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 403:
            st.warning("⚠️ API Key inválida o sin permisos. Configura FINNHUB_API_KEY en Secrets.")
    except requests.exceptions.Timeout:
        st.warning("⏱️ Timeout conectando a Finnhub.")
    except Exception as e:
        st.warning(f"Error de red: {e}")
    return None


@st.cache_data(ttl=30)
def get_gold_price():
    """Cotización en tiempo real XAU/USD (cache 30s)."""
    data = _finnhub_get('quote', {'symbol': 'OANDA:XAU_USD'})
    if data and data.get('c', 0) != 0:
        return {
            'price': data['c'],
            'change': data.get('d', 0),
            'pct': data.get('dp', 0),
            'high': data.get('h', 0),
            'low': data.get('l', 0),
            'prev_close': data.get('pc', 0),
        }
    return None


@st.cache_data(ttl=60)
def get_historical_data(resolution: str = '5', limit: int = 120):
    """Datos históricos OHLC (cache 60s)."""
    now  = int(time.time())
    past = now - 86400 * 7   # últimos 7 días para tener suficientes velas
    data = _finnhub_get('forex/candle', {
        'symbol': 'OANDA:XAU_USD',
        'resolution': resolution,
        'from': past,
        'to': now,
    })
    if data and data.get('s') == 'ok':
        df = pd.DataFrame({
            'open':  data['o'],
            'high':  data['h'],
            'low':   data['l'],
            'close': data['c'],
        })
        df.index = pd.to_datetime(data['t'], unit='s')
        return df.tail(limit)
    return None


@st.cache_data(ttl=300)
def get_news():
    """Noticias recientes de oro (cache 5 min)."""
    data = _finnhub_get('news', {'category': 'forex'})
    if data and isinstance(data, list):
        gold_kw = ['gold', 'xau', 'precious', 'metal', 'commodity', 'fed', 'dollar', 'inflation']
        filtered = [n for n in data if any(k in (n.get('headline','') + n.get('summary','')).lower() for k in gold_kw)]
        return filtered[:6]
    return []


# ══════════════════════════════════════════════════════════════
#  INDICADORES TÉCNICOS
# ══════════════════════════════════════════════════════════════

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close  = df['close']
    period = 14

    # ── RSI ─────────────────────────────────────────────────
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    ag    = gain.ewm(alpha=1/period, adjust=False).mean()
    al    = loss.ewm(alpha=1/period, adjust=False).mean()
    rs    = ag / al.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    # ── Medias móviles ──────────────────────────────────────
    df['sma20'] = close.rolling(20).mean()
    df['ema20'] = close.ewm(span=20, adjust=False).mean()
    df['sma50'] = close.rolling(50).mean()

    # ── Bollinger Bands ─────────────────────────────────────
    std         = close.rolling(20).std()
    df['bb_up'] = df['sma20'] + 2 * std
    df['bb_low'] = df['sma20'] - 2 * std

    # ── ATR ─────────────────────────────────────────────────
    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - close.shift(1)),
            abs(df['low']  - close.shift(1))
        )
    )
    df['atr'] = pd.Series(tr, index=df.index).ewm(alpha=1/period, adjust=False).mean()

    # ── ADX ─────────────────────────────────────────────────
    atr_s = df['atr']
    up    = df['high'].diff().clip(lower=0)
    dn    = (-df['low'].diff()).clip(lower=0)
    plus  = 100 * (up.ewm(alpha=1/period, adjust=False).mean() / atr_s)
    minus = 100 * (dn.ewm(alpha=1/period, adjust=False).mean() / atr_s)
    dx    = 100 * (abs(plus - minus) / (plus + minus).replace(0, np.nan))
    df['adx']   = dx.ewm(alpha=1/period, adjust=False).mean()
    df['di_plus']  = plus
    df['di_minus'] = minus

    # ── MACD ────────────────────────────────────────────────
    ema12       = close.ewm(span=12, adjust=False).mean()
    ema26       = close.ewm(span=26, adjust=False).mean()
    df['macd']  = ema12 - ema26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist']   = df['macd'] - df['macd_signal']

    # ── Stochastic ──────────────────────────────────────────
    low14  = df['low'].rolling(14).min()
    high14 = df['high'].rolling(14).max()
    df['stoch_k'] = 100 * (close - low14) / (high14 - low14).replace(0, np.nan)
    df['stoch_d'] = df['stoch_k'].rolling(3).mean()

    return df


# ══════════════════════════════════════════════════════════════
#  MOTOR DE SEÑALES
# ══════════════════════════════════════════════════════════════

def calculate_signal(df: pd.DataFrame, price: dict) -> dict:
    """Calcula señal de trading con score ponderado multi-factor."""
    score  = 50
    reason = []

    rsi       = float(df['rsi'].iloc[-1])       if pd.notna(df['rsi'].iloc[-1])       else 50
    adx       = float(df['adx'].iloc[-1])        if pd.notna(df['adx'].iloc[-1])        else 20
    macd_h    = float(df['macd_hist'].iloc[-1])  if pd.notna(df['macd_hist'].iloc[-1])  else 0
    stoch_k   = float(df['stoch_k'].iloc[-1])    if pd.notna(df['stoch_k'].iloc[-1])    else 50
    stoch_d   = float(df['stoch_d'].iloc[-1])    if pd.notna(df['stoch_d'].iloc[-1])    else 50
    bb_up     = float(df['bb_up'].iloc[-1])      if pd.notna(df['bb_up'].iloc[-1])      else price['price']
    bb_low    = float(df['bb_low'].iloc[-1])     if pd.notna(df['bb_low'].iloc[-1])     else price['price']
    sma20     = float(df['sma20'].iloc[-1])      if pd.notna(df['sma20'].iloc[-1])      else price['price']
    sma50     = float(df['sma50'].iloc[-1])      if pd.notna(df['sma50'].iloc[-1])      else price['price']
    cur_price = price['price']

    # ── RSI ─────────────────────────────────────────────────
    if rsi < 30:
        score += 15; reason.append(f"RSI sobrevendido ({rsi:.1f})")
    elif rsi < 40:
        score += 8;  reason.append(f"RSI bajo ({rsi:.1f})")
    elif rsi > 70:
        score -= 15; reason.append(f"RSI sobrecomprado ({rsi:.1f})")
    elif rsi > 60:
        score -= 8;  reason.append(f"RSI alto ({rsi:.1f})")

    # ── ADX — fuerza de tendencia ───────────────────────────
    di_plus  = float(df['di_plus'].iloc[-1])  if pd.notna(df['di_plus'].iloc[-1])  else 25
    di_minus = float(df['di_minus'].iloc[-1]) if pd.notna(df['di_minus'].iloc[-1]) else 25
    if adx > 25:
        if di_plus > di_minus:
            score += 10; reason.append(f"Tendencia alcista fuerte (ADX {adx:.1f})")
        else:
            score -= 10; reason.append(f"Tendencia bajista fuerte (ADX {adx:.1f})")

    # ── MACD ────────────────────────────────────────────────
    if macd_h > 0:
        score += 8; reason.append("MACD histograma positivo")
    elif macd_h < 0:
        score -= 8; reason.append("MACD histograma negativo")

    # ── Stochastic ──────────────────────────────────────────
    if stoch_k < 20 and stoch_k > stoch_d:
        score += 7; reason.append(f"Stoch cruce alcista sobrevendido ({stoch_k:.1f})")
    elif stoch_k > 80 and stoch_k < stoch_d:
        score -= 7; reason.append(f"Stoch cruce bajista sobrecomprado ({stoch_k:.1f})")

    # ── Bollinger Bands ─────────────────────────────────────
    bb_range = bb_up - bb_low
    if bb_range > 0:
        bb_pos = (cur_price - bb_low) / bb_range
        if bb_pos < 0.1:
            score += 8; reason.append("Precio en banda inferior (soporte)")
        elif bb_pos > 0.9:
            score -= 8; reason.append("Precio en banda superior (resistencia)")

    # ── Medias móviles ──────────────────────────────────────
    if cur_price > sma20 > sma50:
        score += 5; reason.append("Precio > SMA20 > SMA50 (tendencia alcista)")
    elif cur_price < sma20 < sma50:
        score -= 5; reason.append("Precio < SMA20 < SMA50 (tendencia bajista)")

    # ── Momentum del precio ─────────────────────────────────
    if price['pct'] > 0.3:
        score += 3; reason.append(f"Momentum positivo ({price['pct']:+.2f}%)")
    elif price['pct'] < -0.3:
        score -= 3; reason.append(f"Momentum negativo ({price['pct']:+.2f}%)")

    score = max(0, min(100, score))

    # ── Clasificar señal ────────────────────────────────────
    if score >= 65:
        signal, color, emoji = 'COMPRA', C_GREEN, '🟢'
    elif score >= 58:
        signal, color, emoji = 'POSIBLE COMPRA', '#90EE90', '🔼'
    elif score <= 35:
        signal, color, emoji = 'VENTA', C_RED, '🔴'
    elif score <= 42:
        signal, color, emoji = 'POSIBLE VENTA', '#FF9090', '🔽'
    else:
        signal, color, emoji = 'NEUTRAL', C_GRAY, '⚪'

    # ── Niveles SL/TP ───────────────────────────────────────
    atr_val = float(df['atr'].iloc[-1]) if pd.notna(df['atr'].iloc[-1]) else cur_price * 0.005
    if 'COMPRA' in signal:
        sl = cur_price - 1.5 * atr_val
        tp1 = cur_price + 1.5 * atr_val
        tp2 = cur_price + 2.5 * atr_val
    elif 'VENTA' in signal:
        sl = cur_price + 1.5 * atr_val
        tp1 = cur_price - 1.5 * atr_val
        tp2 = cur_price - 2.5 * atr_val
    else:
        sl = tp1 = tp2 = None

    return {
        'signal': signal, 'color': color, 'emoji': emoji,
        'score': score, 'reason': reason,
        'rsi': rsi, 'adx': adx, 'macd_h': macd_h,
        'stoch_k': stoch_k, 'stoch_d': stoch_d,
        'atr': atr_val, 'sl': sl, 'tp1': tp1, 'tp2': tp2,
        'bb_up': bb_up, 'bb_low': bb_low, 'sma20': sma20, 'sma50': sma50,
    }


# ══════════════════════════════════════════════════════════════
#  GRÁFICO PRINCIPAL
# ══════════════════════════════════════════════════════════════

def build_chart(df: pd.DataFrame, sig: dict) -> go.Figure:
    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.50, 0.18, 0.16, 0.16],
        subplot_titles=('XAU/USD — Velas + BB + SMA', 'MACD', 'RSI (14)', 'Stochastic (14,3)')
    )

    # ── Velas + Bandas ──────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['open'], high=df['high'],
        low=df['low'], close=df['close'],
        name='XAU/USD',
        increasing_line_color=C_GREEN,
        decreasing_line_color=C_RED
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df.index, y=df['bb_up'], name='BB Superior',
        line=dict(color='rgba(75,159,255,0.5)', width=1, dash='dot')
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df.index, y=df['bb_low'], name='BB Inferior',
        line=dict(color='rgba(75,159,255,0.5)', width=1, dash='dot'),
        fill='tonexty', fillcolor='rgba(75,159,255,0.05)'
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=df.index, y=df['sma20'], name='SMA20',
        line=dict(color=C_GOLD, width=1)
    ), row=1, col=1)

    if df['sma50'].notna().sum() > 0:
        fig.add_trace(go.Scatter(
            x=df.index, y=df['sma50'], name='SMA50',
            line=dict(color='#FF8C00', width=1, dash='dash')
        ), row=1, col=1)

    # SL/TP lines
    if sig['sl']:
        clr_sl  = C_RED   if 'COMPRA' in sig['signal'] else C_GREEN
        clr_tp1 = C_GREEN if 'COMPRA' in sig['signal'] else C_RED
        fig.add_hline(y=sig['sl'],  line_color=clr_sl,  line_dash='dash', line_width=1,
                      annotation_text=f"SL {sig['sl']:,.2f}", row=1, col=1)
        fig.add_hline(y=sig['tp1'], line_color=clr_tp1, line_dash='dash', line_width=1,
                      annotation_text=f"TP1 {sig['tp1']:,.2f}", row=1, col=1)
        fig.add_hline(y=sig['tp2'], line_color=clr_tp1, line_dash='dot',  line_width=1,
                      annotation_text=f"TP2 {sig['tp2']:,.2f}", row=1, col=1)

    # ── MACD ────────────────────────────────────────────────
    colors_macd = [C_GREEN if v >= 0 else C_RED for v in df['macd_hist'].fillna(0)]
    fig.add_trace(go.Bar(
        x=df.index, y=df['macd_hist'], name='MACD Hist',
        marker_color=colors_macd, opacity=0.7
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['macd'], name='MACD',
        line=dict(color=C_BLUE, width=1)
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['macd_signal'], name='Señal',
        line=dict(color=C_GOLD, width=1, dash='dot')
    ), row=2, col=1)

    # ── RSI ─────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=df.index, y=df['rsi'], name='RSI',
        line=dict(color=C_GOLD, width=1.5)
    ), row=3, col=1)
    fig.add_hline(y=70, line_color=C_RED,   line_dash='dot', line_width=1, row=3, col=1)
    fig.add_hline(y=30, line_color=C_GREEN, line_dash='dot', line_width=1, row=3, col=1)
    fig.add_hline(y=50, line_color=C_GRAY,  line_dash='dot', line_width=1, row=3, col=1)

    # ── Stochastic ──────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=df.index, y=df['stoch_k'], name='%K',
        line=dict(color=C_BLUE, width=1.5)
    ), row=4, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['stoch_d'], name='%D',
        line=dict(color=C_GOLD, width=1, dash='dot')
    ), row=4, col=1)
    fig.add_hline(y=80, line_color=C_RED,   line_dash='dot', line_width=1, row=4, col=1)
    fig.add_hline(y=20, line_color=C_GREEN, line_dash='dot', line_width=1, row=4, col=1)

    fig.update_layout(
        template='plotly_dark',
        height=750,
        xaxis_rangeslider_visible=False,
        paper_bgcolor=BG,
        plot_bgcolor='#0E1117',
        legend=dict(orientation='h', yanchor='bottom', y=1.01, xanchor='right', x=1,
                    font=dict(size=10)),
        margin=dict(l=10, r=10, t=30, b=10),
    )
    for i in range(1, 5):
        fig.update_yaxes(gridcolor='#1E2130', row=i, col=1)
        fig.update_xaxes(gridcolor='#1E2130', row=i, col=1)

    return fig


# ══════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════

def render_sidebar():
    with st.sidebar:
        st.markdown(f"<h2 style='color:{C_GOLD};margin-bottom:0'>🔱 K-AURUM</h2>", unsafe_allow_html=True)
        st.markdown("<p style='color:#888;font-size:0.8rem;margin-top:0'>Q-CORE v10.1 | XAU/USD</p>", unsafe_allow_html=True)
        st.divider()

        resolution = st.selectbox(
            '⏱ Temporalidad',
            options=['1', '5', '15', '30', '60', 'D'],
            index=1,
            format_func=lambda x: {'1':'1 Min','5':'5 Min','15':'15 Min','30':'30 Min','60':'1 Hora','D':'Diario'}[x]
        )

        limit = st.slider('📊 Velas a mostrar', min_value=50, max_value=300, value=120, step=10)

        st.divider()
        auto_refresh = st.toggle('🔄 Auto-refresh (30s)', value=False)

        st.divider()
        st.markdown(f"""
        <div class='info-card'>
          <p style='color:{C_GRAY};font-size:0.75rem;margin:0 0 6px 0'>⚙️ CONFIGURACIÓN</p>
          <p style='font-size:0.8rem;margin:0'>
            Para usar tu propia API Key de Finnhub, ve a:<br>
            <b>Settings → Secrets</b> y agrega:<br>
            <code style='color:{C_GOLD}'>FINNHUB_API_KEY = "tu_key"</code>
          </p>
        </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
        <div class='info-card'>
          <p style='color:{C_GRAY};font-size:0.75rem;margin:0 0 6px 0'>⚠️ DISCLAIMER</p>
          <p style='font-size:0.75rem;color:#aaa;margin:0'>
            Solo análisis técnico. No es asesoría financiera. Opera con gestión de riesgo adecuada.
          </p>
        </div>
        """, unsafe_allow_html=True)

        return resolution, limit, auto_refresh


# ══════════════════════════════════════════════════════════════
#  MAIN APP
# ══════════════════════════════════════════════════════════════

def main():
    resolution, limit, auto_refresh = render_sidebar()

    # ── Auto-refresh ────────────────────────────────────────
    if auto_refresh:
        st.markdown(
            f"<p style='color:{C_GRAY};font-size:0.78rem;text-align:right'>"
            f"🔄 Actualizando cada 30s...</p>",
            unsafe_allow_html=True
        )
        time.sleep(30)
        st.rerun()

    # ── Header ──────────────────────────────────────────────
    st.markdown(f"<h1 style='color:{C_GOLD};margin-bottom:0.2rem'>🔱 K-AURUM Q-CORE v10.1</h1>", unsafe_allow_html=True)
    st.markdown(f"<p style='color:{C_GRAY};margin-top:0'>Sistema de análisis técnico XAU/USD · Finnhub · Tiempo real</p>", unsafe_allow_html=True)

    # ── Cargar datos ─────────────────────────────────────────
    with st.spinner('Cargando datos de mercado...'):
        price = get_gold_price()
        df    = get_historical_data(resolution=resolution, limit=limit)

    if price is None or df is None or df.empty:
        st.error('❌ No se pudieron cargar datos. Verifica tu FINNHUB_API_KEY en Settings → Secrets.')
        st.code('''# .streamlit/secrets.toml
FINNHUB_API_KEY = "tu_api_key_aqui"
''', language='toml')
        st.markdown('[Obtener API Key gratuita en Finnhub](https://finnhub.io/register)', unsafe_allow_html=False)
        st.stop()

    df  = calculate_indicators(df)
    sig = calculate_signal(df, price)

    # ══════════════════════════════════════════════════════
    #  FILA 1: Métricas de precio
    # ══════════════════════════════════════════════════════
    c1, c2, c3, c4, c5 = st.columns(5)

    pct_color = C_GREEN if price['pct'] >= 0 else C_RED
    c1.metric('💰 XAU/USD',  f"${price['price']:,.2f}",  f"{price['pct']:+.2f}%")
    c2.metric('📈 Máx. día', f"${price['high']:,.2f}")
    c3.metric('📉 Mín. día', f"${price['low']:,.2f}")
    c4.metric('◀ Cierre ant.', f"${price['prev_close']:,.2f}")
    c5.metric('📐 ATR (14)', f"${sig['atr']:,.2f}")

    st.divider()

    # ══════════════════════════════════════════════════════
    #  FILA 2: Indicadores + Señal
    # ══════════════════════════════════════════════════════
    col_ind, col_sig = st.columns([3, 1])

    with col_ind:
        i1, i2, i3, i4, i5 = st.columns(5)
        i1.metric('RSI (14)',    f"{sig['rsi']:.1f}",     delta="Sobrecomprado" if sig['rsi']>70 else ("Sobrevendido" if sig['rsi']<30 else None))
        i2.metric('ADX (14)',    f"{sig['adx']:.1f}",     delta="Tendencia" if sig['adx']>25 else "Lateral")
        i3.metric('MACD Hist',   f"{sig['macd_h']:+.2f}", delta="Positivo" if sig['macd_h']>0 else "Negativo")
        i4.metric('Stoch %K',   f"{sig['stoch_k']:.1f}")
        i5.metric('Score',       f"{sig['score']}/100")

    with col_sig:
        st.markdown(f"""
        <div class='signal-box' style='background:{sig["color"]}22;border:2px solid {sig["color"]};'>
          <div style='font-size:1.8rem'>{sig["emoji"]}</div>
          <div style='color:{sig["color"]};font-size:1.1rem;margin-top:4px'>{sig["signal"]}</div>
          <div style='color:#ccc;font-size:0.85rem;margin-top:2px'>Score: {sig["score"]}/100</div>
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════
    #  FILA 3: Gráfico principal
    # ══════════════════════════════════════════════════════
    fig = build_chart(df, sig)
    st.plotly_chart(fig, use_container_width=True)

    # ══════════════════════════════════════════════════════
    #  FILA 4: Análisis + Niveles
    # ══════════════════════════════════════════════════════
    col_anal, col_lvl, col_news = st.columns([2, 1, 2])

    with col_anal:
        st.markdown(f"#### 🔍 Análisis de confluencia")
        if sig['reason']:
            for r in sig['reason']:
                icon = '🟢' if any(k in r.lower() for k in ['alcist','sobrevendido','positiv','soport','bajo','momentum pos']) else '🔴' if any(k in r.lower() for k in ['bajist','sobrecomprado','negativ','resistencia','alto','momentum neg']) else '⚪'
                st.markdown(f"{icon} {r}")
        else:
            st.info("Sin señales de confluencia claras.")

    with col_lvl:
        st.markdown("#### 📐 Niveles clave")
        cur = price['price']
        st.markdown(f"""
        <div class='info-card'>
          <p style='margin:3px 0;color:{C_GOLD}'>💰 Precio: <b>${cur:,.2f}</b></p>
          <p style='margin:3px 0;color:#aaa'>📊 SMA20: ${sig['sma20']:,.2f}</p>
          <p style='margin:3px 0;color:#aaa'>📊 SMA50: ${sig['sma50']:,.2f}</p>
          <p style='margin:3px 0;color:{C_BLUE}'>⬆ BB Sup: ${sig['bb_up']:,.2f}</p>
          <p style='margin:3px 0;color:{C_BLUE}'>⬇ BB Inf: ${sig['bb_low']:,.2f}</p>
          {"<hr style='border-color:#333;margin:6px 0'>" if sig['sl'] else ""}
          {f"<p style='margin:3px 0;color:{C_RED}'>🛑 SL: ${sig['sl']:,.2f}</p>" if sig['sl'] else ""}
          {f"<p style='margin:3px 0;color:{C_GREEN}'>🎯 TP1: ${sig['tp1']:,.2f}</p>" if sig['tp1'] else ""}
          {f"<p style='margin:3px 0;color:{C_GREEN}'>🎯 TP2: ${sig['tp2']:,.2f}</p>" if sig['tp2'] else ""}
        </div>
        """, unsafe_allow_html=True)

    with col_news:
        st.markdown("#### 📰 Noticias recientes")
        with st.spinner('Cargando noticias...'):
            news = get_news()
        if news:
            for n in news[:4]:
                headline = n.get('headline', '')[:80]
                url      = n.get('url', '#')
                source   = n.get('source', '')
                dt       = pd.to_datetime(n.get('datetime', 0), unit='s').strftime('%d/%m %H:%M') if n.get('datetime') else ''
                st.markdown(f"""
                <div style='background:#1A1E2E;border-left:3px solid {C_GOLD};
                            padding:8px 12px;border-radius:6px;margin-bottom:8px'>
                  <a href='{url}' target='_blank' style='color:white;text-decoration:none;font-size:0.83rem;font-weight:500'>
                    {headline}...
                  </a>
                  <p style='color:{C_GRAY};font-size:0.72rem;margin:3px 0 0 0'>{source} · {dt}</p>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No se encontraron noticias recientes.")

    # ── Footer ──────────────────────────────────────────────
    st.divider()
    ts = pd.Timestamp.now().strftime('%d/%m/%Y %H:%M:%S')
    st.markdown(
        f"<p style='color:{C_GRAY};font-size:0.75rem;text-align:center'>"
        f"🔱 K-AURUM Q-CORE v10.1 · Última actualización: {ts} · "
        f"Datos: Finnhub · XAU/USD OANDA · "
        f"<b>⚠️ No es asesoría financiera</b></p>",
        unsafe_allow_html=True
    )


if __name__ == '__main__':
    main()