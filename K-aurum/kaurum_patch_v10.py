"""
K-AURUM PATCH v10 — Dos cambios quirúrgicos sobre v9.0
=======================================================
INTEGRACIÓN: al final del archivo original k-AURUM.py, añadir:
    from kaurum_patch_v10 import apply_patch
    apply_patch()
Y en if __name__ == "__main__": antes de root.mainloop():
    apply_patch()

Cambios:
  1. Noticias en español con probabilidad alcista/bajista por noticia
  2. SmartSignalStateMachine — no repite señal sin invalidación real;
     en vez de eso busca el próximo POI (OB, liquidez, manipulación)
     y solo dispara cuando el precio llega y confirma.
"""

import re, threading, time, datetime
from collections import deque
import numpy as np

# ══════════════════════════════════════════════════════════════════════════════
#  1. TRADUCTOR DE NOTICIAS + PROBABILIDAD DIRECCIONAL
# ══════════════════════════════════════════════════════════════════════════════

# Diccionario de traducción por frase/keyword (inglés → español)
_TRANS = {
    # Eventos económicos
    "non-farm payrolls":  "Nóminas no agrícolas (NFP)",
    "nfp":               "NFP",
    "fomc":              "FOMC (Fed)",
    "federal reserve":   "Reserva Federal",
    "interest rate":     "Tasa de interés",
    "rate hike":         "Subida de tasas",
    "rate cut":          "Recorte de tasas",
    "inflation":         "Inflación",
    "cpi":               "IPC (Inflación)",
    "pce":               "PCE (Gasto consumo)",
    "gdp":               "PIB",
    "jobs report":       "Informe de empleo",
    "unemployment":      "Desempleo",
    "payrolls":          "Nóminas",
    "treasury":          "Tesoro EE.UU.",
    "powell":            "Powell (Fed)",
    "ecb":               "BCE",
    "yields rise":       "Rendimientos suben",
    "yields fall":       "Rendimientos caen",
    "dollar strong":     "Dólar fuerte",
    "dollar weak":       "Dólar débil",
    "usd rises":         "USD sube",
    "usd falls":         "USD cae",
    # Eventos geopolíticos/mercado
    "safe haven":        "Refugio seguro",
    "risk off":          "Risk-off (huida al oro)",
    "risk on":           "Risk-on (apetito de riesgo)",
    "geopolitical":      "Tensión geopolítica",
    "war":               "Guerra",
    "sanctions":         "Sanciones",
    "conflict":          "Conflicto",
    "recession":         "Recesión",
    "stimulus":          "Estímulo fiscal",
    "easing":            "Flexibilización",
    "hawkish":           "Hawkish (restrictivo)",
    "dovish":            "Dovish (expansivo)",
    "taper":             "Tapering",
    # Movimientos de precio
    "surges":            "sube con fuerza",
    "rallies":           "repunta",
    "climbs":            "escala",
    "soars":             "se dispara",
    "falls":             "cae",
    "drops":             "baja",
    "slides":            "desliza a la baja",
    "tumbles":           "desploma",
    "plunges":           "se hunde",
    "rises":             "sube",
    "gains":             "avanza",
    "declines":          "retrocede",
    "sell off":          "Venta masiva",
    "correction":        "Corrección",
    "gold up":           "Oro al alza",
    "gold down":         "Oro a la baja",
    "record high":       "Máximo histórico",
    "buying":            "Compras",
    "outflows":          "Salidas de capital",
    "inflows":           "Entradas de capital",
}

# Pesos de impacto direccional por keyword  (>0 = alcista, <0 = bajista para oro)
_IMPACT_WEIGHTS = {
    # Muy alcistas para oro
    "war": +8, "sanctions": +7, "conflict": +6, "recession": +6,
    "risk off": +7, "safe haven": +7, "rate cut": +6, "dovish": +5,
    "dollar weak": +5, "usd falls": +5, "stimulus": +5, "easing": +4,
    "geopolitical": +5, "inflation": +4, "cpi": +3, "yields fall": +4,
    "gold up": +6, "record high": +5, "surges": +5, "soars": +5,
    "buying": +3, "inflows": +4, "safe": +3, "fear": +4, "panic": +4,
    "fomc": +2, "fed pause": +5, "powell": +2,
    # Muy bajistas para oro
    "rate hike": -6, "hawkish": -5, "dollar strong": -5, "usd rises": -5,
    "risk on": -5, "equities up": -4, "yields rise": -5, "taper": -4,
    "outflows": -4, "sell off": -5, "correction": -3, "gold down": -6,
    "plunges": -5, "tumbles": -4, "slides": -3, "drops": -4,
    "profit taking": -2, "strong dollar": -5,
}

# Categorías de eventos para el encabezado
_EVENT_CATEGORY = {
    "nfp": "📊 DATOS LABORALES",
    "non-farm": "📊 DATOS LABORALES",
    "cpi": "📈 INFLACIÓN",
    "pce": "📈 INFLACIÓN",
    "fomc": "🏦 BANCO CENTRAL",
    "federal reserve": "🏦 BANCO CENTRAL",
    "interest rate": "🏦 BANCO CENTRAL",
    "powell": "🏦 BANCO CENTRAL",
    "gdp": "📉 CRECIMIENTO",
    "recession": "📉 CRECIMIENTO",
    "war": "⚔️ GEOPOLÍTICO",
    "sanctions": "⚔️ GEOPOLÍTICO",
    "conflict": "⚔️ GEOPOLÍTICO",
    "gold": "🥇 METALES",
    "safe haven": "🛡️ REFUGIO",
    "risk off": "🛡️ REFUGIO",
}


