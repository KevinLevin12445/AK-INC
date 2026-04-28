#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# K-AURUM Q-CORE v10.0 — Finnhub+Macro+COT+Calendar+News+Scoring+WalkForward+MonteCarlo+SignalLogger

import tkinter as tk
from tkinter import ttk, font as tkfont
import threading
import time
import datetime
import urllib.request
import json
import xml.etree.ElementTree as ET
from collections import deque, defaultdict
import numpy as np
import math
import sys
import traceback
import queue
import requests
from bs4 import BeautifulSoup
import re
import pandas as pd
from sklearn.linear_model import LinearRegression
import warnings
import csv
import os
import copy
import random
warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# OpenBB Platform v4 — pip install openbb
OPENBB_AVAILABLE = False
try:
    from openbb import obb
    if hasattr(obb, 'equity') or hasattr(obb, 'news'):
        OPENBB_AVAILABLE = True
except ImportError:
    pass

import yfinance as yf

# ══════════════════════════════════════════════════════════════════════════════
#  K-AURUM Q-CORE v10.0 — INTEGRACIÓN Finnhub (Finnhub)
# ══════════════════════════════════════════════════════════════════════════════
#
#  CONFIGURACIÓN:
#  1. Obtener API Key GRATUITA en: https://financialmodelingprep.com/developer
#  2. Configurar FINNHUB_API_KEY abajo o variable de entorno: export FINNHUB_API_KEY=...
#
#  NUEVAS FUNCIONALIDADES v10.0:
#  ◈ FinnhubPriceFeed       → Cotización GCUSD en tiempo real (15s)
#  ◈ FinnhubCOTEngine       → Commitment of Traders oro (CFTC vía Finnhub)
#  ◈ FinnhubMacroEngine     → DXY, VIX, Yields, SPY, GLD en tiempo real
#  ◈ FinnhubEconomicCalendar→ Eventos macro de alto impacto
#  ◈ FinnhubNewsEngine      → Noticias forex/gold vía Finnhub
#  ◈ FinnhubTechnicalInd    → RSI, MACD, BB, ADX calculados por Finnhub
#  ◈ FinnhubStrategyEngine  → Señal de confluencia (score 0-100, mín 65)
#  ◈ FinnhubIntegrationMgr  → Gestor central de todos los módulos Finnhub
#
#  ESTRATEGIA DE TRADING REAL (Finnhub Strategy v10.0):
#  ENTRADAS: Confluencia mínima de 4 factores:
#    1. Macro Finnhub alineado (DXY + VIX + Yields + SPY + TLT + GLD)
#    2. COT institucional > 5% neto en dirección
#    3. Técnico: VWAP + BB + RSI + MACD + ADX (Finnhub calculado)
#    4. Zona demand/supply activa dentro de 1.5 ATR
#    5. Sin eventos de alto impacto en 30 minutos (Finnhub Calendar)
#    6. Sentimiento noticias Finnhub alineado
#  RIESGO: 0.5% de cuenta | SL: 1.5x ATR | TP1: 1.5 RR | TP2: 2.5 RR
#  TAMAÑO: Kelly fraccionado ajustado por score (0.1x - 1.0x)
# ══════════════════════════════════════════════════════════════════════════════

# ── CONFIGURACIÓN Finnhub ──────────────────────────────────────────────────────────
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "d7og4rhr01qsb7beqjsgd7og4rhr01qsb7beqjt0")
FINNHUB_BASE = "https://finnhub.io/api/v1"
FINNHUB_WS = "wss://ws.finnhub.io"
FINNHUB_HEADERS = {"User-Agent": "K-AURUM/10.0", "Accept": "application/json"}
FINNHUB_TIMEOUT = 8
FINNHUB_CACHE_TTL = 60

# ── CACHE GLOBAL Finnhub ───────────────────────────────────────────────────────────
_finnhub_cache    = {}
_finnhub_cache_ts = {}
_finnhub_lock_g   = threading.Lock()


