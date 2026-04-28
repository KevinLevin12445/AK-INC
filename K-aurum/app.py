import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests, re, time

st.set_page_config(page_title='K-AURUM Q-CORE v10.1', layout='wide')

C_GOLD='#FFD700'; C_RED='#FF4B4B'; C_GREEN='#00FA9A'; C_GRAY='#808080'; BG='#0E1117'
FINNHUB_API_KEY='TU_API_KEY'
FINNHUB_BASE='https://finnhub.io/api/v1'

st.markdown(f"<style>.main{{background:{BG};color:white}}</style>", unsafe_allow_html=True)

def _finnhub_get(endpoint, params=None):
    params=params or {}
    params['token']=FINNHUB_API_KEY
    try:
        r=requests.get(f'{FINNHUB_BASE}/{endpoint}', params=params, timeout=10)
        if r.status_code==200:
            return r.json()
    except:
        pass
    return None

class DataEngine:
    @st.cache_data(ttl=60)
    def get_gold_price(self):
        data=_finnhub_get('quote', {'symbol':'OANDA:XAU_USD'})
        if data and data.get('c',0)!=0:
            return {'price':data['c'],'change':data['d'],'pct':data['dp'],'high':data['h'],'low':data['l']}
        return None

    @st.cache_data(ttl=300)
    def get_historical_data(self, resolution='5', limit=100):
        now=int(time.time())
        past=now-86400*5
        data=_finnhub_get('forex/candle', {
            'symbol':'OANDA:XAU_USD','resolution':resolution,'from':past,'to':now
        })
        if data and data.get('s')=='ok':
            df=pd.DataFrame({'open':data['o'],'high':data['h'],'low':data['l'],'close':data['c']})
            df.index=pd.to_datetime(data['t'], unit='s')
            return df.tail(limit)
        return None

def calculate_indicators(df):
    close=df['close']; period=14
    delta=close.diff()
    gain=delta.clip(lower=0)
    loss=(-delta).clip(lower=0)
    ag=gain.ewm(alpha=1/period, adjust=False).mean()
    al=loss.ewm(alpha=1/period, adjust=False).mean()
    rs=ag/al.replace(0,np.nan)
    df['rsi']=100-(100/(1+rs))
    df['sma20']=close.rolling(20).mean()
    std=close.rolling(20).std()
    df['bb_up']=df['sma20']+2*std
    df['bb_low']=df['sma20']-2*std
    tr=np.maximum(df['high']-df['low'], np.maximum(abs(df['high']-close.shift(1)), abs(df['low']-close.shift(1))))
    atr=pd.Series(tr,index=df.index).ewm(alpha=1/period, adjust=False).mean()
    up=df['high'].diff().clip(lower=0)
    dn=(-df['low'].diff()).clip(lower=0)
    plus=100*(up.ewm(alpha=1/period, adjust=False).mean()/atr)
    minus=100*(dn.ewm(alpha=1/period, adjust=False).mean()/atr)
    dx=100*(abs(plus-minus)/(plus+minus))
    df['adx']=dx.ewm(alpha=1/period, adjust=False).mean()
    return df

st.title('🔱 K-AURUM Q-CORE v10.1 | Deploy Fixed')
engine=DataEngine()
price=engine.get_gold_price()
df=engine.get_historical_data()

if price and df is not None and not df.empty:
    df=calculate_indicators(df)
    c1,c2,c3,c4=st.columns(4)
    c1.metric('XAU/USD', f"${price['price']:,.2f}", f"{price['pct']:+.2f}%")
    rsi=float(df['rsi'].iloc[-1]) if pd.notna(df['rsi'].iloc[-1]) else 50
    adx=float(df['adx'].iloc[-1]) if pd.notna(df['adx'].iloc[-1]) else 20
    score=50
    if rsi<35: score+=10
    if rsi>65: score-=10
    if adx>25: score+=5
    signal='NEUTRAL'; col=C_GRAY
    if score>60: signal='BUY'; col=C_GREEN
    elif score<40: signal='SELL'; col=C_RED
    c2.metric('RSI', f'{rsi:.1f}')
    c3.metric('ADX', f'{adx:.1f}')
    c4.markdown(f"<div style='background:{col};padding:12px;border-radius:10px;color:black;text-align:center;font-weight:bold'>{signal}<br>Score {score}</div>", unsafe_allow_html=True)

    fig=make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,row_heights=[0.7,0.3])
    fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close']), row=1,col=1)
    fig.add_trace(go.Scatter(x=df.index,y=df['bb_up']), row=1,col=1)
    fig.add_trace(go.Scatter(x=df.index,y=df['bb_low']), row=1,col=1)
    fig.add_trace(go.Scatter(x=df.index,y=df['rsi']), row=2,col=1)
    fig.update_layout(template='plotly_dark', height=650, xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.error('No se pudieron cargar datos. Verifica tu FINNHUB_API_KEY.')