def translate_headline(text: str) -> str:
    """Traducción aproximada de headline. No perfecto, suficiente para contexto."""
    t = text
    for en, es in sorted(_TRANS.items(), key=lambda x: -len(x[0])):
        t = re.sub(re.escape(en), es, t, flags=re.IGNORECASE)
    return t


def score_directional(text: str) -> dict:
    """
    Calcula impacto direccional para oro en base a keywords.
    Retorna:
      bull_pts, bear_pts, net, prob_alcista (0-100), prob_bajista (0-100),
      categoria, palabras_clave_detectadas
    """
    t = text.lower()
    bull_pts = 0; bear_pts = 0; detected = []

    for kw, w in _IMPACT_WEIGHTS.items():
        if kw in t:
            if w > 0:
                bull_pts += w
                detected.append((kw, w))
            else:
                bear_pts += abs(w)
                detected.append((kw, w))

    total = bull_pts + bear_pts + 1e-9
    prob_alcista = round(bull_pts / total * 100)
    prob_bajista = round(bear_pts / total * 100)
    net = bull_pts - bear_pts

    # Categoría dominante
    categoria = "📰 GENERAL"
    for kw, cat in _EVENT_CATEGORY.items():
        if kw in t:
            categoria = cat; break

    return {
        "bull_pts":     bull_pts,
        "bear_pts":     bear_pts,
        "net":          net,
        "prob_alcista": prob_alcista,
        "prob_bajista": prob_bajista,
        "categoria":    categoria,
        "keywords":     detected[:4],
    }


def format_news_line_es(h: dict) -> tuple:
    """
    Retorna (texto_display, tag_color) para insertar en Text widget.
    h = dict con keys: title, score, impact, pub, source
    """
    title_es  = translate_headline(h.get("title", ""))
    analysis  = score_directional(h.get("title","") + " " + h.get("desc",""))
    cat       = analysis["categoria"]
    prob_a    = analysis["prob_alcista"]
    prob_b    = analysis["prob_bajista"]
    net       = analysis["net"]
    pub       = h.get("pub","")[:16]
    impact    = h.get("impact", False)

    if net >= 6:
        tag   = "BULL_STRONG"; arrow = "▲▲"
    elif net >= 2:
        tag   = "BULL";        arrow = "▲"
    elif net <= -6:
        tag   = "BEAR_STRONG"; arrow = "▼▼"
    elif net <= -2:
        tag   = "BEAR";        arrow = "▼"
    else:
        tag   = "NEUTRAL_N";   arrow = "◆"

    if impact:
        tag = "IMPACT"

    bar_bull = "█" * int(prob_a / 10) + "░" * (10 - int(prob_a / 10))
    text = (
        f"{cat}  {pub}\n"
        f"{arrow} {title_es}\n"
        f"   ↑{prob_a:>3}% {bar_bull} ↓{prob_b:>3}%\n"
    )
    if analysis["keywords"]:
        kw_str = " · ".join(k for k, _ in analysis["keywords"][:3])
        text += f"   [{kw_str}]\n"
    text += "\n"
    return text, tag


# ══════════════════════════════════════════════════════════════════════════════
#  2. SMART SIGNAL STATE MACHINE
#  Estados: SCANNING → TRACKING_POI → AWAITING_ENTRY → ACTIVE → COOLDOWN
#
#  Lógica:
#  SCANNING:      Buscando un POI (OB, zona liquidez, sweep) hacia donde
#                 el precio se dirige. Muestra "Buscando setup..."
#  TRACKING_POI:  Precio se dirige hacia un POI identificado. Muestra
#                 "Precio → OB DEMAND @ 3280.50 (dist: 4.2 ATR)"
#  AWAITING_ENTRY: Precio llegó al POI. Espera confirmación (vela de
#                 reversión, delta flip, RSI). Muestra "En zona, esperando conf."
#  ACTIVE:        Señal activa con entry/sl/tp. Monitorea invalidación.
#  COOLDOWN:      Señal fue invalidada. Espera N velas antes de buscar de nuevo.
# ══════════════════════════════════════════════════════════════════════════════

_STATE_SCANNING       = "SCANNING"
_STATE_TRACKING       = "TRACKING_POI"
_STATE_AWAITING       = "AWAITING_ENTRY"
_STATE_ACTIVE         = "ACTIVE"
_STATE_COOLDOWN       = "COOLDOWN"

# Parámetros de la máquina de estados
_COOLDOWN_BARS        = 8    # velas M5 de enfriamiento tras invalidación
_POI_ARRIVAL_ATR      = 0.8  # precio a menos de X ATR del POI → AWAITING
_CONFIRM_RSI_LONG     = 38   # RSI debe estar en zona para confirmar LONG
_CONFIRM_RSI_SHORT    = 62   # RSI debe estar en zona para confirmar SHORT
_INVALIDATE_ATR_LONG  = 1.5  # precio cruza X ATR por debajo de entry → invalidado
_INVALIDATE_ATR_SHORT = 1.5  # precio cruza X ATR por encima de entry → invalidado