def _finnhub_get(url: str, params: dict = None, ttl: int = FINNHUB_CACHE_TTL):
    """Realiza una petición GET a Finnhub con caché y manejo de errores."""
    if params is None:
        params = {}
    params["token"] = FINNHUB_API_KEY
    cache_key = url + str(sorted(params.items()))
    now = time.time()
    with _finnhub_lock_g:
        if cache_key in _finnhub_cache and now - _finnhub_cache_ts.get(cache_key, 0) < ttl:
            return _finnhub_cache[cache_key]
    try:
        resp = requests.get(url, params=params, headers=FINNHUB_HEADERS, timeout=FINNHUB_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            with _finnhub_lock_g:
                _finnhub_cache[cache_key]    = data
                _finnhub_cache_ts[cache_key] = now
            return data
        elif resp.status_code == 403:
            print(f"[Finnhub] API Key inválida o sin permisos: {url}")
        else:
            print(f"[Finnhub] HTTP {resp.status_code}: {url}")
    except requests.exceptions.Timeout:
        print(f"[Finnhub] Timeout: {url}")
    except Exception as e:
        print(f"[Finnhub] Error: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  Finnhub PRICE FEED — Cotización en tiempo real XAUUSD via WebSocket
# ══════════════════════════════════════════════════════════════════════════════
try:
    import websocket as _websocket_lib
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False

class FinnhubPriceFeed:
    """
    Obtiene cotización en tiempo real del oro vía Finnhub WebSocket.
    Símbolo principal: OANDA:XAU_USD
    Fallback REST: /quote?symbol=OANDA:XAU_USD
    Segundo fallback: yfinance GC=F
    """
    GOLD_SYMBOL_WS  = "OANDA:XAU_USD"
    GOLD_SYMBOL_REST = "OANDA:XAU_USD"

    def __init__(self, update_interval: int = 1):
        self._lock     = threading.Lock()
        self._data     = {}
        self._running  = True
        self._interval = update_interval
        self._last_ok  = 0
        self._errors   = 0
        self.ws        = None
        self._ws_connected = False

    def start(self):
        if WEBSOCKET_AVAILABLE:
            threading.Thread(target=self._ws_thread, daemon=True, name="FinnhubWS").start()
        else:
            # Fallback a polling REST si websocket no está disponible
            threading.Thread(target=self._rest_loop, daemon=True, name="FinnhubREST").start()

    def _ws_thread(self):
        """Hilo principal del WebSocket — reconecta automáticamente."""
        while self._running:
            try:
                self.ws = _websocket_lib.WebSocketApp(
                    f"{FINNHUB_WS}?token={FINNHUB_API_KEY}",
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close
                )
                self.ws.on_open = self._on_open
                self.ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                print(f"[FinnhubPriceFeed] WS exception: {e}")
            if self._running:
                time.sleep(5)  # Esperar antes de reconectar

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "trade":
                trades = data.get("data", [])
                if trades:
                    last_trade = trades[-1]
                    price = float(last_trade.get("p", 0))
                    vol   = float(last_trade.get("v", 0))
                    if price > 0:
                        with self._lock:
                            prev_price = self._data.get("price", price)
                            prev_close = self._data.get("prev", price)
                            change = price - prev_close if prev_close > 0 else 0.0
                            pct    = (change / prev_close * 100) if prev_close > 0 else 0.0
                            self._data = {
                                "price":  price,
                                "change": round(change, 2),
                                "pct":    round(pct, 4),
                                "high":   max(self._data.get("high", price), price),
                                "low":    min(self._data.get("low", price), price) if self._data.get("low") else price,
                                "volume": self._data.get("volume", 0) + vol,
                                "open":   self._data.get("open", price),
                                "prev":   prev_close,
                                "symbol": self.GOLD_SYMBOL_WS,
                                "ts":     time.time(),
                                "source": "Finnhub/WS",
                            }
                            self._last_ok = time.time()
                            self._errors  = 0
        except Exception as e:
            print(f"[FinnhubPriceFeed] WS parse error: {e}")

    def _on_error(self, ws, error):
        self._errors += 1
        self._ws_connected = False
        print(f"[FinnhubPriceFeed] WS Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self._ws_connected = False
        print("[FinnhubPriceFeed] WS Closed")

    def _on_open(self, ws):
        self._ws_connected = True
        print(f"[FinnhubPriceFeed] WS Conectado — suscribiendo a {self.GOLD_SYMBOL_WS}")
        ws.send(json.dumps({"type": "subscribe", "symbol": self.GOLD_SYMBOL_WS}))

    def _rest_loop(self):
        """Loop de polling REST como fallback."""
        while self._running:
            try:
                self._fetch_rest()
            except Exception as e:
                self._errors += 1
                print(f"[FinnhubPriceFeed] REST error: {e}")
            time.sleep(15)

    def _fetch_rest(self):
        """Obtiene precio via REST API de Finnhub."""
        url  = f"{FINNHUB_BASE}/quote"
        data = _finnhub_get(url, params={"symbol": self.GOLD_SYMBOL_REST}, ttl=15)
        if data and "c" in data and float(data.get("c", 0)) > 0:
            price = float(data["c"])
            with self._lock:
                self._data = {
                    "price":  price,
                    "change": float(data.get("d", 0)),
                    "pct":    float(data.get("dp", 0)),
                    "high":   float(data.get("h", 0)),
                    "low":    float(data.get("l", 0)),
                    "volume": 0,
                    "open":   float(data.get("o", 0)),
                    "prev":   float(data.get("pc", 0)),
                    "symbol": self.GOLD_SYMBOL_REST,
                    "ts":     time.time(),
                    "source": "Finnhub/REST",
                }
                self._last_ok = time.time()
                self._errors  = 0

    def get(self) -> dict:
        with self._lock:
            if not self._data:
                # Si no hay datos, intentar REST inmediatamente
                try:
                    self._fetch_rest()
                except Exception:
                    pass
                return self._data if self._data else {}
            age = time.time() - self._data.get("ts", 0)
            # Si los datos son muy viejos (>60s) y WS no está conectado, hacer REST
            if age > 60 and not self._ws_connected:
                try:
                    self._fetch_rest()
                except Exception:
                    pass
            return {**self._data, "age_s": round(age), "stale": age > 120, "ok": self._errors < 5}

    def stop(self):
        self._running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  Finnhub HISTORICAL DATA — Datos OHLCV intraday y diarios
# ══════════════════════════════════════════════════════════════════════════════
class FinnhubHistoricalData:
    """
    Descarga datos históricos OHLCV para XAUUSD vía Finnhub forex/candle.
    Símbolo principal: OANDA:XAU_USD
    Endpoint: GET /forex/candle?symbol=OANDA:XAU_USD&resolution=5&from=UNIX&to=UNIX
    """
    # Mapa de intervalos al formato Finnhub (resoluciones en minutos o D/W/M)
    INTERVAL_MAP = {
        "M1": "1",  "1min": "1",  "1": "1",
        "M5": "5",  "5min": "5",  "5": "5",
        "M15": "15", "15min": "15", "15": "15",
        "M30": "30", "30min": "30", "30": "30",
        "H1": "60",  "1hour": "60", "60": "60",
        "H4": "D",   "4hour": "D",
        "D1": "D",   "1day": "D",
    }
    # Mapeo de intervalos para yfinance
    YF_INTERVAL_MAP = {
        "1": "1m", "5": "5m", "15": "15m", "30": "30m",
        "60": "60m", "D": "1d",
    }
    YF_PERIOD_MAP = {
        "1": "7d", "5": "60d", "15": "60d", "30": "60d",
        "60": "730d", "D": "max",
    }
    GOLD_SYMBOL = "OANDA:XAU_USD"

    def get_ohlcv(self, interval: str = "5min", limit: int = 300,
                  symbol: str = None) -> pd.DataFrame:
        sym = symbol or self.GOLD_SYMBOL
        res = self.INTERVAL_MAP.get(interval, "5")

        # Calcular rango de tiempo
        to_ts   = int(time.time())
        mins    = int(res) if res not in ("D", "W", "M") else 1440
        from_ts = to_ts - (limit * mins * 60 * 2)  # x2 para asegurar suficientes velas

        url  = f"{FINNHUB_BASE}/forex/candle"
        data = _finnhub_get(url, params={
            "symbol":     sym,
            "resolution": res,
            "from":       from_ts,
            "to":         to_ts,
        }, ttl=30)

        if data and data.get("s") == "ok":
            df = pd.DataFrame({
                "date":   pd.to_datetime(data["t"], unit="s"),
                "open":   data["o"],
                "high":   data["h"],
                "low":    data["l"],
                "close":  data["c"],
                "volume": data.get("v", [0] * len(data["c"])),
            })
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)
            return df.tail(limit)

        # Fallback: yfinance GC=F
        try:
            yf_interval = self.YF_INTERVAL_MAP.get(res, "5m")
            yf_period   = self.YF_PERIOD_MAP.get(res, "60d")
            ticker = yf.Ticker("GC=F")
            df = ticker.history(period=yf_period, interval=yf_interval)
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
                df.index.name = "date"
                return df[["open", "high", "low", "close", "volume"]].dropna().tail(limit)
        except Exception as e:
            print(f"[FinnhubHistoricalData] yfinance fallback error: {e}")

        return pd.DataFrame()

    def get_as_rates_list(self, interval: str = "M5", limit: int = 300) -> list:
        """Retorna lista de dicts compatible con el formato MT5 rates."""
        df = self.get_ohlcv(interval, limit)
        if df.empty:
            return []
        rates = []
        for ts, row in df.iterrows():
            rates.append({
                "time":        ts.timestamp() if hasattr(ts, "timestamp") else float(ts),
                "open":        float(row["open"]),
                "high":        float(row["high"]),
                "low":         float(row["low"]),
                "close":       float(row["close"]),
                "tick_volume": float(row["volume"]) if row["volume"] > 0 else 1.0,
            })
        return rates


# ══════════════════════════════════════════════════════════════════════════════
#  Finnhub ECONOMIC CALENDAR — Calendario de eventos macro
# ══════════════════════════════════════════════════════════════════════════════
class FinnhubEconomicCalendar:
    """Descarga el calendario económico de Finnhub con eventos de alto impacto."""
    HIGH_IMPACT_GOLD = [
        "non-farm", "nfp", "fomc", "fed", "cpi", "pce", "gdp", "ism",
        "interest rate", "powell", "yellen", "ecb", "treasury", "inflation",
        "rate decision", "employment", "unemployment", "payrolls", "jobs",
        "consumer price", "producer price", "retail sales", "durable goods",
    ]
    RELEVANT_COUNTRIES = {"US", "EU", "UK", "CN", "JP", "DE", "FR"}

    def __init__(self, update_interval: int = 900):
        self._lock     = threading.Lock()
        self._events   = []
        self._running  = True
        self._interval = update_interval
        self._last_ok  = 0

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="FinnhubCalendar").start()

    def _loop(self):
        self._fetch()
        while self._running:
            time.sleep(self._interval)
            try:
                self._fetch()
            except Exception as e:
                print(f"[FinnhubCalendar] {e}")

    def _fetch(self):
        today    = datetime.datetime.utcnow()
        tomorrow = today + datetime.timedelta(days=2)
        # Finnhub Economic Calendar: GET /calendar/economic?from=YYYY-MM-DD&to=YYYY-MM-DD
        url      = f"{FINNHUB_BASE}/calendar/economic"
        params   = {"from": today.strftime("%Y-%m-%d"), "to": tomorrow.strftime("%Y-%m-%d")}
        data     = _finnhub_get(url, params=params, ttl=600)
        # Finnhub retorna {"economicCalendar": [...]}
        if data and isinstance(data, dict):
            data = data.get("economicCalendar", [])
        if not data or not isinstance(data, list):
            # Fallback si Finnhub falla (ej. 401 o sin datos)
            self._fallback_fetch()
            return
        events = []
        for ev in data:
            country = str(ev.get("country", "")).upper()
            if country not in self.RELEVANT_COUNTRIES:
                continue
            title  = str(ev.get("event", "")).lower()
            impact = str(ev.get("impact", "")).upper()
            is_gold_relevant = any(kw in title for kw in self.HIGH_IMPACT_GOLD)
            if impact not in ("HIGH", "MEDIUM") and not is_gold_relevant:
                continue
            # Finnhub usa campo 'time' (ISO string) en lugar de 'date'
            dt_str = ev.get("time", ev.get("date", ""))
            raw_dt = None
            try:
                raw_dt = datetime.datetime.strptime(dt_str[:16], "%Y-%m-%d %H:%M")
            except:
                try:
                    raw_dt = datetime.datetime.strptime(dt_str[:10], "%Y-%m-%d")
                except:
                    pass
            events.append({
                "title":    ev.get("event", ""),
                "country":  country,
                "impact":   impact,
                "date":     dt_str,
                "raw_dt":   raw_dt,
                "actual":   ev.get("actual"),
                "estimate": ev.get("estimate"),
                "previous": ev.get("previous"),
                "gold_rel": is_gold_relevant,
            })
        with self._lock:
            self._events  = events
            self._last_ok = time.time()

    def _fallback_fetch(self):
        """Fallback simple si Finnhub falla, usando datos estáticos o yfinance si es posible."""
        # En un entorno real, aquí se podría hacer scraping de ForexFactory o similar.
        # Por ahora, insertamos un evento dummy para evitar que el sistema se rompa.
        now = datetime.datetime.utcnow()
        dummy_event = {
            "title":    "Fallback Event (Finnhub API Error)",
            "country":  "US",
            "impact":   "MEDIUM",
            "date":     now.strftime("%Y-%m-%d %H:%M"),
            "raw_dt":   now + datetime.timedelta(hours=2),
            "actual":   None,
            "estimate": None,
            "previous": None,
            "gold_rel": False,
        }
        with self._lock:
            self._events = [dummy_event]
            self._last_ok = time.time()

    def get_events(self) -> list:
        with self._lock:
            return list(self._events)

    def get_next_high_impact(self) -> dict:
        now = datetime.datetime.utcnow()
        with self._lock:
            candidates = [e for e in self._events
                          if e.get("raw_dt") and e["raw_dt"] > now
                          and (e["impact"] == "HIGH" or e.get("gold_rel"))]
        if not candidates:
            return {}
        candidates.sort(key=lambda e: e["raw_dt"])
        nxt = candidates[0]
        diff_min = (nxt["raw_dt"] - now).total_seconds() / 60
        return {**nxt, "minutes_away": round(diff_min)}

    def is_high_impact_now(self, window_min: int = 30) -> bool:
        now = datetime.datetime.utcnow()
        with self._lock:
            for e in self._events:
                if e.get("raw_dt") is None:
                    continue
                diff = (e["raw_dt"] - now).total_seconds() / 60
                if -5 <= diff <= window_min and e["impact"] == "HIGH":
                    return True
        return False

    def get_impact_score(self) -> float:
        if self.is_high_impact_now(30):
            return 0.0
        if self.is_high_impact_now(60):
            return 0.5
        if self.is_high_impact_now(120):
            return 0.8
        return 1.0

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  Finnhub COT ENGINE — Commitment of Traders vía Finnhub
# ══════════════════════════════════════════════════════════════════════════════
class FinnhubCOTEngine:
    """Descarga datos COT del oro vía Finnhub. Endpoint: /stable/commitment-of-traders-report"""
    def __init__(self, update_interval: int = 3600):
        self._lock     = threading.Lock()
        self._data     = {
            "net_institutional": 0.0, "long_pct": 50.0, "short_pct": 50.0,
            "net_change": 0.0, "bias": "NEUTRAL", "date": None,
            "source": "Finnhub/COT", "stale": True, "open_interest": 0,
        }
        self._running  = True
        self._interval = update_interval
        self._last_ok  = 0

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="FinnhubCOTEngine").start()

    def _loop(self):
        self._fetch()
        while self._running:
            time.sleep(self._interval)
            try:
                self._fetch()
            except Exception as e:
                print(f"[FinnhubCOTEngine] {e}")

    def _fetch(self):
        """
        Obtiene datos COT reales de la CFTC y analiza el Hedge Institucional.
        Compara posiciones de 'Commercials' (Hedgers) vs 'Non-Commercials' (Speculators).
        """
        cftc_url = "https://www.cftc.gov/dea/futures/deacmxlf.htm"
        try:
            response = requests.get(cftc_url, timeout=FINNHUB_TIMEOUT)
            response.raise_for_status()
            text = response.text
            
            # Buscar el bloque de GOLD - COMEX
            if "GOLD - COMMODITY EXCHANGE INC." in text:
                block = text.split("GOLD - COMMODITY EXCHANGE INC.")[1].split("-----------------------------------------------------------------------")[0]
                
                # Extraer posiciones Non-Commercial (Speculators)
                # Formato típico: Long | Short | Spreading
                non_comm_match = re.search(r"NON-COMMERCIAL\s+([\d,]+)\s+([\d,]+)", block)
                comm_match = re.search(r"COMMERCIAL\s+([\d,]+)\s+([\d,]+)", block)
                oi_match = re.search(r"OPEN INTEREST:\s+([\d,]+)", block)
                
                if non_comm_match and comm_match and oi_match:
                    spec_long = float(non_comm_match.group(1).replace(",", ""))
                    spec_short = float(non_comm_match.group(2).replace(",", ""))
                    comm_long = float(comm_match.group(1).replace(",", ""))
                    comm_short = float(comm_match.group(2).replace(",", ""))
                    oi = float(oi_match.group(1).replace(",", ""))
                    
                    # Análisis de Hedge Institucional:
                    # Los comerciales suelen estar en contra de la tendencia (hedging).
                    # Si los comerciales están muy cortos, confirma tendencia alcista de especuladores.
                    spec_net = spec_long - spec_short
                    comm_net = comm_long - comm_short
                    
                    # Score de Hedge: -1 a 1 (1 = Hedge institucional fuerte a favor de compra)
                    hedge_score = -comm_net / (comm_long + comm_short) if (comm_long + comm_short) > 0 else 0
                    
                    total_spec = max(spec_long + spec_short, 1.0)
                    net_pct = (spec_net / total_spec) * 100
                    
                    if net_pct > 20 and hedge_score > 0.3: bias = "STRONG_BULLISH"
                    elif net_pct > 10: bias = "BULLISH"
                    elif net_pct < -20 and hedge_score < -0.3: bias = "STRONG_BEARISH"
                    elif net_pct < -10: bias = "BEARISH"
                    else: bias = "NEUTRAL"
                    
                    with self._lock:
                        self._data = {
                            "net_institutional": round(net_pct, 2),
                            "hedge_score":       round(hedge_score, 2),
                            "bias":              bias,
                            "open_interest":     int(oi),
                            "spec_long":         int(spec_long),
                            "spec_short":        int(spec_short),
                            "comm_net":          int(comm_net),
                            "source":            "CFTC/Direct_Hedge",
                            "date":              datetime.datetime.utcnow().strftime("%Y-%m-%d"),
                            "stale":             False
                        }
                        self._last_ok = time.time()
                    return
            print(f"[FinnhubCOTEngine] No se pudo parsear el bloque de ORO en CFTC.")

        except requests.exceptions.RequestException as e:
            print(f"[FinnhubCOTEngine] Error al acceder a CFTC: {e}")
        except Exception as e:
            print(f"[FinnhubCOTEngine] Error al parsear CFTC: {e}")

        # Fallback a yfinance para datos COT aproximados (ej. GLD ETF flows)
        try:
            ticker = yf.Ticker("GLD")
            hist = ticker.history(period="5d", interval="1d")
            if not hist.empty:
                latest_close = hist["Close"].iloc[-1]
                prev_close = hist["Close"].iloc[-2] if len(hist) > 1 else latest_close
                change_pct = (latest_close - prev_close) / prev_close * 100 if prev_close != 0 else 0
                
                # Usar el cambio porcentual de GLD como proxy para el sentimiento COT
                net_inst = change_pct * 5 # Escalar para que sea comparable
                if net_inst > 15:   bias = "BULLISH"
                elif net_inst < -15: bias = "BEARISH"
                elif net_inst > 5:  bias = "MILD_BULLISH"
                elif net_inst < -5: bias = "MILD_BEARISH"
                else:              bias = "NEUTRAL"

                with self._lock:
                    self._data = {
                        "net_institutional": round(net_inst, 2),
                        "long_pct":          50.0 + net_inst/2, # Aproximación
                        "short_pct":         50.0 - net_inst/2, # Aproximación
                        "net_change":        round(change_pct, 2),
                        "bias":              bias,
                        "date":              datetime.datetime.utcnow().strftime("%Y-%m-%d"),
                        "source":            "yfinance/GLD_proxy",
                        "stale":             False,
                        "open_interest":     0,
                        "raw_longs":         0,
                        "raw_shorts":        0,
                    }
                    self._last_ok = time.time()
                    print(f"[FinnhubCOTEngine] Usando yfinance GLD proxy para COT: {bias}")
                    return
        except Exception as e:
            print(f"[FinnhubCOTEngine] Error en yfinance GLD proxy: {e}")

        # Si todo falla, mantener datos por defecto
        with self._lock:
            self._data["stale"] = True
            print("[FinnhubCOTEngine] Fallo al obtener datos COT de todas las fuentes.")
        return

    def get(self) -> dict:
        with self._lock:
            d = dict(self._data)
            d["stale"] = (time.time() - self._last_ok) > self._interval * 2
            return d

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  Finnhub MACRO ENGINE — Datos macro en tiempo real vía Finnhub
# ══════════════════════════════════════════════════════════════════════════════
class FinnhubMacroEngine:
    """
    Obtiene datos macro en tiempo real vía Finnhub:
    DXY (UUP), VIX (^VIX), Yields 10Y (^TNX), SPY, TLT, GLD.
    Genera sesgo direccional para el oro.
    """
    def __init__(self, calendar=None, update_interval: int = 300):
        self._lock     = threading.Lock()
        self._state    = {
            "bias": "NEUTRAL", "strength": "NEUTRAL", "regime": "NEUTRAL",
            "score_macro": 20, "details": {}, "transition": False,
            "last_update": 0, "bull_pts": 0, "bear_pts": 0,
            "dxy": None, "vix": None, "yield_10y": None, "calendar_score": 1.0,
        }
        self._running  = True
        self._interval = update_interval
        self._calendar = calendar
        self._last_ok  = 0

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="FinnhubMacroEngine").start()

    def _loop(self):
        self._update()
        while self._running:
            time.sleep(self._interval)
            try:
                self._update()
            except Exception as e:
                print(f"[FinnhubMacroEngine] {e}")

    def _q(self, sym):
        """Obtiene precio y cambio % de un símbolo vía Finnhub Quote API."""
        url  = f"{FINNHUB_BASE}/quote"
        data = _finnhub_get(url, params={"symbol": sym}, ttl=60)
        # Finnhub Quote: {c: current, d: change, dp: change%, h: high, l: low, o: open, pc: prev_close}
        if data and "c" in data and float(data.get("c", 0)) > 0:
            return (float(data.get("c", 0)), float(data.get("dp", 0)))
        return (0.0, 0.0)

    def _update(self):
        bull_pts = 0; bear_pts = 0; details = {}
        dxy_val = vix_val = tnx_val = None

        try:
            dxy_val, dxy_chg = self._q("UUP")
            if dxy_chg > 0.3:   bear_pts += 2; details["dxy"] = f"BEARISH(DXY↑ {dxy_chg:+.2f}%)"
            elif dxy_chg < -0.3: bull_pts += 2; details["dxy"] = f"BULLISH(DXY↓ {dxy_chg:+.2f}%)"
            else:                details["dxy"] = f"NEUTRAL(DXY {dxy_chg:+.2f}%)"
        except: details["dxy"] = "N/A"

        try:
            vix_val, vix_chg = self._q("^VIX")
            if vix_val > 25:    bull_pts += 3; details["vix"] = f"BULLISH(VIX={vix_val:.1f} RISK-OFF)"
            elif vix_val > 20:  bull_pts += 2; details["vix"] = f"MILD_BULL(VIX={vix_val:.1f}↑)"
            elif vix_val < 14:  bear_pts += 1; details["vix"] = f"BEARISH(VIX={vix_val:.1f} RISK-ON)"
            else:               details["vix"] = f"NEUTRAL(VIX={vix_val:.1f})"
        except: details["vix"] = "N/A"

        try:
            tnx_val, tnx_chg = self._q("^TNX")
            if tnx_chg < -2.0:  bull_pts += 2; details["yields"] = f"BULLISH(Yield↓ {tnx_chg:+.2f}%)"
            elif tnx_chg > 2.0: bear_pts += 2; details["yields"] = f"BEARISH(Yield↑ {tnx_chg:+.2f}%)"
            elif tnx_val > 4.5: bear_pts += 1; details["yields"] = f"MILD_BEAR(Yield={tnx_val:.2f}%)"
            else:               details["yields"] = f"NEUTRAL(Yield={tnx_val:.2f}%)"
        except: details["yields"] = "N/A"

        try:
            spy_p, spy_chg = self._q("SPY")
            if spy_chg < -1.5:  bull_pts += 2; details["equities"] = f"BULLISH(SPY {spy_chg:+.2f}%)"
            elif spy_chg > 0.8: bear_pts += 1; details["equities"] = f"BEARISH(SPY +{spy_chg:.2f}%)"
            else:               details["equities"] = f"NEUTRAL(SPY {spy_chg:+.2f}%)"
        except: details["equities"] = "N/A"

        try:
            tlt_p, tlt_chg = self._q("TLT")
            if tlt_chg > 0.5:   bull_pts += 1; details["bonds"] = f"BULLISH(TLT↑ {tlt_chg:+.2f}%)"
            elif tlt_chg < -0.5: bear_pts += 1; details["bonds"] = f"BEARISH(TLT↓ {tlt_chg:+.2f}%)"
            else:               details["bonds"] = f"NEUTRAL(TLT {tlt_chg:+.2f}%)"
        except: details["bonds"] = "N/A"

        try:
            gld_p, gld_chg = self._q("GLD")
            if gld_chg > 0.5:   bull_pts += 1; details["gld_etf"] = f"BULLISH(GLD↑ {gld_chg:+.2f}%)"
            elif gld_chg < -0.5: bear_pts += 1; details["gld_etf"] = f"BEARISH(GLD↓ {gld_chg:+.2f}%)"
            else:               details["gld_etf"] = f"NEUTRAL(GLD {gld_chg:+.2f}%)"
        except: details["gld_etf"] = "N/A"

        cal_score = 1.0
        if self._calendar:
            cal_score = self._calendar.get_impact_score()
            if cal_score < 0.5:  details["calendar"] = "⚡ ALTO IMPACTO — NO OPERAR"
            elif cal_score < 1.0: details["calendar"] = "⚠ IMPACTO PRÓXIMO — REDUCIR"
            else:                details["calendar"] = "✓ Sin eventos críticos"

        net = bull_pts - bear_pts
        if net >= 5:    bias = "BULLISH"; strength = "STRONG"
        elif net >= 3:  bias = "BULLISH"; strength = "MODERATE"
        elif net >= 1:  bias = "BULLISH"; strength = "WEAK"
        elif net <= -5: bias = "BEARISH"; strength = "STRONG"
        elif net <= -3: bias = "BEARISH"; strength = "MODERATE"
        elif net <= -1: bias = "BEARISH"; strength = "WEAK"
        else:           bias = "NEUTRAL"; strength = "NEUTRAL"

        if bias == "BULLISH" and strength in ("MODERATE", "STRONG"):
            regime = "RISK_OFF"
        elif bias == "BEARISH" and strength in ("MODERATE", "STRONG"):
            regime = "RISK_ON"
        elif strength == "WEAK":
            regime = "TRANSITION"
        else:
            regime = "NEUTRAL"

        raw_score   = min(10, bull_pts if bias == "BULLISH" else bear_pts)
        score_macro = int(raw_score / 10 * 25 * cal_score)

        with self._lock:
            self._state = {
                "bias":          bias,
                "strength":      strength,
                "regime":        regime,
                "score_macro":   score_macro,
                "details":       details,
                "transition":    regime == "TRANSITION",
                "last_update":   time.time(),
                "bull_pts":      bull_pts,
                "bear_pts":      bear_pts,
                "dxy":           dxy_val,
                "vix":           vix_val,
                "yield_10y":     tnx_val,
                "calendar_score": cal_score,
                "source":        "Finnhub",
            }
            self._last_ok = time.time()

    def get(self) -> dict:
        with self._lock:
            return dict(self._state)

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  Finnhub NEWS ENGINE — Noticias forex/gold vía Finnhub
# ══════════════════════════════════════════════════════════════════════════════
class FinnhubNewsEngine:
    """Obtiene noticias relevantes para el oro vía Finnhub con análisis de sentimiento."""
    GOLD_BULL_KW = [
        "rise", "surge", "gain", "rally", "climb", "soar", "jump", "higher", "bullish",
        "strong", "demand", "safe haven", "inflation", "war", "crisis", "uncertainty",
        "geopolitical", "fed pause", "rate cut", "dollar weak", "usd falls", "buying",
        "record", "high", "haven", "sanctions", "conflict", "recession", "fear", "panic",
        "risk off", "dovish", "stimulus", "easing", "gold up", "xauusd up", "gold rally",
        "precious metals", "gold demand", "central bank buying", "gold reserves"
    ]
    GOLD_BEAR_KW = [
        "fall", "drop", "decline", "slide", "tumble", "plunge", "lower", "bearish", "weak",
        "sell", "rate hike", "dollar strong", "usd rises", "hawkish", "taper", "pressure",
        "outflows", "risk on", "equities up", "yields rise", "gold down", "xauusd down",
        "sell off", "correction", "profit taking", "strong dollar", "gold falls"
    ]
    HIGH_IMPACT_KW = [
        "nfp", "non-farm", "fomc", "fed", "cpi", "pce", "gdp", "ism", "jobs report",
        "interest rate", "powell", "yellen", "ecb", "treasury", "inflation data",
        "rate decision", "fed meeting", "employment", "unemployment", "payrolls"
    ]

    def __init__(self, update_interval: int = 120):
        self._lock      = threading.Lock()
        self._headlines = []
        self._sentiment = "NEUTRAL"
        self._score     = 0
        self._impact    = False
        self._running   = True
        self._interval  = update_interval
        self._last_ok   = 0
        self._finnhub_ok    = False

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="FinnhubNewsEngine").start()

    def _loop(self):
        self._fetch()
        while self._running:
            time.sleep(self._interval)
            try:
                self._fetch()
            except Exception as e:
                print(f"[FinnhubNewsEngine] {e}")

    def _score_text(self, text: str) -> tuple:
        t    = text.lower()
        bull = sum(1 for w in self.GOLD_BULL_KW if w in t)
        bear = sum(1 for w in self.GOLD_BEAR_KW if w in t)
        hi   = any(w in t for w in self.HIGH_IMPACT_KW)
        return bull - bear, hi

    def _fetch(self):
        headlines   = []
        total_score = 0
        has_impact  = False

        # Noticias generales de mercado forex
        url  = f"{FINNHUB_BASE}/news"
        data = _finnhub_get(url, params={"category": "forex"}, ttl=120)
        if data and isinstance(data, list):
            self._finnhub_ok = True
            for item in data[:15]:
                title = str(item.get("headline", ""))
                body  = str(item.get("summary", ""))
                sc, hi = self._score_text(title + " " + body)
                total_score += sc
                if hi: has_impact = True
                ts = item.get("datetime", 0)
                pub_date = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
                headlines.append({
                    "title":  title[:100], "score": sc, "impact": hi,
                    "pub":    pub_date,
                    "source": "Finnhub/Forex", "url": item.get("url", ""),
                })
        else:
            self._finnhub_ok = False # Marcar como fallido si la primera llamada falla

        # Noticias de GLD como proxy de oro
        url2  = f"{FINNHUB_BASE}/company-news"
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        last_week = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        data2 = _finnhub_get(url2, params={"symbol": "GLD", "from": last_week, "to": today}, ttl=120)
        if data2 and isinstance(data2, list):
            self._finnhub_ok = True # Si esta tiene éxito, marcar como OK
            for item in data2[:10]:
                title = str(item.get("headline", ""))
                body  = str(item.get("summary", ""))
                sc, hi = self._score_text(title + " " + body)
                total_score += sc
                if hi: has_impact = True
                ts = item.get("datetime", 0)
                pub_date = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
                headlines.append({
                    "title":  title[:100], "score": sc, "impact": hi,
                    "pub":    pub_date,
                    "source": "Finnhub/GLD", "url": item.get("url", ""),
                })

        if total_score >= 3:   sentiment = "BULLISH"
        elif total_score <= -3: sentiment = "BEARISH"
        elif total_score >= 1: sentiment = "MILD_BULLISH"
        elif total_score <= -1: sentiment = "MILD_BEARISH"
        else:                  sentiment = "NEUTRAL"

        with self._lock:
            self._headlines = headlines
            self._sentiment = sentiment
            self._score     = total_score
            self._impact    = has_impact
            self._last_ok   = time.time()

        if not self._finnhub_ok and not headlines: # Si Finnhub falló y no hay headlines
            self._fallback_fetch()

    def _fallback_fetch(self):
        """Fallback simple si Finnhub News falla, usando datos estáticos."""
        now = datetime.datetime.utcnow()
        dummy_headline = {
            "title":    "Noticia de Fallback (Finnhub API Error)",
            "score":    0,
            "impact":   False,
            "pub":      now.strftime("%Y-%m-%d %H:%M"),
            "source":   "Fallback/Dummy",
            "url":      "#",
        }
        with self._lock:
            self._headlines = [dummy_headline]
            self._sentiment = "NEUTRAL"
            self._score     = 0
            self._impact    = False
            self._last_ok   = time.time()

    def get_state(self) -> dict:
        with self._lock:
            return {
                "headlines":  list(self._headlines),
                "sentiment":  self._sentiment,
                "score":      self._score,
                "impact":     self._impact,
                "finnhub_ok":     self._finnhub_ok,
                "last_fetch": self._last_ok,
            }

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  Finnhub TECHNICAL INDICATORS — Indicadores técnicos vía Finnhub
# ══════════════════════════════════════════════════════════════════════════════
class FinnhubTechnicalIndicators:
    """
    Indicadores técnicos calculados LOCALMENTE para evitar endpoints Premium de Finnhub.
    Usa FinnhubHistoricalData para obtener los precios.
    """
    def __init__(self, hist_engine=None):
        self.hist = hist_engine or FinnhubHistoricalData()

    def _get_df(self, symbol, interval, limit=300):
        return self.hist.get_ohlcv(interval=interval, limit=limit, symbol=symbol)

    def get_rsi(self, symbol: str = None, period: int = 14, interval: str = "5") -> float:
        """RSI con Wilder's Smoothing (estándar institucional)."""
        df = self._get_df(symbol, interval, limit=period*10)
        if df is None or len(df) < period: return 50.0
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not rsi.empty else 50.0

    def get_ema(self, symbol: str = None, period: int = 20, interval: str = "5") -> float:
        df = self._get_df(symbol, interval, limit=period*5)
        if df is None or df.empty: return 0.0
        ema = df['close'].ewm(span=period, adjust=False).mean()
        return float(ema.iloc[-1]) if not ema.empty else 0.0

    def get_macd(self, symbol: str = None, interval: str = "5") -> dict:
        df = self._get_df(symbol, interval, limit=100)
        if df is None or df.empty: return {"macd": 0.0, "signal": 0.0, "hist": 0.0, "trend": "NEUTRAL"}
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        m = float(macd.iloc[-1]); s = float(signal.iloc[-1]); h = float(hist.iloc[-1])
        return {"macd": m, "signal": s, "hist": h, "trend": "BULLISH" if h > 0 else "BEARISH"}

    def get_bollinger(self, symbol: str = None, period: int = 20, interval: str = "5") -> dict:
        df = self._get_df(symbol, interval, limit=period*5)
        if df is None or df.empty: return {"upper": 0.0, "mid": 0.0, "lower": 0.0}
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper = sma + (std * 2)
        lower = sma - (std * 2)
        return {"upper": float(upper.iloc[-1]), "mid": float(sma.iloc[-1]), "lower": float(lower.iloc[-1])}

    def get_adx(self, symbol: str = None, period: int = 14, interval: str = "5") -> dict:
        """ADX con Wilder's Smoothing (estándar institucional)."""
        df = self._get_df(symbol, interval, limit=period*10)
        if df is None or len(df) < period: return {"adx": 0.0, "trend": "UNKNOWN"}
        high, low, close = df['high'], df['low'], df['close']
        plus_dm = high.diff()
        minus_dm = low.diff()
        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = pd.Series(tr).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        plus_di = 100 * (pd.Series(plus_dm).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
        adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        adx_val = float(adx.iloc[-1]) if not adx.empty else 0.0
        return {"adx": adx_val, "trend": "STRONG" if adx_val > 25 else "WEAK" if adx_val < 15 else "MODERATE"}

    def get_full_analysis(self, symbol: str = None, interval: str = "5") -> dict:
        return {
            "rsi":       self.get_rsi(symbol, 14, interval),
            "rsi_slow":  self.get_rsi(symbol, 21, interval),
            "ema_fast":  self.get_ema(symbol, 9,  interval),
            "ema_slow":  self.get_ema(symbol, 21, interval),
            "ema_200":   self.get_ema(symbol, 200, "D"),
            "macd":      self.get_macd(symbol, interval),
            "bollinger": self.get_bollinger(symbol, 20, interval),
            "adx":       self.get_adx(symbol, 14, interval),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  Finnhub STRATEGY ENGINE — Estrategia de trading real integrada v10.0
# ══════════════════════════════════════════════════════════════════════════════
class FinnhubStrategyEngine:
    """
    Estrategia de trading real que combina TODAS las herramientas Finnhub + técnicas:

    ENTRADAS (confluencia mínima de 4 factores):
    1. MACRO Finnhub: sesgo macro (DXY, VIX, Yields, SPY, GLD)
    2. COT Finnhub: posicionamiento institucional neto
    3. TÉCNICO: Z-score VWAP + BB + RSI + MACD + ADX
    4. ZONAS: precio en zona demand/supply activa
    5. CALENDARIO: sin eventos de alto impacto próximos
    6. SENTIMIENTO: noticias Finnhub + RSS

    GESTIÓN DE RIESGO:
    - Stop loss: 1.5x ATR dinámico
    - TP1: 1.5 RR (zona POC o EMA)
    - TP2: 2.5 RR (zona VAH/VAL o Fibonacci)
    - Tamaño: Kelly fraccionado ajustado por score
    - Máximo 0.5% de cuenta por trade
    """
    MIN_SCORE_TRADE = 65
    MIN_CONFLUENCES = 4
    MAX_RISK_PCT    = 0.005
    RR_TP1          = 1.5
    RR_TP2          = 2.5

    def __init__(self):
        self._last_signal  = "NEUTRAL"
        self._last_score   = 0
        self._signal_count = 0

    def evaluate(self, macro_state: dict, cot_data: dict, tech_signal: dict,
                 mr_state: dict, news_finnhub: dict, news_legacy: dict,
                 liq_state: dict, reaction_zones: list, price: float,
                 atr: float, calendar=None, finnhub_tech: dict = None) -> dict:
        result = {
            "signal": "NEUTRAL", "score": 0, "confluences": 0, "conditions": {},
            "entry": price, "sl": None, "tp1": None, "tp2": None,
            "size_factor": 0.0, "confidence": "NO_TRADE", "reason": "",
            "finnhub_enhanced": True,
        }

        # ── BLOQUEOS ABSOLUTOS ─────────────────────────────────────────────────
        if calendar and calendar.is_high_impact_now(30):
            result["reason"] = "BLOQUEADO: Evento alto impacto próximo"
            return result
        if news_legacy and news_legacy.get("impact"):
            result["reason"] = "BLOQUEADO: Noticias alto impacto activas"
            return result
        if liq_state and not liq_state.get("can_enter", True):
            result["reason"] = "BLOQUEADO: Liquidez insuficiente"
            return result

        # ── EVALUACIÓN DIRECCIONAL ─────────────────────────────────────────────
        macro_bias = macro_state.get("bias", "NEUTRAL")
        cot_state  = cot_data # Alias para compatibilidad con el bloque de scoring
        cot_bias   = cot_state.get("bias", "NEUTRAL")
        tech_dir   = tech_signal.get("dir", "NEUTRAL")
        mr_sig     = mr_state.get("signal", "NEUTRAL") if mr_state else "NEUTRAL"
        news_sent  = news_finnhub.get("sentiment", "NEUTRAL") if news_finnhub else "NEUTRAL"

        long_votes = short_votes = 0
        if macro_bias in ("BULLISH", "MILD_BULLISH"): long_votes += 1
        elif macro_bias in ("BEARISH", "MILD_BEARISH"): short_votes += 1
        if cot_bias in ("BULLISH", "MILD_BULLISH"): long_votes += 1
        elif cot_bias in ("BEARISH", "MILD_BEARISH"): short_votes += 1
        if tech_dir == "LONG": long_votes += 1
        elif tech_dir == "SHORT": short_votes += 1
        if mr_sig == "MR_LONG": long_votes += 1
        elif mr_sig == "MR_SHORT": short_votes += 1
        if news_sent in ("BULLISH", "MILD_BULLISH"): long_votes += 1
        elif news_sent in ("BEARISH", "MILD_BEARISH"): short_votes += 1

        if long_votes > short_votes and long_votes >= 2:
            direction = "LONG"
        elif short_votes > long_votes and short_votes >= 2:
            direction = "SHORT"
        else:
            result["reason"] = f"Sin consenso: LONG={long_votes} SHORT={short_votes}"
            return result

        # ── SCORING DETALLADO ──────────────────────────────────────────────────
        conditions = {}; score = 0; confluences = 0

        # 1. MACRO (0-25 pts)
        macro_str = macro_state.get("strength", "NEUTRAL")
        macro_pts = {"STRONG": 25, "MODERATE": 18, "WEAK": 10, "NEUTRAL": 5}.get(macro_str, 5)
        if (direction == "LONG" and macro_bias in ("BULLISH", "MILD_BULLISH")) or            (direction == "SHORT" and macro_bias in ("BEARISH", "MILD_BEARISH")):
            score += macro_pts; conditions["macro_aligned"] = True; confluences += 1
        else:
            score += macro_pts // 3; conditions["macro_aligned"] = False

        # 2. COT & HEDGE INSTITUCIONAL (0-15 pts)
        cot_net = cot_state.get("net_institutional", 0)
        hedge_score = cot_state.get("hedge_score", 0)
        cot_pts = 15
        # Alineación con especuladores + confirmación de hedge institucional
        if direction == "LONG":
            if cot_net > 5 and hedge_score > 0.1:
                score += cot_pts; conditions["cot_aligned"] = True; confluences += 1
            elif cot_net > 0:
                score += cot_pts // 2; conditions["cot_aligned"] = "WEAK"
            else:
                conditions["cot_aligned"] = False
        elif direction == "SHORT":
            if cot_net < -5 and hedge_score < -0.1:
                score += cot_pts; conditions["cot_aligned"] = True; confluences += 1
            elif cot_net < 0:
                score += cot_pts // 2; conditions["cot_aligned"] = "WEAK"
            else:
                conditions["cot_aligned"] = False

        # 3. TÉCNICO (0-30 pts)
        tech_score_raw = tech_signal.get("score", 0)
        tech_max       = max(tech_signal.get("max_score", 6), 1)
        tech_pts       = int((tech_score_raw / tech_max) * 30)
        score += tech_pts
        if tech_pts >= 15: conditions["tech_strong"] = True; confluences += 1
        else: conditions["tech_strong"] = False

        # Finnhub Technical Enhancement
        if finnhub_tech:
            rsi_val   = finnhub_tech.get("rsi", 50)
            adx_val   = finnhub_tech.get("adx", {}).get("adx", 0)
            macd_hist = finnhub_tech.get("macd", {}).get("hist", 0)
            bb        = finnhub_tech.get("bollinger", {})
            bb_upper  = bb.get("upper", 0)
            bb_lower  = bb.get("lower", 0)
            finnhub_bonus = 0
            if direction == "LONG":
                if rsi_val < 35:  finnhub_bonus += 3; conditions["finnhub_rsi_oversold"] = True
                if macd_hist > 0: finnhub_bonus += 2; conditions["finnhub_macd_bull"] = True
                if bb_lower > 0 and price <= bb_lower * 1.002:
                    finnhub_bonus += 3; conditions["finnhub_bb_lower"] = True; confluences += 1
            elif direction == "SHORT":
                if rsi_val > 65:  finnhub_bonus += 3; conditions["finnhub_rsi_overbought"] = True
                if macd_hist < 0: finnhub_bonus += 2; conditions["finnhub_macd_bear"] = True
                if bb_upper > 0 and price >= bb_upper * 0.998:
                    finnhub_bonus += 3; conditions["finnhub_bb_upper"] = True; confluences += 1
            if adx_val > 25: finnhub_bonus += 2; conditions["finnhub_adx_strong"] = True
            score = min(score + finnhub_bonus, 100)

        # 4. ZONAS DEMAND/SUPPLY (0-15 pts)
        zone_pts = 0
        if reaction_zones and atr > 0:
            for zone in reaction_zones:
                if not zone.get("active"): continue
                z_mid = zone.get("mid", 0)
                dist  = abs(price - z_mid) / atr
                if direction == "LONG" and zone.get("type") == "DEMAND" and dist < 1.5:
                    zone_pts = max(zone_pts, int(15 * (1 - dist / 1.5)))
                    conditions["in_demand_zone"] = True; confluences += 1; break
                elif direction == "SHORT" and zone.get("type") == "SUPPLY" and dist < 1.5:
                    zone_pts = max(zone_pts, int(15 * (1 - dist / 1.5)))
                    conditions["in_supply_zone"] = True; confluences += 1; break
        score += zone_pts

        # 5. SENTIMIENTO NOTICIAS Finnhub (0-10 pts)
        news_score_finnhub = news_finnhub.get("score", 0) if news_finnhub else 0
        if direction == "LONG" and news_score_finnhub > 0:
            news_pts = min(10, news_score_finnhub * 2); conditions["news_bullish"] = True; confluences += 1
        elif direction == "SHORT" and news_score_finnhub < 0:
            news_pts = min(10, abs(news_score_finnhub) * 2); conditions["news_bearish"] = True; confluences += 1
        else:
            news_pts = 3
        score += news_pts

        # 6. LIQUIDEZ (0-5 pts)
        liq_level = liq_state.get("level", "LOW") if liq_state else "LOW"
        liq_pts   = {"HIGH": 5, "NORMAL": 3, "LOW": 0}.get(liq_level, 0)
        score += liq_pts
        if liq_pts >= 3: conditions["liquidity_ok"] = True

        score = max(0, min(100, score))
        result["score"]       = score
        result["confluences"] = confluences
        result["conditions"]  = conditions

        # ── FILTRO ANTI-OVERFITTING (Z-Score & Volatilidad) ───────────────────
        # Evitar señales en mercados laterales o sin momentum real
        if finnhub_tech:
            adx_val = finnhub_tech.get("adx", {}).get("adx", 0)
            if adx_val < 15: # Mercado sin tendencia clara
                score -= 10
                conditions["low_trend_penalty"] = True
        
        # ── DECISIÓN FINAL ─────────────────────────────────────────────────────
        if score < self.MIN_SCORE_TRADE:
            result["reason"] = f"Score insuficiente: {score}/100 (mín {self.MIN_SCORE_TRADE})"
            return result
        if confluences < self.MIN_CONFLUENCES:
            result["reason"] = f"Confluencias insuficientes: {confluences} (mín {self.MIN_CONFLUENCES})"
            return result

        # ── NIVELES DE ENTRADA ─────────────────────────────────────────────────
        if atr <= 0: atr = 1.5
        stop_dist = atr * 1.5
        if direction == "LONG":
            sl  = price - stop_dist
            tp1 = price + stop_dist * self.RR_TP1
            tp2 = price + stop_dist * self.RR_TP2
        else:
            sl  = price + stop_dist
            tp1 = price - stop_dist * self.RR_TP1
            tp2 = price - stop_dist * self.RR_TP2

        # Ajustar TP a POC si disponible
        if tech_signal.get("poc"):
            poc = tech_signal["poc"]
            if direction == "LONG" and poc > price:   tp1 = min(tp1, poc)
            elif direction == "SHORT" and poc < price: tp1 = max(tp1, poc)

        # ── TAMAÑO DE POSICIÓN (Kelly fraccionado) ─────────────────────────────
        prob_win    = max(0.40, min(0.75, 0.45 + (score - 65) / 100))
        kelly       = max(0.0, min(0.25, (prob_win * self.RR_TP1 - (1 - prob_win)) / self.RR_TP1))
        size_factor = max(0.1, min(1.0, kelly * (score / 100)))

        if score >= 85:   confidence = "HIGH"
        elif score >= 75: confidence = "NORMAL"
        elif score >= 65: confidence = "WEAK"
        else:             confidence = "NO_TRADE"

        result.update({
            "signal":      direction,
            "entry":       round(price, 2),
            "sl":          round(sl, 2),
            "tp1":         round(tp1, 2),
            "tp2":         round(tp2, 2),
            "size_factor": round(size_factor, 3),
            "confidence":  confidence,
            "reason":      f"Score={score} | Confluencias={confluences} | {direction}",
            "prob_win":    round(prob_win * 100, 1),
            "rr_ratio":    self.RR_TP1,
            "stop_dist":   round(stop_dist, 2),
        })
        self._last_signal  = direction
        self._last_score   = score
        self._signal_count += 1
        return result


# ══════════════════════════════════════════════════════════════════════════════
#  Finnhub INTEGRATION MANAGER — Gestor central de todos los módulos Finnhub
# ══════════════════════════════════════════════════════════════════════════════
class FinnhubIntegrationManager:
    """Gestor central que inicializa y coordina todos los módulos Finnhub."""
    def __init__(self):
        self.calendar     = FinnhubEconomicCalendar(update_interval=900)
        self.price_feed   = FinnhubPriceFeed(update_interval=15)
        self.cot_engine   = FinnhubCOTEngine(update_interval=3600)
        self.macro_engine = FinnhubMacroEngine(calendar=self.calendar, update_interval=300)
        self.news_engine  = FinnhubNewsEngine(update_interval=120)
        self.historical   = FinnhubHistoricalData()
        self.tech_ind     = FinnhubTechnicalIndicators()
        self.strategy     = FinnhubStrategyEngine()
        self._running     = False
        self._lock        = threading.Lock()

    def start(self):
        self._running = True
        self.calendar.start()
        self.price_feed.start()
        self.cot_engine.start()
        self.macro_engine.start()
        self.news_engine.start()
        print("[Finnhub] Integration Manager iniciado — todos los módulos activos")

    def get_full_state(self) -> dict:
        return {
            "price":    self.price_feed.get(),
            "cot":      self.cot_engine.get(),
            "macro":    self.macro_engine.get(),
            "news":     self.news_engine.get_state(),
            "calendar": {
                "events":          self.calendar.get_events()[:10],
                "next_high":       self.calendar.get_next_high_impact(),
                "impact_score":    self.calendar.get_impact_score(),
                "high_impact_now": self.calendar.is_high_impact_now(),
            },
        }

    def get_enhanced_signal(self, tech_signal: dict, mr_state: dict,
                            liq_state: dict, reaction_zones: list,
                            price: float, atr: float,
                            legacy_news: dict = None) -> dict:
        """Genera señal mejorada combinando Finnhub + señales existentes del sistema."""
        state = self.get_full_state()
        try:
            finnhub_tech = self.tech_ind.get_full_analysis("OANDA:XAU_USD", "5")
        except Exception:
            finnhub_tech = None
        return self.strategy.evaluate(
            macro_state    = state["macro"],
            cot_data       = state["cot"],
            tech_signal    = tech_signal,
            mr_state       = mr_state,
            news_finnhub       = state["news"],
            news_legacy    = legacy_news,
            liq_state      = liq_state,
            reaction_zones = reaction_zones,
            price          = price,
            atr            = atr,
            calendar       = self.calendar,
            finnhub_tech       = finnhub_tech,
        )

    def get_finnhub_rates_fallback(self, interval: str = "M5", limit: int = 300) -> list:
        """Retorna datos OHLCV de Finnhub como fallback si MT5 no está disponible."""
        return self.historical.get_as_rates_list(interval, limit)

    def stop(self):
        self._running = False
        self.calendar.stop(); self.price_feed.stop(); self.cot_engine.stop()
        self.macro_engine.stop(); self.news_engine.stop()
        print("[Finnhub] Integration Manager detenido")


# ── Instancia global Finnhub (singleton) ──────────────────────────────────────────
_finnhub_manager_instance = None
_finnhub_manager_lock     = threading.Lock()


def get_finnhub_manager() -> FinnhubIntegrationManager:
    global _finnhub_manager_instance
    with _finnhub_manager_lock:
        if _finnhub_manager_instance is None:
            _finnhub_manager_instance = FinnhubIntegrationManager()
        return _finnhub_manager_instance


def init_finnhub(api_key: str = None) -> FinnhubIntegrationManager:
    global FINNHUB_API_KEY
    if api_key:
        FINNHUB_API_KEY = api_key
    manager = get_finnhub_manager()
    manager.start()
    return manager



# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
SYMBOL              = "XAUUSD"
BASE_PRICE          = 0.0
UPDATE_MS           = 3000
CALC_INTERVAL_S     = 4.0
CHART_REDRAW_EVERY  = 2
SUBCHART_REDRAW_EVERY = 3
TICK_BUFFER_SIZE    = 2000
NEWS_UPDATE_MS      = 60000
BOOK_UPDATE_MS      = 800
SESSION_BARS_M5     = 96
VALUE_AREA_PCT      = 0.70
PRICE_STEP          = 0.10
ATR_PERIOD          = 14
ANOMALY_WINDOW      = 30
ANOMALY_SIGMA       = 2.5
MIN_ATR_M15         = 0.20
MIN_DELTA_POC       = 0.6
TRADE_HOURS         = (8, 17)
FIB_LEVELS          = [0.0, 0.236, 0.382, 0.500, 0.618, 0.786, 1.0]
ORDERBOOK_LEVELS    = 10
HEATMAP_BARS        = 80
MAX_LATENCY_MS      = 50
RECONNECT_SECONDS   = 15
INTEGRITY_INTERVAL  = 30
VWAP_WINDOW         = 50
ZSCORE_THRESHOLD    = 1.2   # anti-overfitting: exige desviación mayor (era 0.8)
ZSCORE_STRONG       = 2.5
YIELD_CORR_WINDOW   = 40
VOL_TARGET          = 0.20
RISK_PER_TRADE_PCT  = 0.005
SIGNAL_MIN_SCORE    = 3   # anti-overfitting: mínimo 3 condiciones (era 2)
REGIME_WINDOW       = 100
REGIME_SLOPE_THRESH = 0.15
REACTION_ZONE_LOOKBACK = 80   # barras M5 para detectar zonas
REACTION_ZONE_MERGE_ATR = 0.5  # fusionar zonas a menos de X ATR
COT_UPDATE_INTERVAL = 3600     # segundos entre actualizaciones COT

# ── NUEVOS PARÁMETROS v9.0 ────────────────────────────────────────────────────
SCORE_MACRO_MAX      = 25
SCORE_LIQ_MAX        = 20
SCORE_TECH_MAX       = 30
SCORE_SENT_MAX       = 10
SCORE_HIGH_CONF      = 70
SCORE_NORMAL         = 62   # anti-overfitting: era 60
SCORE_WEAK           = 45   # anti-overfitting: era 40
SCORE_MIN_DEFAULT    = 45   # anti-overfitting: era 40
SCORE_MIN_TRANSITION = 62   # anti-overfitting: era 60
LIQ_LOW_VOL_PERCENTILE = 30
LIQ_HIGH_SPREAD_ATR    = 0.3
MACRO_UPDATE_INTERVAL  = 300
BT_TRAIN_PCT     = 0.70
BT_MONTECARLO_N  = 1000
BT_MIN_TRADES    = 100
SIGNAL_LOG_DIR   = os.path.expanduser("~/.kaurum_logs")
os.makedirs(SIGNAL_LOG_DIR, exist_ok=True)
SIGNAL_LOG_CSV   = os.path.join(SIGNAL_LOG_DIR, "signals.csv")

# ── PALETA ────────────────────────────────────────────────────────────────────
BG_ROOT   = "#0d0f12"; BG_PANEL  = "#1a1f28"; BG_HEADER = "#0f1419"
BG_CARD   = "#161b24"; BG_CARD2  = "#0f1419"; BG_SUBCARD= "#0d1117"
BG_CHART  = "#0a0e14"; SEP       = "#2a3a4a"; SEP2      = "#d4941a"
C_GOLD    = "#d4941a"; C_GOLD2   = "#f0b429"; C_GOLD3   = "#ffd060"
C_AMBER   = "#ff8c00"; C_WHITE   = "#e8eef7"; C_WHITE2  = "#c5d3e8"
C_GRAY0   = "#a0b0c8"; C_GRAY1   = "#7a8fa0"; C_GRAY2   = "#4a5a70"
C_GRAY3   = "#2a3a4a"; C_GRAY4   = "#1a2530"
C_GREEN   = "#00d966"; C_GREEN2  = "#00b854"; C_GREEN3  = "#66ff99"
C_GREEN4  = "#1b5e20"; C_RED     = "#ff3333"; C_RED2    = "#dd0000"
C_RED3    = "#ff6666"; C_RED4    = "#5c0011"
C_CYAN    = "#00e5ff"; C_CYAN2   = "#00bcd4"; C_BLUE    = "#2979ff"
C_BLUE2   = "#448aff"; C_BLUE3   = "#1a3a7a"; C_YELLOW  = "#ffd600"
C_TEAL    = "#1de9b6"; C_TEAL2   = "#00bfa5"; C_PURPLE  = "#aa00ff"
C_ORANGE  = "#ff6d00"; C_PINK    = "#f06292"; C_FIB     = "#b39ddb"
C_PRICE   = "#00e5ff"; C_VWAP    = "#6cb4ff"; C_VWAP_DARK="#2a5888"
C_POC     = "#ffd600"; C_POC_DARK= "#665800"; C_VAH     = "#ff4081"
C_VAL     = "#69f0ae"; C_PP      = "#b0bec5"
C_R1="#ff6666"; C_R2="#ff3333"; C_R3="#dd0000"
C_S1="#66ff99"; C_S2="#00d966"; C_S3="#00b854"
GRID      = "#1a2530"
COL_REAL  = "#00d966"; COL_DEMO  = "#ffd600"
COL_ERROR = "#ff3333"; COL_CHECKING = "#ff8c00"
C_SUPPLY  = "#ff4444"; C_DEMAND  = "#44ff88"  # zonas de reacción

GOLD_BULL_KW = ["rise","surge","gain","rally","climb","soar","jump","higher","bullish",
    "strong","demand","safe haven","inflation","war","crisis","uncertainty","geopolitical",
    "fed pause","rate cut","dollar weak","usd falls","buying","record","high","haven",
    "sanctions","conflict","recession","fear","panic","risk off","dovish","stimulus",
    "easing","gold up","xauusd up"]
GOLD_BEAR_KW = ["fall","drop","decline","slide","tumble","plunge","lower","bearish","weak",
    "sell","rate hike","dollar strong","usd rises","hawkish","taper","pressure","outflows",
    "risk on","equities up","yields rise","gold down","xauusd down","sell off","correction",
    "profit taking","strong dollar"]
GOLD_HI_KW = ["nfp","non-farm","fomc","fed","cpi","pce","gdp","ism","jobs report",
    "interest rate","powell","yellen","ecb","treasury","inflation data","rate decision",
    "fed meeting","employment","unemployment","payrolls"]


# ══════════════════════════════════════════════════════════════════════════════
#  DATA PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
class DataPipeline:
    _price_offset = 0.0

    @classmethod
    def set_offset(cls, cfd_price, future_price):
        if cfd_price and future_price:
            cls._price_offset = cfd_price - future_price

    @classmethod
    def apply_offset(cls, price):
        return price + cls._price_offset if price else None

    def __init__(self, symbol="XAUUSD", interval="5min"):
        self.symbol   = symbol
        self.interval = interval
        self._cache   = {}
        self._cache_ts= {}
        self._CACHE_TTL = 120

    @staticmethod
    def _flatten_yf_columns(df):
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        return df

    @staticmethod
    def _to_interval_yf(interval_str):
        s = interval_str.strip()
        s = s.replace("min", "m").replace("hour", "h").replace("day", "d")
        return s


    def get_finnhub_historical(self, interval: str = "5min", limit: int = 300) -> pd.DataFrame:
        """Obtiene datos OHLCV históricos de OANDA:XAU_USD vía Finnhub."""
        return FinnhubHistoricalData().get_ohlcv(interval, limit)

    def get_finnhub_quote(self, symbol: str = "GCUSD") -> dict:
        """Obtiene cotización en tiempo real vía Finnhub (v10.0)."""
        url  = f"{FINNHUB_BASE}/quote/{symbol}"
        data = _finnhub_get(url, ttl=15)
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
        return {}

    def get_finnhub_economic_calendar(self) -> list:
        """Obtiene calendario económico de Finnhub (v10.0)."""
        today    = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        tomorrow = (datetime.datetime.utcnow() + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        url      = f"{FINNHUB_BASE}/economic_calendar"
        data     = _finnhub_get(url, params={"from": today, "to": tomorrow}, ttl=600)
        if data and isinstance(data, list):
            return [e for e in data if e.get("impact") in ("HIGH", "MEDIUM")]
        return []

    def get_rates(self, start_date, end_date):
        cache_key = f"rates_{start_date}_{end_date}"
        now = time.time()
        if cache_key in self._cache and now - self._cache_ts.get(cache_key, 0) < self._CACHE_TTL:
            return self._cache[cache_key]
        ticker = "XAU=F" if "XAU" in self.symbol else "GC=F"
        if OPENBB_AVAILABLE:
            try:
                try:
                    # Corregir intervalo para yfinance (ej. '5min' -> '5m')
                    yf_interval = self._to_interval_yf(self.interval)
                    res = obb.equity.price.historical(ticker, start_date=str(start_date)[:10],
                                                      end_date=str(end_date)[:10],
                                                      interval=yf_interval, provider='yfinance')
                except Exception:
                    yf_interval = self._to_interval_yf(self.interval)
                    res = obb.derivatives.futures.historical(ticker, start_date=str(start_date)[:10],
                                                             end_date=str(end_date)[:10],
                                                             interval=yf_interval)
                data = res.to_df()
                data.columns = [str(c).lower() for c in data.columns]
                need = ['open','high','low','close','volume']
                for col in need:
                    if col not in data.columns:
                        raise ValueError(f"missing {col}")
                data = data[need].dropna()
                for col in ['open','high','low','close']:
                    data[col] = data[col] + self._price_offset
                if not data.empty:
                    self._cache[cache_key] = data
                    self._cache_ts[cache_key] = now
                    return data
            except Exception as e:
                print(f"[DataPipeline] OpenBB: {e}")
        try:
            yf_interval = self._to_interval_yf(self.interval)
            df = yf.download(ticker, start=start_date, end=end_date,
                             interval=yf_interval, progress=False,
                             auto_adjust=True, actions=False)
            if df.empty:
                return None
            df = self._flatten_yf_columns(df)
            need = ['open','high','low','close']
            for col in need:
                if col not in df.columns:
                    return None
            if 'volume' not in df.columns:
                df['volume'] = 1.0
            for col in ['open','high','low','close']:
                df[col] = df[col] + self._price_offset
            df = df[['open','high','low','close','volume']].dropna()
            if df.empty:
                return None
            self._cache[cache_key] = df
            self._cache_ts[cache_key] = now
            return df
        except Exception as e:
            print(f"[DataPipeline] yfinance: {e}")
            return None

    def get_yield_curve(self, start_date, end_date):
        if OPENBB_AVAILABLE:
            try:
                if hasattr(obb, 'economy') and hasattr(obb.economy, 'fred'):
                    data = obb.economy.fred.series(series_id="DGS10",
                                                   start_date=str(start_date)[:10],
                                                   end_date=str(end_date)[:10],
                                                   frequency="d").to_df()
                    data = data.resample('D').last().dropna()
                    if not data.empty:
                        return data['value']
            except Exception as e:
                print(f"[DataPipeline] yield OpenBB: {e}")
        try:
            tlt = yf.download("TLT", start=start_date, end=end_date,
                              interval="1d", progress=False, auto_adjust=True, actions=False)
            if tlt.empty:
                return pd.Series(dtype=float)
            tlt = self._flatten_yf_columns(tlt)
            if 'close' in tlt.columns:
                return tlt['close']
        except Exception as e:
            print(f"[DataPipeline] yield fallback: {e}")
        return pd.Series(dtype=float)

    def get_dxy(self, start_date, end_date):
        try:
            df = yf.download("DX-Y.NYB", start=start_date, end=end_date,
                             interval="1d", progress=False, auto_adjust=True, actions=False)
            if df.empty:
                return pd.Series(dtype=float)
            df = self._flatten_yf_columns(df)
            if 'close' in df.columns:
                return df['close']
        except:
            pass
        return pd.Series(dtype=float)


# ══════════════════════════════════════════════════════════════════════════════
#  OpenBB COT ENGINE — Posicionamiento institucional (oro: código 088691)
# ══════════════════════════════════════════════════════════════════════════════
class OpenBBCOTEngine:
    """
    Descarga datos COT (Commitments of Traders) de CFTC vía OpenBB.
    Calcula ratio institucional neto: (longs - shorts) / total * 100.
    Positivo = institucionales netos compradores (bullish gold).
    Negativo = institucionales netos vendedores (bearish gold).
    Se actualiza cada COT_UPDATE_INTERVAL segundos (datos semanales CFTC).
    Fallback: COT vía CFTC.gov si OpenBB falla.
    """
    GOLD_COT_ID   = "088691"
    CFTC_URL      = ("https://www.cftc.gov/dea/futures/deacomrep.htm")
    HEADERS       = {"User-Agent": "Mozilla/5.0"}

    def __init__(self):
        self._lock            = threading.Lock()
        self._running         = True
        self._last_update     = 0
        self._cot_data        = {
            "net_institutional": 0.0,    # (longs-shorts)/total*100 — non-commercial
            "long_pct":          50.0,   # % longs no-comerciales
            "short_pct":         50.0,
            "net_change":        0.0,    # cambio respecto semana anterior
            "bias":              "NEUTRAL",  # BULLISH / BEARISH / NEUTRAL
            "date":              None,
            "source":            "N/A",
            "stale":             True,
        }

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            self._fetch()
            time.sleep(COT_UPDATE_INTERVAL)

    def _fetch(self):
        # ── NUEVO v10.0: Finnhub COT primero (más actualizado) ────────────────────
        finnhub_cot = get_finnhub_manager().cot_engine.get()
        if not finnhub_cot.get("stale", True) and finnhub_cot.get("source") == "Finnhub/COT":
            with self._lock:
                self._cot_data = finnhub_cot
                self._last_update = time.time()
            return
        # Intento 1: OpenBB regulators.cftc.cot
        if OPENBB_AVAILABLE:
            try:
                r  = obb.regulators.cftc.cot(id=self.GOLD_COT_ID, limit=2)
                df = r.to_df()
                if not df.empty:
                    self._parse_openbb_cot(df)
                    return
            except Exception as e:
                print(f"[COT] OpenBB: {e}")
        # Fallback: Yahoo Finance GC=F COT-like via positioning
        self._fetch_fallback()

    def _parse_openbb_cot(self, df):
        # Columnas estándar OpenBB COT:
        # noncomm_positions_long_all, noncomm_positions_short_all
        lc = "noncomm_positions_long_all"
        sc = "noncomm_positions_short_all"
        if lc not in df.columns or sc not in df.columns:
            # Intentar nombres alternativos
            cols = [c for c in df.columns if 'long' in c.lower() and 'comm' in c.lower()]
            if not cols:
                return
            lc = cols[0]
            sc_candidates = [c for c in df.columns if 'short' in c.lower() and 'comm' in c.lower()]
            sc = sc_candidates[0] if sc_candidates else lc
        row_latest = df.iloc[-1]
        row_prev   = df.iloc[-2] if len(df) > 1 else row_latest
        longs  = float(row_latest[lc])
        shorts = float(row_latest[sc])
        total  = longs + shorts if longs + shorts > 0 else 1
        net    = (longs - shorts) / total * 100
        longs_prev  = float(row_prev[lc])
        shorts_prev = float(row_prev[sc])
        total_prev  = longs_prev + shorts_prev if longs_prev + shorts_prev > 0 else 1
        net_prev    = (longs_prev - shorts_prev) / total_prev * 100
        bias = "BULLISH" if net > 10 else "BEARISH" if net < -10 else "NEUTRAL"
        date_val = None
        if 'date' in df.columns:
            date_val = str(df['date'].iloc[-1])[:10]
        with self._lock:
            self._cot_data = {
                "net_institutional": round(net, 2),
                "long_pct":         round(longs / total * 100, 1),
                "short_pct":        round(shorts / total * 100, 1),
                "net_change":       round(net - net_prev, 2),
                "bias":             bias,
                "date":             date_val,
                "source":           "OpenBB/CFTC",
                "stale":            False,
            }
            self._last_update = time.time()

    def _fetch_fallback(self):
        # Proxy: ratio de posicionamiento GC=F en Yahoo Finance (OI largo vs corto)
        # Usa diferencia de open interest entre días consecutivos como proxy institucional
        try:
            import yfinance as yf
            gc = yf.Ticker("GC=F")
            hist = gc.history(period="10d")
            if hist.empty or len(hist) < 2:
                return
            # Días con volumen alto = presión compradora vs vendedora
            avg_vol = hist['Volume'].mean()
            last_vol = hist['Volume'].iloc[-1]
            last_close = hist['Close'].iloc[-1]
            prev_close = hist['Close'].iloc[-2]
            direction = "BULLISH" if last_close > prev_close else "BEARISH"
            # Net simplificado: volumen relativo * dirección
            vol_ratio = (last_vol / avg_vol - 1) * 100 if avg_vol > 0 else 0
            net_proxy = vol_ratio if direction == "BULLISH" else -vol_ratio
            bias = "BULLISH" if net_proxy > 5 else "BEARISH" if net_proxy < -5 else "NEUTRAL"
            with self._lock:
                self._cot_data = {
                    "net_institutional": round(net_proxy, 2),
                    "long_pct":         60.0 if bias == "BULLISH" else 40.0,
                    "short_pct":        40.0 if bias == "BULLISH" else 60.0,
                    "net_change":       0.0,
                    "bias":             bias,
                    "date":             str(datetime.date.today()),
                    "source":           "yfinance-proxy",
                    "stale":            False,
                }
                self._last_update = time.time()
        except Exception as e:
            print(f"[COT] fallback: {e}")

    def get(self):
        with self._lock:
            d = dict(self._cot_data)
            d["stale"] = (time.time() - self._last_update) > COT_UPDATE_INTERVAL * 2
            return d

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  OpenBB NEWS ENGINE — Noticias + Sentimiento vía OpenBB + RSS
# ══════════════════════════════════════════════════════════════════════════════
class OpenBBNewsEngine:
    """
    Obtiene noticias macro vía OpenBB (obb.news.world con provider yfinance/tiingo),
    complementado con RSS de Kitco y Yahoo Finance Gold.
    Calcula sentimiento ponderado: titulares recientes pesan más.
    """
    KITCO_RSS = "https://www.kitco.com/rss/news.xml"
    YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F&region=US&lang=en-US"
    FF_URL    = "https://www.forexfactory.com/ff_calendar_thisweek.xml"
    HEADERS   = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def __init__(self):
        self._lock        = threading.Lock()
        self._headlines   = deque(maxlen=40)
        self._sentiment   = "NEUTRAL"
        self._score       = 0
        self._impact_now  = False
        self._next_event  = None
        self._events      = []
        self._ff_ok       = False
        self._last_fetch  = 0
        self._openbb_ok   = False

    def fetch_async(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _fetch_url(self, url, timeout=10):
        try:
            r = requests.get(url, headers=self.HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.text
        except:
            pass
        try:
            req = urllib.request.Request(url, headers=self.HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="ignore")
        except:
            return ""

    def _parse_rss(self, xml_text):
        items = []
        try:
            root = ET.fromstring(xml_text)
            for item in root.iter("item"):
                items.append({
                    "title": item.findtext("title", "").strip(),
                    "desc":  item.findtext("description", "").strip(),
                    "pub":   item.findtext("pubDate", "").strip()
                })
        except:
            pass
        return items

    def _parse_ff(self, xml_text):
        events = []
        try:
            root = ET.fromstring(xml_text)
            for ev in root.findall(".//event"):
                country = ev.findtext("country", "").strip().upper()
                if country not in ("USD","EUR","GBP","CNY","JPY","XAU"):
                    continue
                title    = ev.findtext("title", "").strip()
                date_str = ev.findtext("date", "").strip()
                time_str = ev.findtext("time", "").strip()
                impact   = ev.findtext("impact", "").strip()
                forecast = ev.findtext("forecast", "").strip()
                previous = ev.findtext("previous", "").strip()
                actual   = ev.findtext("actual", "").strip()
                raw_dt   = self._parse_ff_dt(date_str, time_str)
                events.append({"title": title, "country": country, "date": date_str,
                    "time": time_str, "impact": impact, "forecast": forecast,
                    "previous": previous, "actual": actual, "raw_dt": raw_dt})
        except ET.ParseError:
            pass
        return events

    def _parse_ff_dt(self, date_str, time_str):
        combined = f"{date_str} {time_str}".strip()
        for fmt in ["%A %b %d %Y %I:%M%p", "%A %b %d %Y %I:%M %p",
                    "%b %d %Y %I:%M%p", "%b %d %Y %I:%M %p"]:
            try:
                return datetime.datetime.strptime(combined, fmt)
            except:
                continue
        return None

    def _score_text(self, text):
        t    = text.lower()
        bull = sum(1 for w in GOLD_BULL_KW if w in t)
        bear = sum(1 for w in GOLD_BEAR_KW if w in t)
        hi   = any(w in t for w in GOLD_HI_KW)
        return bull - bear, hi

    def _run(self):
        headlines = []; total_score = 0; has_impact = False; openbb_ok = False

        # ── NUEVO v10.0: Finnhub News primero ─────────────────────────────────────
        try:
            finnhub_news = get_finnhub_manager().news_engine.get_state()
            if finnhub_news.get("finnhub_ok"):
                for item in finnhub_news.get("headlines", []):
                    total_score += item.get("score", 0)
                    if item.get("impact"): has_impact = True
                    headlines.append(item)
        except Exception as e:
            print(f"[News] Finnhub state error: {e}")

        # ── NUEVO v10.0: Calendario Finnhub como fuente de eventos ────────────────
        try:
            finnhub_events = get_finnhub_manager().calendar.get_events()
            if finnhub_events:
                now_utc = datetime.datetime.utcnow()
                for ev in finnhub_events:
                    dt = ev.get("raw_dt")
                    if dt:
                        diff_min = (dt - now_utc).total_seconds() / 60
                        if ev.get("impact") == "HIGH" and -5 <= diff_min <= 30:
                            has_impact = True
        except Exception as e:
            print(f"[News] Finnhub calendar error: {e}")

        # Intento OpenBB news
        if OPENBB_AVAILABLE:
            try:
                # yfinance no es un proveedor válido para obb.news.world en v4
                # Usamos 'benzinga' o 'tiingo' si están disponibles, o intentamos sin provider
                try:
                    r = obb.news.world(limit=20, provider='benzinga')
                except:
                    r = obb.news.world(limit=20)
                df = r.to_df()
                if not df.empty:
                    openbb_ok = True
                    for _, row in df.iterrows():
                        title = str(row.get('title', row.get('headline', '')))
                        body  = str(row.get('text', row.get('content', '')))
                        sc, hi = self._score_text(title + " " + body)
                        total_score += sc
                        if hi: has_impact = True
                        headlines.append({
                            "title":   title[:100],
                            "score":   sc,
                            "impact":  hi,
                            "pub":     str(row.get('date', ''))[:16],
                            "source":  "OpenBB"
                        })
            except Exception as e:
                print(f"[News] OpenBB: {e}")

        # RSS fallback/complemento
        for url in [self.KITCO_RSS, self.YAHOO_RSS]:
            xml = self._fetch_url(url, timeout=8)
            if not xml:
                continue
            for it in self._parse_rss(xml)[:15]:
                sc, hi = self._score_text(it["title"] + " " + it["desc"])
                total_score += sc
                if hi: has_impact = True
                headlines.append({
                    "title":  it["title"][:100],
                    "score":  sc,
                    "impact": hi,
                    "pub":    it["pub"][:16],
                    "source": "RSS"
                })
            if len(headlines) >= 30:
                break

        # ForexFactory
        events = []; ff_ok = False
        ff_xml = self._fetch_url(self.FF_URL)
        if ff_xml and "<event>" in ff_xml:
            events = self._parse_ff(ff_xml)
            ff_ok  = len(events) > 0

        next_ev = None; impact_now = has_impact
        now_utc = datetime.datetime.utcnow()
        for ev in sorted(events, key=lambda e: e["raw_dt"] or datetime.datetime.max):
            dt = ev.get("raw_dt")
            if dt is None:
                continue
            diff_min = (dt - now_utc).total_seconds() / 60
            if ev["impact"] == "High" and -5 <= diff_min <= 30:
                impact_now = True
            if ev["impact"] == "High" and diff_min > 0 and next_ev is None:
                next_ev = {**ev, "minutes_away": round(diff_min)}

        sentiment = ("BULLISH" if total_score >= 2 else
                     "BEARISH" if total_score <= -2 else "NEUTRAL")
        with self._lock:
            self._events = events
            self._headlines.clear()
            for h in headlines:
                self._headlines.appendleft(h)
            self._score      = total_score
            self._impact_now = impact_now
            self._sentiment  = sentiment
            self._next_event = next_ev
            self._ff_ok      = ff_ok
            self._openbb_ok  = openbb_ok
            self._last_fetch = time.time()

    def get_state(self):
        with self._lock:
            return {
                "events":     list(self._events[:15]),
                "headlines":  list(self._headlines),
                "sentiment":  self._sentiment,
                "score":      self._score,
                "impact":     self._impact_now,
                "next_event": self._next_event,
                "ff_ok":      self._ff_ok,
                "openbb_ok":  self._openbb_ok,
                "last_fetch": self._last_fetch,
            }


# ══════════════════════════════════════════════════════════════════════════════
#  REACTION ZONES ENGINE — Supply & Demand
# ══════════════════════════════════════════════════════════════════════════════
class ReactionZoneEngine:
    """
    Detecta zonas de reacción institucional (supply & demand) en barras M5.
    Algoritmo:
      1. Identifica velas de impulso (body > 1.5x ATR promedio)
      2. La zona base = vela anterior al impulso (origen del movimiento)
      3. Clasifica como SUPPLY (zona + impulso bajista) o DEMAND (zona + impulso alcista)
      4. Una zona está "activa" si el precio no la ha vuelto a visitar
      5. Fusiona zonas solapadas dentro del umbral REACTION_ZONE_MERGE_ATR
    """
    def __init__(self):
        pass

    def calc_zones(self, rates, atr):
        if not rates or len(rates) < 20 or atr < 0.01:
            return []
        sess    = rates[-REACTION_ZONE_LOOKBACK:]
        avg_body = np.mean([abs(r['close'] - r['open']) for r in sess]) + 1e-9
        zones   = []
        cur_price = float(sess[-1]['close'])

        for i in range(2, len(sess) - 1):
            bar  = sess[i]
            prev = sess[i - 1]
            body = abs(bar['close'] - bar['open'])
            if body < avg_body * 1.5:
                continue

            # Impulso alcista → zona demand en la vela base (previa)
            if bar['close'] > bar['open']:
                z_top    = max(prev['open'], prev['close'])
                z_bot    = prev['low']
                z_type   = "DEMAND"
                active   = cur_price > z_bot  # no ha penetrado la zona por abajo
            else:
                z_top    = prev['high']
                z_bot    = min(prev['open'], prev['close'])
                z_type   = "SUPPLY"
                active   = cur_price < z_top  # no ha penetrado la zona por arriba

            # Filtrar zonas mínimas
            if z_top - z_bot < atr * 0.15:
                continue

            # Verificar que el precio no haya penetrado la zona después de formarse
            future_bars = sess[i + 1:]
            if z_type == "DEMAND":
                penetrated = any(fb['low'] < z_bot for fb in future_bars)
            else:
                penetrated = any(fb['high'] > z_top for fb in future_bars)
            if penetrated:
                active = False

            zones.append({
                "type":    z_type,
                "top":     round(z_top, 2),
                "bot":     round(z_bot, 2),
                "mid":     round((z_top + z_bot) / 2, 2),
                "active":  active,
                "idx":     i,
                "strength": round(body / avg_body, 2),
            })

        # Fusionar zonas solapadas del mismo tipo
        zones = self._merge_zones(zones, atr)
        # Solo devolver las activas y las más recientes
        active  = [z for z in zones if z["active"]][-8:]
        passive = [z for z in zones if not z["active"]][-3:]
        return active + passive

    def _merge_zones(self, zones, atr):
        if not zones:
            return zones
        merged = []
        used   = set()
        for i, z in enumerate(zones):
            if i in used:
                continue
            group = [z]
            for j, z2 in enumerate(zones):
                if j <= i or j in used:
                    continue
                if z2["type"] != z["type"]:
                    continue
                overlap = min(z["top"], z2["top"]) - max(z["bot"], z2["bot"])
                if overlap > 0 or abs(z["mid"] - z2["mid"]) < atr * REACTION_ZONE_MERGE_ATR:
                    group.append(z2)
                    used.add(j)
            used.add(i)
            top = max(g["top"] for g in group)
            bot = min(g["bot"] for g in group)
            merged.append({
                "type":    z["type"],
                "top":     round(top, 2),
                "bot":     round(bot, 2),
                "mid":     round((top + bot) / 2, 2),
                "active":  z["active"],
                "strength": round(np.mean([g["strength"] for g in group]), 2),
            })
        return merged

    def nearest_zone(self, price, zones):
        """Retorna (zona_supply_más_cercana_arriba, zona_demand_más_cercana_abajo)."""
        supply = [z for z in zones if z["type"] == "SUPPLY" and z["mid"] > price and z["active"]]
        demand = [z for z in zones if z["type"] == "DEMAND" and z["mid"] < price and z["active"]]
        supply.sort(key=lambda z: z["mid"])
        demand.sort(key=lambda z: z["mid"], reverse=True)
        return supply[0] if supply else None, demand[0] if demand else None


# ══════════════════════════════════════════════════════════════════════════════
#  MACRO ENGINE v9.0 — Causa → Consecuencia → Sesgo
# ══════════════════════════════════════════════════════════════════════════════
class MacroEngine:
    """
    Interpreta relaciones macro para generar sesgo (bullish/bearish/neutral)
    y fuerza (débil/moderado/fuerte). No lee datos, los razona.
    Entradas: yields, DXY, VIX, inflación (via proxies yfinance).
    """
    def __init__(self, data_pipe):
        self._pipe    = data_pipe
        self._lock    = threading.Lock()
        self._state   = self._default_state()
        self._running = True
        self._last_update = 0

    def _default_state(self):
        return {
            "bias":       "NEUTRAL",   # BULLISH / BEARISH / NEUTRAL
            "strength":   "NEUTRAL",   # WEAK / MODERATE / STRONG
            "regime":     "NEUTRAL",   # RISK_ON / RISK_OFF / NEUTRAL / TRANSITION
            "score_macro": 20,         # 0-40
            "details":    {},
            "transition": False,
            "last_update": 0,
        }

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception as e:
                print(f"[MacroEngine] {e}")
            time.sleep(MACRO_UPDATE_INTERVAL)

    def _update(self):
        # ── NUEVO v10.0: Finnhub primero, yfinance como fallback ──────────────────
        finnhub_state = get_finnhub_manager().macro_engine.get()
        if finnhub_state.get("source") == "Finnhub" and finnhub_state.get("last_update", 0) > 0:
            with self._lock:
                self._state = finnhub_state
            return
        # Fallback legacy con yfinance
        self._update_legacy()

    def _update_legacy(self):
        """Actualización legacy usando yfinance (fallback cuando Finnhub no disponible)."""
        end   = datetime.datetime.utcnow()
        start = end - datetime.timedelta(days=30)
        scores = {}
        bull_pts = 0; bear_pts = 0

        # 1. YIELDS (TLT proxy: precio TLT sube → yields bajan → bullish gold)
        try:
            yields = self._pipe.get_yield_curve(start, end)
            if yields is not None and len(yields) >= 5:
                yld_chg = float(yields.iloc[-1]) - float(yields.iloc[-5])
                if yld_chg > 1.0:   bull_pts += 2; scores["yields"] = "BULLISH(↑TLT=↓rates)"
                elif yld_chg < -1.0: bear_pts += 2; scores["yields"] = "BEARISH(↓TLT=↑rates)"
                else:               scores["yields"] = "NEUTRAL"
        except: scores["yields"] = "N/A"

        # 2. DXY (inversa del oro: DXY sube → bearish gold)
        try:
            dxy = self._pipe.get_dxy(start, end)
            if dxy is not None and len(dxy) >= 5:
                dxy_chg = float(dxy.iloc[-1]) - float(dxy.iloc[-5])
                if dxy_chg > 0.5:    bear_pts += 2; scores["dxy"] = "BEARISH(↑DXY)"
                elif dxy_chg < -0.5: bull_pts += 2; scores["dxy"] = "BULLISH(↓DXY)"
                else:                scores["dxy"] = "NEUTRAL"
        except: scores["dxy"] = "N/A"

        # 3. VIX proxy (^VIX): alta volatilidad → risk-off → bullish gold
        try:
            vix_df = yf.download("^VIX", start=start, end=end,
                                 interval="1d", progress=False, auto_adjust=True, actions=False)
            if not vix_df.empty:
                if isinstance(vix_df.columns, pd.MultiIndex):
                    vix_df.columns = vix_df.columns.get_level_values(0)
                vix_df.columns = [c.lower() for c in vix_df.columns]
                vix_last = float(vix_df['close'].iloc[-1])
                vix_ma   = float(vix_df['close'].rolling(10).mean().iloc[-1])
                if vix_last > 25:
                    bull_pts += 2; scores["vix"] = f"BULLISH(VIX={vix_last:.1f} RISK-OFF)"
                elif vix_last > vix_ma * 1.15:
                    bull_pts += 1; scores["vix"] = f"MILD_BULL(VIX↑ {vix_last:.1f})"
                elif vix_last < 15:
                    bear_pts += 1; scores["vix"] = f"BEARISH(VIX={vix_last:.1f} RISK-ON)"
                else:
                    scores["vix"] = f"NEUTRAL(VIX={vix_last:.1f})"
        except: scores["vix"] = "N/A"

        # 4. SPY (risk-on/off): SPY cae → flight-to-safety → bullish gold
        try:
            spy = yf.download("SPY", start=start, end=end,
                              interval="1d", progress=False, auto_adjust=True, actions=False)
            if not spy.empty:
                if isinstance(spy.columns, pd.MultiIndex):
                    spy.columns = spy.columns.get_level_values(0)
                spy.columns = [c.lower() for c in spy.columns]
                spy_ret = float(spy['close'].pct_change(5).iloc[-1]) * 100
                if spy_ret < -3.0:   bull_pts += 2; scores["equities"] = f"BULLISH(SPY {spy_ret:.1f}%)"
                elif spy_ret > 3.0:  bear_pts += 1; scores["equities"] = f"BEARISH(SPY +{spy_ret:.1f}%)"
                else:                scores["equities"] = f"NEUTRAL(SPY {spy_ret:+.1f}%)"
        except: scores["equities"] = "N/A"

        net = bull_pts - bear_pts
        if net >= 4:    bias = "BULLISH"; strength = "STRONG"
        elif net >= 2:  bias = "BULLISH"; strength = "MODERATE"
        elif net == 1:  bias = "BULLISH"; strength = "WEAK"
        elif net <= -4: bias = "BEARISH"; strength = "STRONG"
        elif net <= -2: bias = "BEARISH"; strength = "MODERATE"
        elif net == -1: bias = "BEARISH"; strength = "WEAK"
        else:           bias = "NEUTRAL"; strength = "NEUTRAL"

        regime = "NEUTRAL"
        if bias == "BULLISH" and strength in ("MODERATE","STRONG"):
            regime = "RISK_OFF"
        elif bias == "BEARISH" and strength in ("MODERATE","STRONG"):
            regime = "RISK_ON"
        elif strength == "WEAK":
            regime = "TRANSITION"

        raw_score   = min(8, bull_pts if bias == "BULLISH" else bear_pts)
        score_macro = int(raw_score / 8 * SCORE_MACRO_MAX)

        with self._lock:
            self._state = {
                "bias":        bias,
                "strength":    strength,
                "regime":      regime,
                "score_macro": score_macro,
                "details":     scores,
                "transition":  regime == "TRANSITION",
                "last_update": time.time(),
                "bull_pts":    bull_pts,
                "bear_pts":    bear_pts,
                "source":      "yfinance",
            }

    def get(self):
        with self._lock:
            return dict(self._state)

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  LIQUIDITY LAYER v9.0
# ══════════════════════════════════════════════════════════════════════════════
class LiquidityLayer:
    """
    Aproxima liquidez usando volumen, volatilidad y rango de expansión.
    Determina si se puede operar agresivamente o no.
    """
    def assess(self, rates_m5, atr, ob_data=None):
        if not rates_m5 or len(rates_m5) < 20:
            return {"score_liq": 10, "level": "LOW", "can_enter": False,
                    "details": "Datos insuficientes", "penalty": -10}

        recent = rates_m5[-20:]
        vols   = [r['tick_volume'] for r in recent]
        vol_now = float(vols[-1])
        vol_avg = float(np.mean(vols[:-1])) + 1e-9

        # Ratio de volumen actual vs media
        vol_ratio = vol_now / vol_avg

        # Expansión de rango (rango actual vs ATR)
        last_range = recent[-1]['high'] - recent[-1]['low']
        range_ratio = last_range / max(atr, 0.01)

        # Spread (si disponible en ob_data)
        spread_ok = True
        if ob_data:
            spread = ob_data.get("spread", 0)
            if spread > atr * LIQ_HIGH_SPREAD_ATR:
                spread_ok = False

        # Scoring liquidez 0-20
        score = 0
        details = []

        if vol_ratio >= 1.5:
            score += 8; details.append(f"Vol alta ({vol_ratio:.1f}x)")
        elif vol_ratio >= 0.8:
            score += 5; details.append(f"Vol normal ({vol_ratio:.1f}x)")
        else:
            score += 0; details.append(f"Vol baja ({vol_ratio:.1f}x)")

        if range_ratio >= 0.8:
            score += 7; details.append(f"Rango ok ({range_ratio:.2f})")
        elif range_ratio >= 0.4:
            score += 4; details.append(f"Rango reducido ({range_ratio:.2f})")
        else:
            score += 0; details.append(f"Rango mínimo ({range_ratio:.2f})")

        if spread_ok:
            score += 5; details.append("Spread ok")
        else:
            score += 0; details.append("Spread alto")

        score = min(SCORE_LIQ_MAX, score)

        if score >= 15:
            level = "HIGH"
        elif score >= 8:
            level = "NORMAL"
        else:
            level = "LOW"

        can_enter = level != "LOW"
        penalty   = 0 if level == "HIGH" else (-5 if level == "NORMAL" else -15)

        return {
            "score_liq": score,
            "level":     level,
            "can_enter": can_enter,
            "penalty":   penalty,
            "details":   " | ".join(details),
            "vol_ratio": round(vol_ratio, 2),
            "range_ratio": round(range_ratio, 2),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  QUANT SCORER v9.0 — Score 0-100
# ══════════════════════════════════════════════════════════════════════════════
class QuantScorer:
    """
    Combina Macro(40) + Liquidez(20) + Técnico(30) + Sentimiento(10) = 100.
    Genera probabilidades LONG/SHORT y nivel de confianza.
    """
    def score(self, macro_state, liq_state, tech_signal, news_state, regime_transition=False):
        # ── Macro (0-40) ───────────────────────────────────────────────────────
        score_macro = macro_state.get("score_macro", 20)
        macro_bias  = macro_state.get("bias", "NEUTRAL")
        macro_str   = macro_state.get("strength", "NEUTRAL")

        # ── Liquidez (0-20) ────────────────────────────────────────────────────
        score_liq = liq_state.get("score_liq", 10)
        liq_penalty = liq_state.get("penalty", 0)

        # ── Técnico (0-30) ─────────────────────────────────────────────────────
        tech_score_raw = tech_signal.get("score", 0)
        tech_max       = max(tech_signal.get("max_score", 6), 1)
        score_tech     = int((tech_score_raw / tech_max) * SCORE_TECH_MAX)

        # ── Sentimiento (0-10) ─────────────────────────────────────────────────
        news_sent = news_state.get("sentiment", "NEUTRAL") if news_state else "NEUTRAL"
        news_hi   = news_state.get("impact", False) if news_state else False
        if news_sent == "BULLISH" and not news_hi:
            score_sent = 8
        elif news_sent == "BEARISH" and not news_hi:
            score_sent = 2
        elif news_hi:
            score_sent = 0   # noticias de alto impacto → penalizar
        else:
            score_sent = 5

        # ── Total ──────────────────────────────────────────────────────────────
        total = score_macro + score_liq + score_tech + score_sent + liq_penalty
        total = max(0, min(100, total))

        # Ajuste por transición
        min_thresh = SCORE_MIN_TRANSITION if regime_transition else SCORE_MIN_DEFAULT

        # Nivel de confianza
        if total >= SCORE_HIGH_CONF:
            confidence = "HIGH"
        elif total >= SCORE_NORMAL:
            confidence = "NORMAL"
        elif total >= SCORE_WEAK:
            confidence = "WEAK"
        else:
            confidence = "NO_TRADE"

        # Probabilidad LONG/SHORT
        tech_dir = tech_signal.get("dir", "NEUTRAL")
        if macro_bias == "BULLISH" and tech_dir == "LONG":
            prob_long  = min(90, 45 + total * 0.45)
            prob_short = 100 - prob_long
        elif macro_bias == "BEARISH" and tech_dir == "SHORT":
            prob_short = min(90, 45 + total * 0.45)
            prob_long  = 100 - prob_short
        elif macro_bias == "BULLISH":
            prob_long  = 55 + min(15, total * 0.15)
            prob_short = 100 - prob_long
        elif macro_bias == "BEARISH":
            prob_short = 55 + min(15, total * 0.15)
            prob_long  = 100 - prob_short
        else:
            prob_long  = 50.0
            prob_short = 50.0

        # Dominancia macro bloquea señales débiles
        can_long  = True
        can_short = True
        if macro_bias == "BEARISH" and macro_str == "STRONG":
            can_long = False   # bloquear LONG débil con macro bearish fuerte
        if macro_bias == "BULLISH" and macro_str == "STRONG":
            can_short = False  # bloquear SHORT débil con macro bullish fuerte

        # Tamaño de posición ajustado (factor 0.0-1.0)
        if regime_transition:
            size_factor = 0.5
        elif confidence == "HIGH":
            size_factor = 1.0
        elif confidence == "NORMAL":
            size_factor = 0.75
        elif confidence == "WEAK":
            size_factor = 0.5
        else:
            size_factor = 0.0

        return {
            "total":         total,
            "confidence":    confidence,
            "can_trade":     total >= min_thresh,
            "can_long":      can_long,
            "can_short":     can_short,
            "prob_long":     round(prob_long, 1),
            "prob_short":    round(prob_short, 1),
            "size_factor":   size_factor,
            "min_thresh":    min_thresh,
            "score_macro":   score_macro,
            "score_liq":     score_liq,
            "score_tech":    score_tech,
            "score_sent":    score_sent,
            "liq_penalty":   liq_penalty,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNAL LOGGER v9.0
# ══════════════════════════════════════════════════════════════════════════════
class SignalLogger:
    """
    Registra todas las señales con metadatos completos en CSV.
    Permite análisis posterior de winrate, PF, drawdown por régimen/score.
    """
    FIELDS = [
        "timestamp", "type", "entry", "sl", "tp1", "tp2", "tp3",
        "result", "duration_bars", "rsi", "macro_bias", "macro_strength",
        "regime", "score_total", "score_macro", "score_liq", "score_tech", "score_sent",
        "prob_long", "prob_short", "liq_level", "confidence",
        "atr", "sd_pos", "cot_bias", "news_sentiment",
    ]

    def __init__(self):
        self._lock      = threading.Lock()
        self._pending   = []
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(SIGNAL_LOG_CSV):
            with open(SIGNAL_LOG_CSV, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=self.FIELDS).writeheader()

    def log(self, signal_dict, score_dict, macro_state, liq_state, extra=None):
        row = {
            "timestamp":      datetime.datetime.utcnow().isoformat(),
            "type":           signal_dict.get("dir", "NEUTRAL"),
            "entry":          signal_dict.get("entry", ""),
            "sl":             signal_dict.get("sl", ""),
            "tp1":            signal_dict.get("tp1", ""),
            "tp2":            signal_dict.get("tp2", ""),
            "tp3":            signal_dict.get("tp3", ""),
            "result":         "",  # se actualiza cuando cierra
            "duration_bars":  "",
            "rsi":            signal_dict.get("rsi", 50),
            "macro_bias":     macro_state.get("bias", "N/A"),
            "macro_strength": macro_state.get("strength", "N/A"),
            "regime":         macro_state.get("regime", "N/A"),
            "score_total":    score_dict.get("total", 0),
            "score_macro":    score_dict.get("score_macro", 0),
            "score_liq":      score_dict.get("score_liq", 0),
            "score_tech":     score_dict.get("score_tech", 0),
            "score_sent":     score_dict.get("score_sent", 0),
            "prob_long":      score_dict.get("prob_long", 50),
            "prob_short":     score_dict.get("prob_short", 50),
            "liq_level":      liq_state.get("level", "N/A"),
            "confidence":     score_dict.get("confidence", "N/A"),
            "atr":            extra.get("atr", "") if extra else "",
            "sd_pos":         signal_dict.get("sd_pos", ""),
            "cot_bias":       signal_dict.get("cot_bias", "N/A"),
            "news_sentiment": extra.get("news_sentiment", "N/A") if extra else "N/A",
        }
        with self._lock:
            try:
                with open(SIGNAL_LOG_CSV, "a", newline="") as f:
                    csv.DictWriter(f, fieldnames=self.FIELDS).writerow(row)
            except Exception as e:
                print(f"[SignalLogger] {e}")
            self._pending.append(row)
            if len(self._pending) > 500:
                self._pending = self._pending[-500:]

    def get_stats(self):
        """Calcula métricas básicas de la sesión."""
        with self._lock:
            rows = [r for r in self._pending if r.get("result") in ("WIN","LOSS")]
        if len(rows) < 3:
            return {}
        wins  = [r for r in rows if r["result"] == "WIN"]
        losses= [r for r in rows if r["result"] == "LOSS"]
        wr    = len(wins) / len(rows) * 100
        return {
            "total":   len(rows),
            "winrate": round(wr, 1),
            "wins":    len(wins),
            "losses":  len(losses),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  WALK-FORWARD + MONTE CARLO BACKTESTER v9.0
# ══════════════════════════════════════════════════════════════════════════════
class WalkForwardBacktester:
    """
    Walk-forward: divide datos en ventanas train/test.
    Monte Carlo: reordena trades, aplica slippage, calcula distribución de equity.
    """
    def __init__(self, train_pct=BT_TRAIN_PCT, mc_runs=BT_MONTECARLO_N):
        self.train_pct = train_pct
        self.mc_runs   = mc_runs

    # ── UTILIDADES ─────────────────────────────────────────────────────────────
    @staticmethod
    def _calc_metrics(returns):
        """Calcula métricas avanzadas dado un array de retornos por trade."""
        if len(returns) < 3:
            return {}
        arr   = np.array(returns)
        wins  = arr[arr > 0]; losses = arr[arr <= 0]
        wr    = len(wins) / len(arr)
        pf    = (wins.sum() / abs(losses.sum())) if losses.sum() != 0 else np.inf
        equity = np.cumsum(arr)
        dd     = np.maximum.accumulate(equity) - equity
        max_dd = float(dd.max()) if len(dd) > 0 else 0.0
        exp    = float(arr.mean())
        std    = float(arr.std()) + 1e-9
        sharpe = exp / std * np.sqrt(252)
        neg    = arr[arr < 0]
        sortino_denom = float(np.std(neg)) + 1e-9 if len(neg) > 0 else 1e-9
        sortino = exp / sortino_denom * np.sqrt(252)
        calmar  = (float(equity[-1]) / max_dd) if max_dd > 0 else 0.0
        return {
            "n_trades":      len(arr),
            "winrate":       round(wr * 100, 1),
            "profit_factor": round(pf, 2),
            "sharpe":        round(sharpe, 2),
            "sortino":       round(sortino, 2),
            "max_drawdown":  round(max_dd, 4),
            "calmar":        round(calmar, 2),
            "expectancy":    round(exp, 4),
            "total_return":  round(float(equity[-1]), 4),
        }

    @staticmethod
    def _simulate_trades_from_df(df, signal_col="signal", ret_col="fwd_ret",
                                  entry_col="close", atr_col="atr",
                                  slippage_pct=0.0003):
        """
        Extrae retornos por trade desde un DataFrame con señales precalculadas.
        """
        returns = []
        for _, row in df.iterrows():
            sig = row.get(signal_col, "NEUTRAL")
            if sig not in ("LONG", "SHORT"):
                continue
            fwd = float(row.get(ret_col, 0))
            slp = slippage_pct * (1 if sig == "LONG" else -1)
            ret = (fwd - slp) if sig == "LONG" else (-fwd - slp)
            returns.append(ret)
        return returns

    # ── WALK-FORWARD ───────────────────────────────────────────────────────────
    def run_walk_forward(self, df, signal_col="signal", ret_col="fwd_ret",
                         n_windows=5):
        """
        Divide df en n ventanas, evalúa train vs test en cada ciclo.
        Retorna métricas out-of-sample acumuladas.
        """
        if df is None or len(df) < BT_MIN_TRADES * 2:
            return {"error": "Datos insuficientes para walk-forward"}
        n = len(df)
        window = n // n_windows
        oos_returns = []
        window_results = []
        for i in range(n_windows - 1):
            train_end = (i + 1) * window
            test_end  = min(train_end + window, n)
            # train (in-sample)
            train_df = df.iloc[:train_end]
            # test (out-of-sample)
            test_df  = df.iloc[train_end:test_end]
            train_rets = self._simulate_trades_from_df(train_df, signal_col, ret_col)
            test_rets  = self._simulate_trades_from_df(test_df,  signal_col, ret_col)
            oos_returns.extend(test_rets)
            window_results.append({
                "window":     i + 1,
                "train_n":    len(train_rets),
                "test_n":     len(test_rets),
                "test_wr":    round(sum(1 for r in test_rets if r > 0) / max(len(test_rets), 1) * 100, 1),
                "test_ret":   round(sum(test_rets), 4),
            })
        oos_metrics = self._calc_metrics(oos_returns)
        oos_metrics["windows"] = window_results
        oos_metrics["is_robust"] = (
            oos_metrics.get("winrate", 0) > 45 and
            oos_metrics.get("profit_factor", 0) > 1.1 and
            oos_metrics.get("max_drawdown", 1) < 0.30
        )
        return oos_metrics

    # ── MONTE CARLO ────────────────────────────────────────────────────────────
    def run_monte_carlo(self, returns_list, slippage_sigma=0.0002):
        """
        Simula BT_MONTECARLO_N reordenamientos de trades + variación de slippage.
        Retorna distribución de equity final, worst-case DD y prob de ruina.
        """
        if len(returns_list) < BT_MIN_TRADES:
            return {"error": "Trades insuficientes para Monte Carlo"}
        finals = []
        max_dds = []
        for _ in range(self.mc_runs):
            shuffled = random.sample(returns_list, len(returns_list))
            slippage = np.random.normal(0, slippage_sigma, len(shuffled))
            adj = [r - s for r, s in zip(shuffled, slippage)]
            equity = np.cumsum(adj)
            finals.append(float(equity[-1]))
            dd = float(np.max(np.maximum.accumulate(equity) - equity))
            max_dds.append(dd)
        finals  = np.array(finals)
        max_dds = np.array(max_dds)
        prob_ruin = float(np.mean(finals < -0.30))  # pérdida > 30%
        return {
            "equity_p5":    round(float(np.percentile(finals, 5)), 4),
            "equity_p50":   round(float(np.percentile(finals, 50)), 4),
            "equity_p95":   round(float(np.percentile(finals, 95)), 4),
            "worst_dd_p95": round(float(np.percentile(max_dds, 95)), 4),
            "prob_ruin":    round(prob_ruin * 100, 1),
            "is_robust":    prob_ruin < 0.05 and np.percentile(finals, 50) > 0,
            "n_runs":       self.mc_runs,
        }

    def quick_backtest_from_log(self):
        """Lee signals.csv y retorna métricas para display."""
        try:
            df = pd.read_csv(SIGNAL_LOG_CSV)
            if len(df) < 5:
                return {}
            closed = df[df["result"].isin(["WIN","LOSS"])].copy()
            if len(closed) < 5:
                return {}
            returns = []
            for _, row in closed.iterrows():
                entry = float(row.get("entry", 0) or 0)
                tp1   = float(row.get("tp1", 0) or 0)
                sl    = float(row.get("sl", 0) or 0)
                if entry == 0 or tp1 == 0 or sl == 0:
                    continue
                is_win  = row["result"] == "WIN"
                sig     = row.get("type", "NEUTRAL")
                if sig == "LONG":
                    ret = (tp1 - entry) / entry if is_win else (sl - entry) / entry
                elif sig == "SHORT":
                    ret = (entry - tp1) / entry if is_win else (entry - sl) / entry
                else:
                    continue
                returns.append(float(ret))
            if len(returns) < 5:
                return {}
            metrics = self._calc_metrics(returns)
            mc      = self.run_monte_carlo(returns)
            metrics["monte_carlo"] = mc
            return metrics
        except Exception as e:
            print(f"[WFBacktester] {e}")
            return {}


# ══════════════════════════════════════════════════════════════════════════════
#  WIDTH CACHE
# ══════════════════════════════════════════════════════════════════════════════
class WidthCache:
    def __init__(self):
        self._cache = {}

    def get(self, widget):
        wid = id(widget)
        cached = self._cache.get(wid, 0)
        if cached > 10:
            real = widget.winfo_width()
            if abs(real - cached) > 10:
                self._cache[wid] = real
            return self._cache[wid]
        real = widget.winfo_width()
        self._cache[wid] = max(real, 1)
        return self._cache[wid]

_WIDTH_CACHE = WidthCache()


# ══════════════════════════════════════════════════════════════════════════════
#  MT5 ENGINES (sin cambios funcionales)
# ══════════════════════════════════════════════════════════════════════════════
class MT5PriceEngine:
    def __init__(self, symbol, verifier):
        self.symbol    = symbol
        self._verifier = verifier
        self.connected = False
        self.account   = {}
        self._demo_p   = 3300.0
        self._rng      = np.random.default_rng(42)
        self._tick_lock = threading.Lock()
        self._last_tick = None
        self._actual_symbol = None # To store the dynamically found symbol

    def _find_mt5_symbol(self, base_symbol):
        if not MT5_AVAILABLE:
            return None
        
        # Pre-emptive search for common variations
        search_terms = [base_symbol, "GOLD", "XAUUSD"]
        suffixes = ["", ".pro", ".raw", ".f", "m", "_i", "+", "s", "_s", "_pro", "_raw", ".ecn", ".stp"]
        
        # Get all available symbols
        symbols = mt5.symbols_get()
        if symbols is None:
            print("[MT5] Could not get symbols list from MT5 terminal.")
            return None

        # 1. First pass: exact matches or with known suffixes
        for term in search_terms:
            for suffix in suffixes:
                candidate = term + suffix
                if mt5.symbol_select(candidate, True):
                    print(f"[MT5] Found and selected symbol: {candidate}")
                    return candidate

        # 2. Second pass: fuzzy match (contains XAU or GOLD)
        for s in symbols:
            name_upper = s.name.upper()
            if "XAU" in name_upper or "GOLD" in name_upper:
                if mt5.symbol_select(s.name, True):
                    print(f"[MT5] Fuzzy matched and selected: {s.name}")
                    return s.name

        print(f"[MT5] CRITICAL: No gold-related symbol found in Market Watch.")
        return None


    def connect(self):
        if not MT5_AVAILABLE:
            self.connected = False
            return False
        try:
            # Force connection to SureLeverage if specified, otherwise general init
            if not mt5.initialize(server="SureLeverage Funding"):
                print("[MT5] Init with server 'SureLeverage Funding' failed, trying default...")
                if not mt5.initialize():
                    self.connected = False
                    return False

            # Dynamically find the correct symbol (CRITICAL for real-time price)
            self._actual_symbol = self._find_mt5_symbol(self.symbol)
            if not self._actual_symbol:
                print(f"[MT5] Failed to find any Gold symbol for {self.symbol}")
                self.connected = False
                return False

            # Force symbol to be visible in Market Watch for data flow
            mt5.symbol_select(self._actual_symbol, True)
            
            acc = mt5.account_info()
            if acc is None:
                self.connected = False
                return False
                
            self.account = {
                "login":   acc.login,
                "balance": acc.balance,
                "type":    "REAL" if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL else "DEMO",
                "server":  acc.server,
                "currency": acc.currency,
            }
            self.connected = True
            print(f"[MT5] CONNECTED TO {self.account['server']} | Symbol: {self._actual_symbol}")
            return True

        except Exception as e:
            print("[MT5] connect ERROR:")
            traceback.print_exc()
            self.connected = False
            return False

    def get_rates(self, timeframe_str, count):
        tf_map = {"M1": mt5.TIMEFRAME_M1 if MT5_AVAILABLE else 1,
                  "M5": mt5.TIMEFRAME_M5 if MT5_AVAILABLE else 5,
                  "M15": mt5.TIMEFRAME_M15 if MT5_AVAILABLE else 15,
                  "H1": mt5.TIMEFRAME_H1 if MT5_AVAILABLE else 60,
                  "D1": mt5.TIMEFRAME_D1 if MT5_AVAILABLE else 1440}
        if not self.connected or not MT5_AVAILABLE:
            return None
        try:
            tf  = tf_map.get(timeframe_str, mt5.TIMEFRAME_M5)
            raw = mt5.copy_rates_from_pos(self._actual_symbol, tf, 0, count)
            if raw is None or len(raw) == 0:
                return None        
            return [{"time": int(r['time']), "open": float(r['open']),
                    "high": float(r['high']), "low": float(r['low']),
                    "close": float(r['close']), "tick_volume": float(r['tick_volume']),
                    "real_volume": float(r['real_volume'] if 'real_volume' in r.dtype.names else r['tick_volume'])}
                    for r in raw]
        except Exception as e:
            print(f"[MT5] get_rates: {e}")
            return None

    def get_current_price(self):
        if not self.connected or not MT5_AVAILABLE or not self._actual_symbol:
            return None
        try:
            tick = mt5.symbol_info_tick(self._actual_symbol)
            if tick is None:
                # One last attempt to find the symbol if it was lost
                self._actual_symbol = self._find_mt5_symbol(self.symbol)
                tick = mt5.symbol_info_tick(self._actual_symbol)
                if tick is None: return None
            with self._tick_lock:
                self._last_tick = {"bid": tick.bid, "ask": tick.ask,
                                   "last": tick.last if tick.last > 0 else (tick.bid + tick.ask) / 2,
                                   "time": tick.time}
            if self._verifier:
                self._verifier.record_tick(tick.time)
            return self._last_tick
        except:
            return None


class RealAccountVerifier:
    def __init__(self):
        self._lock           = threading.Lock()
        self._latencies      = deque(maxlen=200)
        self._last_tick_ts   = None
        self._start_time     = time.time()
        self._conn_drops     = 0
        self._real_ok        = False
        self._status         = "INITIALIZING"

    def record_tick(self, server_ts):
        local_ts = time.time()
        lat_ms   = (local_ts - server_ts) * 1000 if server_ts > 0 else 0
        with self._lock:
            self._latencies.append(abs(lat_ms))
            self._last_tick_ts = local_ts
            self._real_ok  = True
            self._status   = "CONNECTED"

    def get_state(self):
        with self._lock:
            lats    = list(self._latencies)
            avg_lat = float(np.mean(lats)) if lats else 0.0
            p99_lat = float(np.percentile(lats, 99)) if lats else 0.0
            uptime  = str(datetime.timedelta(seconds=int(time.time() - self._start_time)))
            stale   = (time.time() - (self._last_tick_ts or 0)) > 30
            if stale and self._real_ok:
                self._conn_drops += 1
                self._real_ok = False
                self._status  = "STALE"
            return {
                "real_ok":         self._real_ok,
                "avg_latency_ms":  avg_lat,
                "p99_latency_ms":  p99_lat,
                "uptime":          uptime,
                "conn_drops":      self._conn_drops,
                "status":          self._status,
            }


class MT5Watchdog(threading.Thread):
    def __init__(self, engine, verifier):
        super().__init__(daemon=True)
        self.engine   = engine
        self.verifier = verifier
        self._running = True

    def run(self):
        while self._running:
            time.sleep(RECONNECT_SECONDS)
            if not self.engine.connected:
                print("[Watchdog] Reconnecting MT5...")
                self.engine.connect()

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  REAL ORDER BOOK ENGINE
# ══════════════════════════════════════════════════════════════════════════════
class RealOrderBookEngine:
    def __init__(self, mt5_engine):
        self.mt5  = mt5_engine
        self.mode = "CHECKING"
        self._lock              = threading.Lock()
        self._data              = None
        self._running           = False
        self._rng               = np.random.default_rng(77)
        self._imbalance_history = deque(maxlen=100)

    def start(self):
        if not self.mt5.connected:
            # print("[OB] Demo mode disabled for real account enforcement.")
            return
        self._check_l2_availability()
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _check_l2_availability(self):
        if not MT5_AVAILABLE:
            self.mode = "DEMO"; return
        try:
            ok = mt5.market_book_add(self.mt5.symbol)
            if not ok:
                self.mode = "RECONSTRUCTED"; return
            time.sleep(0.4)
            book = mt5.market_book_get(self.mt5.symbol)
            if book is None or len(book) == 0:
                try: mt5.market_book_release(self.mt5.symbol)
                except: pass
                self.mode = "RECONSTRUCTED"
            else:
                self.mode = "REAL"
        except:
            self.mode = "RECONSTRUCTED"

    def _start_demo_loop(self):
        self.mode = "DEMO"; self._running = True
        threading.Thread(target=self._demo_loop, daemon=True).start()

    def _demo_loop(self):
        price = BASE_PRICE or 3300.0
        while self._running:
            price += self._rng.normal(0, 0.10)
            step   = 0.5
            asks   = []; bids = []; tot_a = 0; tot_b = 0
            for i in range(1, ORDERBOOK_LEVELS + 1):
                decay = 1 / (1 + i * 0.25)
                va = max(0.1, float(abs(self._rng.normal(10, 5))) * decay)
                vb = max(0.1, float(abs(self._rng.normal(10, 5))) * decay)
                if self._rng.random() < 0.08: va *= self._rng.uniform(3, 8)
                if self._rng.random() < 0.08: vb *= self._rng.uniform(3, 8)
                asks.append({"price": round(price + i*step, 2), "volume": round(va, 2),
                             "side": "ASK", "is_wall": va > 25})
                bids.append({"price": round(price - i*step, 2), "volume": round(vb, 2),
                             "side": "BID", "is_wall": vb > 25})
                tot_a += va; tot_b += vb
            imb = (tot_b - tot_a) / max(tot_b + tot_a, 1)
            with self._lock:
                self._data = {"asks": asks, "bids": bids, "mode": "DEMO",
                    "spread": round(asks[0]["price"] - bids[0]["price"], 2),
                    "imbalance": round(imb, 3), "total_ask": round(tot_a, 2),
                    "total_bid": round(tot_b, 2), "ts": time.time()}
            self._imbalance_history.append(imb)
            time.sleep(BOOK_UPDATE_MS / 1000)

    def _loop(self):
        while self._running:
            try:
                if self.mode == "REAL": self._update_real()
                else: self._update_reconstructed()
            except Exception as e:
                print(f"[OB] {e}")
            time.sleep(BOOK_UPDATE_MS / 1000)

    def _update_real(self):
        try:
            book = mt5.market_book_get(self.mt5.symbol)
            if book is None:
                self.mode = "RECONSTRUCTED"; return
            asks = []; bids = []; tot_a = 0; tot_b = 0
            for e in book:
                vol  = round(e.volume, 2)
                item = {"price": round(e.price, 2), "volume": vol,
                        "side": "ASK" if e.type == 1 else "BID", "is_wall": vol > 30}
                if e.type == 1: asks.append(item); tot_a += vol
                else:           bids.append(item); tot_b += vol
            asks.sort(key=lambda x: x["price"])
            bids.sort(key=lambda x: x["price"], reverse=True)
            imb = (tot_b - tot_a) / max(tot_b + tot_a, 1)
            self._imbalance_history.append(imb)
            with self._lock:
                self._data = {
                    "asks": asks[:ORDERBOOK_LEVELS], "bids": bids[:ORDERBOOK_LEVELS],
                    "mode": "REAL",
                    "spread": round(asks[0]["price"] - bids[0]["price"], 2) if asks and bids else 0,
                    "imbalance": round(imb, 3), "total_ask": round(tot_a, 2),
                    "total_bid": round(tot_b, 2), "ts": time.time()}
        except Exception as e:
            print(f"[OB] real: {e}")

    def _update_reconstructed(self):
        if not self.mt5.connected: return
        try:
            from_dt = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
            ticks   = mt5.copy_ticks_from(self.mt5._actual_symbol, from_dt, 1000, mt5.COPY_TICKS_ALL)
            if ticks is None or len(ticks) == 0: return
            step = 0.5; ask_lvl = {}; bid_lvl = {}
            for t in ticks:
                a = round(round(float(t['ask']) / step) * step, 2)
                b = round(round(float(t['bid']) / step) * step, 2)
                v = max(float(t.get('volume', 0.01)), 0.01)
                ask_lvl[a] = ask_lvl.get(a, 0) + v
                bid_lvl[b] = bid_lvl.get(b, 0) + v
            tick_now = mt5.symbol_info_tick(self.mt5._actual_symbol)
            cur_ask  = tick_now.ask if tick_now else 0
            cur_bid  = tick_now.bid if tick_now else 0
            asks = sorted([{"price": p, "volume": round(v, 2), "side": "ASK", "is_wall": v > 20}
                           for p, v in ask_lvl.items() if p >= cur_bid],
                          key=lambda x: x["price"])
            bids = sorted([{"price": p, "volume": round(v, 2), "side": "BID", "is_wall": v > 20}
                           for p, v in bid_lvl.items() if p <= cur_ask],
                          key=lambda x: x["price"], reverse=True)
            tot_a = sum(e["volume"] for e in asks[:ORDERBOOK_LEVELS])
            tot_b = sum(e["volume"] for e in bids[:ORDERBOOK_LEVELS])
            imb   = (tot_b - tot_a) / max(tot_b + tot_a, 1)
            self._imbalance_history.append(imb)
            with self._lock:
                self._data = {
                    "asks": asks[:ORDERBOOK_LEVELS], "bids": bids[:ORDERBOOK_LEVELS],
                    "mode": "RECONSTRUCTED",
                    "spread": round(cur_ask - cur_bid, 2) if cur_ask else 0,
                    "imbalance": round(imb, 3), "total_ask": round(tot_a, 2),
                    "total_bid": round(tot_b, 2), "ts": time.time()}
        except Exception as e:
            print(f"[OB] recon: {e}")

    def get(self):
        with self._lock:
            return dict(self._data) if self._data else None

    def stop(self):
        self._running = False
        if self.mt5.connected and MT5_AVAILABLE and self.mode == "REAL":
            try: mt5.market_book_release(self.mt5.symbol)
            except: pass


# ══════════════════════════════════════════════════════════════════════════════
#  REAL DELTA ENGINE
# ══════════════════════════════════════════════════════════════════════════════
class RealDeltaEngine:
    def __init__(self, mt5_engine, verifier):
        self.mt5          = mt5_engine
        self._verifier    = verifier
        self._lock        = threading.Lock()
        self._running     = False
        self._ticks       = deque(maxlen=TICK_BUFFER_SIZE)
        self._delta_cum   = 0.0
        self._buy_vol     = 0.0
        self._sell_vol    = 0.0
        self._passive_vol = 0.0
        self._last_price  = 0.0
        self._price_delta = {}
        self._delta_by_bar = deque(maxlen=100)
        self._last_fetch_t = None
        self._last_minute  = -1

    def start(self):
        self._running = True
        if self.mt5.connected:
            threading.Thread(target=self._loop_real, daemon=True).start()
        else:
            # print("[Delta] Demo mode disabled for real account enforcement.")
            pass

    def _loop_real(self):
        while self._running:
            try: self._fetch_and_classify()
            except Exception as e: print(f"[Delta] {e}")
            time.sleep(0.8)

    def _fetch_and_classify(self):
        now     = datetime.datetime.utcnow()
        from_dt = self._last_fetch_t if self._last_fetch_t else now - datetime.timedelta(minutes=2)
        ticks   = None
        try:
            ticks = mt5.copy_ticks_from(self.mt5._actual_symbol, from_dt, 2000, mt5.COPY_TICKS_TRADE)
        except: pass
        if ticks is None or len(ticks) == 0:
            try: ticks = mt5.copy_ticks_from(self.mt5._actual_symbol, from_dt, 2000, mt5.COPY_TICKS_ALL)
            except: pass
        if ticks is None or len(ticks) == 0:
            self._last_fetch_t = now; return
        self._last_fetch_t = now
        for t in ticks:
            ask    = float(t['ask']); bid = float(t['bid'])
            last   = float(t.get('last', 0))
            volume = max(float(t.get('volume', 0.01)), 0.01)
            if last > 0:
                side = "BUY" if last >= ask else "SELL" if last <= bid else "PASSIVE"
            else:
                side = (("BUY" if ask > self._last_price
                         else "SELL" if ask < self._last_price
                         else "PASSIVE") if self._last_price > 0 else "PASSIVE")
            self._last_price = ask
            p_lvl = round(round(ask / 0.5) * 0.5, 2)
            with self._lock:
                self._ticks.append({"time": float(t['time']), "price": round(ask, 2),
                                    "ask": round(ask, 2), "bid": round(bid, 2),
                                    "volume": volume, "side": side, "p_lvl": p_lvl})
                if side == "BUY":
                    self._buy_vol  += volume; self._delta_cum += volume
                    self._price_delta[p_lvl] = self._price_delta.get(p_lvl, 0) + volume
                elif side == "SELL":
                    self._sell_vol += volume; self._delta_cum -= volume
                    self._price_delta[p_lvl] = self._price_delta.get(p_lvl, 0) - volume
                else:
                    self._passive_vol += volume

    def get_state(self):
        with self._lock:
            total   = self._buy_vol + self._sell_vol
            buy_pct = (self._buy_vol / total * 100) if total > 0 else 50.0
            recent  = list(self._ticks)[-200:]
            rb = sum(t["volume"] for t in recent if t["side"] == "BUY")
            rs = sum(t["volume"] for t in recent if t["side"] == "SELL")
            rp = sum(t["volume"] for t in recent if t["side"] == "PASSIVE")
            rt = max(rb + rs + rp, 1e-9)
            return {
                "delta_cum":     round(self._delta_cum, 2),
                "buy_vol":       round(self._buy_vol, 2),
                "sell_vol":      round(self._sell_vol, 2),
                "buy_pct":       round(buy_pct, 1),
                "sell_pct":      round(100 - buy_pct, 1),
                "aggr_buy_pct":  round(rb / rt * 100, 1),
                "aggr_sell_pct": round(rs / rt * 100, 1),
                "passive_pct":   round(rp / rt * 100, 1),
                "price_delta":   dict(self._price_delta),
                "n_ticks":       len(self._ticks),
                "positive":      self._delta_cum >= 0,
                "delta_bars":    list(self._delta_by_bar)[-50:],
            }

    def get_heatmap_column(self, price, atr):
        lo = price - atr * 4.5; hi = price + atr * 4.5; n = 60
        step = (hi - lo) / n; col = []
        with self._lock:
            pd_data = dict(self._price_delta)
        if not pd_data: return []
        max_abs = max(abs(v) for v in pd_data.values()) if pd_data else 1.0
        max_abs = max(max_abs, 1e-9)
        for i in range(n):
            p_center = lo + (i + 0.5) * step
            p_lvl    = round(round(p_center / 0.5) * 0.5, 2)
            dv = pd_data.get(p_lvl, 0)
            for adj in [p_lvl - 0.5, p_lvl + 0.5]:
                dv += pd_data.get(adj, 0) * 0.30
            intensity  = min(abs(dv) / max_abs, 1.0)
            buy_ratio  = 0.5 + (dv / (max_abs * 2)) if max_abs > 0 else 0.5
            buy_ratio  = max(0.0, min(1.0, buy_ratio))
            col.append({"price": round(p_center, 2), "buy_ratio": buy_ratio,
                        "intensity": intensity, "delta": round(dv, 2)})
        return col

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  INSTITUTIONAL PRICE FEED
# ══════════════════════════════════════════════════════════════════════════════
class InstitutionalPriceFeed:
    YAHOO_URL = "https://query2.finance.yahoo.com/v8/finance/chart/XAUUSD=X?interval=1m&range=1d"
    HEADERS   = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}

    def __init__(self):
        self._lock    = threading.Lock()
        self._price   = None; self._change = None; self._pct = None
        self._high    = None; self._low    = None; self._ts  = 0
        self._running = False

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            try: self._fetch()
            except Exception as e: print(f"[InstFeed] {e}")
            time.sleep(30)

    def _fetch(self):
        try:
            resp = requests.get(self.YAHOO_URL, headers=self.HEADERS, timeout=10)
            if resp.status_code != 200: return
            data = resp.json()
            res  = data.get("chart", {}).get("result", [{}])
            if not res: return
            meta = res[0].get("meta", {})
            px   = meta.get("regularMarketPrice")
            prev = meta.get("previousClose", px)
            if px is None: return
            with self._lock:
                self._price  = px
                self._change = round(px - (prev or px), 2)
                self._pct    = round(self._change / (prev or 1) * 100, 3)
                self._high   = meta.get("regularMarketDayHigh")
                self._low    = meta.get("regularMarketDayLow")
                self._ts     = time.time()
        except Exception as e:
            print(f"[InstFeed] {e}")

    def get(self):
        with self._lock:
            if self._price is None: return None
            age = time.time() - self._ts
            return {"price": self._price, "change": self._change, "pct": self._pct,
                    "high": self._high, "low": self._low,
                    "age_s": round(age), "stale": age > 120}

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  COMMODITY NEWS ANALYZER
# ══════════════════════════════════════════════════════════════════════════════
class CommodityNewsAnalyzer:
    URL = ("https://commodity.worldmonitor.app/?lat=20.0000&lon=0.0000&zoom=1.58"
           "&view=global&timeRange=7d&layers=pipelines%2Cais%2Csanctions%2Cweather%2C"
           "economic%2Cwaterways%2Coutages%2Cnatural%2Cminerals%2Cfires%2Cclimate%2CtradeRoutes")
    HIGH_IMPACT_KW   = ["war","sanction","embargo","strike","blockade","explosion",
                        "mine collapse","coup","extreme weather","hurricane","flooding",
                        "military","attack","terror"]
    MEDIUM_IMPACT_KW = ["delay","slowdown","maintenance","protest","fire","drought",
                        "labor dispute","shortage","disruption"]
    LOW_IMPACT_KW    = ["routine","update","forecast","meeting","report","analysis"]

    def __init__(self, update_interval_sec=60):
        self.update_interval = update_interval_sec
        self.current_events  = []
        self.highest_impact  = "LOW"
        self.risk_multiplier = 1.0
        self._lock           = threading.Lock()
        self._running        = True
        self._last_fetch     = 0

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            self._fetch_and_classify()
            time.sleep(self.update_interval)

    def _fetch_and_classify(self):
        try:
            resp = requests.get(self.URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200: return
            soup   = BeautifulSoup(resp.text, 'html.parser')
            alerts = soup.find_all(
                ['div','li','span','article'],
                class_=re.compile('alert|event|incident|warning|news|card|item')
            )
            events = []; impact_level = "LOW"
            for alert in alerts:
                text = alert.get_text().lower()
                if any(kw in text for kw in self.HIGH_IMPACT_KW):
                    events.append(("HIGH", text[:300]))
                    impact_level = "HIGH"
                elif any(kw in text for kw in self.MEDIUM_IMPACT_KW) and impact_level != "HIGH":
                    events.append(("MEDIUM", text[:300]))
                    if impact_level == "LOW": impact_level = "MEDIUM"
                elif any(kw in text for kw in self.LOW_IMPACT_KW):
                    events.append(("LOW", text[:200]))
            risk_mult = (0.0 if impact_level == "HIGH"
                         else 0.5 if impact_level == "MEDIUM" else 1.0)
            with self._lock:
                self.current_events  = events[:25]
                self.highest_impact  = impact_level
                self.risk_multiplier = risk_mult
                self._last_fetch     = time.time()
        except Exception as e:
            print(f"[CommodityNews] {e}")

    def get_status(self):
        with self._lock:
            return {"highest_impact":  self.highest_impact,
                    "risk_multiplier": self.risk_multiplier,
                    "events":          self.current_events.copy(),
                    "last_update":     self._last_fetch}

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  QUANT ENGINE — Cálculos técnicos MT5
# ══════════════════════════════════════════════════════════════════════════════
class QuantEngine:
    def calc_vwap_bands(self, rates):
        if not rates or len(rates) < 10: return None
        sess = rates[-SESSION_BARS_M5:] if len(rates) >= SESSION_BARS_M5 else rates
        prices_arr = np.array([r['close'] for r in sess])
        highs      = np.array([r['high']  for r in sess])
        lows       = np.array([r['low']   for r in sess])
        opens      = np.array([r['open']  for r in sess])
        volumes    = np.array([r['tick_volume'] for r in sess], dtype=float)
        typical    = (highs + lows + prices_arr) / 3
        cum_pv     = np.cumsum(typical * volumes)
        cum_vol    = np.cumsum(np.where(volumes > 0, volumes, 1e-9))
        vwap       = cum_pv / cum_vol
        devsq      = np.cumsum(((typical - vwap) ** 2) * volumes)
        std        = np.sqrt(devsq / cum_vol)
        std        = np.where(std < 1e-6, 1e-6, std)
        sd_pos     = (prices_arr[-1] - vwap[-1]) / std[-1]
        return {
            "prices": prices_arr, "vwap": vwap, "std": std,
            "highs": highs, "lows": lows, "opens": opens, "volumes": volumes,
            "current": float(prices_arr[-1]),
            "sd_pos":  float(sd_pos),
            "vwap_last": float(vwap[-1]),
            "std_last":  float(std[-1]),
        }

    def calc_order_blocks(self, rates, n_bars=100):
        if not rates or len(rates) < 10: return []
        sess = rates[-n_bars:]
        obs  = []
        for i in range(2, len(sess) - 1):
            if sess[i-1]['close'] < sess[i-1]['open'] and sess[i]['close'] > sess[i-1]['open']:
                obs.append({'type': 'BULLISH', 'top': sess[i-1]['high'],
                            'bottom': sess[i-1]['low'], 'time': sess[i-1]['time'],
                            'active': sess[-1]['close'] > sess[i-1]['low']})
            elif sess[i-1]['close'] > sess[i-1]['open'] and sess[i]['close'] < sess[i-1]['low']:
                obs.append({'type': 'BEARISH', 'top': sess[i-1]['high'],
                            'bottom': sess[i-1]['low'], 'time': sess[i-1]['time'],
                            'active': sess[-1]['close'] < sess[i-1]['high']})
        return [ob for ob in obs if ob['active']][-6:]

    def calc_volume_profile_global(self, rates):
        if not rates or len(rates) < 5: return None
        sess = rates[-SESSION_BARS_M5:] if len(rates) >= SESSION_BARS_M5 else rates
        prices = []; volumes = []
        for r in sess:
            hl = r['high'] - r['low']
            if hl < 1e-6: hl = 0.01
            for j in range(5):
                prices.append(r['low'] + hl * (j + 0.5) / 5)
                volumes.append(r['tick_volume'] / 5)
        prices  = np.array(prices); volumes = np.array(volumes)
        if len(prices) == 0: return None
        mn   = np.floor(np.min(prices) / PRICE_STEP) * PRICE_STEP
        mx   = np.ceil(np.max(prices)  / PRICE_STEP) * PRICE_STEP
        bins = np.arange(mn, mx + PRICE_STEP, PRICE_STEP)
        if len(bins) < 2: return None
        hist, edges = np.histogram(prices, bins=bins, weights=volumes)
        poc_idx = int(np.argmax(hist)); poc = float(edges[poc_idx])
        total   = float(np.sum(hist)); target = total * VALUE_AREA_PCT
        cur_vol = float(hist[poc_idx]); u_idx = poc_idx; l_idx = poc_idx
        while cur_vol < target:
            u_v = float(hist[u_idx + 1]) if u_idx + 1 < len(hist) else 0.0
            l_v = float(hist[l_idx - 1]) if l_idx - 1 >= 0 else 0.0
            if u_v >= l_v and u_idx + 1 < len(hist): u_idx += 1; cur_vol += u_v
            elif l_idx - 1 >= 0: l_idx -= 1; cur_vol += l_v
            else: break
        vah = float(edges[min(u_idx, len(edges) - 1)])
        val = float(edges[l_idx])
        nonzero  = hist[hist > 0]
        mean_vol = float(np.mean(nonzero)) if len(nonzero) > 0 else 1.0
        return {"bins": edges[:-1], "hist": hist, "poc": poc, "vah": vah, "val": val,
                "delta_poc": float(hist[poc_idx]) / mean_vol if mean_vol > 0 else 1.0,
                "mean_vol": mean_vol, "total_vol": total}

    def calc_anomalies(self, rates, window=ANOMALY_WINDOW, sigma=ANOMALY_SIGMA):
        if not rates or len(rates) < window + 2: return None
        closes = np.array([r['close'] for r in rates])
        rets   = np.diff(closes) / np.maximum(closes[:-1], 1e-9) * 100
        mr = np.zeros_like(rets); sr = np.zeros_like(rets)
        for i in range(window, len(rets)):
            w = rets[i - window:i]; mr[i] = np.mean(w); sr[i] = np.std(w)
        upper = mr + sr * sigma; lower = mr - sr * sigma
        sig = ("EXTREME_HIGH" if rets[-1] > upper[-1] else
               "EXTREME_LOW"  if rets[-1] < lower[-1] else "NEUTRAL")
        return {"rets": rets, "upper": upper, "lower": lower, "mean": mr,
                "last_ret": float(rets[-1]), "last_upper": float(upper[-1]),
                "last_lower": float(lower[-1]), "signal": sig}

    def calc_pivots(self, rates_d1):
        if not rates_d1 or len(rates_d1) < 2: return None
        prev = rates_d1[-2]; H = prev['high']; L = prev['low']; C = prev['close']
        PP = (H + L + C) / 3
        return {"PP": round(PP, 2), "R1": round(2*PP-L, 2), "R2": round(PP+(H-L), 2),
                "R3": round(H+2*(PP-L), 2), "S1": round(2*PP-H, 2), "S2": round(PP-(H-L), 2),
                "S3": round(L-2*(H-PP), 2), "prev_H": round(H, 2),
                "prev_L": round(L, 2), "prev_C": round(C, 2)}

    def calc_fibonacci(self, rates, n_bars=SESSION_BARS_M5):
        if not rates or len(rates) < 10: return None
        sess   = rates[-n_bars:] if len(rates) >= n_bars else rates
        s_high = max(r['high'] for r in sess)
        s_low  = min(r['low']  for r in sess)
        rng    = s_high - s_low
        if rng < 0.01: return None
        levels = {f: round(s_high - f*rng, 2) for f in FIB_LEVELS}
        return {"levels": levels, "swing_h": s_high, "swing_l": s_low,
                "range": rng, "midpoint": round((s_high + s_low) / 2, 2)}

    def calc_atr(self, rates, period=ATR_PERIOD):
        if not rates or len(rates) < period + 1: return 1.5
        trs = [max(rates[i]['high'] - rates[i]['low'],
                   abs(rates[i]['high'] - rates[i-1]['close']),
                   abs(rates[i]['low']  - rates[i-1]['close']))
               for i in range(1, len(rates))]
        return float(np.mean(trs[-period:]))

    def calc_rsi(self, rates, period=14):
        if not rates or len(rates) < period + 2: return 50.0
        closes = np.array([r['close'] for r in rates])
        deltas = np.diff(closes)
        gains  = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_g  = np.mean(gains[-period:])
        avg_l  = np.mean(losses[-period:])
        if avg_l == 0: return 100.0
        return round(100 - 100 / (1 + avg_g / avg_l), 1)

    def calc_macd(self, rates, fast=12, slow=26, signal_p=9):
        if not rates or len(rates) < slow + signal_p + 5:
            return {"macd": 0.0, "signal": 0.0, "hist": 0.0, "trend": "NEUTRAL"}
        closes = pd.Series([r['close'] for r in rates])
        ema_f  = closes.ewm(span=fast, adjust=False).mean()
        ema_s  = closes.ewm(span=slow, adjust=False).mean()
        macd   = ema_f - ema_s
        sig    = macd.ewm(span=signal_p, adjust=False).mean()
        hist   = macd - sig
        trend  = "BULLISH" if hist.iloc[-1] > 0 else "BEARISH"
        return {"macd": float(macd.iloc[-1]), "signal": float(sig.iloc[-1]),
                "hist": float(hist.iloc[-1]), "trend": trend}

    def evaluate_strategy(self, vwap_data, vol_prof, anom_m5, anom_m15,
                          pivots, fib, atr_m15, news_state, rsi=50,
                          macd_data=None, delta_state=None,
                          reaction_zones=None, cot_data=None,
                          macro_state=None, liq_state=None):
        if not all([vwap_data, vol_prof, anom_m5, anom_m15]):
            return self._neutral(0, {})
        if news_state and news_state.get("impact"):
            return self._neutral(0, {}, news_impact=True)

        # ── NUEVO v9.0: Bloquear entradas en baja liquidez ────────────────────
        if liq_state and not liq_state.get("can_enter", True):
            return self._neutral(30, {}, news_impact=False)

        price  = vwap_data["current"]
        sd_pos = vwap_data["sd_pos"]
        poc    = vol_prof["poc"]
        vah    = vol_prof["vah"]
        val    = vol_prof["val"]
        a5     = anom_m5["signal"]
        a15_ret = anom_m15["last_ret"]
        utc_h  = datetime.datetime.utcnow().hour
        in_session = TRADE_HOURS[0] <= utc_h < TRADE_HOURS[1]
        quality_ok = (atr_m15 >= MIN_ATR_M15 and vol_prof["delta_poc"] >= MIN_DELTA_POC)

        def near_level(p, levels_list):
            return any(abs(p - lv) < atr_m15 * 1.5 for lv in levels_list)

        pivot_supports    = [pivots[k] for k in ["S1","S2","S3"]] if pivots else []
        pivot_resistances = [pivots[k] for k in ["R1","R2","R3"]] if pivots else []
        fib_supports      = [fib["levels"].get(k, 0) for k in [0.618, 0.786]] if fib else []
        fib_resistances   = [fib["levels"].get(k, 0) for k in [0.236, 0.382]] if fib else []

        near_sup = near_level(price, pivot_supports + fib_supports)
        near_res = near_level(price, pivot_resistances + fib_resistances)

        # Zonas de reacción como confirmador adicional
        near_demand_zone = False
        near_supply_zone = False
        if reaction_zones:
            rz_engine = ReactionZoneEngine()
            sup_z, dem_z = rz_engine.nearest_zone(price, reaction_zones)
            if dem_z and abs(price - dem_z["mid"]) < atr_m15 * 2:
                near_demand_zone = True
            if sup_z and abs(price - sup_z["mid"]) < atr_m15 * 2:
                near_supply_zone = True

        # COT institucional como confirmador
        cot_bull = cot_data and cot_data.get("bias") == "BULLISH"
        cot_bear = cot_data and cot_data.get("bias") == "BEARISH"

        macd_bull = macd_data and macd_data.get("trend") == "BULLISH"
        macd_bear = macd_data and macd_data.get("trend") == "BEARISH"
        delta_bull = delta_state and delta_state.get("buy_pct", 50) > 58
        delta_bear = delta_state and delta_state.get("buy_pct", 50) < 42

        # ── NUEVO v9.0: Sesgo macro como confirmador ──────────────────────────
        macro_bull = macro_state and macro_state.get("bias") == "BULLISH"
        macro_bear = macro_state and macro_state.get("bias") == "BEARISH"

        long_conds = {
            "Precio < VAL":           price < val,
            "VWAP ≤ -1.5σ":           sd_pos <= -ZSCORE_THRESHOLD,
            "Anomalía M5 baja":       a5 == "EXTREME_LOW",
            "Soporte Pivote/Fib":     near_sup,
            "RSI oversold (<35)":     rsi < 35,  # anti-overfitting: era <40
            "Zona Demand activa":     near_demand_zone,
        }
        short_conds = {
            "Precio > VAH":           price > vah,
            "VWAP ≥ +1.5σ":           sd_pos >= ZSCORE_THRESHOLD,
            "Anomalía M5 alta":       a5 == "EXTREME_HIGH",
            "Resistencia Pivote/Fib": near_res,
            "RSI overbought (>65)":   rsi > 65,  # anti-overfitting: era >60
            "Zona Supply activa":     near_supply_zone,
        }

        long_score  = sum(long_conds.values())
        short_score = sum(short_conds.values())

        # Bonus: MACD, Delta, COT — máx +0.5 c/u, pero solo si score base >= 2
        # anti-overfitting: evitar que bonuses acumulativos disparen señales débiles
        if long_score >= 2:
            if macd_bull:  long_score  = min(long_score  + 0.5, 6)
            if delta_bull: long_score  = min(long_score  + 0.5, 6)
            if cot_bull:   long_score  = min(long_score  + 0.5, 6)
        if short_score >= 2:
            if macd_bear:  short_score = min(short_score + 0.5, 6)
            if delta_bear: short_score = min(short_score + 0.5, 6)
            if cot_bear:   short_score = min(short_score + 0.5, 6)

        # ── NUEVO v9.0: Bonus macro alineado (+0.5 — reducido de +1.0) ─────────
        # anti-overfitting: macro ya pesa en QuantScorer, no duplicar aquí
        if macro_bull and long_score >= 2:  long_score  = min(long_score  + 0.5, 6)
        if macro_bear and short_score >= 2: short_score = min(short_score + 0.5, 6)

        news_bull = news_state and news_state.get("sentiment") == "BULLISH"
        news_bear = news_state and news_state.get("sentiment") == "BEARISH"
        news_hi   = news_state and news_state.get("impact", False)

        def prob_calc(score, q_ok, in_sess, news_align):
            # anti-overfitting: probabilidades calibradas más conservadoras
            # score 6 → 78% (era 88%), score 2 → 42% (era 52%)
            base_tbl = {6: 78, 5: 70, 4: 62, 3: 54, 2: 42, 1: 35, 0: 28}
            base = base_tbl.get(min(int(score), 6), 28)
            return min(84, max(25,
                base
                + (3  if q_ok      else  0)
                + (2  if in_sess   else -5)
                + (3  if news_align else  0)
                + (-5 if news_hi   else  0)
            ))

        MAX_SCORE = 6

        if long_score >= SIGNAL_MIN_SCORE:
            entry = price
            sl    = (pivots["S2"] - atr_m15 * 1.5) if pivots else val - atr_m15 * 1.5
            tp1   = poc
            tp2   = pivots["PP"] if pivots else vah
            tp3   = pivots["R1"] if pivots else vah + atr_m15 * 3
            rr    = (tp1 - entry) / max(entry - sl, 0.01)
            return {
                "dir": "LONG", "signal": "BUY ▲",
                "prob":       prob_calc(long_score, quality_ok, in_session, news_bull),
                "score":      long_score, "max_score": MAX_SCORE,
                "entry":      round(entry, 2), "sl": round(sl, 2),
                "tp1":        round(tp1, 2), "tp2": round(tp2, 2), "tp3": round(tp3, 2),
                "rr":         round(rr, 2), "conds": long_conds, "quality": quality_ok,
                "in_session": in_session, "sd_pos": round(sd_pos, 2),
                "news_align": news_bull, "news_impact": news_hi, "rsi": rsi,
                "cot_bias":   cot_data.get("bias", "N/A") if cot_data else "N/A",
            }

        if short_score >= SIGNAL_MIN_SCORE:
            entry = price
            sl    = (pivots["R2"] + atr_m15 * 1.5) if pivots else vah + atr_m15 * 1.5
            tp1   = poc
            tp2   = pivots["PP"] if pivots else val
            tp3   = pivots["S1"] if pivots else val - atr_m15 * 3
            rr    = (entry - tp1) / max(sl - entry, 0.01)
            return {
                "dir": "SHORT", "signal": "SELL ▼",
                "prob":       prob_calc(short_score, quality_ok, in_session, news_bear),
                "score":      short_score, "max_score": MAX_SCORE,
                "entry":      round(entry, 2), "sl": round(sl, 2),
                "tp1":        round(tp1, 2), "tp2": round(tp2, 2), "tp3": round(tp3, 2),
                "rr":         round(rr, 2), "conds": short_conds, "quality": quality_ok,
                "in_session": in_session,  "sd_pos": round(sd_pos, 2),
                "news_align": news_bear,   "news_impact": news_hi, "rsi": rsi,
                "cot_bias":   cot_data.get("bias", "N/A") if cot_data else "N/A",
            }

        best_score = max(long_score, short_score)
        best_conds = long_conds if long_score >= short_score else short_conds
        best_dir   = "LONG"  if long_score >= short_score else "SHORT"
        return self._neutral(
            prob_calc(best_score, quality_ok, in_session, False),
            best_conds, best_dir, int(best_score)
        )

    def _neutral(self, prob=35, conds=None, dir_hint="", score=0, news_impact=False):
        return {"dir": "NEUTRAL", "signal": "WAIT", "prob": prob,
                "score": score, "max_score": 6,
                "entry": None, "sl": None, "tp1": None, "tp2": None, "tp3": None,
                "rr": 0, "conds": conds or {}, "quality": False, "in_session": False,
                "sd_pos": 0.0, "news_align": False, "news_impact": news_impact,
                "dir_hint": dir_hint, "rsi": 50, "cot_bias": "N/A"}


# ══════════════════════════════════════════════════════════════════════════════
#  QUANT SIGNAL GENERATOR (pipeline externo yfinance/OpenBB)
# ══════════════════════════════════════════════════════════════════════════════
class QuantSignalGenerator:
    def __init__(self, vwap_window=VWAP_WINDOW, z_thresh=ZSCORE_THRESHOLD,
                 yield_corr_window=YIELD_CORR_WINDOW):
        self.vwap_window       = vwap_window
        self.z_thresh          = z_thresh
        self.yield_corr_window = yield_corr_window

    def compute_vwap_bands(self, df):
        df = df.copy()
        df['typical'] = (df['high'] + df['low'] + df['close']) / 3.0
        vol = df['volume'].replace(0, 1e-9)
        df['cum_pv']  = (df['typical'] * vol).cumsum()
        df['cum_vol'] = vol.cumsum()
        df['vwap']    = df['cum_pv'] / df['cum_vol']
        df['returns'] = df['close'].pct_change()
        span = max(20, self.vwap_window // 2)
        df['rv'] = df['returns'].ewm(span=span, adjust=False).std() * np.sqrt(252 * 390)
        df['rv'] = df['rv'].clip(lower=1e-6)
        rolling_std  = df['close'].rolling(self.vwap_window, min_periods=10).std()
        rolling_mean = df['close'].rolling(self.vwap_window, min_periods=10).mean()
        rolling_std  = rolling_std.clip(lower=1e-6)
        df['zscore'] = (df['close'] - rolling_mean) / rolling_std
        return df

    @staticmethod
    def _strip_tz(series):
        if isinstance(series.index, pd.DatetimeIndex) and series.index.tz is not None:
            series = series.copy()
            series.index = series.index.tz_localize(None)
        return series

    @staticmethod
    def _normalize_index(df):
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df = df.copy()
            df.index = df.index.tz_localize(None)
        return df

    def compute_yield_anomaly(self, price_df, yield_series):
        zero_sig = pd.Series(0, index=price_df.index)
        if yield_series is None or yield_series.empty:
            return zero_sig
        if len(yield_series) < self.yield_corr_window:
            return zero_sig
        try:
            price_df     = self._normalize_index(price_df)
            yield_series = self._strip_tz(yield_series)
            merged = pd.DataFrame({'gold': price_df['close'], 'yield': yield_series}).dropna()
            if len(merged) < self.yield_corr_window:
                return zero_sig
            merged['gold_ret']   = merged['gold'].pct_change()
            merged['yield_diff'] = merged['yield'].diff()
            merged['corr'] = (merged['gold_ret']
                              .rolling(self.yield_corr_window, min_periods=20)
                              .corr(merged['yield_diff']))
            w = self.yield_corr_window
            merged['yield_z'] = (
                (merged['yield'] - merged['yield'].rolling(w, min_periods=20).mean())
                / merged['yield'].rolling(w, min_periods=20).std().clip(lower=1e-9)
            )
            merged['signal'] = 0
            # anti-overfitting: umbral z-score más exigente (era 1.5)
            merged.loc[(merged['corr'] < -0.35) & (merged['yield_z'] < -2.0), 'signal'] = 1
            merged.loc[(merged['corr'] < -0.35) & (merged['yield_z'] > 2.0),  'signal'] = -1
            return merged['signal'].reindex(price_df.index, fill_value=0).fillna(0)
        except Exception as e:
            print(f"[QuantSignal] yield: {e}")
            return zero_sig

    def compute_dxy_signal(self, price_df, dxy_series):
        zero_sig = pd.Series(0, index=price_df.index)
        if dxy_series is None or dxy_series.empty:
            return zero_sig
        try:
            price_df   = self._normalize_index(price_df)
            dxy_series = self._strip_tz(dxy_series)
            merged = pd.DataFrame({'gold': price_df['close'], 'dxy': dxy_series}).dropna()
            if len(merged) < 20:
                return zero_sig
            w = min(30, len(merged) - 1)
            merged['dxy_z'] = (
                (merged['dxy'] - merged['dxy'].rolling(w, min_periods=10).mean())
                / merged['dxy'].rolling(w, min_periods=10).std().clip(lower=1e-9)
            )
            merged['signal'] = 0
            # anti-overfitting: umbral DXY más exigente (era 1.5)
            merged.loc[merged['dxy_z'] >  2.0, 'signal'] = -1
            merged.loc[merged['dxy_z'] < -2.0, 'signal'] =  1
            return merged['signal'].reindex(price_df.index, fill_value=0).fillna(0)
        except Exception as e:
            print(f"[QuantSignal] DXY: {e}")
            return zero_sig

    def detect_regime(self, df, window=REGIME_WINDOW):
        if len(df) < window:
            return "UNKNOWN"
        prices    = df['close'].values[-window:]
        x         = np.arange(window).reshape(-1, 1)
        lr        = LinearRegression().fit(x, prices)
        slope     = lr.coef_[0]
        price_std = np.std(prices) + 1e-9
        slope_norm = slope / price_std
        y_pred    = lr.predict(x)
        ss_res    = np.sum((prices - y_pred) ** 2)
        ss_tot    = np.sum((prices - np.mean(prices)) ** 2) + 1e-9
        r_squared = 1 - ss_res / ss_tot
        if abs(slope_norm) > REGIME_SLOPE_THRESH and r_squared > 0.35:
            return "TREND_UP" if slope_norm > 0 else "TREND_DOWN"
        return "MEAN_REVERSION"

    def generate_signal(self, df, yield_signal, dxy_signal, news_multiplier):
        if df is None or len(df) < self.vwap_window:
            return "NEUTRAL", 0.0, "UNKNOWN", 0
        last   = df.iloc[-1]
        z      = float(last.get('zscore', 0.0))
        regime = self.detect_regime(df)
        thresh = self.z_thresh if regime.startswith("TREND") else self.z_thresh * 1.5  # anti-overfitting: era 1.25x
        base   = "LONG" if z <= -thresh else "SHORT" if z >= thresh else "NEUTRAL"
        y_sig  = int(yield_signal.iloc[-1]) if len(yield_signal) > 0 else 0
        d_sig  = int(dxy_signal.iloc[-1])   if len(dxy_signal)   > 0 else 0
        votes  = [1 if base == "LONG" else (-1 if base == "SHORT" else 0), y_sig, d_sig]
        vote_sum = sum(votes)
        if vote_sum >= 2:
            final = "LONG"
        elif vote_sum <= -2:
            final = "SHORT"
        elif vote_sum == 1 and abs(z) > thresh:
            final = "LONG"
        elif vote_sum == -1 and abs(z) > thresh:
            final = "SHORT"
        else:
            final = base
        if abs(z) >= ZSCORE_STRONG:
            final = "LONG" if z < 0 else "SHORT"
        if news_multiplier == 0.0:
            final = "NEUTRAL"
        strength = min(3, abs(vote_sum) + (1 if abs(z) >= ZSCORE_STRONG else 0))
        return final, z, regime, int(strength)


# ══════════════════════════════════════════════════════════════════════════════
#  MEAN REVERSION ENGINE
# ══════════════════════════════════════════════════════════════════════════════
class MeanReversionEngine:
    BB_WINDOW   = 20
    BB_STDDEV   = 2.0
    RSI_PERIOD  = 14
    STOCH_K     = 14
    STOCH_D     = 3
    MR_SCORE_MIN = 3
    MR_ATR_MIN  = 1.2
    MR_HIST_LEN = 200

    def __init__(self):
        self._lock         = threading.Lock()
        self._last_state   = {}
        self._signal_hist  = deque(maxlen=self.MR_HIST_LEN)

    def _calc_bb(self, closes):
        n = self.BB_WINDOW
        if len(closes) < n:
            m = float(closes[-1]); return m, m, m, 0.5, 0.0
        window = closes[-n:]
        mid = float(np.mean(window)); std = float(np.std(window, ddof=1))
        upper = mid + self.BB_STDDEV * std; lower = mid - self.BB_STDDEV * std
        cur   = float(closes[-1])
        pct_b = (cur - lower) / (upper - lower + 1e-9)
        bw    = (upper - lower) / (mid + 1e-9)
        return upper, mid, lower, pct_b, bw

    def _calc_rsi(self, closes):
        p = self.RSI_PERIOD
        if len(closes) < p + 2: return 50.0
        deltas = np.diff(closes[-(p+2):])
        gains  = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_g  = np.mean(gains[-p:]); avg_l = np.mean(losses[-p:])
        if avg_l < 1e-9: return 100.0
        return round(100.0 - 100.0 / (1.0 + avg_g / avg_l), 2)

    def _calc_stoch(self, highs, lows, closes):
        k_per = self.STOCH_K; d_per = self.STOCH_D
        if len(closes) < k_per + d_per: return 50.0, 50.0, "NEUTRAL"
        k_vals = []
        for i in range(k_per - 1, len(closes)):
            h = np.max(highs[i - k_per + 1: i + 1])
            l = np.min(lows[i  - k_per + 1: i + 1])
            k = 100.0 * (closes[i] - l) / (h - l + 1e-9)
            k_vals.append(k)
        k_arr = np.array(k_vals)
        d_arr = np.array([np.mean(k_arr[max(0, i - d_per + 1): i + 1])
                          for i in range(len(k_arr))])
        k = float(k_arr[-1]); d = float(d_arr[-1])
        zona = "OVERSOLD" if k < 20 and d < 20 else "OVERBOUGHT" if k > 80 and d > 80 else "NEUTRAL"
        return k, d, zona

    def _calc_atr(self, rates, period=14):
        if not rates or len(rates) < period + 1: return 1.5
        trs = [max(rates[i]['high'] - rates[i]['low'],
                   abs(rates[i]['high'] - rates[i-1]['close']),
                   abs(rates[i]['low']  - rates[i-1]['close']))
               for i in range(1, len(rates))]
        return float(np.mean(trs[-period:])) if trs else 1.5

    def _market_structure(self, closes, n=10):
        if len(closes) < n: return "RANGING"
        seg = closes[-n:]
        hh  = all(seg[i] >= seg[i-1] for i in range(1, len(seg)))
        ll  = all(seg[i] <= seg[i-1] for i in range(1, len(seg)))
        if hh: return "UPTREND"
        if ll: return "DOWNTREND"
        return "RANGING"

    def analyze(self, rates, poc=None, vah=None, val=None):
        if not rates or len(rates) < self.BB_WINDOW + self.STOCH_K + 5:
            return self._neutral_state()
        closes = np.array([r['close'] for r in rates], dtype=float)
        highs  = np.array([r['high']  for r in rates], dtype=float)
        lows   = np.array([r['low']   for r in rates], dtype=float)
        vols   = np.array([r['tick_volume'] for r in rates], dtype=float)
        price  = float(closes[-1])
        atr    = self._calc_atr(rates)
        if atr < self.MR_ATR_MIN:
            return self._neutral_state()

        bb_upper, bb_mid, bb_lower, pct_b, bw = self._calc_bb(closes)
        rsi = self._calc_rsi(closes)
        k, d, stoch_zona = self._calc_stoch(highs, lows, closes)
        struct = self._market_structure(closes)

        # Z-score vs VWAP rolling
        window = 30
        if len(closes) >= window:
            w_vols = np.where(vols[-window:] <= 0, 1e-9, vols[-window:])
            vwap_r = np.sum(closes[-window:] * w_vols) / np.sum(w_vols)
            std_r  = max(float(np.std(closes[-window:], ddof=1)), 1e-9)
            z_vwap = float((price - vwap_r) / std_r)
        else:
            z_vwap = 0.0

        poc_dist = (price - poc) / atr if poc and atr > 0 else 0.0

        long_conf = {
            "BB inferior tocada":     pct_b < 0.05,
            "RSI oversold (<28)":     rsi < 28,       # anti-overfitting: era <32
            "Stoch oversold (<20)":   stoch_zona == "OVERSOLD",
            "Z-VWAP bajo (<-2.0)":    z_vwap < -2.0,  # anti-overfitting: era <-1.5
            "Precio bajo POC (>1ATR)": poc_dist < -1.0,
        }
        short_conf = {
            "BB superior tocada":      pct_b > 0.95,
            "RSI overbought (>72)":    rsi > 72,       # anti-overfitting: era >68
            "Stoch overbought (>80)":  stoch_zona == "OVERBOUGHT",
            "Z-VWAP alto (>+2.0)":     z_vwap > 2.0,  # anti-overfitting: era >+1.5
            "Precio sobre POC (>1ATR)": poc_dist > 1.0,
        }

        long_score  = sum(long_conf.values())
        short_score = sum(short_conf.values())

        entry = target = stop = target2 = None
        signal = "NEUTRAL"; active_conf = {}; score = 0

        if long_score >= self.MR_SCORE_MIN and struct != "DOWNTREND":
            signal = "MR_LONG"; active_conf = long_conf; score = long_score
            entry  = price; stop = price - atr * 1.5
            target = poc if poc else bb_mid
            target2 = vah if vah else bb_upper
        elif short_score >= self.MR_SCORE_MIN and struct != "UPTREND":
            signal = "MR_SHORT"; active_conf = short_conf; score = short_score
            entry  = price; stop = price + atr * 1.5
            target = poc if poc else bb_mid
            target2 = val if val else bb_lower
        else:
            active_conf = long_conf if long_score >= short_score else short_conf
            score = max(long_score, short_score)

        # anti-overfitting: probabilidades calibradas (era {5:74, 4:65, 3:56, 2:44...})
        prob_tbl = {5: 65, 4: 56, 3: 48, 2: 38, 1: 30, 0: 25}
        prob = prob_tbl.get(score, 25)
        if struct == "RANGING": prob = min(72, prob + 4)  # era +5, cap era 82
        rr = abs(target - entry) / abs(entry - stop) if entry and target and stop and entry != stop else 0.0

        state = {
            "signal": signal, "score": score, "max_score": 5, "prob": prob,
            "rr": round(rr, 2), "confirmadores": active_conf,
            "entry": round(entry, 2) if entry else None,
            "target": round(target, 2) if target else None,
            "target2": round(target2, 2) if target2 else None,
            "stop": round(stop, 2) if stop else None,
            "bb_upper": round(bb_upper, 2), "bb_mid": round(bb_mid, 2),
            "bb_lower": round(bb_lower, 2), "pct_b": round(pct_b, 3),
            "bb_bw": round(bw, 4), "rsi": rsi,
            "stoch_k": round(k, 1), "stoch_d": round(d, 1), "stoch_zona": stoch_zona,
            "zscore_vwap": round(z_vwap, 3), "poc_dist_atr": round(poc_dist, 2),
            "structure": struct, "atr": round(atr, 3), "price": round(price, 2),
            "long_score": long_score, "short_score": short_score,
            "long_conf": long_conf, "short_conf": short_conf,
        }
        with self._lock:
            self._last_state = state
            if signal != "NEUTRAL":
                self._signal_hist.append({"ts": time.time(), "signal": signal,
                                          "score": score, "price": price})
        return state

    def _neutral_state(self):
        return {"signal": "NEUTRAL", "score": 0, "max_score": 5, "prob": 30,
                "rr": 0.0, "confirmadores": {}, "entry": None, "target": None,
                "target2": None, "stop": None, "bb_upper": None, "bb_mid": None,
                "bb_lower": None, "pct_b": 0.5, "bb_bw": 0.0, "rsi": 50.0,
                "stoch_k": 50.0, "stoch_d": 50.0, "stoch_zona": "NEUTRAL",
                "zscore_vwap": 0.0, "poc_dist_atr": 0.0, "structure": "RANGING",
                "atr": 0.0, "price": 0.0, "long_score": 0, "short_score": 0,
                "long_conf": {}, "short_conf": {}}

    def get_last_state(self):
        with self._lock:
            return dict(self._last_state) if self._last_state else self._neutral_state()


# ══════════════════════════════════════════════════════════════════════════════
#  RISK MANAGER
# ══════════════════════════════════════════════════════════════════════════════
class RiskManager:
    def __init__(self, account_balance=100_000,
                 risk_per_trade_pct=RISK_PER_TRADE_PCT,
                 vol_target=VOL_TARGET):
        self.balance        = account_balance
        self.risk_per_trade = risk_per_trade_pct
        self.vol_target     = vol_target

    def position_size(self, atr, entry_price, news_multiplier, regime, signal_strength=1):
        risk_capital = self.balance * self.risk_per_trade * news_multiplier
        if regime == "MEAN_REVERSION": risk_capital *= 0.6
        scale = 0.5 + signal_strength * 0.25
        risk_capital *= scale
        size = risk_capital / (max(atr, 0.1) * 100)
        return round(max(0.01, min(size, 10.0)), 2)

    def dynamic_stop(self, entry_price, atr, regime):
        multiplier = 2.5 if regime.startswith("TREND") else 1.5
        return max(multiplier * atr, atr)

    def take_profit(self, entry_price, vwap, regime, atr):
        if regime == "MEAN_REVERSION":
            tp1 = vwap
            diff = abs(entry_price - vwap)
            tp2  = vwap + (0.5 * diff) if entry_price > vwap else vwap - (0.5 * diff)
        else:
            if entry_price < vwap:
                tp1 = vwap + atr; tp2 = vwap + 2 * atr
            else:
                tp1 = vwap - atr; tp2 = vwap - 2 * atr
        return tp1, tp2


# ══════════════════════════════════════════════════════════════════════════════
#  CALC THREAD
# ══════════════════════════════════════════════════════════════════════════════
class CalcThread(threading.Thread):
    def __init__(self, mt5_engine, quant, ob_engine, delta_engine,
                 news_engine, result_queue, commodity_news, cot_engine,
                 macro_engine=None, signal_logger=None):
        super().__init__(daemon=True)
        self.mt5            = mt5_engine
        self.quant          = quant
        self.ob             = ob_engine
        self.delta          = delta_engine
        self.news           = news_engine
        self.rq             = result_queue
        self.commodity_news = commodity_news
        self.cot_engine     = cot_engine
        self.macro_engine   = macro_engine    # NUEVO v9.0
        self.signal_logger  = signal_logger   # NUEVO v9.0
        self._running       = True
        self._heatmap_cols  = deque(maxlen=HEATMAP_BARS)
        self.data_pipe      = DataPipeline()
        self.signal_gen     = QuantSignalGenerator()
        self.risk_mgr       = RiskManager()
        self.mr_engine      = MeanReversionEngine()
        self.rz_engine      = ReactionZoneEngine()
        self.liq_layer      = LiquidityLayer()    # NUEVO v9.0
        self.scorer         = QuantScorer()        # NUEVO v9.0
        # ── NUEVO v10.0: Finnhub Integration Manager ──────────────────────────────
        self.finnhub_mgr        = get_finnhub_manager()
        self._last_signal_dir = "NEUTRAL"          # NUEVO v9.0 — evita flip sin invalidación

    def run(self):
        while self._running:
            t0 = time.time()
            try:
                self._calc()
            except Exception as e:
                print(f"[CalcThread] {e}")
                traceback.print_exc()
            elapsed = time.time() - t0
            time.sleep(max(0.1, CALC_INTERVAL_S - elapsed))

    def _calc(self):
        m5  = self.mt5.get_rates("M5",  300)
        m15 = self.mt5.get_rates("M15", 200)
        d1  = self.mt5.get_rates("D1",  10)
        # ── NUEVO v10.0: Finnhub fallback si MT5 no disponible ────────────────────
        if (not m5 or len(m5) < 50) and hasattr(self, 'finnhub_mgr') and self.finnhub_mgr:
            try:
                m5_finnhub = self.finnhub_mgr.get_finnhub_rates_fallback("M5",  300)
                if m5_finnhub and len(m5_finnhub) >= 50:
                    m5  = m5_finnhub
                    m15 = self.finnhub_mgr.get_finnhub_rates_fallback("M15", 200)
                    d1  = self.finnhub_mgr.get_finnhub_rates_fallback("D1",  10)
                    print("[CalcThread] Usando datos Finnhub como fuente de precios")
            except Exception as e:
                print(f"[CalcThread] Finnhub fallback error: {e}")
        if not m5 or len(m5) < 50:
            return

        cfd_now = float(m5[-1]['close'])
        global BASE_PRICE
        BASE_PRICE = cfd_now
        tick = self.mt5.get_current_price()
        if tick and 'last' in tick:
            cfd_now = tick['last']
            BASE_PRICE = cfd_now

        # Offset dinámico
        try:
            future_data = requests.get(
                "https://query2.finance.yahoo.com/v8/finance/chart/GC=F?interval=1m&range=1d",
                timeout=5).text
            f_json      = json.loads(future_data)
            future_now  = f_json['chart']['result'][0]['meta']['regularMarketPrice']
            DataPipeline.set_offset(cfd_now, future_now)
        except:
            pass

        vwap      = self.quant.calc_vwap_bands(m5)
        anom_m5   = self.quant.calc_anomalies(m5)
        anom_m15  = self.quant.calc_anomalies(m15)
        vol_prof  = self.quant.calc_volume_profile_global(m5)
        atr_m15   = self.quant.calc_atr(m15)
        pivots    = self.quant.calc_pivots(d1)
        fib       = self.quant.calc_fibonacci(m5)
        order_blocks = self.quant.calc_order_blocks(m5)
        rsi       = self.quant.calc_rsi(m5)
        macd_data = self.quant.calc_macd(m5)
        news_s    = self.news.get_state()
        ds        = self.delta.get_state() if self.delta else None
        cot_data  = self.cot_engine.get()
        ob_data   = self.ob.get()

        # Zonas de reacción
        reaction_zones = self.rz_engine.calc_zones(m5, atr_m15)

        # ── NUEVO v9.0: Liquidez ──────────────────────────────────────────────
        liq_state = self.liq_layer.assess(m5, atr_m15, ob_data)

        # ── NUEVO v9.0: Macro ─────────────────────────────────────────────────
        macro_state = self.macro_engine.get() if self.macro_engine else {
            "bias": "NEUTRAL", "strength": "NEUTRAL", "regime": "NEUTRAL",
            "score_macro": 20, "details": {}, "transition": False
        }

        signal = self.quant.evaluate_strategy(
            vwap, vol_prof, anom_m5, anom_m15,
            pivots, fib, atr_m15, news_s, rsi,
            macd_data=macd_data, delta_state=ds,
            reaction_zones=reaction_zones, cot_data=cot_data,
            macro_state=macro_state, liq_state=liq_state,  # NUEVO v9.0
        )

        # ── NUEVO v9.0: Score 0-100 ───────────────────────────────────────────
        score_result = self.scorer.score(
            macro_state, liq_state, signal, news_s,
            regime_transition=macro_state.get("transition", False)
        )

        # ── NUEVO v9.0: Protección de dirección (no flip sin invalidación) ────
        new_dir = signal.get("dir", "NEUTRAL")
        if (self._last_signal_dir != "NEUTRAL" and
                new_dir != "NEUTRAL" and
                new_dir != self._last_signal_dir):
            # Requiere score alto para cambiar de dirección
            if score_result["total"] < SCORE_MIN_TRANSITION:
                signal = self.quant._neutral(
                    score_result.get("prob_long", 35),
                    signal.get("conds", {}),
                    self._last_signal_dir,
                    signal.get("score", 0)
                )
                score_result["blocked_flip"] = True
            else:
                self._last_signal_dir = new_dir
        elif new_dir != "NEUTRAL":
            self._last_signal_dir = new_dir

        # ── NUEVO v9.0: Bloqueo por dominancia macro ──────────────────────────
        if new_dir == "LONG" and not score_result.get("can_long", True):
            signal = self.quant._neutral(
                score_result.get("prob_long", 35), signal.get("conds", {}),
                "LONG", signal.get("score", 0))
            score_result["blocked_macro"] = True
        elif new_dir == "SHORT" and not score_result.get("can_short", True):
            signal = self.quant._neutral(
                score_result.get("prob_short", 35), signal.get("conds", {}),
                "SHORT", signal.get("score", 0))
            score_result["blocked_macro"] = True

        # ── NUEVO v9.0: Bloqueo por liquidez baja ─────────────────────────────
        if not liq_state.get("can_enter", True) and signal.get("dir") != "NEUTRAL":
            signal["liq_blocked"] = True

        # ── NUEVO v9.0: Log señal si es operativa ─────────────────────────────
        if (self.signal_logger and
                signal.get("dir") != "NEUTRAL" and
                score_result.get("can_trade", False)):
            self.signal_logger.log(
                signal, score_result, macro_state, liq_state,
                extra={"atr": atr_m15,
                       "news_sentiment": news_s.get("sentiment","N/A") if news_s else "N/A"}
            )

        price   = vwap["current"] if vwap else BASE_PRICE

        quant_signal = "NEUTRAL"; quant_z = 0.0; quant_regime = "UNKNOWN"
        quant_size   = 0.0; quant_sl = quant_tp1 = quant_tp2 = None
        quant_strength = 0
        try:
            end   = datetime.datetime.utcnow()
            start = end - datetime.timedelta(days=10)
            df = self.data_pipe.get_rates(start, end)
            if df is not None and len(df) >= 50:
                df           = self.signal_gen.compute_vwap_bands(df)
                yield_series = self.data_pipe.get_yield_curve(start, end)
                dxy_series   = self.data_pipe.get_dxy(start, end)
                yield_signal = self.signal_gen.compute_yield_anomaly(df, yield_series)
                dxy_signal   = self.signal_gen.compute_dxy_signal(df, dxy_series)
                news_status  = (self.commodity_news.get_status()
                                if self.commodity_news
                                else {"risk_multiplier": 1.0})
                quant_signal, quant_z, quant_regime, quant_strength = \
                    self.signal_gen.generate_signal(df, yield_signal, dxy_signal,
                                                    news_status["risk_multiplier"])
                quant_atr   = float(df['rv'].iloc[-1]) if 'rv' in df.columns else atr_m15
                quant_entry = cfd_now
                # Ajuste de size por score v9.0
                size_factor = score_result.get("size_factor", 1.0)
                quant_size  = self.risk_mgr.position_size(
                    quant_atr, quant_entry, news_status["risk_multiplier"],
                    quant_regime, quant_strength) * size_factor
                quant_stop_dist = self.risk_mgr.dynamic_stop(quant_entry, quant_atr, quant_regime)
                vwap_last = vwap["vwap_last"] if vwap else quant_entry
                if quant_signal == "LONG":
                    quant_sl = quant_entry - quant_stop_dist
                elif quant_signal == "SHORT":
                    quant_sl = quant_entry + quant_stop_dist
                quant_tp1, quant_tp2 = self.risk_mgr.take_profit(
                    quant_entry, vwap_last, quant_regime, quant_atr)
        except Exception as e:
            print(f"[CalcThread quant] {e}")

        if self.delta and atr_m15:
            col = self.delta.get_heatmap_column(price, atr_m15)
            if col: self._heatmap_cols.append(col)

        mr_state = self.mr_engine.analyze(
            m5, poc=vol_prof["poc"] if vol_prof else None,
            vah=vol_prof["vah"] if vol_prof else None,
            val=vol_prof["val"] if vol_prof else None)

        # ── NUEVO v10.0: Generar señal Finnhub mejorada ──────────────────────────
        finnhub_signal = {}; finnhub_tech = {}; finnhub_macro_state = {}; finnhub_cot_state = {}
        if hasattr(self, 'finnhub_mgr') and self.finnhub_mgr and price and price > 0:
            try:
                tech_sig_for_finnhub = {
                    "dir":       signal.get("dir", "NEUTRAL") if signal else "NEUTRAL",
                    "score":     score_result.get("total", 0) if score_result else 0,
                    "max_score": 100,
                    "poc":       vol_prof.get("poc") if vol_prof else None,
                }
                finnhub_signal = self.finnhub_mgr.get_enhanced_signal(
                    tech_signal    = tech_sig_for_finnhub,
                    mr_state       = mr_state,
                    liq_state      = liq_state,
                    reaction_zones = reaction_zones if reaction_zones else [],
                    price          = price,
                    atr            = atr_m15 if atr_m15 else 1.5,
                    legacy_news    = news_s,
                )
                finnhub_full = self.finnhub_mgr.get_full_state()
                finnhub_macro_state = finnhub_full.get("macro", {})
                finnhub_cot_state   = finnhub_full.get("cot", {})
                try:
                    finnhub_tech = self.finnhub_mgr.tech_ind.get_full_analysis("GCUSD", "5min")
                except Exception:
                    finnhub_tech = {}
            except Exception as e:
                print(f"[CalcThread] Finnhub signal error: {e}")

        self.rq.put({
            "m5": m5, "m15": m15, "d1": d1,
            "vwap": vwap, "anom_m5": anom_m5, "anom_m15": anom_m15,
            "vol_prof": vol_prof, "atr_m15": atr_m15,
            "pivots": pivots, "fib": fib, "rsi": rsi, "news_s": news_s,
            "signal": signal, "price": price, "ob_data": ob_data, "ds": ds,
            "macd": macd_data, "order_blocks": order_blocks,
            "reaction_zones": reaction_zones,
            "heatmap_cols":   list(self._heatmap_cols),
            "quant_signal":   quant_signal,
            "quant_z":        quant_z,
            "quant_regime":   quant_regime,
            "quant_strength": quant_strength,
            "quant_size":     quant_size,
            "quant_sl":       quant_sl,
            "quant_tp1":      quant_tp1,
            "quant_tp2":      quant_tp2,
            "mr_state":       mr_state,
            "cot_data":       cot_data,
            # NUEVO v9.0
            "macro_state":    macro_state,
            "liq_state":      liq_state,
            "score_result":   score_result,
            # ── NUEVO v10.0: Finnhub data ──────────────────────────────────────────
            "finnhub_signal":     finnhub_signal,
            "finnhub_tech":       finnhub_tech,
            "finnhub_macro":      finnhub_macro_state,
            "finnhub_cot":        finnhub_cot_state,
        })

    def stop(self):
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES TKINTER
# ══════════════════════════════════════════════════════════════════════════════
def _lset(widget, text, fg=None):
    text    = str(text)
    changed = widget.cget("text") != text
    if fg: changed = changed or widget.cget("fg") != fg
    if changed:
        kw = {"text": text}
        if fg: kw["fg"] = fg
        widget.config(**kw)

def _draw_hbar(canvas, ratio, fill_col, bg_col="#0c1c2c", height=7):
    w = _WIDTH_CACHE.get(canvas)
    fill_px = max(0, min(w, int(w * ratio)))
    canvas.delete("all")
    if fill_px > 0:
        canvas.create_rectangle(0, 0, fill_px, height, fill=fill_col, outline="")
    if fill_px < w:
        canvas.create_rectangle(fill_px, 0, w, height, fill=bg_col, outline="")

def _draw_hbar2(canvas, ratio_a, col_a, ratio_b, col_b, height=7):
    w    = _WIDTH_CACHE.get(canvas)
    px_a = max(0, min(w, int(w * ratio_a)))
    px_b = max(0, min(w, int(w * (ratio_a + ratio_b))))
    canvas.delete("all")
    if px_a > 0:
        canvas.create_rectangle(0, 0, px_a, height, fill=col_a, outline="")
    if px_b > px_a:
        canvas.create_rectangle(px_a, 0, px_b, height, fill=col_b, outline="")


# ══════════════════════════════════════════════════════════════════════════════
#  K-AURUM TERMINAL
# ══════════════════════════════════════════════════════════════════════════════
class KAurumTerminal:
    def __init__(self, root):
        self.root = root
        self.root.title("K-AURUM Q-CORE v10.0  │  XAUUSD CFD  │  Finnhub + OpenBB + Macro + COT + Calendar")
        self.root.geometry("1960x1080")
        self.root.configure(bg=BG_ROOT)
        self.root.minsize(1600, 900)

        self._verifier      = RealAccountVerifier()
        self.mt5_engine     = MT5PriceEngine(SYMBOL, self._verifier)
        self.quant          = QuantEngine()
        self.inst_feed      = InstitutionalPriceFeed()
        self.news_engine    = OpenBBNewsEngine()
        self.commodity_news = CommodityNewsAnalyzer(update_interval_sec=60)
        self.cot_engine     = OpenBBCOTEngine()
        self.ob_engine      = None
        self.delta_engine   = None
        self._watchdog      = None
        self._calc_thread   = None
        self._result_queue  = queue.Queue(maxsize=5)
        self.running        = True
        self._last_result   = None
        self._chart_tab     = 0
        self._user_panned   = False
        self._is_panning    = False
        self._tick_count    = 0
        self._last_quant_sig = None
        self._last_mr_sig    = "NEUTRAL"
        # NUEVO v9.0
        self.macro_engine   = MacroEngine(DataPipeline())
        self.signal_logger  = SignalLogger()
        self.wf_backtest    = WalkForwardBacktester()
        # ── NUEVO v10.0: Inicializar Finnhub Integration Manager ──────────────────
        try:
            self._finnhub_mgr = init_finnhub()
            api_preview = FINNHUB_API_KEY[:8] if len(FINNHUB_API_KEY) > 8 else FINNHUB_API_KEY
            print(f"[K-AURUM v10.0] Finnhub Integration Manager iniciado (API: {api_preview}...)")
        except Exception as _finnhub_init_err:
            print(f"[K-AURUM v10.0] Finnhub init error: {_finnhub_init_err}")
            self._finnhub_mgr = None

        self._build_ui()
        self._connect_and_start()
        self.root.after(UPDATE_MS, self._tick)
        self.root.after(NEWS_UPDATE_MS, self._news_tick)

    # ── UI BUILD ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_topbar()
        self._build_statusbar()
        self._build_body()

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=BG_HEADER, height=60)
        bar.pack(fill=tk.X); bar.pack_propagate(False)
        # ── NUEVO v10.0: Panel Finnhub en topbar ──────────────────────────────────
        try:
            finnhub_frame = tk.Frame(bar, bg="#0a1628", bd=1, relief="solid")
            finnhub_frame.pack(side="right", padx=8, pady=4)
            tk.Label(finnhub_frame, text="◈ Finnhub v10", bg="#0a1628", fg="#4488ff",
                     font=("Consolas", 8, "bold")).pack(side="left", padx=3)
            self._lbl_finnhub_signal = tk.Label(finnhub_frame, text="Finnhub:---",
                bg="#0a1628", fg="#aaaaaa", font=("Consolas", 8, "bold"))
            self._lbl_finnhub_signal.pack(side="left", padx=4)
            self._lbl_finnhub_conf = tk.Label(finnhub_frame, text="---",
                bg="#0a1628", fg="#aaaaaa", font=("Consolas", 8))
            self._lbl_finnhub_conf.pack(side="left", padx=2)
            self._lbl_finnhub_sl = tk.Label(finnhub_frame, text="SL:--- TP1:---",
                bg="#0a1628", fg="#888888", font=("Consolas", 7))
            self._lbl_finnhub_sl.pack(side="left", padx=4)
            self._lbl_finnhub_rsi = tk.Label(finnhub_frame, text="RSI:--",
                bg="#0a1628", fg="#cccccc", font=("Consolas", 7))
            self._lbl_finnhub_rsi.pack(side="left", padx=2)
            self._lbl_finnhub_vix = tk.Label(finnhub_frame, text="VIX:--",
                bg="#0a1628", fg="#cccccc", font=("Consolas", 7))
            self._lbl_finnhub_vix.pack(side="left", padx=2)
        except Exception as e:
            print(f"[Finnhub UI] Panel error: {e}")

        lf = tk.Frame(bar, bg=BG_HEADER); lf.pack(side=tk.LEFT, padx=16, pady=8)
        tk.Label(lf, text="K-AURUM Q-CORE v8.0", fg=C_GOLD, bg=BG_HEADER,
                 font=("Courier New", 11, "bold")).pack(anchor="w")
        tk.Label(lf, text="XAUUSD CFD  │  OpenBB + Zonas", fg=C_GRAY1, bg=BG_HEADER,
                 font=("Courier New", 8)).pack(anchor="w")

        tk.Frame(bar, bg=C_GOLD, width=2).pack(side=tk.LEFT, fill=tk.Y, pady=8, padx=4)

        sf = tk.Frame(bar, bg=BG_HEADER); sf.pack(side=tk.LEFT, padx=12)
        tk.Label(sf, text="XAUUSD CFD", fg=C_GOLD, bg=BG_HEADER,
                 font=("Courier New", 9, "bold")).pack(anchor="w")
        self.lbl_inst = tk.Label(sf, text="SPOT: ---", fg=C_GRAY1,
                                 bg=BG_HEADER, font=("Courier New", 7))
        self.lbl_inst.pack(anchor="w")

        tk.Frame(bar, bg=SEP, width=1).pack(side=tk.LEFT, fill=tk.Y, pady=8, padx=8)
        self.lbl_price = tk.Label(bar, text="0.00", fg=C_WHITE,
                                  bg=BG_HEADER, font=("Courier New", 32, "bold"))
        self.lbl_price.pack(side=tk.LEFT, padx=12)

        pf = tk.Frame(bar, bg=BG_HEADER); pf.pack(side=tk.LEFT, padx=8)
        self.lbl_chg = tk.Label(pf, text="+0.00 (0.00%)", fg=C_GREEN,
                                bg=BG_HEADER, font=("Courier New", 10, "bold"))
        self.lbl_chg.pack(anchor="w")
        self.lbl_spread_top = tk.Label(pf, text="SPREAD: --", fg=C_GRAY2,
                                       bg=BG_HEADER, font=("Courier New", 7))
        self.lbl_spread_top.pack(anchor="w")
        self.lbl_rsi_top = tk.Label(pf, text="Z-Score: 0.00", fg=C_CYAN,
                                    bg=BG_HEADER, font=("Courier New", 7))
        self.lbl_rsi_top.pack(anchor="w")

        tk.Frame(bar, bg=SEP, width=1).pack(side=tk.LEFT, fill=tk.Y, pady=8, padx=10)
        self.live_badge = tk.Label(bar, text=" ● MT5 REAL ", fg=C_GREEN,
                                   bg="#1a3a2a", font=("Courier New", 8, "bold"))
        self.live_badge.pack(side=tk.LEFT, padx=6)
        self.lbl_ob_mode_top = tk.Label(bar, text=" OB: ACTIVE ", fg=C_GREEN,
                                        bg="#1a3a2a", font=("Courier New", 7, "bold"))
        self.lbl_ob_mode_top.pack(side=tk.LEFT, padx=4)
        self.lbl_latency = tk.Label(bar, text=" LAT: --ms ", fg=C_GRAY1,
                                    bg=BG_HEADER, font=("Courier New", 7))
        self.lbl_latency.pack(side=tk.LEFT, padx=4)
        self.lbl_news_warn = tk.Label(bar, text="", fg=C_RED,
                                      bg=BG_HEADER, font=("Courier New", 8, "bold"))
        self.lbl_news_warn.pack(side=tk.LEFT, padx=6)

        rf = tk.Frame(bar, bg=BG_HEADER); rf.pack(side=tk.RIGHT, padx=16)
        self.lbl_clock = tk.Label(rf, text="00:00:00", fg=C_GOLD,
                                  bg=BG_HEADER, font=("Courier New", 14, "bold"))
        self.lbl_clock.pack(anchor="e")
        self.lbl_session = tk.Label(rf, text="SESSION: --", fg=C_GRAY1,
                                    bg=BG_HEADER, font=("Courier New", 7))
        self.lbl_session.pack(anchor="e")
        self.lbl_date = tk.Label(rf, text=datetime.datetime.utcnow().strftime("%Y-%m-%d UTC"),
                                  fg=C_GRAY2, bg=BG_HEADER, font=("Courier New", 7))
        self.lbl_date.pack(anchor="e")
        tk.Frame(self.root, bg=C_GOLD, height=2).pack(fill=tk.X)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=BG_CARD2, height=24)
        bar.pack(fill=tk.X); bar.pack_propagate(False)

        tabs_f = tk.Frame(bar, bg=BG_CARD2); tabs_f.pack(side=tk.LEFT, padx=12)
        self._tab_btns = []
        for i, name in enumerate(["VWAP", "PIVOTS", "ANALYSIS", "MEAN REVERSION"]):
            btn = tk.Label(tabs_f, text=f" {name} ", fg=C_GRAY1, bg=BG_CARD2,
                           font=("Courier New", 8, "bold"), cursor="hand2",
                           relief=tk.FLAT, padx=6)
            btn.pack(side=tk.LEFT, padx=2)
            btn.bind("<Button-1>", lambda e, idx=i: self._switch_tab(idx))
            self._tab_btns.append(btn)
        self._update_tab_style()

        for lbl_txt, attr, col in [
            ("ATR:", "lbl_ib_atr", C_AMBER), ("|", None, C_GRAY2),
            ("Z:", "lbl_ib_z", C_CYAN),      ("|", None, C_GRAY2),
            ("REGIME:", "lbl_ib_regime", C_TEAL), ("|", None, C_GRAY2),
            ("RISK:", "lbl_ib_risk", C_GREEN), ("|", None, C_GRAY2),
            ("BARS:", "lbl_ib_bars", C_GRAY1),
        ]:
            w = tk.Label(bar, text=lbl_txt if attr is None else "--",
                         fg=col, bg=BG_CARD2, font=("Courier New", 7))
            w.pack(side=tk.RIGHT, padx=2)
            if attr: setattr(self, attr, w)
        tk.Frame(self.root, bg=C_GOLD, height=1).pack(fill=tk.X)

    def _switch_tab(self, idx):
        self._chart_tab = idx; self._user_panned = False; self._update_tab_style()

    def _update_tab_style(self):
        for i, btn in enumerate(self._tab_btns):
            btn.config(
                fg=C_GOLD     if i == self._chart_tab else C_GRAY1,
                bg=BG_CARD    if i == self._chart_tab else BG_CARD2,
                relief=tk.SUNKEN if i == self._chart_tab else tk.FLAT,
                borderwidth=2 if i == self._chart_tab else 0,
            )

    def _build_body(self):
        body = tk.Frame(self.root, bg=BG_ROOT); body.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(body, bg=BG_PANEL, width=260)
        left.pack(side=tk.LEFT, fill=tk.Y); left.pack_propagate(False)
        lc = tk.Canvas(left, bg=BG_PANEL, highlightthickness=0)
        lsb = tk.Scrollbar(left, orient=tk.VERTICAL, command=lc.yview)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        lc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lc.configure(yscrollcommand=lsb.set)
        lf = tk.Frame(lc, bg=BG_PANEL)
        lf_id = lc.create_window((0, 0), window=lf, anchor="nw")

        def _resize_left(event):
            lc.itemconfig(lf_id, width=event.width)

        lc.bind("<Configure>", _resize_left)
        lf.bind("<Configure>", lambda e: lc.configure(scrollregion=lc.bbox("all")))
        self._build_left(lf)

        center = tk.Frame(body, bg=BG_ROOT); center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_center(center)

        right = tk.Frame(body, bg=BG_PANEL, width=210)
        right.pack(side=tk.RIGHT, fill=tk.Y); right.pack_propagate(False)
        rc = tk.Canvas(right, bg=BG_PANEL, highlightthickness=0)
        rsb = tk.Scrollbar(right, orient=tk.VERTICAL, command=rc.yview)
        rsb.pack(side=tk.RIGHT, fill=tk.Y)
        rc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rc.configure(yscrollcommand=rsb.set)
        rf2 = tk.Frame(rc, bg=BG_PANEL)
        rf_id = rc.create_window((0, 0), window=rf2, anchor="nw")

        def _resize_right(event):
            rc.itemconfig(rf_id, width=event.width)
        rc.bind("<Configure>", _resize_right)
        rf2.bind("<Configure>", lambda e: rc.configure(scrollregion=rc.bbox("all")))
        self._build_right(rf2)

    def _build_left(self, parent):
        def sec(title, color=C_GOLD):
            tk.Frame(parent, bg=C_GOLD, height=1).pack(fill=tk.X, padx=6, pady=(6, 0))
            tk.Label(parent, text=title, fg=color, bg=BG_PANEL,
                     font=("Courier New", 7, "bold")).pack(anchor="w", padx=8, pady=(2, 2))

        # ── Señal principal ────────────────────────────────────────────────────
        sec("◈ SEÑAL CUANTITATIVA", C_GOLD)
        self.lbl_sig = tk.Label(parent, text="NEUTRAL", fg=C_GRAY1,
                                bg=BG_PANEL, font=("Courier New", 18, "bold"))
        self.lbl_sig.pack(pady=(2, 0))

        self.lbl_strength_ui = tk.Label(parent, text="STRENGTH: ☆☆☆", fg=C_GOLD,
                                        bg=BG_PANEL, font=("Courier New", 8))
        self.lbl_strength_ui.pack()

        zf = tk.Frame(parent, bg=BG_PANEL); zf.pack(fill=tk.X, padx=8, pady=1)
        tk.Label(zf, text="Z-SCORE", fg=C_GRAY1, bg=BG_PANEL,
                 font=("Courier New", 7)).pack(side=tk.LEFT)
        self.lbl_z = tk.Label(zf, text="0.00", fg=C_CYAN,
                              bg=BG_PANEL, font=("Courier New", 8, "bold"))
        self.lbl_z.pack(side=tk.RIGHT)
        self.z_cv = tk.Canvas(parent, height=6, bg=C_GRAY3, highlightthickness=0)
        self.z_cv.pack(fill=tk.X, padx=8, pady=(0, 2))

        self.lbl_regime_ui = tk.Label(parent, text="REGIME: --", fg=C_TEAL,
                                      bg=BG_PANEL, font=("Courier New", 7))
        self.lbl_regime_ui.pack()

        for label, attr, col in [
            ("LOTE",      "lbl_pos_size", C_GOLD),
            ("STOP LOSS", "lbl_sl",       C_RED),
            ("TP1",       "lbl_tp1",      C_GREEN2),
            ("TP2",       "lbl_tp2",      C_CYAN2),
        ]:
            f = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=8, pady=1)
            tk.Label(f, text=label, fg=C_GRAY1, bg=BG_PANEL,
                     font=("Courier New", 7)).pack(side=tk.LEFT)
            w = tk.Label(f, text="------", fg=col, bg=BG_PANEL,
                         font=("Courier New", 8, "bold"))
            w.pack(side=tk.RIGHT); setattr(self, attr, w)

        rrf = tk.Frame(parent, bg=BG_PANEL); rrf.pack(fill=tk.X, padx=8, pady=(3, 2))
        tk.Label(rrf, text="RISK MULT", fg=C_GRAY1, bg=BG_PANEL,
                 font=("Courier New", 7)).pack(side=tk.LEFT)
        self.lbl_risk_mult = tk.Label(rrf, text="1.00", fg=C_AMBER,
                                      bg=BG_PANEL, font=("Courier New", 9, "bold"))
        self.lbl_risk_mult.pack(side=tk.RIGHT)

        # ── VWAP + VP ──────────────────────────────────────────────────────────
        sec("◈ VWAP + PERFIL DE VOLUMEN", C_CYAN)
        for label, attr, col in [
            ("VWAP",   "lbl_vwap", C_WHITE),
            ("VWAP σ", "lbl_sd",   C_CYAN),
            ("VAH",    "lbl_vah",  C_VAH),
            ("POC",    "lbl_poc",  C_POC),
            ("VAL",    "lbl_val",  C_VAL),
            ("ATR M15","lbl_atr",  C_AMBER),
        ]:
            f = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=8, pady=1)
            tk.Label(f, text=label, fg=C_GRAY1, bg=BG_PANEL,
                     font=("Courier New", 7)).pack(side=tk.LEFT)
            w = tk.Label(f, text="------", fg=col, bg=BG_PANEL,
                         font=("Courier New", 8, "bold"))
            w.pack(side=tk.RIGHT); setattr(self, attr, w)

        # ── Zonas de Reacción ─────────────────────────────────────────────────
        sec("◈ ZONAS DE REACCIÓN", C_ORANGE)
        sf = tk.Frame(parent, bg=BG_PANEL); sf.pack(fill=tk.X, padx=8, pady=1)
        tk.Label(sf, text="SUPPLY próxima", fg=C_GRAY1, bg=BG_PANEL,
                 font=("Courier New", 7)).pack(side=tk.LEFT)
        self.lbl_rz_supply = tk.Label(sf, text="---", fg=C_SUPPLY,
                                      bg=BG_PANEL, font=("Courier New", 8, "bold"))
        self.lbl_rz_supply.pack(side=tk.RIGHT)
        df2 = tk.Frame(parent, bg=BG_PANEL); df2.pack(fill=tk.X, padx=8, pady=1)
        tk.Label(df2, text="DEMAND próxima", fg=C_GRAY1, bg=BG_PANEL,
                 font=("Courier New", 7)).pack(side=tk.LEFT)
        self.lbl_rz_demand = tk.Label(df2, text="---", fg=C_DEMAND,
                                      bg=BG_PANEL, font=("Courier New", 8, "bold"))
        self.lbl_rz_demand.pack(side=tk.RIGHT)
        cf = tk.Frame(parent, bg=BG_PANEL); cf.pack(fill=tk.X, padx=8, pady=1)
        tk.Label(cf, text="Zonas activas", fg=C_GRAY1, bg=BG_PANEL,
                 font=("Courier New", 7)).pack(side=tk.LEFT)
        self.lbl_rz_count = tk.Label(cf, text="0", fg=C_GOLD,
                                     bg=BG_PANEL, font=("Courier New", 8, "bold"))
        self.lbl_rz_count.pack(side=tk.RIGHT)

        # ── COT Institucional ────────────────────────────────────────────────
        sec("◈ COT INSTITUCIONAL (CFTC)", C_PURPLE)
        self.lbl_cot_bias = tk.Label(parent, text="N/A", fg=C_GRAY1,
                                     bg=BG_PANEL, font=("Courier New", 12, "bold"))
        self.lbl_cot_bias.pack()
        for label, attr, col in [
            ("Net Inst.", "lbl_cot_net",   C_CYAN),
            ("Long %",    "lbl_cot_long",  C_GREEN),
            ("Short %",   "lbl_cot_short", C_RED),
            ("Cambio",    "lbl_cot_chg",   C_GOLD),
            ("Fuente",    "lbl_cot_src",   C_GRAY1),
        ]:
            f = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=8, pady=1)
            tk.Label(f, text=label, fg=C_GRAY1, bg=BG_PANEL,
                     font=("Courier New", 7)).pack(side=tk.LEFT)
            w = tk.Label(f, text="--", fg=col, bg=BG_PANEL,
                         font=("Courier New", 7, "bold"))
            w.pack(side=tk.RIGHT); setattr(self, attr, w)
        self.cot_bar_cv = tk.Canvas(parent, height=7, bg=C_GRAY3, highlightthickness=0)
        self.cot_bar_cv.pack(fill=tk.X, padx=8, pady=(1, 3))

        # ── Mean Reversion ───────────────────────────────────────────────────
        sec("◈ MEAN REVERSION", C_TEAL)
        self.lbl_mr_sig = tk.Label(parent, text="NEUTRAL", fg=C_GRAY1,
                                   bg=BG_PANEL, font=("Courier New", 14, "bold"))
        self.lbl_mr_sig.pack(pady=(2, 0))
        mr_score_f = tk.Frame(parent, bg=BG_PANEL); mr_score_f.pack(fill=tk.X, padx=8)
        tk.Label(mr_score_f, text="SCORE", fg=C_GRAY1, bg=BG_PANEL,
                 font=("Courier New", 7)).pack(side=tk.LEFT)
        self.lbl_mr_score = tk.Label(mr_score_f, text="0/5", fg=C_GOLD,
                                     bg=BG_PANEL, font=("Courier New", 8, "bold"))
        self.lbl_mr_score.pack(side=tk.RIGHT)
        self.mr_score_cv = tk.Canvas(parent, height=5, bg=C_GRAY3, highlightthickness=0)
        self.mr_score_cv.pack(fill=tk.X, padx=8, pady=(1, 2))
        for label, attr, col in [
            ("PROB %",    "lbl_mr_prob",   C_GOLD),
            ("R/R",       "lbl_mr_rr",     C_CYAN),
            ("ENTRY",     "lbl_mr_entry",  C_WHITE),
            ("TARGET",    "lbl_mr_target", C_GREEN),
            ("STOP",      "lbl_mr_stop",   C_RED),
            ("BB %B",     "lbl_mr_pctb",   C_TEAL),
            ("STOCH K/D", "lbl_mr_stoch",  C_PURPLE),
            ("STRUCTURE", "lbl_mr_struct", C_AMBER),
        ]:
            f = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=8, pady=1)
            tk.Label(f, text=label, fg=C_GRAY1, bg=BG_PANEL,
                     font=("Courier New", 7)).pack(side=tk.LEFT)
            w = tk.Label(f, text="--", fg=col, bg=BG_PANEL,
                         font=("Courier New", 7, "bold"))
            w.pack(side=tk.RIGHT); setattr(self, attr, w)

        # Confirmadores MR
        self._mr_conf_labels = {}
        sec("  CONFIRMADORES MR", C_TEAL)
        mr_conf_keys = [
            "BB inferior tocada", "BB superior tocada",
            "RSI oversold (<32)", "RSI overbought (>68)",
            "Stoch oversold (<20)", "Stoch overbought (>80)",
            "Z-VWAP bajo (<-1.5)", "Z-VWAP alto (>+1.5)",
            "Precio bajo POC (>1ATR)", "Precio sobre POC (>1ATR)",
        ]
        for key in mr_conf_keys:
            f  = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=10, pady=0)
            lv = tk.Label(f, text=f"○ {key}", fg=C_GRAY2, bg=BG_PANEL,
                          font=("Courier New", 6))
            lv.pack(side=tk.LEFT)
            self._mr_conf_labels[key] = lv

        # ── Sentimiento noticias ─────────────────────────────────────────────
        sec("◈ SENTIMIENTO NOTICIAS", C_AMBER)
        self.lbl_news_sent = tk.Label(parent, text="NEUTRAL", fg=C_GRAY1,
                                      bg=BG_PANEL, font=("Courier New", 10, "bold"))
        self.lbl_news_sent.pack()
        self.lbl_news_src = tk.Label(parent, text="OpenBB: N/A  |  FF: N/A",
                                     fg=C_GRAY2, bg=BG_PANEL, font=("Courier New", 6))
        self.lbl_news_src.pack()

        # ── Real Account Status ───────────────────────────────────────────────
        sec("◈ CONEXIÓN", C_TEAL)
        self.lbl_ver_status = tk.Label(parent, text="CHECKING...", fg=C_AMBER,
                                       bg=BG_PANEL, font=("Courier New", 7, "bold"))
        self.lbl_ver_status.pack(anchor="w", padx=8)
        for label, attr, col in [
            ("LATENCY", "lbl_ver_lat",   C_TEAL),
            ("UPTIME",  "lbl_ver_up",    C_GRAY0),
            ("DROPS",   "lbl_ver_drops", C_GRAY0),
        ]:
            f = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=8, pady=1)
            tk.Label(f, text=label, fg=C_GRAY1, bg=BG_PANEL,
                     font=("Courier New", 6)).pack(side=tk.LEFT)
            w = tk.Label(f, text="--", fg=col, bg=BG_PANEL,
                         font=("Courier New", 7, "bold"))
            w.pack(side=tk.RIGHT); setattr(self, attr, w)

        # ── Signal Log ───────────────────────────────────────────────────────
        sec("◈ SIGNAL LOG")
        self.log_text = tk.Text(parent, bg=BG_SUBCARD, fg=C_GRAY1,
                                font=("Courier New", 6), height=7,
                                state=tk.DISABLED, wrap=tk.WORD,
                                relief=tk.FLAT, bd=0)
        self.log_text.pack(fill=tk.X, padx=6, pady=(0, 4))
        for tag, col in [("buy", C_GREEN), ("sell", C_RED),
                          ("wait", C_GRAY1), ("warning", C_AMBER)]:
            self.log_text.tag_config(tag, foreground=col)
        self._log("K-AURUM v8.0 iniciado\n", "wait")

    def _build_center(self, parent):
        self.fig = Figure(figsize=(14, 8.8), facecolor=BG_ROOT)
        self.gs  = gridspec.GridSpec(
            3, 3,
            height_ratios=[3.6, 1.0, 1.1], width_ratios=[3, 1, 1],
            hspace=0.06, wspace=0.06,
            left=0.01, right=0.99, top=0.985, bottom=0.025,
        )
        self.ax_main  = self.fig.add_subplot(self.gs[0, :])
        self.ax_vp    = self.fig.add_subplot(self.gs[1, 0])
        self.ax_delta = self.fig.add_subplot(self.gs[1, 1])
        self.ax_ob    = self.fig.add_subplot(self.gs[1, 2])
        self.ax_sub1  = self.fig.add_subplot(self.gs[2, :2])
        self.ax_sub2  = self.fig.add_subplot(self.gs[2, 2])
        for ax in [self.ax_main, self.ax_vp, self.ax_delta,
                   self.ax_ob, self.ax_sub1, self.ax_sub2]:
            ax.set_facecolor(BG_CHART)
            ax.tick_params(colors=C_GRAY1, labelsize=6, length=2)
            for sp in ax.spines.values(): sp.set_color(C_GRAY3)
            ax.grid(True, color=GRID, linewidth=0.25, alpha=0.9)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.toolbar = NavigationToolbar2Tk(self.canvas, parent, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect("scroll_event",         self._on_scroll)
        self.canvas.mpl_connect("button_press_event",   self._on_press)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("motion_notify_event",  self._on_motion)
        self._last_mouse_x = 0; self._last_mouse_y = 0

    def _build_right(self, parent):
        def sec(title, color=C_GOLD):
            tk.Frame(parent, bg=C_GOLD, height=1).pack(fill=tk.X, padx=6, pady=(6, 0))
            tk.Label(parent, text=title, fg=color, bg=BG_PANEL,
                     font=("Courier New", 7, "bold")).pack(anchor="w", padx=8, pady=(2, 2))

        # ── Sentimiento OpenBB ────────────────────────────────────────────────
        sec("◈ NOTICIAS + SENTIMIENTO", C_AMBER)
        self.news_text = tk.Text(parent, bg=BG_SUBCARD, fg=C_WHITE,
                                 font=("Courier New", 7), state=tk.DISABLED,
                                 wrap=tk.WORD, relief=tk.FLAT, bd=0, height=14)
        self.news_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.news_text.tag_config("HIGH",     foreground="#ff6666",
                                  font=("Courier New", 8, "bold"))
        self.news_text.tag_config("MEDIUM",   foreground="#ffaa66")
        self.news_text.tag_config("LOW",      foreground="#88ff88")
        self.news_text.tag_config("BULLISH",  foreground=C_GREEN,
                                  font=("Courier New", 7, "bold"))
        self.news_text.tag_config("BEARISH",  foreground=C_RED,
                                  font=("Courier New", 7, "bold"))
        self.news_text.tag_config("NEUTRAL_T", foreground=C_GRAY1)
        self.news_text.tag_config("openbb",   foreground=C_TEAL)

        # ── ORDER BOOK ────────────────────────────────────────────────────────
        sec("◈ ORDER BOOK DEPTH")
        hdr = tk.Frame(parent, bg=BG_PANEL); hdr.pack(fill=tk.X, padx=8, pady=(0, 2))
        self.lbl_ob_mode_r = tk.Label(hdr, text="MODE: --", fg=C_AMBER,
                                      bg=BG_PANEL, font=("Courier New", 7, "bold"))
        self.lbl_ob_mode_r.pack(side=tk.LEFT)
        self.lbl_spread_r = tk.Label(hdr, text="SPD: --", fg=C_YELLOW,
                                     bg=BG_PANEL, font=("Courier New", 7, "bold"))
        self.lbl_spread_r.pack(side=tk.RIGHT)

        imbf = tk.Frame(parent, bg=BG_PANEL); imbf.pack(fill=tk.X, padx=8, pady=(0, 1))
        tk.Label(imbf, text="IMBALANCE", fg=C_GRAY1, bg=BG_PANEL,
                 font=("Courier New", 6)).pack(side=tk.LEFT)
        self.lbl_imbalance = tk.Label(imbf, text="--", fg=C_CYAN,
                                      bg=BG_PANEL, font=("Courier New", 7, "bold"))
        self.lbl_imbalance.pack(side=tk.RIGHT)
        self.imb_cv = tk.Canvas(parent, height=5, bg=C_GRAY3, highlightthickness=0)
        self.imb_cv.pack(fill=tk.X, padx=8, pady=(0, 2))

        self.ob_ask_rows = []
        for _ in range(ORDERBOOK_LEVELS):
            f  = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=5, pady=0)
            pl = tk.Label(f, text="-------  ▲", fg=C_RED, bg=BG_PANEL,
                          font=("Courier New", 7, "bold"), width=12, anchor="w")
            pl.pack(side=tk.LEFT)
            cv = tk.Canvas(f, height=7, bg=BG_SUBCARD, highlightthickness=0)
            cv.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            vl = tk.Label(f, text="---", fg=C_GRAY1, bg=BG_PANEL,
                          font=("Courier New", 6), width=5, anchor="e")
            vl.pack(side=tk.RIGHT)
            self.ob_ask_rows.append((pl, cv, vl))

        mf = tk.Frame(parent, bg=BG_CARD); mf.pack(fill=tk.X, padx=4, pady=2)
        self.lbl_ob_price = tk.Label(mf, text="-------", fg=C_YELLOW,
                                     bg=BG_CARD, font=("Courier New", 11, "bold"))
        self.lbl_ob_price.pack(pady=2)

        self.ob_bid_rows = []
        for _ in range(ORDERBOOK_LEVELS):
            f  = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=5, pady=0)
            pl = tk.Label(f, text="-------  ▼", fg=C_GREEN, bg=BG_PANEL,
                          font=("Courier New", 7, "bold"), width=12, anchor="w")
            pl.pack(side=tk.LEFT)
            cv = tk.Canvas(f, height=7, bg=BG_SUBCARD, highlightthickness=0)
            cv.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
            vl = tk.Label(f, text="---", fg=C_GRAY1, bg=BG_PANEL,
                          font=("Courier New", 6), width=5, anchor="e")
            vl.pack(side=tk.RIGHT)
            self.ob_bid_rows.append((pl, cv, vl))

        # ── CUMULATIVE DELTA ──────────────────────────────────────────────────
        sec("◈ CUMULATIVE DELTA")
        self.lbl_delta = tk.Label(parent, text="+0", fg=C_GREEN,
                                  bg=BG_PANEL, font=("Courier New", 17, "bold"))
        self.lbl_delta.pack()
        self.delta_cv = tk.Canvas(parent, height=7, bg=C_GRAY3, highlightthickness=0)
        self.delta_cv.pack(fill=tk.X, padx=8, pady=(1, 2))
        for label, attr, col in [
            ("BUY VOL",  "lbl_buy_vol",   C_GREEN),
            ("SELL VOL", "lbl_sell_vol",  C_RED),
            ("B/S %",    "lbl_delta_pct", C_CYAN),
        ]:
            f = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=8, pady=1)
            tk.Label(f, text=label, fg=C_GRAY1, bg=BG_PANEL,
                     font=("Courier New", 7)).pack(side=tk.LEFT)
            w = tk.Label(f, text="--", fg=col, bg=BG_PANEL,
                         font=("Courier New", 7, "bold"))
            w.pack(side=tk.RIGHT); setattr(self, attr, w)

        # ── ORDER FLOW ────────────────────────────────────────────────────────
        sec("◈ ORDER FLOW")
        self.of_data = {}
        for label, attr, col in [
            ("Agg. Buy",  "of_ab", C_GREEN),
            ("Agg. Sell", "of_as", C_RED),
            ("Passive",   "of_pa", C_CYAN),
        ]:
            f  = tk.Frame(parent, bg=BG_PANEL); f.pack(fill=tk.X, padx=8, pady=1)
            tk.Label(f, text=label, fg=C_GRAY1, bg=BG_PANEL,
                     font=("Courier New", 7)).pack(side=tk.LEFT)
            pv = tk.Label(f, text="--%", fg=col, bg=BG_PANEL,
                          font=("Courier New", 7, "bold"))
            pv.pack(side=tk.RIGHT)
            cv2 = tk.Canvas(f, height=5, bg=BG_SUBCARD, highlightthickness=0, width=52)
            cv2.pack(side=tk.RIGHT, padx=4)
            self.of_data[attr] = (pv, cv2, col)

    # ── CONEXIÓN ───────────────────────────────────────────────────────────────
    def _connect_and_start(self):
        def _run():
            ok  = self.mt5_engine.connect()
            acc = self.mt5_engine.account
            if ok:
                a_type    = acc.get("type", "?")
                badge_txt = f" ● {a_type} #{acc.get('login','')} "
                badge_fg  = COL_REAL if a_type == "REAL" else COL_DEMO
                badge_bg  = "#001200" if a_type == "REAL" else "#1a1400"
            else:
                badge_txt = " ● DEMO (sin MT5) "
                badge_fg  = COL_DEMO; badge_bg = "#151000"
            self.root.after(0, lambda: self.live_badge.config(
                text=badge_txt, fg=badge_fg, bg=badge_bg))

            self.ob_engine    = RealOrderBookEngine(self.mt5_engine)
            self.delta_engine = RealDeltaEngine(self.mt5_engine, self._verifier)
            self.ob_engine.start()
            self.delta_engine.start()

            self._watchdog = MT5Watchdog(self.mt5_engine, self._verifier)
            self._watchdog.start()
            self.commodity_news.start()
            self.cot_engine.start()
            self.macro_engine.start()  # NUEVO v9.0

            self._calc_thread = CalcThread(
                self.mt5_engine, self.quant,
                self.ob_engine, self.delta_engine,
                self.news_engine, self._result_queue,
                self.commodity_news, self.cot_engine,
                macro_engine=self.macro_engine,    # NUEVO v9.0
                signal_logger=self.signal_logger,  # NUEVO v9.0
            )
            self._calc_thread.start()

            def _upd_ob():
                mode = getattr(self.ob_engine, "mode", "N/A")
                col  = {"REAL": COL_REAL, "RECONSTRUCTED": COL_DEMO,
                        "UNAVAILABLE": COL_ERROR, "DEMO": COL_DEMO,
                        "CHECKING": COL_CHECKING}.get(mode, C_GRAY1)
                self.lbl_ob_mode_top.config(text=f" OB: {mode} ", fg=col,
                                            bg="#001200" if mode == "REAL" else "#120c00")
                self.lbl_ob_mode_r.config(text=f"MODE: {mode}", fg=col)
            self.root.after(3000, _upd_ob)

            self.inst_feed.start()
            self.news_engine.fetch_async()

        threading.Thread(target=_run, daemon=True).start()

    # ── TICKS ─────────────────────────────────────────────────────────────────
    def _update_finnhub_panel(self, result: dict):
        """Actualiza los indicadores Finnhub en la UI (v10.0)."""
        try:
            finnhub_signal = result.get("finnhub_signal", {})
            finnhub_tech   = result.get("finnhub_tech",   {})
            finnhub_macro  = result.get("finnhub_macro",  {})
            if not finnhub_signal:
                return
            sig_dir   = finnhub_signal.get("signal", "NEUTRAL")
            sig_score = finnhub_signal.get("score", 0)
            sig_conf  = finnhub_signal.get("confidence", "NO_TRADE")
            sl        = finnhub_signal.get("sl")
            tp1       = finnhub_signal.get("tp1")
            if hasattr(self, "_lbl_finnhub_signal"):
                col = "#00ff88" if sig_dir == "LONG" else "#ff4444" if sig_dir == "SHORT" else "#aaaaaa"
                self._lbl_finnhub_signal.config(text=f"Finnhub:{sig_dir}[{sig_score}]", fg=col)
            if hasattr(self, "_lbl_finnhub_conf"):
                col = "#00ff88" if sig_conf == "HIGH" else "#ffaa00" if sig_conf == "NORMAL" else "#aaaaaa"
                self._lbl_finnhub_conf.config(text=sig_conf, fg=col)
            if hasattr(self, "_lbl_finnhub_sl") and sl and tp1:
                self._lbl_finnhub_sl.config(text=f"SL:{sl:.1f} TP1:{tp1:.1f}")
            if hasattr(self, "_lbl_finnhub_rsi") and finnhub_tech:
                rsi_val = finnhub_tech.get("rsi", 50)
                col = "#ff4444" if rsi_val > 70 else "#00ff88" if rsi_val < 30 else "#cccccc"
                self._lbl_finnhub_rsi.config(text=f"RSI:{rsi_val:.1f}", fg=col)
            if hasattr(self, "_lbl_finnhub_vix") and finnhub_macro:
                vix_val = finnhub_macro.get("vix", 0)
                if vix_val:
                    col = "#ff4444" if vix_val > 25 else "#ffaa00" if vix_val > 18 else "#cccccc"
                    self._lbl_finnhub_vix.config(text=f"VIX:{vix_val:.1f}", fg=col)
        except Exception:
            pass

    def _tick(self):
        if not self.running: return
        self._tick_count += 1
        try:
            result = None
            while not self._result_queue.empty():
                try: result = self._result_queue.get_nowait()
                except queue.Empty: break
            if result: self._last_result = result
            if self._last_result:
                self._refresh(self._last_result)
                self._update_finnhub_panel(self._last_result)  # NUEVO v10.0
        except Exception as e:
            print(f"[UI-tick] {e}")
        self.root.after(UPDATE_MS, self._tick)

    def _news_tick(self):
        if not self.running: return
        self._update_news_panel()
        self.root.after(NEWS_UPDATE_MS, self._news_tick)

    def _on_scroll(self, event):
        if event.inaxes != self.ax_main: return
        self._user_panned = True
        scale = 1 / 1.15 if event.button == 'up' else 1.15
        cur_xlim, cur_ylim = self.ax_main.get_xlim(), self.ax_main.get_ylim()
        nw = (cur_xlim[1] - cur_xlim[0]) * scale
        nh = (cur_ylim[1] - cur_ylim[0]) * scale
        rx = (cur_xlim[1] - event.xdata) / (cur_xlim[1] - cur_xlim[0])
        ry = (cur_ylim[1] - event.ydata) / (cur_ylim[1] - cur_ylim[0])
        self.ax_main.set_xlim([event.xdata - nw*(1-rx), event.xdata + nw*rx])
        self.ax_main.set_ylim([event.ydata - nh*(1-ry), event.ydata + nh*ry])
        self.canvas.draw_idle()

    def _on_press(self, event):
        if event.button == 3:
            self._is_panning = True; self._user_panned = True
            self._last_mouse_x = event.xdata; self._last_mouse_y = event.ydata

    def _on_release(self, event):
        self._is_panning = False

    def _on_motion(self, event):
        if self._is_panning and event.inaxes == self.ax_main:
            dx = self._last_mouse_x - event.xdata
            dy = self._last_mouse_y - event.ydata
            self.ax_main.set_xlim(self.ax_main.get_xlim() + np.array([dx, dx]))
            self.ax_main.set_ylim(self.ax_main.get_ylim() + np.array([dy, dy]))
            self.canvas.draw_idle()

    # ── REFRESH ───────────────────────────────────────────────────────────────
    def _refresh(self, r):
        m5        = r["m5"]
        vwap_data = r["vwap"]
        vol_prof  = r["vol_prof"]
        signal    = r["signal"]
        anom_m5   = r["anom_m5"]
        anom_m15  = r["anom_m15"]
        price     = r["price"]
        news_s    = r["news_s"]
        tc        = self._tick_count

        self._update_topbar(m5, news_s, r)
        self._update_left(signal, vwap_data, r)
        self._update_right_labels(price, r)
        self._update_news_panel()

        if tc % CHART_REDRAW_EVERY == 0:
            self._draw_main(vwap_data, vol_prof, signal, anom_m5, anom_m15, price, r)
        if tc % SUBCHART_REDRAW_EVERY == 0:
            self._draw_subcharts(vwap_data, vol_prof, anom_m5, anom_m15, price, r)
        if tc % 5 == 0:
            self._update_verification()

        self.canvas.draw_idle()

    def _update_news_panel(self):
        # Combina CommodityNews + OpenBBNews
        commodity = self.commodity_news.get_status()
        news_s    = self.news_engine.get_state() if hasattr(self.news_engine, 'get_state') else {}
        self.news_text.config(state=tk.NORMAL)
        self.news_text.delete(1.0, tk.END)
        impact = commodity["highest_impact"]
        mult   = commodity["risk_multiplier"]
        sent   = news_s.get("sentiment", "NEUTRAL")
        score  = news_s.get("score", 0)

        self.news_text.insert(tk.END, f"SENTIMIENTO: {sent}  (score: {score:+d})\n",
                              "BULLISH" if sent == "BULLISH" else
                              "BEARISH" if sent == "BEARISH" else "NEUTRAL_T")
        self.news_text.insert(tk.END, f"IMPACTO: {impact}  RISK MULT: {mult:.2f}\n\n", impact)

        # Titulares noticias (OpenBB + RSS)
        headlines = news_s.get("headlines", [])
        if headlines:
            self.news_text.insert(tk.END, "── TITULARES ──\n", "openbb")
        for h in list(headlines)[:12]:
            src   = h.get("source", "")
            sc    = h.get("score", 0)
            tag   = "BULLISH" if sc > 0 else "BEARISH" if sc < 0 else "NEUTRAL_T"
            src_tag = "[OBB]" if src == "OpenBB" else "[RSS]"
            self.news_text.insert(tk.END, f"{src_tag} {h['title'][:80]}\n", tag)

        # Próximo evento
        next_ev = news_s.get("next_event")
        if next_ev:
            self.news_text.insert(tk.END,
                f"\n⚡ PRÓXIMO: {next_ev['title']} en {next_ev.get('minutes_away','?')} min\n",
                "HIGH")

        # Commodity events
        if commodity["events"]:
            self.news_text.insert(tk.END, "\n── COMMODITY EVENTS ──\n", "openbb")
            for ev in commodity["events"][:8]:
                self.news_text.insert(tk.END, f"[{ev[0]}] {ev[1][:80]}\n\n", ev[0])

        self.news_text.config(state=tk.DISABLED)
        warn = (" ⚡ ALTO IMPACTO - NO OPERAR " if impact == "HIGH"
                else " ⚠ IMPACTO MEDIO - REDUCIR RIESGO " if impact == "MEDIUM" else "")
        self.lbl_news_warn.config(text=warn)

    def _update_topbar(self, rates, news_s, r):
        cur  = rates[-1]['close']
        prev = rates[-2]['close'] if len(rates) > 1 else cur
        chg  = cur - prev; pct = chg / prev * 100 if prev else 0
        sign = "+" if chg >= 0 else ""; col = C_GREEN if chg >= 0 else C_RED
        _lset(self.lbl_price, f"{cur:,.2f}", col)
        _lset(self.lbl_chg, f"{sign}{chg:.2f} ({sign}{pct:.2f}%)", col)
        now   = datetime.datetime.utcnow()
        in_s  = TRADE_HOURS[0] <= now.hour < TRADE_HOURS[1]
        _lset(self.lbl_clock,   now.strftime("%H:%M:%S"))
        _lset(self.lbl_session,
              f"SESSION: {'LONDON/NY — ACTIVE' if in_s else 'CLOSED'}",
              C_GREEN2 if in_s else C_RED)
        _lset(self.lbl_date, now.strftime("%Y-%m-%d  UTC"))
        inst = self.inst_feed.get()
        if inst:
            chg_i = inst.get("change", 0) or 0
            _lset(self.lbl_inst,
                  f"SPOT: {inst['price']:,.2f}  {'+'if chg_i>=0 else''}{chg_i:.2f}",
                  C_GRAY0 if not inst["stale"] else C_RED)
        ob = r.get("ob_data")
        if ob: _lset(self.lbl_spread_top, f"SPREAD: {ob.get('spread',0):.2f}")
        quant_z = r.get("quant_z", 0.0)
        _lset(self.lbl_rsi_top, f"Z-Score: {quant_z:+.2f}", C_CYAN)
        vs  = self._verifier.get_state()
        lat = vs["avg_latency_ms"]
        _lset(self.lbl_latency, f" LAT: {lat:.0f}ms ",
              C_GREEN if lat < MAX_LATENCY_MS else C_RED)
        _lset(self.lbl_ib_atr,    f"{r.get('atr_m15',0):.3f}")
        _lset(self.lbl_ib_bars,   str(len(rates)))
        _lset(self.lbl_ib_z,      f"{quant_z:+.2f}")
        _lset(self.lbl_ib_regime, r.get("quant_regime", "UNKNOWN"))
        risk_mult = self.commodity_news.get_status()["risk_multiplier"]
        _lset(self.lbl_ib_risk, f"{risk_mult:.2f}")

    def _update_left(self, sig, vwap_data, r):
        quant_signal   = r.get("quant_signal",   "NEUTRAL")
        quant_z        = r.get("quant_z",        0.0)
        quant_regime   = r.get("quant_regime",   "UNKNOWN")
        quant_size     = r.get("quant_size",     0.0)
        quant_sl       = r.get("quant_sl")
        quant_tp1      = r.get("quant_tp1")
        quant_tp2      = r.get("quant_tp2")
        quant_strength = r.get("quant_strength", 0)

        col = C_GREEN if quant_signal == "LONG" else C_RED if quant_signal == "SHORT" else C_GRAY1
        _lset(self.lbl_sig, quant_signal, col)
        stars = "★" * quant_strength + "☆" * (3 - quant_strength)
        _lset(self.lbl_strength_ui, f"STRENGTH: {stars}", C_GOLD)
        _lset(self.lbl_z, f"{quant_z:+.2f}")
        _lset(self.lbl_regime_ui, f"REGIME: {quant_regime}", C_TEAL)
        _lset(self.lbl_pos_size,  f"{quant_size:.2f} lots")
        _lset(self.lbl_sl,  f"{quant_sl:.2f}"  if quant_sl  else "---")
        _lset(self.lbl_tp1, f"{quant_tp1:.2f}" if quant_tp1 else "---")
        _lset(self.lbl_tp2, f"{quant_tp2:.2f}" if quant_tp2 else "---")

        w = self.z_cv.winfo_width()
        if w > 10:
            norm_z  = max(0, min(1, (quant_z + 3) / 6))
            fill_px = int(w * norm_z)
            self.z_cv.delete("all")
            self.z_cv.create_rectangle(0, 0, fill_px, 6,
                fill=C_GREEN if quant_z < 0 else C_RED, outline="")

        if vwap_data:
            _lset(self.lbl_vwap, f"{vwap_data['vwap_last']:,.2f}")
            sd     = vwap_data['sd_pos']
            sd_col = C_RED if abs(sd) >= 2 else C_GREEN if abs(sd) <= 1 else C_AMBER
            _lset(self.lbl_sd, f"{sd:+.2f}σ", sd_col)
        vol_prof = r.get("vol_prof")
        if vol_prof:
            _lset(self.lbl_vah, f"{vol_prof['vah']:,.2f}")
            _lset(self.lbl_poc, f"{vol_prof['poc']:,.2f}")
            _lset(self.lbl_val, f"{vol_prof['val']:,.2f}")
        _lset(self.lbl_atr, f"{r.get('atr_m15',0):.3f}")

        # Zonas de reacción
        zones     = r.get("reaction_zones", [])
        price     = r.get("price", 0)
        atr_m15   = r.get("atr_m15", 1.5)
        rz_engine = ReactionZoneEngine()
        if zones:
            sup_z, dem_z = rz_engine.nearest_zone(price, zones)
            active_count = sum(1 for z in zones if z["active"])
            _lset(self.lbl_rz_count, str(active_count), C_GOLD)
            if sup_z:
                _lset(self.lbl_rz_supply,
                      f"{sup_z['bot']:.2f}–{sup_z['top']:.2f}", C_SUPPLY)
            else:
                _lset(self.lbl_rz_supply, "---", C_GRAY1)
            if dem_z:
                _lset(self.lbl_rz_demand,
                      f"{dem_z['bot']:.2f}–{dem_z['top']:.2f}", C_DEMAND)
            else:
                _lset(self.lbl_rz_demand, "---", C_GRAY1)
        else:
            _lset(self.lbl_rz_count, "0"); _lset(self.lbl_rz_supply, "---")
            _lset(self.lbl_rz_demand, "---")

        # COT
        cot = r.get("cot_data", {})
        if cot:
            bias    = cot.get("bias", "N/A")
            net     = cot.get("net_institutional", 0.0)
            lp      = cot.get("long_pct", 50.0)
            sp      = cot.get("short_pct", 50.0)
            chg     = cot.get("net_change", 0.0)
            src     = cot.get("source", "N/A")
            bias_col = C_GREEN if bias == "BULLISH" else C_RED if bias == "BEARISH" else C_GRAY1
            _lset(self.lbl_cot_bias,  bias, bias_col)
            _lset(self.lbl_cot_net,   f"{net:+.1f}%", bias_col)
            _lset(self.lbl_cot_long,  f"{lp:.1f}%")
            _lset(self.lbl_cot_short, f"{sp:.1f}%")
            _lset(self.lbl_cot_chg,   f"{chg:+.1f}%",
                  C_GREEN if chg >= 0 else C_RED)
            _lset(self.lbl_cot_src, src)
            _draw_hbar2(self.cot_bar_cv, lp/100, C_GREEN2, sp/100, C_RED2)

        # Sentimiento noticias
        news_s = r.get("news_s", {})
        if news_s:
            sent     = news_s.get("sentiment", "NEUTRAL")
            openbb_ok = news_s.get("openbb_ok", False)
            ff_ok    = news_s.get("ff_ok", False)
            sent_col = C_GREEN if sent == "BULLISH" else C_RED if sent == "BEARISH" else C_GRAY1
            _lset(self.lbl_news_sent, sent, sent_col)
            _lset(self.lbl_news_src,
                  f"OpenBB: {'OK' if openbb_ok else 'N/A'}  |  FF: {'OK' if ff_ok else 'N/A'}")

        # Mean Reversion
        mr = r.get("mr_state")
        if mr: self._update_mr_panel(mr)

        # Log señales
        if quant_signal != "NEUTRAL" and quant_signal != self._last_quant_sig:
            tag = "buy" if quant_signal == "LONG" else "sell"
            self._log(f"[{datetime.datetime.utcnow().strftime('%H:%M')}] "
                      f"Q-SIGNAL: {quant_signal}  Z={quant_z:.2f}  STR={quant_strength}\n", tag)
            self._last_quant_sig = quant_signal
        elif quant_signal == "NEUTRAL" and self._last_quant_sig not in (None, "NEUTRAL"):
            self._log(f"[{datetime.datetime.utcnow().strftime('%H:%M')}] SEÑAL → WAIT\n", "wait")
            self._last_quant_sig = "NEUTRAL"

    def _update_mr_panel(self, mr):
        sig    = mr.get("signal", "NEUTRAL")
        score  = mr.get("score", 0)
        prob   = mr.get("prob", 30)
        rr     = mr.get("rr", 0.0)
        entry  = mr.get("entry"); target = mr.get("target"); stop = mr.get("stop")
        pct_b  = mr.get("pct_b", 0.5); k = mr.get("stoch_k", 50.0); d = mr.get("stoch_d", 50.0)
        struct = mr.get("structure", "RANGING")
        sig_col = C_GREEN if sig == "MR_LONG" else C_RED if sig == "MR_SHORT" else C_GRAY1
        _lset(self.lbl_mr_sig,    sig, sig_col)
        _lset(self.lbl_mr_score,  f"{score}/5", C_GOLD)
        _lset(self.lbl_mr_prob,   f"{prob}%", sig_col if sig != "NEUTRAL" else C_GRAY1)
        _lset(self.lbl_mr_rr,     f"{rr:.2f}")
        _lset(self.lbl_mr_entry,  f"{entry:,.2f}"  if entry  else "---")
        _lset(self.lbl_mr_target, f"{target:,.2f}" if target else "---",
              C_GREEN if sig == "MR_LONG" else C_RED if sig == "MR_SHORT" else C_GRAY1)
        _lset(self.lbl_mr_stop,   f"{stop:,.2f}"   if stop   else "---")
        _lset(self.lbl_mr_pctb,   f"{pct_b:.3f}",
              C_GREEN if pct_b < 0.15 else C_RED if pct_b > 0.85 else C_CYAN)
        _lset(self.lbl_mr_stoch,  f"{k:.0f}/{d:.0f}",
              C_GREEN if k < 20 else C_RED if k > 80 else C_GRAY1)
        _lset(self.lbl_mr_struct, struct,
              {"UPTREND": C_GREEN, "DOWNTREND": C_RED, "RANGING": C_CYAN}.get(struct, C_GRAY1))
        w = self.mr_score_cv.winfo_width()
        if w > 10:
            fill_px = int(w * score / 5.0)
            self.mr_score_cv.delete("all")
            self.mr_score_cv.create_rectangle(0, 0, fill_px, 5, fill=sig_col, outline="")
            self.mr_score_cv.create_rectangle(fill_px, 0, w, 5, fill=C_GRAY3, outline="")
        long_conf  = mr.get("long_conf",  {})
        short_conf = mr.get("short_conf", {})
        all_conf   = {**long_conf, **short_conf}
        for key, lbl_w in self._mr_conf_labels.items():
            if all_conf.get(key, False):
                lbl_w.config(text=f"● {key}", fg=sig_col if sig != "NEUTRAL" else C_TEAL)
            else:
                lbl_w.config(text=f"○ {key}", fg=C_GRAY2)
        if sig != "NEUTRAL" and sig != self._last_mr_sig:
            tag = "buy" if sig == "MR_LONG" else "sell"
            self._log(f"[{datetime.datetime.utcnow().strftime('%H:%M')}] "
                      f"MR: {sig}  {score}/5  RR={rr:.2f}\n", tag)
        self._last_mr_sig = sig

    def _update_right_labels(self, price, r):
        ob = r.get("ob_data")
        if ob:
            asks = ob.get("asks", []); bids = ob.get("bids", [])
            mode = ob.get("mode", "N/A"); spread = ob.get("spread", 0)
            imbalance = ob.get("imbalance", 0)
            mode_col  = COL_REAL if mode in ("REAL", "L2-REAL") else COL_ERROR
            _lset(self.lbl_ob_mode_r,   f"MODE: {mode}", mode_col)
            _lset(self.lbl_ob_mode_top, f" OB: {mode} ", mode_col)
            _lset(self.lbl_spread_r,    f"SPD: {spread:.2f}")
            _lset(self.lbl_ob_price,    f"{price:,.2f}")
            _lset(self.lbl_imbalance,   f"{imbalance:+.3f}",
                  C_GREEN if imbalance > 0 else C_RED)
            iw  = _WIDTH_CACHE.get(self.imb_cv); mid = iw // 2
            bar_w = int(abs(imbalance) * mid)
            self.imb_cv.delete("all")
            if imbalance > 0:
                self.imb_cv.create_rectangle(mid, 0, mid + bar_w, 5, fill=C_GREEN2, outline="")
            else:
                self.imb_cv.create_rectangle(mid - bar_w, 0, mid, 5, fill=C_RED2, outline="")
            self.imb_cv.create_line(mid, 0, mid, 5, fill=C_GRAY1, width=1)
            all_vols = [e["volume"] for e in asks + bids]
            max_vol  = max(all_vols) if all_vols else 1
            for i, (pl, cv, vl) in enumerate(self.ob_ask_rows):
                if i < len(asks):
                    e    = asks[i]; pct = e["volume"] / max_vol
                    is_w = e.get("is_wall", False)
                    _lset(pl, f"{e['price']:,.2f}  ▲", C_RED2 if is_w else C_RED)
                    _lset(vl, f"{e['volume']:.1f}")
                    r_  = int(80  + pct*160); g_ = int(0 + pct*20)
                    _draw_hbar(cv, pct, f"#{r_:02x}{g_:02x}20", BG_SUBCARD, height=7)
                else:
                    _lset(pl, "-------  ▲"); _lset(vl, "---")
            for i, (pl, cv, vl) in enumerate(self.ob_bid_rows):
                if i < len(bids):
                    e    = bids[i]; pct = e["volume"] / max_vol
                    is_w = e.get("is_wall", False)
                    _lset(pl, f"{e['price']:,.2f}  ▼", C_GREEN2 if is_w else C_GREEN)
                    _lset(vl, f"{e['volume']:.1f}")
                    g_ = int(60 + pct*170)
                    _draw_hbar(cv, pct, f"#14{g_:02x}30", BG_SUBCARD, height=7)
                else:
                    _lset(pl, "-------  ▼"); _lset(vl, "---")
        ds = r.get("ds")
        if ds:
            d_col = C_GREEN if ds["positive"] else C_RED
            _lset(self.lbl_delta,     f"{ds['delta_cum']:+,.2f}", d_col)
            _lset(self.lbl_buy_vol,   f"{ds['buy_vol']:,.2f}")
            _lset(self.lbl_sell_vol,  f"{ds['sell_vol']:,.2f}")
            _lset(self.lbl_delta_pct,
                  f"{ds['buy_pct']:.1f}% / {ds['sell_pct']:.1f}%",
                  C_GREEN if ds["buy_pct"] > 50 else C_RED)
            _draw_hbar2(self.delta_cv, ds["buy_pct"]/100, C_GREEN2, ds["sell_pct"]/100, C_RED2)
            for attr, val in [("of_ab", ds["aggr_buy_pct"]),
                               ("of_as", ds["aggr_sell_pct"]),
                               ("of_pa", ds["passive_pct"])]:
                pv, cv2, col = self.of_data[attr]
                _lset(pv, f"{val:.0f}%")
                _draw_hbar(cv2, val / 100, col, BG_SUBCARD, height=5)

    def _update_verification(self):
        vs  = self._verifier.get_state()
        col = COL_REAL if vs["real_ok"] else COL_ERROR
        _lset(self.lbl_ver_status,
              f"{vs['status']}  {'MT5 CONNECTED' if vs['real_ok'] else 'MT5 DISCONNECTED'}",
              col)
        lat = vs["avg_latency_ms"]
        _lset(self.lbl_ver_lat, f"{lat:.1f}ms", C_GREEN if lat < MAX_LATENCY_MS else C_RED)
        _lset(self.lbl_ver_up, vs["uptime"])
        drops = vs["conn_drops"]
        _lset(self.lbl_ver_drops, str(drops), C_RED if drops > 0 else C_GREEN)

    # ── GRÁFICOS ───────────────────────────────────────────────────────────────
    def _draw_main(self, vwap_data, vol_prof, signal, anom_m5, anom_m15, price, r):
        tab = self._chart_tab
        if tab == 0:   self._draw_vwap_chart(vwap_data, vol_prof, signal, price, r)
        elif tab == 1: self._draw_pivots_fib_chart(vwap_data, vol_prof, signal, price, r)
        elif tab == 2: self._draw_delta_ob_chart(vwap_data, vol_prof, signal, price, r)
        else:          self._draw_mean_reversion_chart(vwap_data, vol_prof, price, r)

    def _draw_zones_on_ax(self, ax, zones, n, minY, maxY):
        """Dibuja zonas de reacción (supply/demand) sobre cualquier gráfico."""
        if not zones: return
        for z in zones:
            if not (minY <= z["mid"] <= maxY): continue
            color = C_SUPPLY if z["type"] == "SUPPLY" else C_DEMAND
            alpha = 0.20 if z["active"] else 0.07
            ax.add_patch(mpatches.Rectangle(
                (0, z["bot"]), n, z["top"] - z["bot"],
                color=color, alpha=alpha, zorder=2))
            ax.axhline(z["top"], color=color, lw=0.6, ls="--",
                       alpha=0.5 if z["active"] else 0.2, zorder=2)
            ax.axhline(z["bot"], color=color, lw=0.6, ls="--",
                       alpha=0.5 if z["active"] else 0.2, zorder=2)
            if z["active"]:
                ax.text(n + 0.5, z["mid"],
                        f" {z['type'][0]} {z['mid']:.2f}",
                        color=color, fontsize=5, va="center", alpha=0.85, zorder=10)

    def _draw_vp_overlay(self, ax, vol_prof, n, minY, maxY):
        bins = vol_prof["bins"]; hist = vol_prof["hist"]
        poc  = vol_prof["poc"]; vah = vol_prof["vah"]; val = vol_prof["val"]
        max_h = max(hist) if max(hist) > 0 else 1
        bar_w = n * 0.12
        for b, h in zip(bins, hist):
            if not (minY <= b <= maxY) or h <= 0: continue
            w_px = bar_w * h / max_h
            clr  = C_POC if abs(b - poc) < PRICE_STEP * 1.5 else C_BLUE3
            ax.barh(b, w_px, height=PRICE_STEP * 0.85, left=n + 0.3,
                    color=clr, alpha=0.65, zorder=5)
        for lv, lc, lt, ls in [
            (poc, C_POC, f" POC {poc:.2f}", ":"),
            (vah, C_VAH, f" VAH {vah:.2f}", "--"),
            (val, C_VAL, f" VAL {val:.2f}", "--"),
        ]:
            ax.axhline(lv, color=lc, lw=0.9 if lv == poc else 0.8,
                       ls=ls, alpha=0.95 if lv == poc else 0.80)
            ax.text(0.98, lv, lt, color=lc, fontsize=5.5, va="center", ha="right",
                    transform=ax.get_yaxis_transform(), fontweight="bold")
        ax.axhline(self._cur_p if hasattr(self, '_cur_p') else lv, color=C_CYAN, lw=1.0, alpha=0.90)

    def _draw_vwap_chart(self, vwap_data, vol_prof, signal, price, r):
        ax = self.ax_main; ax.clear(); ax.set_facecolor("#010508")
        for sp in ax.spines.values(): sp.set_color(C_GRAY3)
        ax.tick_params(colors=C_GRAY1, labelsize=6.5, length=3)
        ax.grid(True, color="#040c18", linewidth=0.28, alpha=0.85)
        if vwap_data is None:
            ax.set_title("VWAP — Sin datos", color=C_GOLD, loc="left", fontsize=9); return
        prices_arr = vwap_data["prices"]; vwap = vwap_data["vwap"]; std = vwap_data["std"]
        highs = vwap_data["highs"]; lows = vwap_data["lows"]; opens = vwap_data["opens"]
        volumes = vwap_data["volumes"]; n = len(prices_arr); x = np.arange(n)
        cur_p = float(prices_arr[-1]); atr = max(r.get("atr_m15", 1.5), 0.5)
        minY = min(float(np.min(prices_arr)) - atr, float(vwap[-1] - std[-1]*5)) - 0.5
        maxY = max(float(np.max(prices_arr)) + atr, float(vwap[-1] + std[-1]*5)) + 0.5
        ax.set_xlim(-2, n + 12); ax.set_ylim(minY, maxY)
        band_cfg = [(4,"#1a0404","#c0392b",0.30,0.35),(3,"#1a0d04","#e67e22",0.35,0.40),
                    (2,"#1a1804","#f1c40f",0.40,0.45),(1,"#041a08","#2ecc71",0.45,0.55)]
        for b, fill_c, edge_c, fa, ea in reversed(band_cfg):
            ax.fill_between(x, vwap - b*std, vwap + b*std, color=fill_c, alpha=fa, zorder=2)
            for sg in [1, -1]:
                ax.plot(x, vwap + sg*b*std, color=edge_c, lw=0.7, alpha=ea, zorder=3)
                ax.text(n-1, float(vwap[-1] + sg*b*std[-1]),
                        f" {'+' if sg>0 else ''}{sg*b}σ",
                        color=edge_c, fontsize=5, va="center", alpha=0.8, zorder=10)
        ax.plot(x, vwap, color=C_VWAP, lw=1.8, alpha=0.95, zorder=6)
        for i in range(1, n):
            col_s = C_GREEN if prices_arr[i] >= vwap[i] else C_RED
            ax.plot([x[i-1], x[i]], [prices_arr[i-1], prices_arr[i]],
                    color=col_s, lw=1.2, alpha=0.9, zorder=7)
        v_max   = max(volumes) if len(volumes) > 0 else 1
        v_range = (maxY - minY) * 0.08
        ax.bar(np.arange(n), volumes / v_max * v_range, bottom=minY,
               color=[C_GREEN4 if prices_arr[i] >= opens[i] else C_RED4 for i in range(n)],
               alpha=0.5, width=0.8, zorder=3)
        ax.scatter([n-1], [cur_p], s=80, color=C_CYAN, zorder=20,
                   edgecolors=C_WHITE, linewidths=1.0)

        # Order Blocks
        obs = r.get("order_blocks", [])
        for ob in obs:
            color = C_GREEN if ob['type'] == 'BULLISH' else C_RED
            ax.add_patch(mpatches.Rectangle((0, ob['bottom']), n, ob['top'] - ob['bottom'],
                                            color=color, alpha=0.12, zorder=2))

        # Zonas de reacción
        self._draw_zones_on_ax(ax, r.get("reaction_zones", []), n, minY, maxY)

        qs  = r.get("quant_signal", "NEUTRAL"); qz = r.get("quant_z", 0.0)
        sig_col = C_GREEN if qs == "LONG" else C_RED if qs == "SHORT" else C_GRAY2
        if qs != "NEUTRAL":
            ax.axhline(cur_p, color=sig_col, lw=0.8, ls="--", alpha=0.6, zorder=5)
            ax.text(1, cur_p + (maxY-minY)*0.01, f" {qs}  Z={qz:+.2f}",
                    color=sig_col, fontsize=7, va="bottom", fontweight="bold", zorder=19)
        if vol_prof: self._draw_vp_overlay(ax, vol_prof, n, minY, maxY)
        ax.text(-1, cur_p, f"{cur_p:,.2f}", color=C_YELLOW, fontsize=7,
                va="center", ha="right", fontweight="bold",
                bbox=dict(facecolor=BG_CARD, edgecolor=C_YELLOW,
                          linewidth=0.8, boxstyle="round,pad=0.2"), zorder=22)
        ax.set_title(
            f"◈ VWAP BANDS  │  Z: {qz:+.2f}  │  {qs}  │  XAUUSD {cur_p:,.2f}",
            color=sig_col if qs != "NEUTRAL" else C_GOLD,
            loc="left", fontsize=8.5, fontweight="bold", pad=4)

    def _draw_pivots_fib_chart(self, vwap_data, vol_prof, signal, price, r):
        ax = self.ax_main; ax.clear(); ax.set_facecolor("#010508")
        for sp in ax.spines.values(): sp.set_color(C_GRAY3)
        ax.tick_params(colors=C_GRAY1, labelsize=6.5, length=3)
        ax.grid(True, color="#040c18", linewidth=0.28, alpha=0.85)
        if vwap_data is None:
            ax.set_title("PIVOTS/FIB — Sin datos", color=C_GOLD, loc="left", fontsize=9); return
        prices_arr = vwap_data["prices"]; n = len(prices_arr); cur_p = float(prices_arr[-1])
        atr = max(r.get("atr_m15", 1.5), 0.5); x = np.arange(n)
        pivots = r.get("pivots"); fib = r.get("fib")
        all_y  = [cur_p - atr*6, cur_p + atr*6]
        if pivots: all_y += [pivots.get("R3", cur_p+atr*6), pivots.get("S3", cur_p-atr*6)]
        if fib:    all_y += list(fib["levels"].values())
        minY = min(all_y) - 1.0; maxY = max(all_y) + 1.0
        ax.set_xlim(-4, n + 22); ax.set_ylim(minY, maxY)
        ax.plot(x, prices_arr, color=C_PRICE, lw=1.6, zorder=10)
        ax.scatter([n-1], [cur_p], s=65, color=C_CYAN, zorder=20, edgecolors=C_WHITE, linewidths=0.9)
        if pivots:
            pvt_cfg = [("R3",C_R3,(0,(1,3))),("R2",C_R2,(0,(5,3))),("R1",C_R1,(0,(8,3))),
                       ("PP",C_PP,(0,())),("S1",C_S1,(0,(8,3))),("S2",C_S2,(0,(5,3))),
                       ("S3",C_S3,(0,(1,3)))]
            for key, cp, ls in pvt_cfg:
                val = pivots.get(key, 0)
                if not (minY <= val <= maxY): continue
                ax.axhline(val, color=cp, lw=0.9, ls=ls, alpha=0.85, zorder=8)
                ax.text(n+1, val, f"  {key} {val:,.2f}", color=cp, fontsize=7,
                        va="center", fontweight="bold", zorder=15)
        if fib:
            fib_colors = {0.0:"#cccccc",0.236:"#ef9a9a",0.382:"#ce93d8",
                          0.500:"#90caf9",0.618:"#a5d6a7",0.786:"#80cbc4",1.0:"#cccccc"}
            for lvl, fval in fib["levels"].items():
                if not (minY <= fval <= maxY): continue
                fc = fib_colors.get(lvl, C_FIB)
                ax.axhline(fval, color=fc, lw=0.7,
                           ls=(0,(3,5)) if lvl not in (0.0,1.0) else (0,()), alpha=0.72, zorder=7)
                ax.text(n+1, fval, f"  {lvl:.3f}  {fval:,.2f}",
                        color=fc, fontsize=6.5, va="center", alpha=0.9, zorder=14)
        if vwap_data:
            ax.plot(x, vwap_data["vwap"], color=C_VWAP, lw=0.9, ls="--", alpha=0.55, zorder=5)

        # Zonas de reacción
        self._draw_zones_on_ax(ax, r.get("reaction_zones", []), n, minY, maxY)

        if vol_prof: self._draw_vp_overlay(ax, vol_prof, n, minY, maxY)
        ax.text(-1, cur_p, f"{cur_p:,.2f}", color=C_YELLOW, fontsize=7,
                va="center", ha="right", fontweight="bold",
                bbox=dict(facecolor=BG_CARD, edgecolor=C_YELLOW,
                          linewidth=0.8, boxstyle="round,pad=0.2"), zorder=22)
        ax.set_title(f"◈ CLASSIC PIVOTS + FIBONACCI  │  XAUUSD {cur_p:,.2f}",
                     color=C_GOLD, loc="left", fontsize=8.5, fontweight="bold", pad=4)

    def _draw_delta_ob_chart(self, vwap_data, vol_prof, signal, price, r):
        ax = self.ax_main; ax.clear(); ax.set_facecolor("#010508")
        for sp in ax.spines.values(): sp.set_color(C_GRAY3)
        ax.tick_params(colors=C_GRAY1, labelsize=6.5, length=3)
        ax.grid(True, color="#040c18", linewidth=0.28, alpha=0.85)
        if vwap_data is None:
            ax.set_title("DELTA/OB — Sin datos", color=C_GOLD, loc="left", fontsize=9); return
        prices_arr = vwap_data["prices"]; n = len(prices_arr); cur_p = float(prices_arr[-1])
        atr = max(r.get("atr_m15", 1.5), 0.5); x = np.arange(n)
        minY = float(np.min(prices_arr)) - atr * 3; maxY = float(np.max(prices_arr)) + atr * 3
        ax.set_xlim(-2, n + 10); ax.set_ylim(minY, maxY)
        ax.plot(x, prices_arr, color=C_PRICE, lw=1.5, zorder=8)
        ax.scatter([n-1], [cur_p], s=65, color=C_CYAN, zorder=20, edgecolors=C_WHITE, linewidths=0.9)

        # Heatmap delta
        hm_cols = r.get("heatmap_cols", [])
        if hm_cols:
            n_cols = min(len(hm_cols), n)
            for ci, col_data in enumerate(hm_cols[-n_cols:]):
                x_pos = n - n_cols + ci
                for cell in col_data:
                    cp = cell["price"]
                    if not (minY <= cp <= maxY): continue
                    br = cell["buy_ratio"]; intensity = cell["intensity"]
                    if intensity < 0.05: continue
                    r_  = int(255 * (1 - br)); g_ = int(255 * br); b_ = 0
                    hex_color = f"#{r_:02x}{g_:02x}{b_:02x}"
                    ax.add_patch(mpatches.Rectangle(
                        (x_pos, cp - PRICE_STEP/2), 1, PRICE_STEP,
                        color=hex_color, alpha=intensity * 0.55, zorder=3))

        # Order Blocks
        obs = r.get("order_blocks", [])
        for ob in obs:
            color = C_GREEN if ob['type'] == 'BULLISH' else C_RED
            ax.add_patch(mpatches.Rectangle((0, ob['bottom']), n, ob['top'] - ob['bottom'],
                                            color=color, alpha=0.15, zorder=4))
            ax.axhline(ob['top'],    color=color, lw=0.6, ls="--", alpha=0.4, zorder=4)
            ax.axhline(ob['bottom'], color=color, lw=0.6, ls="--", alpha=0.4, zorder=4)
            lbl = "BULL OB" if ob['type'] == 'BULLISH' else "BEAR OB"
            ax.text(n + 0.5, (ob['top'] + ob['bottom'])/2,
                    f" {lbl}", color=color, fontsize=5, va="center", zorder=10)

        # Zonas de reacción
        self._draw_zones_on_ax(ax, r.get("reaction_zones", []), n, minY, maxY)

        if vol_prof: self._draw_vp_overlay(ax, vol_prof, n, minY, maxY)
        ax.text(-1, cur_p, f"{cur_p:,.2f}", color=C_YELLOW, fontsize=7,
                va="center", ha="right", fontweight="bold",
                bbox=dict(facecolor=BG_CARD, edgecolor=C_YELLOW,
                          linewidth=0.8, boxstyle="round,pad=0.2"), zorder=22)
        ax.set_title(f"◈ DELTA HEATMAP + ORDER BLOCKS + ZONAS  │  XAUUSD {cur_p:,.2f}",
                     color=C_GOLD, loc="left", fontsize=8.5, fontweight="bold", pad=4)

    def _draw_mean_reversion_chart(self, vwap_data, vol_prof, price, r):
        ax = self.ax_main; ax.clear(); ax.set_facecolor("#010508")
        for sp in ax.spines.values(): sp.set_color(C_GRAY3)
        ax.tick_params(colors=C_GRAY1, labelsize=6.5, length=3)
        ax.grid(True, color="#040c18", linewidth=0.28, alpha=0.85)
        if vwap_data is None:
            ax.set_title("MEAN REV — Sin datos", color=C_GOLD, loc="left", fontsize=9); return
        prices_arr = vwap_data["prices"]; n = len(prices_arr); cur_p = float(prices_arr[-1])
        atr = max(r.get("atr_m15", 1.5), 0.5); x = np.arange(n)
        mr  = r.get("mr_state", {})
        bb_upper = mr.get("bb_upper"); bb_mid = mr.get("bb_mid"); bb_lower = mr.get("bb_lower")
        all_y = [cur_p - atr*4, cur_p + atr*4]
        if bb_upper: all_y += [bb_upper, bb_lower]
        minY = min(all_y) - 1.0; maxY = max(all_y) + 1.0
        ax.set_xlim(-2, n + 10); ax.set_ylim(minY, maxY)
        ax.plot(x, prices_arr, color=C_PRICE, lw=1.5, zorder=8)
        if bb_upper and bb_mid and bb_lower:
            ax.fill_between(x, bb_lower, bb_upper, color=C_BLUE3, alpha=0.15, zorder=2)
            ax.plot(x, [bb_upper]*n, color=C_RED,   lw=1.0, ls="--", alpha=0.75, zorder=5)
            ax.plot(x, [bb_mid]*n,   color=C_GRAY1, lw=0.8, ls=":",  alpha=0.60, zorder=5)
            ax.plot(x, [bb_lower]*n, color=C_GREEN, lw=1.0, ls="--", alpha=0.75, zorder=5)
            ax.text(n+0.3, bb_upper, f" BB+ {bb_upper:.2f}", color=C_RED,   fontsize=5.5, va="center")
            ax.text(n+0.3, bb_lower, f" BB- {bb_lower:.2f}", color=C_GREEN, fontsize=5.5, va="center")
        sig = mr.get("signal", "NEUTRAL")
        entry  = mr.get("entry"); target = mr.get("target"); stop = mr.get("stop")
        sig_col = C_GREEN if sig == "MR_LONG" else C_RED if sig == "MR_SHORT" else C_GRAY2
        if sig != "NEUTRAL" and entry:
            ax.axhline(entry,  color=sig_col, lw=1.0, ls="-",  alpha=0.8, zorder=7)
            if target: ax.axhline(target, color=C_GREEN, lw=0.8, ls="--", alpha=0.7, zorder=7)
            if stop:   ax.axhline(stop,   color=C_RED,   lw=0.8, ls="--", alpha=0.7, zorder=7)
        if vol_prof: self._draw_vp_overlay(ax, vol_prof, n, minY, maxY)
        self._draw_zones_on_ax(ax, r.get("reaction_zones", []), n, minY, maxY)
        ax.scatter([n-1], [cur_p], s=65, color=C_CYAN, zorder=20, edgecolors=C_WHITE, linewidths=0.9)
        ax.text(-1, cur_p, f"{cur_p:,.2f}", color=C_YELLOW, fontsize=7,
                va="center", ha="right", fontweight="bold",
                bbox=dict(facecolor=BG_CARD, edgecolor=C_YELLOW,
                          linewidth=0.8, boxstyle="round,pad=0.2"), zorder=22)
        ax.set_title(f"◈ MEAN REVERSION  │  {sig}  │  XAUUSD {cur_p:,.2f}",
                     color=sig_col if sig != "NEUTRAL" else C_GOLD,
                     loc="left", fontsize=8.5, fontweight="bold", pad=4)

    def _draw_subcharts(self, vwap_data, vol_prof, anom_m5, anom_m15, price, r):
        self._draw_vp_sub(vol_prof, price, r)
        self._draw_delta_bars(r)
        self._draw_ob_imbalance(r)
        self._draw_anomaly_sub(self.ax_sub1, anom_m5,  "Anomalía M5")
        self._draw_anomaly_sub(self.ax_sub2, anom_m15, "Anomalía M15")

    def _draw_vp_sub(self, vol_prof, price, r):
        ax = self.ax_vp; ax.clear(); ax.set_facecolor(BG_CHART)
        for sp in ax.spines.values(): sp.set_color(C_GRAY3)
        ax.tick_params(colors=C_GRAY1, labelsize=6, length=2)
        ax.grid(True, color=GRID, linewidth=0.25, alpha=0.9)
        ax.set_title("Volume Profile", color=C_GOLD, loc="left", fontsize=7, pad=2)
        if not vol_prof: return
        bins = vol_prof["bins"]; hist = vol_prof["hist"]
        poc  = vol_prof["poc"]; vah = vol_prof["vah"]; val = vol_prof["val"]
        max_h = max(hist) if max(hist) > 0 else 1
        colors = []
        for b, h in zip(bins, hist):
            if abs(b - poc) < PRICE_STEP * 1.5: colors.append(C_POC)
            elif b >= val and b <= vah:          colors.append(C_BLUE2)
            else:                                colors.append(C_GRAY3)
        ax.barh(bins, hist / max_h, height=PRICE_STEP * 0.85, color=colors, alpha=0.80)
        for lv, lc, lt, ls in [
            (poc, C_POC, f" POC {poc:.2f}", ":"),
            (vah, C_VAH, f" VAH {vah:.2f}", "--"),
            (val, C_VAL, f" VAL {val:.2f}", "--"),
        ]:
            ax.axhline(lv, color=lc, lw=0.9 if lv == poc else 0.8,
                       ls=ls, alpha=0.95 if lv == poc else 0.80)
            ax.text(0.98, lv, lt, color=lc, fontsize=5.5, va="center", ha="right",
                    transform=ax.get_yaxis_transform(), fontweight="bold")
        ax.axhline(price, color=C_CYAN, lw=1.0, alpha=0.90)

    def _draw_delta_bars(self, r):
        ax = self.ax_delta; ax.clear(); ax.set_facecolor(BG_CHART)
        for sp in ax.spines.values(): sp.set_color(C_GRAY3)
        ax.tick_params(colors=C_GRAY1, labelsize=6, length=2)
        ax.grid(True, color=GRID, linewidth=0.25, alpha=0.9)
        ax.set_title("Delta por Barra", color=C_GOLD, loc="left", fontsize=7, pad=2)
        ds = r.get("ds")
        if not ds: return
        bars = ds.get("delta_bars", [])
        if bars:
            vals = [b["delta"] for b in bars]
            cols = [C_GREEN if v >= 0 else C_RED for v in vals]
            ax.bar(range(len(vals)), vals, color=cols, alpha=0.80, width=0.85)
            ax.axhline(0, color=C_GRAY1, lw=0.5, alpha=0.7)
        else:
            bv = ds.get("buy_vol", 0); sv = ds.get("sell_vol", 0)
            bars_p = ax.bar([0, 1], [bv, -sv], color=[C_GREEN, C_RED], alpha=0.80, width=0.65)
            ax.set_xticks([0, 1])
            ax.set_xticklabels(["BUY", "SELL"], color=C_GRAY1, fontsize=6)
            ax.axhline(0, color=C_GRAY1, lw=0.5, alpha=0.7)
        cum = ds["delta_cum"]; bp = ds.get("buy_pct", 50)
        ax.text(0.98, 0.98, f"Cum: {cum:+,.1f}\nB/S: {bp:.0f}/{100-bp:.0f}%",
                color=C_GREEN if cum >= 0 else C_RED,
                fontsize=6, va="top", ha="right", transform=ax.transAxes, fontweight="bold")

    def _draw_ob_imbalance(self, r):
        ax = self.ax_ob; ax.clear(); ax.set_facecolor(BG_CHART)
        for sp in ax.spines.values(): sp.set_color(C_GRAY3)
        ax.tick_params(colors=C_GRAY1, labelsize=6, length=2)
        ax.grid(True, color=GRID, linewidth=0.25, alpha=0.9)
        ax.set_title("OB Imbalance", color=C_GOLD, loc="left", fontsize=7, pad=2)
        ob = r.get("ob_data")
        if not ob: return
        asks = ob.get("asks", []); bids = ob.get("bids", [])
        if not asks or not bids: return
        n_levels  = min(8, len(bids), len(asks))
        raw_vols  = ([e["volume"] for e in bids[:n_levels]] +
                     [e["volume"] for e in asks[:n_levels]])
        if not raw_vols: return
        cap = float(np.percentile(raw_vols, 90)) * 1.5; cap = max(cap, 1e-9)
        clamp = lambda v: min(v, cap)
        prices_bids = [e["price"] for e in bids[:n_levels]]
        prices_asks = [e["price"] for e in asks[:n_levels]]
        vols_bids   = [clamp(e["volume"]) for e in bids[:n_levels]]
        vols_asks   = [clamp(e["volume"]) for e in asks[:n_levels]]
        ax.barh(prices_bids, [-v for v in vols_bids], height=0.38,
                color=C_GREEN, alpha=0.72, linewidth=0)
        ax.barh(prices_asks, vols_asks, height=0.38,
                color=C_RED, alpha=0.72, linewidth=0)
        ax.axvline(0, color=C_GRAY1, lw=0.8, alpha=0.8)
        mid_price = (asks[0]["price"] + bids[0]["price"]) / 2
        ax.axhline(mid_price, color=C_YELLOW, lw=0.8, ls="--", alpha=0.80)
        ax.text(cap * 0.02, mid_price, f" {mid_price:,.2f}",
                color=C_YELLOW, fontsize=5.5, va="bottom")
        imb = ob.get("imbalance", 0)
        ax.text(0.02, 0.98,
                f"Bid: {ob.get('total_bid',0):.1f}\n"
                f"Ask: {ob.get('total_ask',0):.1f}\n"
                f"IMB: {imb:+.3f}",
                color=C_GREEN if imb > 0 else C_RED, fontsize=5.5, va="top",
                transform=ax.transAxes, fontweight="bold")

    def _draw_anomaly_sub(self, ax, data, title):
        ax.clear(); ax.set_facecolor(BG_CHART)
        for sp in ax.spines.values(): sp.set_color(C_GRAY3)
        ax.tick_params(colors=C_GRAY1, labelsize=5.5, length=2)
        ax.grid(True, color=GRID, linewidth=0.22)
        if data is None:
            ax.set_title(title, color=C_GRAY1, loc="left", fontsize=6.5); return
        rets  = data["rets"]; upper = data["upper"]
        lower = data["lower"]; mr    = data["mean"]
        n = len(rets); x = np.arange(n)
        ax.fill_between(x, lower, upper, color=C_GRAY3, alpha=0.50)
        ax.fill_between(x, mr, upper, where=rets > upper, color=C_RED,   alpha=0.30)
        ax.fill_between(x, lower, mr, where=rets < lower, color=C_GREEN, alpha=0.30)
        ax.plot(x, rets,  color=C_GOLD,  lw=0.75, alpha=0.92)
        ax.plot(x, upper, color=C_RED,   lw=0.55, ls="--", alpha=0.65)
        ax.plot(x, lower, color=C_GREEN, lw=0.55, ls="--", alpha=0.65)
        ax.axhline(0, color=C_GRAY2, lw=0.45, alpha=0.8)
        ex_h = rets > upper; ex_l = rets < lower
        if np.any(ex_h): ax.scatter(x[ex_h], rets[ex_h], color=C_RED,   s=12, zorder=10)
        if np.any(ex_l): ax.scatter(x[ex_l], rets[ex_l], color=C_GREEN, s=12, zorder=10)
        sig = data["signal"]
        sig_txt = {"EXTREME_HIGH": "⬆ EXTR. HI — SELL",
                   "EXTREME_LOW":  "⬇ EXTR. LO — BUY",
                   "NEUTRAL":      "● NEUTRAL"}[sig]
        sig_col = {"EXTREME_HIGH": C_RED, "EXTREME_LOW": C_GREEN, "NEUTRAL": C_GRAY1}[sig]
        ax.set_title(f"{title}  │  {sig_txt}  ret={data['last_ret']:.4f}%",
                     color=sig_col, loc="left", fontsize=7, fontweight="bold", pad=2)

    def _log(self, txt, tag="wait"):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert("1.0", txt, tag)
        self.log_text.config(state=tk.DISABLED)

    def on_close(self):
        self.running = False
        if self._calc_thread:   self._calc_thread.stop()
        if self._watchdog:      self._watchdog.stop()
        if self.ob_engine:      self.ob_engine.stop()
        if self.delta_engine:   self.delta_engine.stop()
        if self.inst_feed:      self.inst_feed.stop()
        if self.cot_engine:     self.cot_engine.stop()
        self.commodity_news.stop()
        if MT5_AVAILABLE:
            try: mt5.shutdown()
            except: pass
        self.root.destroy()


from kaurum_patch_v10 import apply_patch

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"K-AURUM Q-CORE v8.0")
    print(f"OpenBB:  {'OK' if OPENBB_AVAILABLE else 'yfinance fallback'}")
    print(f"MT5:     {'OK' if MT5_AVAILABLE else 'NO DETECTADO'}")
    print(f"Zonas:   Reaction Zone Engine activo")
    print(f"COT:     OpenBBCOTEngine (CFTC gold 088691)")

    apply_patch()  # ✅ YA está importado

    root = tk.Tk()
    app  = KAurumTerminal(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)

    root.mainloop()