class SmartSignalStateMachine:
    """
    Reemplaza el comportamiento simple de re-trigger de señales.
    En vez de emitir una señal cada vez que las condiciones se cumplen,
    sigue un ciclo: busca POI → traquea → espera confirmación → emite → monitorea.
    """

    def __init__(self):
        self._lock       = threading.Lock()
        self._state      = _STATE_SCANNING
        self._direction  = "NEUTRAL"       # dirección del setup actual
        self._poi        = None            # {'type': 'OB'|'RZ'|'LIQ', 'level': float, 'top': float, 'bot': float, 'label': str}
        self._entry_price = None
        self._sl          = None
        self._tp1         = None
        self._tp2         = None
        self._cooldown_remaining = 0
        self._bars_in_state      = 0
        self._last_signal_emitted = None
        self._invalidation_reason = ""
        self._signal_history = deque(maxlen=50)

    def _find_poi(self, price, direction, rates_m5, order_blocks,
                  reaction_zones, atr, pivots, vol_prof):
        """
        Busca el POI más relevante en dirección de la señal.
        LONG: buscamos OB alcista, zona demand, o pool de liquidez debajo.
        SHORT: buscamos OB bajista, zona supply, o pool de liquidez arriba.
        Retorna dict del POI o None.
        """
        candidates = []

        # ── Order Blocks ───────────────────────────────────────────────────
        if order_blocks:
            for ob in order_blocks:
                if not ob.get("active", True): continue
                if direction == "LONG" and ob["type"] == "BULLISH":
                    mid = (ob["top"] + ob["bottom"]) / 2
                    dist = (price - mid) / max(atr, 0.01)
                    if 0 < dist < 15:   # está debajo del precio, en rango
                        candidates.append({
                            "type":  "OB_DEMAND",
                            "level": mid,
                            "top":   ob["top"],
                            "bot":   ob["bottom"],
                            "dist_atr": dist,
                            "label": f"OB Demand @ {mid:.2f}",
                            "priority": 3,
                        })
                elif direction == "SHORT" and ob["type"] == "BEARISH":
                    mid = (ob["top"] + ob["bottom"]) / 2
                    dist = (mid - price) / max(atr, 0.01)
                    if 0 < dist < 15:
                        candidates.append({
                            "type":  "OB_SUPPLY",
                            "level": mid,
                            "top":   ob["top"],
                            "bot":   ob["bottom"],
                            "dist_atr": dist,
                            "label": f"OB Supply @ {mid:.2f}",
                            "priority": 3,
                        })

        # ── Reaction Zones ─────────────────────────────────────────────────
        if reaction_zones:
            for rz in reaction_zones:
                if not rz.get("active", True): continue
                mid = rz["mid"]
                if direction == "LONG" and rz["type"] == "DEMAND":
                    dist = (price - mid) / max(atr, 0.01)
                    if 0 < dist < 20:
                        candidates.append({
                            "type":     "RZ_DEMAND",
                            "level":    mid,
                            "top":      rz["top"],
                            "bot":      rz["bot"],
                            "dist_atr": dist,
                            "label":    f"Zona Demand @ {mid:.2f} (x{rz.get('strength',1):.1f})",
                            "priority": 4,
                        })
                elif direction == "SHORT" and rz["type"] == "SUPPLY":
                    dist = (mid - price) / max(atr, 0.01)
                    if 0 < dist < 20:
                        candidates.append({
                            "type":     "RZ_SUPPLY",
                            "level":    mid,
                            "top":      rz["top"],
                            "bot":      rz["bot"],
                            "dist_atr": dist,
                            "label":    f"Zona Supply @ {mid:.2f} (x{rz.get('strength',1):.1f})",
                            "priority": 4,
                        })

        # ── Pools de liquidez (swing highs/lows recientes) ─────────────────
        if rates_m5 and len(rates_m5) >= 20:
            recent = rates_m5[-40:]
            swing_lows  = [r["low"]  for r in recent[1:-1]
                           if r["low"]  < recent[recent.index(r)-1]["low"]
                           and r["low"] < recent[recent.index(r)+1]["low"]]
            swing_highs = [r["high"] for r in recent[1:-1]
                           if r["high"] > recent[recent.index(r)-1]["high"]
                           and r["high"] > recent[recent.index(r)+1]["high"]]
            # Simplificado para evitar errores de índice
            lows_sorted  = sorted([r["low"]  for r in recent], reverse=False)
            highs_sorted = sorted([r["high"] for r in recent], reverse=True)
            if direction == "LONG" and lows_sorted:
                lq_level = lows_sorted[0]  # pool de liquidez en mínimos
                dist = (price - lq_level) / max(atr, 0.01)
                if 0.5 < dist < 25:
                    candidates.append({
                        "type":     "LIQ_POOL",
                        "level":    lq_level,
                        "top":      lq_level + atr * 0.3,
                        "bot":      lq_level - atr * 0.2,
                        "dist_atr": dist,
                        "label":    f"Pool Liquidez (mín) @ {lq_level:.2f}",
                        "priority": 2,
                    })
            if direction == "SHORT" and highs_sorted:
                lq_level = highs_sorted[0]
                dist = (lq_level - price) / max(atr, 0.01)
                if 0.5 < dist < 25:
                    candidates.append({
                        "type":     "LIQ_POOL",
                        "level":    lq_level,
                        "top":      lq_level + atr * 0.2,
                        "bot":      lq_level - atr * 0.3,
                        "dist_atr": dist,
                        "label":    f"Pool Liquidez (máx) @ {lq_level:.2f}",
                        "priority": 2,
                    })

        # ── Niveles de manipulación (equal highs/lows = stop hunt targets) ─
        if rates_m5 and len(rates_m5) >= 30:
            recent = rates_m5[-60:]
            for i in range(5, len(recent)-5):
                for j in range(i+3, min(i+15, len(recent))):
                    if direction == "LONG":
                        diff = abs(recent[i]["low"] - recent[j]["low"])
                        if diff < atr * 0.15:   # equal lows → stop hunt target
                            level = (recent[i]["low"] + recent[j]["low"]) / 2
                            dist  = (price - level) / max(atr, 0.01)
                            if 1 < dist < 20:
                                candidates.append({
                                    "type":     "EQ_LOWS",
                                    "level":    level,
                                    "top":      level + atr * 0.25,
                                    "bot":      level - atr * 0.15,
                                    "dist_atr": dist,
                                    "label":    f"Equal Lows (manipulación) @ {level:.2f}",
                                    "priority": 5,
                                })
                                break
                    else:
                        diff = abs(recent[i]["high"] - recent[j]["high"])
                        if diff < atr * 0.15:
                            level = (recent[i]["high"] + recent[j]["high"]) / 2
                            dist  = (level - price) / max(atr, 0.01)
                            if 1 < dist < 20:
                                candidates.append({
                                    "type":     "EQ_HIGHS",
                                    "level":    level,
                                    "top":      level + atr * 0.15,
                                    "bot":      level - atr * 0.25,
                                    "dist_atr": dist,
                                    "label":    f"Equal Highs (manipulación) @ {level:.2f}",
                                    "priority": 5,
                                })
                                break

        # ── Pivots como POI ────────────────────────────────────────────────
        if pivots and direction == "LONG":
            for key in ["S1","S2","S3"]:
                lv = pivots.get(key, 0)
                if lv <= 0: continue
                dist = (price - lv) / max(atr, 0.01)
                if 0 < dist < 15:
                    candidates.append({
                        "type": "PIVOT_SUP",
                        "level": lv, "top": lv+atr*0.3, "bot": lv-atr*0.2,
                        "dist_atr": dist,
                        "label": f"Soporte Pivote {key} @ {lv:.2f}",
                        "priority": 1,
                    })
        if pivots and direction == "SHORT":
            for key in ["R1","R2","R3"]:
                lv = pivots.get(key, 0)
                if lv <= 0: continue
                dist = (lv - price) / max(atr, 0.01)
                if 0 < dist < 15:
                    candidates.append({
                        "type": "PIVOT_RES",
                        "level": lv, "top": lv+atr*0.2, "bot": lv-atr*0.3,
                        "dist_atr": dist,
                        "label": f"Resistencia Pivote {key} @ {lv:.2f}",
                        "priority": 1,
                    })

        if not candidates:
            return None

        # Elegir el más cercano con mayor prioridad
        candidates.sort(key=lambda c: (-c["priority"], c["dist_atr"]))
        return candidates[0]

    def _check_confirmation(self, price, direction, rsi, delta_state, vwap_data, atr):
        """
        Verifica si el precio llegó al POI y hay confirmación para entrar.
        Retorna True si se puede emitir la señal.
        Confirmadores: RSI en zona, delta flow, vela de reversa, precio en POI zone.
        """
        confirms = 0

        if direction == "LONG":
            if rsi < _CONFIRM_RSI_LONG:         confirms += 1
            if delta_state and delta_state.get("positive", False): confirms += 1
            if vwap_data:
                sd = vwap_data.get("sd_pos", 0)
                if sd < -0.8:                   confirms += 1
        else:
            if rsi > _CONFIRM_RSI_SHORT:        confirms += 1
            if delta_state and not delta_state.get("positive", True): confirms += 1
            if vwap_data:
                sd = vwap_data.get("sd_pos", 0)
                if sd > 0.8:                    confirms += 1

        return confirms >= 2   # mínimo 2 de 3 confirmadores

    def _check_invalidation(self, price, direction, entry_price, atr, rsi, vwap_data):
        """
        Verifica si la señal activa se invalidó.
        Retorna (True, motivo) si invalidada, (False, "") si sigue válida.
        """
        if entry_price is None: return False, ""

        if direction == "LONG":
            # Precio cayó más de X ATR por debajo de entry
            if price < entry_price - atr * _INVALIDATE_ATR_LONG:
                return True, f"Precio bajo entry −{_INVALIDATE_ATR_LONG}ATR"
            # RSI recuperó zona neutral/alta → momentum alcista agotado
            if rsi is not None and rsi > 62:
                return True, "RSI recuperó zona sobrecompra"
            # VWAP cruza por encima → precio ya en zona neutral
            if vwap_data:
                sd = vwap_data.get("sd_pos", 0)
                if sd > 1.5:
                    return True, "Precio sobre +1.5σ VWAP (objetivo cumplido)"
        else:
            if price > entry_price + atr * _INVALIDATE_ATR_SHORT:
                return True, f"Precio sobre entry +{_INVALIDATE_ATR_SHORT}ATR"
            if rsi is not None and rsi < 38:
                return True, "RSI en zona sobreventa (señal agotada)"
            if vwap_data:
                sd = vwap_data.get("sd_pos", 0)
                if sd < -1.5:
                    return True, "Precio bajo −1.5σ VWAP (objetivo cumplido)"

        return False, ""

    def process(self, price, direction_hint, rsi, delta_state, vwap_data, atr,
                rates_m5, order_blocks, reaction_zones, pivots, vol_prof,
                raw_signal, score_total):
        """
        Punto de entrada principal. Llama a cada ciclo del CalcThread.
        Retorna dict con estado actual y señal (si hay).
        """
        with self._lock:
            self._bars_in_state += 1

            # ── COOLDOWN ───────────────────────────────────────────────────
            if self._state == _STATE_COOLDOWN:
                self._cooldown_remaining -= 1
                if self._cooldown_remaining <= 0:
                    self._state     = _STATE_SCANNING
                    self._direction = "NEUTRAL"
                    self._poi       = None
                return self._build_output(price, atr)

            # ── SCANNING — buscando dirección y POI ────────────────────────
            if self._state == _STATE_SCANNING:
                # Solo buscar si hay señal técnica clara
                if direction_hint != "NEUTRAL" and score_total >= 40:
                    poi = self._find_poi(
                        price, direction_hint, rates_m5, order_blocks,
                        reaction_zones, atr, pivots, vol_prof)
                    if poi:
                        self._poi         = poi
                        self._direction   = direction_hint
                        self._state       = _STATE_TRACKING
                        self._bars_in_state = 0
                return self._build_output(price, atr)

            # ── TRACKING_POI — precio yendo hacia el POI ───────────────────
            if self._state == _STATE_TRACKING:
                if self._poi is None:
                    self._state = _STATE_SCANNING; return self._build_output(price, atr)

                poi_level = self._poi["level"]
                dist      = abs(price - poi_level) / max(atr, 0.01)

                # Precio llegó al POI
                if dist <= _POI_ARRIVAL_ATR:
                    self._state = _STATE_AWAITING
                    self._bars_in_state = 0
                    return self._build_output(price, atr)

                # Si la dirección cambió radicalmente, resetear
                if direction_hint != "NEUTRAL" and direction_hint != self._direction and score_total >= 60:
                    poi = self._find_poi(price, direction_hint, rates_m5,
                                         order_blocks, reaction_zones, atr, pivots, vol_prof)
                    if poi:
                        self._poi       = poi
                        self._direction = direction_hint
                        self._bars_in_state = 0

                # Precio se alejó demasiado del POI en dirección equivocada
                if self._direction == "LONG"  and price < poi_level - atr * 3:
                    self._state = _STATE_SCANNING
                if self._direction == "SHORT" and price > poi_level + atr * 3:
                    self._state = _STATE_SCANNING

                return self._build_output(price, atr)

            # ── AWAITING_ENTRY — en zona, esperando confirmación ───────────
            if self._state == _STATE_AWAITING:
                if self._poi is None:
                    self._state = _STATE_SCANNING; return self._build_output(price, atr)

                confirmed = self._check_confirmation(
                    price, self._direction, rsi, delta_state, vwap_data, atr)

                if confirmed:
                    # Calcular entry/sl/tp
                    self._entry_price = price
                    if self._direction == "LONG":
                        self._sl  = self._poi["bot"] - atr * 0.5
                        self._tp1 = self._poi["level"] + atr * 2.5
                        self._tp2 = self._poi["level"] + atr * 5.0
                    else:
                        self._sl  = self._poi["top"] + atr * 0.5
                        self._tp1 = self._poi["level"] - atr * 2.5
                        self._tp2 = self._poi["level"] - atr * 5.0
                    self._state = _STATE_ACTIVE
                    self._bars_in_state = 0
                    self._signal_history.append({
                        "ts":        time.time(),
                        "direction": self._direction,
                        "poi":       self._poi["label"],
                        "entry":     self._entry_price,
                        "sl":        self._sl,
                    })
                    return self._build_output(price, atr)

                # Timeout en zona sin confirmación → volver a escanear
                if self._bars_in_state > 20:
                    self._state = _STATE_SCANNING
                    self._invalidation_reason = "Timeout en zona sin confirmación"

                # Precio salió de la zona completamente
                poi_level = self._poi["level"]
                dist = abs(price - poi_level) / max(atr, 0.01)
                if dist > _POI_ARRIVAL_ATR * 3:
                    self._state = _STATE_TRACKING
                    self._bars_in_state = 0

                return self._build_output(price, atr)

            # ── ACTIVE — señal activa, monitorear invalidación ─────────────
            if self._state == _STATE_ACTIVE:
                invalidated, reason = self._check_invalidation(
                    price, self._direction, self._entry_price, atr, rsi, vwap_data)

                if invalidated:
                    self._invalidation_reason = reason
                    self._state               = _STATE_COOLDOWN
                    self._cooldown_remaining  = _COOLDOWN_BARS
                    self._entry_price         = None
                    self._bars_in_state       = 0
                    # Actualizar historial con resultado
                    if self._signal_history:
                        self._signal_history[-1]["invalidation"] = reason

                return self._build_output(price, atr)

            return self._build_output(price, atr)

    def _build_output(self, price, atr):
        """Construye el dict de salida para el CalcThread y la UI."""
        state   = self._state
        poi     = self._poi
        direc   = self._direction

        status_msg = {
            _STATE_SCANNING:  "🔍 Escaneando mercado...",
            _STATE_TRACKING:  f"→ Precio hacia {poi['label'] if poi else 'POI'} "
                              f"(dist: {abs(price - poi['level']) / max(atr,0.01):.1f} ATR)"
                              if poi else "→ Siguiendo POI...",
            _STATE_AWAITING:  f"⏳ En zona [{poi['label'] if poi else ''}] — esperando confirmación",
            _STATE_ACTIVE:    f"✅ SEÑAL ACTIVA: {direc} desde {poi['label'] if poi else 'POI'}",
            _STATE_COOLDOWN:  f"🕐 Enfriamiento {self._cooldown_remaining} velas — {self._invalidation_reason}",
        }.get(state, "---")

        signal_out = None
        if state == _STATE_ACTIVE and self._entry_price:
            rr = (abs(self._tp1 - self._entry_price) /
                  max(abs(self._entry_price - self._sl), 0.01))
            signal_out = {
                "dir":    direc,
                "signal": "BUY ▲" if direc == "LONG" else "SELL ▼",
                "entry":  round(self._entry_price, 2),
                "sl":     round(self._sl, 2),
                "tp1":    round(self._tp1, 2),
                "tp2":    round(self._tp2, 2),
                "rr":     round(rr, 2),
                "poi":    poi["label"] if poi else "",
                "poi_type": poi["type"] if poi else "",
            }

        return {
            "sm_state":      state,
            "sm_direction":  direc,
            "sm_status_msg": status_msg,
            "sm_poi":        poi,
            "sm_signal":     signal_out,
            "sm_cooldown":   self._cooldown_remaining,
            "sm_history":    list(self._signal_history)[-5:],
        }

    def get_state_summary(self):
        with self._lock:
            return {
                "state":     self._state,
                "direction": self._direction,
                "poi":       self._poi,
                "cooldown":  self._cooldown_remaining,
                "inv_reason": self._invalidation_reason,
            }


# ══════════════════════════════════════════════════════════════════════════════
#  3. PATCH DE CalcThread — inyecta SmartSignalStateMachine
# ══════════════════════════════════════════════════════════════════════════════

def _patch_calc_thread(CalcThread):
    """Monkey-patch CalcThread para usar SmartSignalStateMachine."""
    original_init = CalcThread.__init__
    original_calc = CalcThread._calc

    def new_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._sm = SmartSignalStateMachine()

    def new_calc(self):
        original_calc(self)
        # Inyectar resultado SM en la queue
        try:
            last = self.rq.queue[-1] if not self.rq.empty() else None
        except Exception:
            last = None
        if last is None: return

        # Extraer datos necesarios para la SM
        m5     = last.get("m5", [])
        price  = last.get("price", 0.0)
        atr    = last.get("atr_m15", 1.5)
        rsi    = last.get("rsi", 50.0)
        ds     = last.get("ds")
        vwap   = last.get("vwap")
        obs    = last.get("order_blocks", [])
        rz     = last.get("reaction_zones", [])
        pivots = last.get("pivots")
        vp     = last.get("vol_prof")
        sig    = last.get("signal", {})
        sr     = last.get("score_result", {})

        direction_hint = sig.get("dir", "NEUTRAL")
        score_total    = sr.get("total", 0)

        sm_result = self._sm.process(
            price, direction_hint, rsi, ds, vwap, atr,
            m5, obs, rz, pivots, vp,
            sig, score_total)

        # Fusionar en el último elemento de la queue (hacky pero funciona)
        try:
            # Obtener el último item y reemplazarlo con sm_result añadido
            import queue as _q
            items = []
            while not self.rq.empty():
                try: items.append(self.rq.get_nowait())
                except: break
            if items:
                items[-1]["sm_result"] = sm_result
            for it in items:
                try: self.rq.put_nowait(it)
                except: pass
        except Exception as e:
            print(f"[SM patch] {e}")

    CalcThread.__init__ = new_init
    CalcThread._calc    = new_calc
    return CalcThread


# ══════════════════════════════════════════════════════════════════════════════
#  4. PATCH DEL PANEL DE NOTICIAS EN LA UI
# ══════════════════════════════════════════════════════════════════════════════

def _patch_news_panel(KAurumTerminal):
    """
    Reemplaza _update_news_panel_from_commodity con versión en español + prob.
    También añade tags de colores al Text widget de noticias.
    """
    # Guardar referencia al método original de inicialización de UI derecha
    original_build_right = KAurumTerminal._build_right if hasattr(KAurumTerminal, '_build_right') else None

    def new_update_news(self, news_s=None, sm_result=None):
        """Panel de noticias en español con barras de probabilidad direccional."""
        if not hasattr(self, 'news_text'): return

        # Recuperar noticias del último resultado si no se pasan
        if news_s is None and self._last_result:
            news_s = self._last_result.get("news_s", {})
        if news_s is None:
            news_s = {}

        self.news_text.config(state='normal')
        self.news_text.delete('1.0', 'end')

        # Configurar tags de color si no existen
        tags_cfg = {
            "BULL_STRONG": "#00ff88",
            "BULL":        "#66dd88",
            "BEAR_STRONG": "#ff3333",
            "BEAR":        "#dd6666",
            "NEUTRAL_N":   "#aaaaaa",
            "IMPACT":      "#ffd600",
            "HEADER":      "#d4941a",
            "SM_ACTIVE":   "#00e5ff",
            "SM_TRACKING": "#6cb4ff",
            "SM_AWAIT":    "#f0b429",
            "SM_COOL":     "#7a8fa0",
            "SM_SCAN":     "#4a5a70",
        }
        for tag, color in tags_cfg.items():
            try:
                self.news_text.tag_config(tag, foreground=color)
            except Exception:
                pass

        # ── Estado de la SM ───────────────────────────────────────────────
        if sm_result:
            sm_state = sm_result.get("sm_state", "")
            sm_msg   = sm_result.get("sm_status_msg", "")
            sm_sig   = sm_result.get("sm_signal")
            sm_tag   = {
                "ACTIVE":       "SM_ACTIVE",
                "AWAITING_ENTRY":"SM_AWAIT",
                "TRACKING_POI": "SM_TRACKING",
                "COOLDOWN":     "SM_COOL",
                "SCANNING":     "SM_SCAN",
            }.get(sm_state, "SM_SCAN")

            self.news_text.insert('end', "═══ SEÑAL INTELIGENTE ════════════\n", "HEADER")
            self.news_text.insert('end', f"{sm_msg}\n", sm_tag)
            if sm_sig:
                d = sm_sig
                self.news_text.insert('end',
                    f"  Entry: {d['entry']:.2f}  SL: {d['sl']:.2f}\n"
                    f"  TP1:   {d['tp1']:.2f}  TP2: {d['tp2']:.2f}\n"
                    f"  R/R:   {d['rr']:.2f}   POI: {d.get('poi','')}\n\n",
                    sm_tag)
            else:
                self.news_text.insert('end', "\n", "SM_SCAN")

        # ── Sentimiento global ────────────────────────────────────────────
        sent  = news_s.get("sentiment", "NEUTRAL")
        score = news_s.get("score", 0)
        impact= news_s.get("impact", False)
        sent_tag = "BULL" if sent == "BULLISH" else ("BEAR" if sent == "BEARISH" else "NEUTRAL_N")

        # Calcular probabilidades globales desde el score acumulado
        abs_score = max(abs(score), 1)
        if sent == "BULLISH":
            prob_a_global = min(85, 50 + abs_score * 5)
            prob_b_global = 100 - prob_a_global
        elif sent == "BEARISH":
            prob_b_global = min(85, 50 + abs_score * 5)
            prob_a_global = 100 - prob_b_global
        else:
            prob_a_global = prob_b_global = 50

        bar = "█" * int(prob_a_global / 10) + "░" * (10 - int(prob_a_global / 10))
        self.news_text.insert('end', "═══ SENTIMIENTO GLOBAL ═══════════\n", "HEADER")
        self.news_text.insert('end',
            f"{'🟢 ALCISTA' if sent=='BULLISH' else '🔴 BAJISTA' if sent=='BEARISH' else '⚪ NEUTRAL'}  "
            f"(score: {score:+d})\n", sent_tag)
        self.news_text.insert('end',
            f"↑{prob_a_global:>3}% {bar} ↓{prob_b_global:>3}%\n", sent_tag)
        if impact:
            self.news_text.insert('end', "⚡ ALTO IMPACTO — NO OPERAR\n\n", "IMPACT")
        else:
            self.news_text.insert('end', "\n", "SM_SCAN")

        # ── Noticias individuales en español ──────────────────────────────
        self.news_text.insert('end', "═══ NOTICIAS (oro) ═══════════════\n", "HEADER")
        headlines = list(news_s.get("headlines", []))[:12]
        for h in headlines:
            try:
                text, tag = format_news_line_es(h)
                self.news_text.insert('end', text, tag)
            except Exception:
                self.news_text.insert('end', h.get("title","")[:80]+"\n", "NEUTRAL_N")

        # ── Próximo evento ────────────────────────────────────────────────
        nxt = news_s.get("next_event")
        if nxt:
            mins = nxt.get("minutes_away", 0)
            title_es = translate_headline(nxt.get("title",""))
            self.news_text.insert('end', "═══ PRÓXIMO EVENTO ═══════════════\n", "HEADER")
            self.news_text.insert('end',
                f"⏰ {title_es}\n   {nxt.get('country','')} — en {mins} min\n",
                "IMPACT")

        self.news_text.config(state='disabled')

        # Actualizar label de advertencia en topbar
        if hasattr(self, 'lbl_news_warn'):
            if impact:
                self.lbl_news_warn.config(text=" ⚡ ALTO IMPACTO — NO OPERAR ")
            elif sent == "BULLISH":
                self.lbl_news_warn.config(text=f" ▲ SESGO ALCISTA {prob_a_global}% ")
            elif sent == "BEARISH":
                self.lbl_news_warn.config(text=f" ▼ SESGO BAJISTA {prob_b_global}% ")
            else:
                self.lbl_news_warn.config(text="")

    # Reemplazar métodos
    KAurumTerminal._update_news_panel_from_commodity = new_update_news
    # Si el método se llama _news_tick, también parchear para pasar sm_result
    original_news_tick = getattr(KAurumTerminal, '_news_tick', None)
    if original_news_tick:
        def new_news_tick(self):
            if not self.running: return
            sm_r = None
            if self._last_result:
                sm_r = self._last_result.get("sm_result")
            news_s = self._last_result.get("news_s") if self._last_result else None
            self._update_news_panel_from_commodity(news_s=news_s, sm_result=sm_r)
            self.root.after(NEWS_UPDATE_MS, self._news_tick)
        KAurumTerminal._news_tick = new_news_tick

    # Patch del _refresh para incluir SM en update_left
    original_refresh = KAurumTerminal._refresh
    def new_refresh(self, r):
        original_refresh(self, r)
        # Actualizar label SM si existe
        sm_r = r.get("sm_result")
        if sm_r and hasattr(self, 'lbl_sm_status'):
            state   = sm_r.get("sm_state","")
            msg     = sm_r.get("sm_status_msg","")
            sm_sig  = sm_r.get("sm_signal")
            col_map = {
                "ACTIVE":        "#00e5ff",
                "AWAITING_ENTRY":"#f0b429",
                "TRACKING_POI":  "#6cb4ff",
                "COOLDOWN":      "#7a8fa0",
                "SCANNING":      "#4a5a70",
            }
            col = col_map.get(state, "#7a8fa0")
            try:
                self.lbl_sm_status.config(text=msg[:55], fg=col)
                if sm_sig and hasattr(self, 'lbl_sm_entry'):
                    self.lbl_sm_entry.config(
                        text=f"Entry:{sm_sig['entry']:.2f}  SL:{sm_sig['sl']:.2f}  "
                             f"TP1:{sm_sig['tp1']:.2f}  RR:{sm_sig['rr']:.2f}",
                        fg="#00e5ff")
                elif hasattr(self, 'lbl_sm_entry'):
                    self.lbl_sm_entry.config(text="Esperando señal...", fg="#4a5a70")
            except Exception:
                pass
    KAurumTerminal._refresh = new_refresh

    return KAurumTerminal


# ══════════════════════════════════════════════════════════════════════════════
#  5. PATCH DE BUILD_LEFT — añade sección SM
# ══════════════════════════════════════════════════════════════════════════════

def _patch_build_left(KAurumTerminal):
    original_build_left = KAurumTerminal._build_left

    def new_build_left(self, parent):
        original_build_left(self, parent)

        # Añadir sección de SM al final del panel izquierdo
        import tkinter as tk
        BG_P = "#1a1f28"; C_G = "#d4941a"; C_C = "#00e5ff"

        tk.Frame(parent, bg=C_G, height=1).pack(fill=tk.X, padx=6, pady=(7,0))
        tk.Label(parent, text="◈ SEÑAL INTELIGENTE (SM)", fg=C_G, bg=BG_P,
                 font=("Courier New", 7, "bold")).pack(anchor="w", padx=8, pady=(2,2))

        self.lbl_sm_status = tk.Label(parent, text="🔍 Escaneando...", fg="#4a5a70",
                                       bg=BG_P, font=("Courier New", 7, "bold"),
                                       wraplength=230, justify=tk.LEFT)
        self.lbl_sm_status.pack(anchor="w", padx=8, pady=(1,0))

        self.lbl_sm_entry = tk.Label(parent, text="Esperando señal...", fg="#4a5a70",
                                      bg=BG_P, font=("Courier New", 6),
                                      wraplength=230, justify=tk.LEFT)
        self.lbl_sm_entry.pack(anchor="w", padx=8, pady=(0,3))

    KAurumTerminal._build_left = new_build_left
    return KAurumTerminal


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def apply_patch():
    """
    Aplica todos los patches. Llamar antes de root.mainloop().
    Los imports son dinámicos para no requerir que las clases estén en scope global.
    """
    import sys
    main_mod = sys.modules.get('__main__')
    if main_mod is None:
        print("[patch v10] No se encontró módulo __main__"); return

    patched = []

    # Patch CalcThread
    ct = getattr(main_mod, 'CalcThread', None)
    if ct:
        _patch_calc_thread(ct)
        patched.append("CalcThread")

    # Patch KAurumTerminal
    kterm = getattr(main_mod, 'KAurumTerminal', None)
    if kterm:
        _patch_news_panel(kterm)
        _patch_build_left(kterm)
        patched.append("KAurumTerminal(news+SM panel)")

    print(f"[K-AURUM patch v10] Aplicado: {', '.join(patched)}")
    if not patched:
        print("  ADVERTENCIA: No se encontraron clases para patchear. "
              "Asegurate de importar este módulo dentro del mismo archivo.")
