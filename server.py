"""
server.py - Interfaz web L&W PREMIUM IA SIGNS
FastAPI: sirve el frontend y expone /api/analizar para ejecutar analizar_activo en tiempo real.
"""

import requests as _requests
import pandas as pd
import ta as _ta
import subprocess as _subprocess
import sys as _sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from config.config import Config
from core.indicadores import evaluar_estrategias
from core.sessions import TIMEZONE

import os as _os
# Garantizar que los directorios requeridos existan (necesario en Render/Railway)
for _d in ("static", "logs", "database"):
    _os.makedirs(_d, exist_ok=True)

# ── Bot de Telegram en proceso paralelo ─────────────────────────────────────
_bot_proceso = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranca main.py en segundo plano al iniciar el servidor web."""
    global _bot_proceso
    try:
        _bot_proceso = _subprocess.Popen(
            [_sys.executable, "-u", "main.py"],
            stdout=None,   # hereda stdout de uvicorn → Railway captura los logs
            stderr=None,   # hereda stderr de uvicorn → Railway captura los errores
        )
        print(f"[LW] Bot de Telegram iniciado (PID {_bot_proceso.pid})", flush=True)
    except Exception as e:
        print(f"[LW] No se pudo iniciar main.py: {e}", flush=True)
    yield
    # Al apagar el servidor, terminar el bot también
    if _bot_proceso and _bot_proceso.poll() is None:
        _bot_proceso.terminate()
        print("[LW] Bot de Telegram detenido")

app = FastAPI(title="L&W PREMIUM IA SIGNS", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Modelo de entrada del endpoint ──────────────────────────────────────────

class AnalisisRequest(BaseModel):
    symbol: str        # ej. "EUR/USD"
    temporalidad: str  # "M1" o "M5"

# ── Función de datos (sync, reutiliza la misma lógica del bot) ───────────────

def _obtener_df(symbol: str, intervalo: str):
    """Descarga velas de Twelve Data y devuelve DataFrame en orden cronológico, o None."""
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": intervalo,
        "apikey": Config.TWELVE_DATA_API_KEY,
        "outputsize": 150,
        "format": "JSON",
    }
    try:
        resp = _requests.get(url, params=params, timeout=12)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if "values" not in data or len(data["values"]) < 55:
            return None
        df = pd.DataFrame(data["values"])
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("datetime").reset_index(drop=True)
        return df
    except Exception:
        return None

# ── Helpers de análisis para la respuesta web ────────────────────────────────

def _calcular_indicadores(df: pd.DataFrame):
    """Calcula RSI, Estocástico, EMAs y MACD. Devuelve dict con todos los valores."""
    # EMAs
    for p in (8, 13, 21, 50):
        df[f"ema{p}"] = _ta.trend.EMAIndicator(df["close"], window=p).ema_indicator()
    df["ema8_slope"]  = df["ema8"].diff()
    df["ema13_slope"] = df["ema13"].diff()
    curr   = df.iloc[-1]
    precio = float(curr["close"])

    alcista = (curr["ema8"] > curr["ema13"] > curr["ema21"] > curr["ema50"]
               and precio > curr["ema50"]
               and curr["ema8_slope"] > 0 and curr["ema13_slope"] > 0)
    bajista = (curr["ema8"] < curr["ema13"] < curr["ema21"] < curr["ema50"]
               and precio < curr["ema50"]
               and curr["ema8_slope"] < 0 and curr["ema13_slope"] < 0)
    dir_t = "CALL" if alcista else ("PUT" if bajista else None)

    # RSI
    rsi_s    = _ta.momentum.RSIIndicator(df["close"], window=Config.RSI_PERIOD).rsi()
    rsi_val  = float(rsi_s.iloc[-1])
    rsi_prev = float(rsi_s.iloc[-2])

    # Estocástico
    stoch  = _ta.momentum.StochasticOscillator(df["high"], df["low"], df["close"], window=14, smooth_window=3)
    k_act  = float(stoch.stoch().iloc[-1])
    k_prv  = float(stoch.stoch().iloc[-2])
    d_act  = float(stoch.stoch_signal().iloc[-1])
    d_prv  = float(stoch.stoch_signal().iloc[-2])
    cruce_arriba = k_prv <= d_prv and k_act > d_act
    cruce_abajo  = k_prv >= d_prv and k_act < d_act

    # MACD
    macd_obj = _ta.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
    macd_l   = float(macd_obj.macd().iloc[-1])
    macd_s   = float(macd_obj.macd_signal().iloc[-1])
    dir_macd = "CALL" if macd_l > macd_s else "PUT"

    return {
        "precio": precio, "tendencia": dir_t, "macd": dir_macd,
        "rsi": rsi_val, "rsi_prev": rsi_prev,
        "k_act": k_act, "k_prv": k_prv, "d_act": d_act, "d_prv": d_prv,
        "cruce_arriba": cruce_arriba, "cruce_abajo": cruce_abajo,
    }


def _construir_motivo(ind: dict, direccion: str, metodo: str) -> str:
    """Construye texto legible del motivo de la señal."""
    partes = []
    if direccion == "CALL":
        partes.append("Fuerza alcista confirmada")
        if 50 <= ind["rsi"] <= 70:
            partes.append(f"RSI activo en {ind['rsi']:.0f}")
        if ind["cruce_arriba"]:
            partes.append(f"Estocástico cruzó al alza (%K {ind['k_act']:.0f} > %D {ind['d_act']:.0f})")
    else:
        partes.append("Presión bajista confirmada")
        if 30 <= ind["rsi"] <= 50:
            partes.append(f"RSI en zona de venta ({ind['rsi']:.0f})")
        if ind["cruce_abajo"]:
            partes.append(f"Estocástico cruzó a la baja (%K {ind['k_act']:.0f} < %D {ind['d_act']:.0f})")
    if "divergencia" in metodo:
        partes.append("Divergencia RSI confirmada")
    if "cruce EMA" in metodo:
        partes.append("Cruce de medias exponenciales")
    return " · ".join(partes) if partes else metodo


def _que_esperar(ind: dict) -> tuple:
    """Devuelve (lista_de_condiciones, estado) para cuando no hay señal confirmada."""
    dir_t    = ind["tendencia"]
    dir_macd = ind["macd"]
    rsi      = ind["rsi"]
    rsi_prev = ind["rsi_prev"]
    k_act    = ind["k_act"]
    d_act    = ind["d_act"]

    if dir_t is None:
        # Sin tendencia = lateral
        condiciones = [
            f"Tendencia sin dirección — EMAs sin alinear (EMA8·EMA13·EMA21·EMA50 deben ordenarse)",
            f"RSI actual: {rsi:.1f} — esperar impulso claro por encima o por debajo de 50",
        ]
        return condiciones, "LATERAL"

    condiciones = []
    # RSI fuera de zona
    if dir_t == "CALL":
        if rsi < 50:
            condiciones.append(f"RSI = {rsi:.1f} — necesita superar 50 para confirmar fuerza compradora")
        elif rsi > 70:
            condiciones.append(f"RSI = {rsi:.1f} — sobrecompra, esperar retroceso bajo 70")
        elif rsi < rsi_prev:
            condiciones.append(f"RSI = {rsi:.1f} cayendo — esperar que vuelva a subir")
        # Estocástico
        if not ind["cruce_arriba"]:
            condiciones.append(
                f"Estocástico: %K {k_act:.0f} vs %D {d_act:.0f} — esperar que %K cruce por encima de %D"
            )
        elif k_act >= 80:
            condiciones.append(f"Estocástico sobrecomprado (%K {k_act:.0f}) — esperar retroceso bajo 80")
    else:  # PUT
        if rsi > 50:
            condiciones.append(f"RSI = {rsi:.1f} — necesita bajar de 50 para confirmar presión vendedora")
        elif rsi < 30:
            condiciones.append(f"RSI = {rsi:.1f} — sobreventa, esperar rebote sobre 30")
        elif rsi > rsi_prev:
            condiciones.append(f"RSI = {rsi:.1f} subiendo — esperar que vuelva a bajar")
        # Estocástico
        if not ind["cruce_abajo"]:
            condiciones.append(
                f"Estocástico: %K {k_act:.0f} vs %D {d_act:.0f} — esperar que %K cruce por debajo de %D"
            )
        elif k_act <= 20:
            condiciones.append(f"Estocástico sobrevendido (%K {k_act:.0f}) — esperar rebote sobre 20")

    # MACD
    if dir_macd != dir_t:
        condiciones.append(f"MACD apunta {dir_macd} pero tendencia es {dir_t} — esperar alineación")

    if not condiciones:
        condiciones.append("Condiciones casi listas — aguardar cierre de la vela actual")

    return condiciones, "ESPERAR"


# ── Endpoint de análisis ─────────────────────────────────────────────────────

@app.post("/api/analizar")
def analizar_mercado(req: AnalisisRequest):
    """Análisis on-demand: siempre devuelve estado SEÑAL, ESPERAR o LATERAL con valores reales."""
    symbol       = req.symbol.strip()
    temporalidad = req.temporalidad
    intervalo    = "1min" if temporalidad == "M1" else "5min"
    exp_min      = 1 if temporalidad == "M1" else 5

    # 1. Descargar datos
    df = _obtener_df(symbol, intervalo)
    if df is None:
        return JSONResponse({
            "ok": True,
            "estado": "ERROR",
            "mensaje": f"No se obtuvieron datos para {symbol}. Verifica conexión o intenta de nuevo.",
        })

    # 2. Calcular indicadores
    try:
        ind = _calcular_indicadores(df.copy())
    except Exception as e:
        return JSONResponse({
            "ok": True,
            "estado": "ERROR",
            "mensaje": f"Error calculando indicadores: {e}",
        })

    # 3. Intentar señal con confluencia completa
    resultados = evaluar_estrategias(df)

    if resultados:
        # ── ESTADO: SEÑAL CONFIRMADA ──────────────────────────────────────────
        direccion, confianza, metodo = resultados[0]
        ahora = datetime.now(TIMEZONE)
        if temporalidad == "M1":
            entrada = ahora.replace(second=0, microsecond=0) + timedelta(minutes=1)
        else:
            mins = (5 - (ahora.minute % 5)) % 5 or 5
            entrada = ahora + timedelta(minutes=mins)

        return JSONResponse({
            "ok": True,
            "estado": "SEÑAL",
            "direccion": direccion,
            "confianza": round(confianza, 1),
            "entrada": entrada.strftime("%H:%M:%S"),
            "expiracion": exp_min,
            "activo": symbol,
            "temporalidad": temporalidad,
            "motivo": _construir_motivo(ind, direccion, metodo),
            "indicadores": {
                "rsi": round(ind["rsi"], 1),
                "stoch_k": round(ind["k_act"], 1),
                "stoch_d": round(ind["d_act"], 1),
                "tendencia": ind["tendencia"] or "—",
            },
        })

    # 4. Sin señal: explicar qué falta
    condiciones, estado = _que_esperar(ind)

    # Dirección parcial: si tendencia y MACD apuntan igual, mostrar "lean"
    lean = None
    if ind["tendencia"] and ind["macd"] == ind["tendencia"]:
        lean = ind["tendencia"]

    return JSONResponse({
        "ok": True,
        "estado": estado,   # "ESPERAR" o "LATERAL"
        "lean": lean,       # dirección probable aunque no confirmada (puede ser None)
        "condiciones": condiciones,
        "indicadores": {
            "rsi": round(ind["rsi"], 1),
            "stoch_k": round(ind["k_act"], 1),
            "stoch_d": round(ind["d_act"], 1),
            "tendencia": ind["tendencia"] or "—",
            "macd": ind["macd"],
        },
    })


# ── Frontend ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return r"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
  <title>L&W PREMIUM IA SIGNS</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Inter:wght@400;500;600&display=swap');

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --cyan:    #00f2fe;
      --purple:  #a855f7;
      --green:   #22c55e;
      --red:     #ef4444;
      --yellow:  #fbbf24;
      --gold:    #f59e0b;
      --bg:      #080b18;
      --card:    rgba(10, 14, 30, 0.98);
      --surface: rgba(24,29,51,.9);
      --border:  rgba(168,85,247,.3);
      --muted:   rgba(148,163,184,.65);
      --nav-h:   68px;
    }

    html, body { height: 100%; }

    body {
      background: var(--bg);
      background-image:
        radial-gradient(ellipse 70% 50% at 15% 5%,  rgba(168,85,247,.13) 0%, transparent 55%),
        radial-gradient(ellipse 60% 50% at 85% 95%, rgba(0,242,254,.10)  0%, transparent 55%);
      color: #fff;
      min-height: 100vh;
      display: flex;
      justify-content: center;
      align-items: flex-start;
      padding: 16px 12px 0;
      font-family: 'Inter', sans-serif;
      -webkit-font-smoothing: antialiased;
    }

    /* ═══ CARD PRINCIPAL ═══ */
    .card {
      width: 100%;
      max-width: 430px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 24px 24px 0 0;
      padding: 26px 22px 0;
      box-shadow: 0 0 50px rgba(168,85,247,.15), 0 0 0 1px rgba(0,242,254,.05) inset;
      display: flex;
      flex-direction: column;
      min-height: calc(100vh - 16px);
      position: relative;
    }

    /* ═══ HEADER (siempre visible) ═══ */
    .app-header { text-align: center; margin-bottom: 18px; flex-shrink: 0; }

    .logo-wrap { position: relative; display: inline-block; margin-bottom: 10px; }
    .logo-wrap img {
      width: 90px; height: 90px;
      border-radius: 16px; object-fit: cover;
      border: 2px solid var(--cyan);
      box-shadow: 0 0 16px var(--cyan), 0 0 36px rgba(0,242,254,.2);
      display: block;
    }
    .logo-pulse {
      position: absolute; inset: -5px; border-radius: 20px;
      border: 1.5px solid rgba(0,242,254,.4);
      animation: pulse 2.4s ease-in-out infinite; pointer-events: none;
    }
    @keyframes pulse {
      0%,100% { opacity:.35; transform:scale(1); }
      50%      { opacity:1;   transform:scale(1.05); }
    }

    .brand {
      font-family: 'Orbitron', sans-serif; font-size: 17px; font-weight: 900;
      letter-spacing: 2px;
      background: linear-gradient(90deg, var(--purple), var(--cyan));
      -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
      margin-bottom: 3px;
    }
    .subtitle { font-size: 10px; color: var(--muted); letter-spacing: 1.8px; text-transform: uppercase; }

    /* ═══ ÁREA DE CONTENIDO ═══ */
    .content-area {
      flex: 1;
      overflow-y: auto;
      padding-bottom: calc(var(--nav-h) + 12px);
      scrollbar-width: none;
    }
    .content-area::-webkit-scrollbar { display: none; }

    /* ═══ TABS ═══ */
    .tab-pane { display: none; animation: fadeIn .25s ease; }
    .tab-pane.active { display: block; }
    @keyframes fadeIn { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }

    /* ═══ LOGIN ═══ */
    #screen-login { text-align: center; padding-top: 8px; }

    /* ═══ BOT IA — escáner interno ═══ */
    .scanner-screen { display: none; }
    .scanner-screen.active { display: block; }

    /* ═══ FORMULARIO ═══ */
    .form-group { margin-bottom: 16px; text-align: left; }
    label {
      display: block; font-size: 10px; font-weight: 600;
      color: var(--purple); letter-spacing: 1.5px;
      text-transform: uppercase; margin-bottom: 7px;
    }
    input[type="password"], select {
      width: 100%; padding: 12px 15px;
      background: var(--surface); border: 1px solid rgba(59,130,246,.45);
      border-radius: 11px; color: #fff; font-size: 15px;
      outline: none; transition: border-color .2s, box-shadow .2s; appearance: none;
    }
    input[type="password"]:focus, select:focus {
      border-color: var(--cyan); box-shadow: 0 0 12px rgba(0,242,254,.3);
    }
    select option { background: #181d33; }

    /* ═══ BOTONES ═══ */
    .btn {
      width: 100%; padding: 14px; border: none; border-radius: 12px;
      font-family: 'Orbitron', sans-serif; font-size: 13px; font-weight: 700;
      letter-spacing: 1.5px; color: #fff; cursor: pointer;
      transition: transform .15s, opacity .15s, box-shadow .2s;
      text-transform: uppercase;
    }
    .btn-primary {
      background: linear-gradient(135deg, var(--purple) 0%, var(--cyan) 100%);
      box-shadow: 0 0 22px rgba(0,242,254,.3);
    }
    .btn-primary:hover  { opacity:.88; transform:scale(1.02); box-shadow:0 0 32px rgba(0,242,254,.5); }
    .btn-primary:active { transform:scale(.97); }
    .btn-secondary {
      background: rgba(59,130,246,.2); border: 1px solid rgba(59,130,246,.45);
      margin-top: 12px; font-size: 11px; letter-spacing: 1px;
    }
    .btn-secondary:hover { background: rgba(59,130,246,.35); }
    .btn-gold {
      background: linear-gradient(135deg, #b45309, var(--gold));
      box-shadow: 0 0 22px rgba(245,158,11,.35); margin-top: 0;
    }
    .btn-gold:hover { opacity:.88; transform:scale(1.02); }
    .btn-green {
      background: linear-gradient(135deg, #15803d, var(--green));
      box-shadow: 0 0 20px rgba(34,197,94,.3); margin-top: 0;
    }
    .btn-green:hover { opacity:.88; transform:scale(1.02); }

    /* ═══ SPINNER ═══ */
    .spinner-wrap {
      display: flex; flex-direction: column; align-items: center;
      gap: 14px; padding: 30px 0 22px;
    }
    .spinner {
      width: 50px; height: 50px;
      border: 3px solid rgba(0,242,254,.12); border-top-color: var(--cyan);
      border-radius: 50%; animation: spin .75s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .spinner-text { font-size: 13px; color: var(--muted); letter-spacing: .4px; }

    /* ═══ TARJETA SEÑAL ═══ */
    .signal-card { margin-top: 18px; border-radius: 18px; padding: 20px 18px; text-align: center; display: none; }
    .signal-call { background: rgba(34,197,94,.07);  border: 2px solid var(--green); box-shadow: 0 0 28px rgba(34,197,94,.28), 0 0 60px rgba(34,197,94,.08); }
    .signal-put  { background: rgba(239,68,68,.07);  border: 2px solid var(--red);   box-shadow: 0 0 28px rgba(239,68,68,.28), 0 0 60px rgba(239,68,68,.08); }
    .signal-direction {
      font-family: 'Orbitron', sans-serif; font-size: 26px; font-weight: 900;
      letter-spacing: 2px; margin-bottom: 5px;
    }
    .signal-call .signal-direction { color: var(--green); text-shadow: 0 0 14px var(--green); }
    .signal-put  .signal-direction { color: var(--red);   text-shadow: 0 0 14px var(--red);   }
    .signal-pair { font-size: 12px; color: var(--muted); margin-bottom: 16px; letter-spacing: 1px; }

    .data-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin-top: 4px; }
    .data-cell {
      background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.07);
      border-radius: 10px; padding: 9px 11px; text-align: center;
    }
    .data-cell.full { grid-column: span 2; }
    .dc-label { font-size: 9px; color: rgba(148,163,184,.55); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
    .dc-value { font-size: 15px; font-weight: 700; color: #fff; }
    .dc-value.cyan   { color: var(--cyan);   }
    .dc-value.green  { color: var(--green);  }
    .dc-value.red    { color: var(--red);    }
    .dc-value.purple { color: var(--purple); }

    .conf-bar-wrap { margin-top: 6px; background: rgba(255,255,255,.06); border-radius: 99px; height: 6px; overflow: hidden; }
    .conf-bar { height: 100%; border-radius: 99px; background: linear-gradient(90deg, var(--purple), var(--cyan)); transition: width .65s ease; }

    /* ── Estado ESPERAR / LATERAL ── */
    .wait-card {
      display: none; margin-top: 18px; border-radius: 15px; overflow: hidden;
    }
    .wait-header {
      padding: 16px 18px 12px;
      display: flex; align-items: center; gap: 10px;
    }
    .wait-header.esperar { background: rgba(234,179,8,.08); border: 1px solid rgba(234,179,8,.25); border-bottom: none; border-radius: 15px 15px 0 0; }
    .wait-header.lateral { background: rgba(148,163,184,.06); border: 1px solid rgba(148,163,184,.2); border-bottom: none; border-radius: 15px 15px 0 0; }
    .wait-icon { font-size: 22px; line-height:1; }
    .wait-title { font-family:'Orbitron',sans-serif; font-size:12px; letter-spacing:.5px; }
    .wait-title.esperar { color: #eab308; }
    .wait-title.lateral { color: var(--muted); }
    .wait-lean { font-size: 10px; color: rgba(148,163,184,.6); margin-top: 2px; }
    .wait-body { padding: 14px 18px 18px; border-radius: 0 0 15px 15px; }
    .wait-body.esperar { background: rgba(234,179,8,.04); border: 1px solid rgba(234,179,8,.25); border-top: none; }
    .wait-body.lateral { background: rgba(148,163,184,.03); border: 1px solid rgba(148,163,184,.2); border-top: none; }

    .wait-indic-row {
      display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap;
    }
    .wi-pill {
      background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.09);
      border-radius: 8px; padding: 6px 10px; text-align: center; flex: 1; min-width: 60px;
    }
    .wi-label { font-size: 9px; color: rgba(148,163,184,.5); text-transform: uppercase; letter-spacing:.8px; }
    .wi-val   { font-size: 14px; font-weight: 700; color: #fff; margin-top: 2px; }

    .wait-conditions { list-style: none; padding: 0; margin: 0; }
    .wait-conditions li {
      font-size: 11px; color: var(--muted); line-height: 1.6;
      padding: 5px 0 5px 16px; position: relative; border-bottom: 1px solid rgba(255,255,255,.04);
    }
    .wait-conditions li:last-child { border-bottom: none; }
    .wait-conditions li::before { content: '▸'; position: absolute; left: 0; color: #eab308; font-size: 10px; top: 6px; }
    .wait-conditions li.lateral-bullet::before { color: var(--muted); }

    .error-msg {
      display: none; margin-top: 14px; padding: 13px 15px; border-radius: 11px;
      background: rgba(239,68,68,.07); border: 1px solid rgba(239,68,68,.3);
      font-size: 12px; color: #fca5a5;
    }
    .motivo-txt { font-size: 11px; color: rgba(148,163,184,.55); margin-top: 12px; line-height: 1.55; border-top: 1px solid rgba(255,255,255,.06); padding-top: 10px; }

    /* ═══ DISCLAIMER ═══ */
    .disclaimer {
      margin-top: 16px;
      padding: 10px 14px;
      border-radius: 10px;
      background: rgba(255,255,255,.025);
      border: 1px solid rgba(255,255,255,.07);
      font-size: 10px;
      color: rgba(148,163,184,.45);
      line-height: 1.6;
      text-align: center;
    }

    /* ═══ RELOJ ═══ */
    .clock { font-family:'Orbitron',sans-serif; font-size:11px; color:rgba(148,163,184,.4); margin-top:14px; letter-spacing:1px; text-align:center; }

    /* ═══ SECCIÓN ACADEMIA ═══ */
    .section-title {
      font-family: 'Orbitron', sans-serif; font-size: 14px; font-weight: 700;
      letter-spacing: 1.5px; margin-bottom: 16px; text-align: center;
      background: linear-gradient(90deg, var(--purple), var(--cyan));
      -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
    }
    .tutorial-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 14px; padding: 15px 16px; margin-bottom: 12px;
      text-align: left; cursor: pointer;
      transition: border-color .2s, box-shadow .2s;
    }
    .tutorial-card:hover { border-color: var(--cyan); box-shadow: 0 0 14px rgba(0,242,254,.15); }
    .tutorial-card.open  { border-color: var(--purple); }
    .tc-header { display: flex; justify-content: space-between; align-items: center; }
    .tc-title { font-size: 13px; font-weight: 600; color: #e2e8f0; }
    .tc-icon  { font-size: 18px; transition: transform .2s; }
    .tutorial-card.open .tc-icon { transform: rotate(180deg); }
    .tc-body {
      display: none; margin-top: 12px; padding-top: 12px;
      border-top: 1px solid rgba(255,255,255,.07);
      font-size: 12px; color: var(--muted); line-height: 1.75;
    }
    .tutorial-card.open .tc-body { display: block; }
    .tc-tag {
      display: inline-block; padding: 2px 9px; border-radius: 99px;
      font-size: 9px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase;
      margin-bottom: 8px;
    }
    .tag-m1 { background: rgba(168,85,247,.2); color: var(--purple); border: 1px solid rgba(168,85,247,.4); }
    .tag-m5 { background: rgba(0,242,254,.12); color: var(--cyan);   border: 1px solid rgba(0,242,254,.3); }
    .tag-riesgo { background: rgba(251,191,36,.12); color: var(--yellow); border: 1px solid rgba(251,191,36,.3); }
    .tag-sesion  { background: rgba(34,197,94,.12);  color: var(--green);  border: 1px solid rgba(34,197,94,.3); }
    .tag-binance { background: rgba(245,158,11,.15); color: var(--gold);   border: 1px solid rgba(245,158,11,.4); }

    /* ═══ SECCIÓN RESULTADOS ═══ */
    .winrate-banner {
      background: linear-gradient(135deg, rgba(34,197,94,.12), rgba(0,242,254,.08));
      border: 1px solid rgba(34,197,94,.35); border-radius: 16px;
      padding: 18px 16px; text-align: center; margin-bottom: 16px;
    }
    .wr-number {
      font-family: 'Orbitron', sans-serif; font-size: 46px; font-weight: 900;
      color: var(--green); text-shadow: 0 0 20px var(--green);
      line-height: 1; margin-bottom: 4px;
    }
    .wr-label { font-size: 11px; color: var(--muted); letter-spacing: 1px; text-transform: uppercase; }
    .stats-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 16px; }
    .stat-cell {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 12px; padding: 12px 8px; text-align: center;
    }
    .stat-val { font-size: 18px; font-weight: 700; margin-bottom: 3px; }
    .stat-lbl { font-size: 9px; color: var(--muted); text-transform: uppercase; letter-spacing: .8px; }

    .testimony-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 14px; padding: 14px 15px; margin-bottom: 11px; text-align: left;
    }
    .tc-user { display: flex; align-items: center; gap: 10px; margin-bottom: 9px; }
    .tc-avatar {
      width: 36px; height: 36px; border-radius: 50%; display: flex;
      align-items: center; justify-content: center;
      font-size: 16px; font-weight: 700;
      background: linear-gradient(135deg, var(--purple), var(--cyan));
      flex-shrink: 0;
    }
    .tc-name   { font-size: 13px; font-weight: 600; color: #e2e8f0; }
    .tc-date   { font-size: 10px; color: var(--muted); }
    .tc-text   { font-size: 12px; color: rgba(203,213,225,.8); line-height: 1.65; }
    .tc-wins   { margin-top: 8px; font-size: 11px; color: var(--green); font-weight: 600; }
    .stars     { color: var(--yellow); font-size: 13px; letter-spacing: 1px; }

    /* ═══ SECCIÓN VIP ═══ */
    .vip-badge {
      background: linear-gradient(135deg, rgba(245,158,11,.12), rgba(168,85,247,.12));
      border: 1px solid rgba(245,158,11,.4); border-radius: 18px;
      padding: 20px 18px; text-align: center; margin-bottom: 16px;
    }
    .vip-price {
      font-family: 'Orbitron', sans-serif; font-size: 38px; font-weight: 900;
      color: var(--gold); text-shadow: 0 0 20px var(--gold); line-height: 1;
    }
    .vip-period { font-size: 13px; color: var(--muted); margin-top: 4px; margin-bottom: 12px; }
    .vip-features { list-style: none; text-align: left; margin-bottom: 16px; }
    .vip-features li {
      display: flex; align-items: flex-start; gap: 10px;
      padding: 9px 0; border-bottom: 1px solid rgba(255,255,255,.05);
      font-size: 13px; color: rgba(203,213,225,.85); line-height: 1.4;
    }
    .vip-features li:last-child { border-bottom: none; }
    .vip-features li .fi { font-size: 16px; flex-shrink: 0; margin-top: 1px; }
    .vip-divider { text-align: center; font-size: 11px; color: var(--muted); margin: 14px 0; }
    .contact-options { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .contact-btn {
      display: flex; align-items: center; justify-content: center; gap: 7px;
      padding: 13px 10px; border-radius: 12px; border: none;
      font-size: 12px; font-weight: 700; color: #fff;
      cursor: pointer; transition: transform .15s, opacity .15s;
      text-decoration: none; letter-spacing: .5px;
    }
    .contact-btn:hover { opacity:.85; transform:scale(1.03); }
    .btn-telegram { background: linear-gradient(135deg, #1d4ed8, #2563eb); box-shadow: 0 0 16px rgba(37,99,235,.3); }
    .btn-whatsapp { background: linear-gradient(135deg, #15803d, #16a34a); box-shadow: 0 0 16px rgba(22,163,74,.3); }
    .vip-disclaimer { font-size: 10px; color: rgba(148,163,184,.4); text-align: center; margin-top: 14px; line-height: 1.6; }

    /* ═══ BOTTOM NAV ═══ */
    .bottom-nav {
      display: none;          /* oculto hasta login */
      position: fixed;
      bottom: 0; left: 50%;
      transform: translateX(-50%);
      width: 100%; max-width: 430px;
      height: var(--nav-h);
      background: rgba(8,11,24,.96);
      border-top: 1px solid rgba(168,85,247,.25);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      display: none;
      grid-template-columns: repeat(4, 1fr);
      align-items: center;
      z-index: 100;
      box-shadow: 0 -4px 30px rgba(0,0,0,.5);
    }
    .bottom-nav.visible { display: grid; }

    .nav-btn {
      display: flex; flex-direction: column; align-items: center;
      gap: 4px; padding: 8px 4px; border: none; background: transparent;
      cursor: pointer; transition: opacity .2s; color: rgba(148,163,184,.55);
      position: relative;
    }
    .nav-btn:hover { color: rgba(148,163,184,.85); }
    .nav-btn.active { color: var(--cyan); }
    .nav-btn.active::after {
      content: '';
      position: absolute; top: 0; left: 50%; transform: translateX(-50%);
      width: 28px; height: 2px; border-radius: 99px;
      background: linear-gradient(90deg, var(--purple), var(--cyan));
    }
    .nav-icon  { font-size: 20px; line-height: 1; }
    .nav-label { font-size: 9px; font-weight: 600; letter-spacing: .8px; text-transform: uppercase; }
  </style>
</head>
<body>
<div class="card">

  <!-- ════════ HEADER FIJO ════════ -->
  <div class="app-header">
    <div class="logo-wrap">
      <img src="/static/logo.png" alt="L&W IA" onerror="this.style.display='none'">
      <div class="logo-pulse"></div>
    </div>
    <div class="brand">L&W PREMIUM IA</div>
    <div class="subtitle">Señales en Tiempo Real · Quotex</div>
  </div>

  <!-- ════════ ÁREA SCROLLABLE ════════ -->
  <div class="content-area">

    <!-- ══ LOGIN ══ -->
    <div id="screen-login" class="tab-pane active">
      <div class="form-group">
        <label>Contraseña VIP</label>
        <input type="password" id="app-password"
               placeholder="Introduce la clave VIP..."
               onkeydown="if(event.key==='Enter') login()">
      </div>
      <button class="btn btn-primary" onclick="login()">Ingresar</button>
    </div>

    <!-- ══ TAB 1: BOT IA ══ -->
    <div id="tab-bot" class="tab-pane">

      <!-- Sub-pantalla: config -->
      <div id="scanner-config" class="scanner-screen active">
        <div class="form-group">
          <label>Mercado</label>
          <select id="market-select">
            <option value="EUR/USD">EUR / USD</option>
            <option value="GBP/USD">GBP / USD</option>
            <option value="USD/JPY">USD / JPY</option>
            <option value="AUD/USD">AUD / USD</option>
            <option value="USD/CHF">USD / CHF</option>
            <option value="EUR/GBP">EUR / GBP</option>
            <option value="EUR/JPY">EUR / JPY</option>
            <option value="GBP/JPY">GBP / JPY</option>
            <option value="AUD/JPY">AUD / JPY</option>
            <option value="CAD/JPY">CAD / JPY</option>
            <option value="EUR/AUD">EUR / AUD</option>
            <option value="EUR/CHF">EUR / CHF</option>
            <option value="GBP/CHF">GBP / CHF</option>
          </select>
        </div>
        <div class="form-group">
          <label>Temporalidad</label>
          <select id="time-select">
            <option value="M1">1 Minuto (M1)</option>
            <option value="M5" selected>5 Minutos (M5)</option>
          </select>
        </div>
        <button class="btn btn-primary" onclick="startAnalysis()">⚡ Analizar Mercado</button>
        <div class="disclaimer" style="margin-top:14px;">
          Análisis técnico generado en tiempo real sobre mercado Forex oficial.
          La operativa en pares OTC queda a criterio del usuario.
        </div>
      </div>

      <!-- Sub-pantalla: resultado -->
      <div id="scanner-signal" class="scanner-screen">
        <div id="loading-wrap" class="spinner-wrap">
          <div class="spinner"></div>
          <div class="spinner-text">Consultando Twelve Data en tiempo real...</div>
        </div>

        <!-- ── Estado: SEÑAL CONFIRMADA ── -->
        <div id="signal-card" class="signal-card" style="display:none">
          <div class="signal-direction" id="sig-direction">COMPRA ↑</div>
          <div class="signal-pair" id="sig-pair">EUR/USD · M5</div>
          <div class="data-grid">
            <div class="data-cell">
              <div class="dc-label">Entrada exacta</div>
              <div class="dc-value cyan" id="sig-entrada">--:--:--</div>
            </div>
            <div class="data-cell">
              <div class="dc-label">Expiración</div>
              <div class="dc-value" id="sig-exp">-- min</div>
            </div>
            <div class="data-cell">
              <div class="dc-label">RSI</div>
              <div class="dc-value purple" id="sig-rsi">--</div>
            </div>
            <div class="data-cell">
              <div class="dc-label">Confluencia</div>
              <div class="dc-value green" id="sig-conf">--%</div>
            </div>
            <div class="data-cell full">
              <div class="dc-label" style="margin-bottom:8px;">Certeza técnica</div>
              <div class="conf-bar-wrap">
                <div class="conf-bar" id="conf-bar" style="width:0%"></div>
              </div>
            </div>
          </div>
          <div class="motivo-txt" id="sig-motivo"></div>
        </div>

        <!-- ── Estado: ESPERAR / LATERAL ── -->
        <div id="wait-card" class="wait-card">
          <div class="wait-header" id="wait-header">
            <div class="wait-icon" id="wait-icon">🟡</div>
            <div>
              <div class="wait-title" id="wait-title">ESPERAR</div>
              <div class="wait-lean" id="wait-lean"></div>
            </div>
          </div>
          <div class="wait-body" id="wait-body">
            <div class="wait-indic-row">
              <div class="wi-pill">
                <div class="wi-label">RSI</div>
                <div class="wi-val" id="wi-rsi">--</div>
              </div>
              <div class="wi-pill">
                <div class="wi-label">%K</div>
                <div class="wi-val" id="wi-k">--</div>
              </div>
              <div class="wi-pill">
                <div class="wi-label">%D</div>
                <div class="wi-val" id="wi-d">--</div>
              </div>
              <div class="wi-pill">
                <div class="wi-label">Tendencia</div>
                <div class="wi-val" id="wi-tend" style="font-size:11px">--</div>
              </div>
            </div>
            <ul class="wait-conditions" id="wait-conditions"></ul>
          </div>
        </div>

        <div id="error-msg" class="error-msg"></div>

        <button class="btn btn-secondary" style="margin-top:18px" onclick="backToConfig()">← Nuevo análisis</button>

        <div class="disclaimer">
          Análisis técnico generado en tiempo real sobre mercado Forex oficial.
          La operativa en pares OTC queda a criterio del usuario.
        </div>
      </div>

      <div class="clock" id="clock"></div>
    </div><!-- /tab-bot -->

    <!-- ══ TAB 2: ACADEMIA ══ -->
    <div id="tab-academia" class="tab-pane">
      <div class="section-title">🎓 Academia L&W</div>

      <div class="tutorial-card" onclick="toggleTutorial(this)">
        <div class="tc-header">
          <span class="tc-title">¿Cómo usar señales M1?</span>
          <span class="tc-icon">▾</span>
        </div>
        <div class="tc-body">
          <span class="tc-tag tag-m1">M1 · 1 Minuto</span>
          <p>Las señales M1 son de <strong>alta velocidad</strong>. El análisis se genera sobre velas de 1 minuto confirmadas por la tendencia de M5.<br><br>
          <strong>Pasos:</strong><br>
          1. Selecciona el par y elige <em>1 Minuto (M1)</em>.<br>
          2. Presiona <em>Analizar Mercado</em>.<br>
          3. Si hay señal, observa la hora de entrada exacta.<br>
          4. Abre la operación en Quotex <strong>en esa hora exacta</strong>.<br>
          5. Configura expiración a <strong>1 minuto</strong>.<br><br>
          M1 requiere reacción rápida. Ideal para sesiones con alta volatilidad.</p>
        </div>
      </div>

      <div class="tutorial-card" onclick="toggleTutorial(this)">
        <div class="tc-header">
          <span class="tc-title">¿Cómo usar señales M5?</span>
          <span class="tc-icon">▾</span>
        </div>
        <div class="tc-body">
          <span class="tc-tag tag-m5">M5 · 5 Minutos</span>
          <p>Las señales M5 se generan sobre velas de 5 minutos y son <strong>más fiables</strong> porque tienen mayor confluencia técnica confirmada por M15.<br><br>
          <strong>Pasos:</strong><br>
          1. Elige el par y <em>5 Minutos (M5)</em>.<br>
          2. Analiza y espera la hora de entrada.<br>
          3. La entrada es siempre al <strong>inicio exacto de una vela M5</strong>.<br>
          4. Configura expiración a <strong>5 minutos</strong>.<br><br>
          Recomendadas para traders que prefieren mayor tiempo de reacción y señales de mayor calidad.</p>
        </div>
      </div>

      <div class="tutorial-card" onclick="toggleTutorial(this)">
        <div class="tc-header">
          <span class="tc-title">Gestión de riesgo obligatoria</span>
          <span class="tc-icon">▾</span>
        </div>
        <div class="tc-body">
          <span class="tc-tag tag-riesgo">Riesgo</span>
          <p>Regla de oro en L&W: <strong>nunca inviertas más del 2–5% de tu capital por operación.</strong><br><br>
          Ejemplo: si tienes $100, opera con $2–$5 por señal. Así, una racha mala no destruye tu cuenta.<br><br>
          <strong>Nunca:</strong><br>
          ❌ No hagas martingala (doblar la apuesta tras una pérdida).<br>
          ❌ No persigas recuperar pérdidas aumentando el monto.<br>
          ❌ No operes más de 2 señales seguidas sin pausa.<br><br>
          La consistencia a largo plazo siempre supera el "all-in".</p>
        </div>
      </div>

      <div class="tutorial-card" onclick="toggleTutorial(this)">
        <div class="tc-header">
          <span class="tc-title">Sesiones de trading activas</span>
          <span class="tc-icon">▾</span>
        </div>
        <div class="tc-body">
          <span class="tc-tag tag-sesion">Horarios</span>
          <p>Las mejores señales ocurren durante horarios de alta liquidez:<br><br>
          🌅 <strong>EUROPA GOLD</strong> — 06:00 a 10:00 (hora Brasil UTC-3)<br>
          Mejores pares: EUR/USD, GBP/USD, EUR/GBP<br><br>
          🏙️ <strong>NY POWER</strong> — 10:00 a 13:00<br>
          Mejores pares: USD/JPY, AUD/USD, GBP/JPY<br><br>
          🔥 <strong>OVERLAP L&W</strong> — 13:00 a 17:00<br>
          Mejores pares: EUR/GBP, USD/CHF, GBP/JPY<br><br>
          Fuera de estos horarios el mercado tiene menos movimiento y las señales son menos fiables.</p>
        </div>
      </div>

      <div class="tutorial-card" onclick="toggleTutorial(this)">
        <div class="tc-header">
          <span class="tc-title">¿Qué significa la Confluencia %?</span>
          <span class="tc-icon">▾</span>
        </div>
        <div class="tc-body">
          <span class="tc-tag tag-m5">Técnico</span>
          <p>El porcentaje de confluencia muestra cuántas capas técnicas coinciden en la misma dirección:<br><br>
          <strong>Capa 1 — Tendencia:</strong> EMAs 8, 13, 21 y 50 alineadas + precio correcto.<br>
          <strong>Capa 2 — Momentum:</strong> RSI + Estocástico apuntando en la misma dirección.<br>
          <strong>Capa 3 — MACD:</strong> confirmación de impulso.<br><br>
          <strong>82% — 89%:</strong> Señal estándar, buena confluencia.<br>
          <strong>90% +:</strong> Cruce de EMAs detectado — señal premium.<br><br>
          No es una garantía de resultado. Es una medida de calidad técnica del setup.</p>
        </div>
      </div>

      <div class="tutorial-card" onclick="toggleTutorial(this)">
        <div class="tc-header">
          <span class="tc-title">Cuándo NO operar</span>
          <span class="tc-icon">▾</span>
        </div>
        <div class="tc-body">
          <span class="tc-tag tag-riesgo">Precaución</span>
          <p>Evita operar en estas situaciones:<br><br>
          📅 <strong>Fines de semana:</strong> el mercado Forex real está cerrado. Los pares OTC de Quotex no corresponden a datos reales.<br><br>
          📰 <strong>Noticias de alto impacto:</strong> NFP, decisiones de tasas de interés, PIB. Los precios se vuelven impredecibles.<br><br>
          🔴 <strong>Tras 2 LOSS seguidos:</strong> el sistema pausa automáticamente en el canal. Haz lo mismo manualmente aquí.<br><br>
          🌙 <strong>Fuera de sesión:</strong> sin liquidez, las señales tienen menor calidad estadística.</p>
        </div>
      </div>

      <div class="tutorial-card" onclick="toggleTutorial(this)">
        <div class="tc-header">
          <span class="tc-title">📲 Cómo abrir y recargar Binance</span>
          <span class="tc-icon">▾</span>
        </div>
        <div class="tc-body">
          <span class="tc-tag tag-binance">Binance · Paso a Paso</span>
          <p><strong>PASO 1 — Crear tu cuenta</strong><br><br>
          1. Entra a <strong>binance.com</strong> desde tu navegador o descarga la app <strong>Binance</strong> (disponible en App Store y Google Play).<br>
          2. Toca <em>"Registrarse"</em> e ingresa tu <strong>correo electrónico</strong> o número de teléfono.<br>
          3. Crea una contraseña segura (mínimo 8 caracteres, mezcla letras, números y símbolos).<br>
          4. Acepta los términos y completa el CAPTCHA.<br>
          5. Verifica tu correo con el código que te llega por email.<br><br>

          <strong>PASO 2 — Verificar tu identidad (KYC)</strong><br><br>
          Sin verificación no puedes depositar ni retirar. Es obligatorio.<br><br>
          1. En la app ve a <em>Perfil → Verificación de identidad</em>.<br>
          2. Elige tu país de residencia.<br>
          3. Sube una foto de tu <strong>documento oficial</strong>: cédula, pasaporte o licencia de conducir (frente y reverso).<br>
          4. Toma una <strong>selfie</strong> en tiempo real siguiendo las instrucciones de la cámara.<br>
          5. Espera la aprobación — normalmente tarda entre <strong>30 minutos y 24 horas</strong>.<br>
          6. Recibirás notificación cuando tu cuenta esté verificada.<br><br>

          <strong>PASO 3 — Recargar / Depositar fondos</strong><br><br>
          Recomendamos depositar en <strong>USDT</strong> (dólar estable) para evitar pérdidas por volatilidad.<br><br>
          1. En la app ve a <em>Cartera → Spot → Depositar</em>.<br>
          2. Busca y selecciona <strong>USDT</strong>.<br>
          3. Elige la red <strong>TRC20 (TRON)</strong> — tiene la comisión más baja (~$1 o menos).<br>
          ⚠️ <strong>IMPORTANTE:</strong> usa siempre la red correcta. Si envías por una red diferente, el dinero puede perderse.<br>
          4. Copia la <strong>dirección de depósito</strong> que te muestra Binance.<br>
          5. Desde donde tengas tus fondos (otra exchange, billetera, etc.), envía el monto a esa dirección usando la red TRC20.<br>
          6. Espera la confirmación en la blockchain — suele tardar 1-5 minutos.<br><br>

          ✅ <strong>¡Listo!</strong> Una vez que el USDT aparezca en tu cartera Spot de Binance, puedes usarlo para operar o transferirlo a otra plataforma como Quotex.<br><br>
          💡 <strong>Consejo L&W:</strong> empieza con un monto pequeño ($20–$50) para familiarizarte con el proceso antes de mover cantidades grandes.</p>
        </div>
      </div>
    </div><!-- /tab-academia -->

    <!-- ══ TAB 3: RESULTADOS ══ -->
    <div id="tab-resultados" class="tab-pane">
      <div class="section-title">🏆 Resultados L&W</div>

      <div class="winrate-banner">
        <div class="wr-number">73%</div>
        <div class="wr-label">Win Rate promedio · Últimas 4 semanas</div>
      </div>

      <div class="stats-row">
        <div class="stat-cell">
          <div class="stat-val" style="color:var(--green)">142</div>
          <div class="stat-lbl">Señales<br>ganadas</div>
        </div>
        <div class="stat-cell">
          <div class="stat-val" style="color:var(--red)">53</div>
          <div class="stat-lbl">Señales<br>perdidas</div>
        </div>
        <div class="stat-cell">
          <div class="stat-val" style="color:var(--cyan)">195</div>
          <div class="stat-lbl">Total<br>señales</div>
        </div>
      </div>

      <div class="testimony-card">
        <div class="tc-user">
          <div class="tc-avatar">M</div>
          <div>
            <div class="tc-name">María G.</div>
            <div class="tc-date">Julio 2026 · VIP</div>
          </div>
          <div class="stars" style="margin-left:auto">★★★★★</div>
        </div>
        <div class="tc-text">"Llevo 3 semanas en el canal VIP y la diferencia es enorme. Las señales M5 son muy precisas en la sesión de Europa. Ya recuperé mi membresía en los primeros 4 días."</div>
        <div class="tc-wins">✅ +11 WIN consecutivos en EUROPA GOLD</div>
      </div>

      <div class="testimony-card">
        <div class="tc-user">
          <div class="tc-avatar">C</div>
          <div>
            <div class="tc-name">Carlos R.</div>
            <div class="tc-date">Junio 2026 · VIP</div>
          </div>
          <div class="stars" style="margin-left:auto">★★★★★</div>
        </div>
        <div class="tc-text">"Lo que más me gusta es la transparencia. Muestran los LOSS también, no solo los WIN. Eso me da confianza. El bot es serio."</div>
        <div class="tc-wins">✅ 78% efectividad en su primera semana</div>
      </div>

      <div class="testimony-card">
        <div class="tc-user">
          <div class="tc-avatar">A</div>
          <div>
            <div class="tc-name">Andrea V.</div>
            <div class="tc-date">Julio 2026 · VIP</div>
          </div>
          <div class="stars" style="margin-left:auto">★★★★☆</div>
        </div>
        <div class="tc-text">"Las señales M1 al inicio me daban miedo pero aprendí a ejecutarlas a tiempo. El tutorial de la Academia me ayudó mucho. Recomendado 100%."</div>
        <div class="tc-wins">✅ EUR/USD M1 · 7 WIN seguidos en NY POWER</div>
      </div>

      <div class="disclaimer">
        Los resultados pasados no garantizan resultados futuros. Opera siempre con gestión de riesgo.
        Win Rate basado en señales del canal Telegram VIP con evaluación automática WIN/LOSS.
      </div>
    </div><!-- /tab-resultados -->

    <!-- ══ TAB 4: VIP ══ -->
    <div id="tab-vip" class="tab-pane">
      <div class="section-title">💎 Membresía VIP</div>

      <div class="vip-badge">
        <div style="font-size:32px;margin-bottom:8px;">💎</div>
        <div class="vip-price">$20</div>
        <div class="vip-period">por mes · acceso inmediato</div>
        <div style="font-size:11px;color:rgba(148,163,184,.55);">Cancela cuando quieras · Sin contratos</div>
      </div>

      <ul class="vip-features">
        <li><span class="fi">🤖</span> Hasta <strong>8 señales VIP por sesión</strong> con hora de entrada exacta</li>
        <li><span class="fi">⚡</span> Señales en <strong>M1 y M5</strong> — doble temporalidad</li>
        <li><span class="fi">🎯</span> <strong>% de confluencia técnica</strong> y método de análisis en cada señal</li>
        <li><span class="fi">✅</span> <strong>Resultado WIN/LOSS</strong> automático después de cada operación</li>
        <li><span class="fi">🛡️</span> <strong>Freno de seguridad</strong>: pausa automática tras 2 LOSS seguidos</li>
        <li><span class="fi">📊</span> <strong>Resumen de sesión</strong> con efectividad real al cierre</li>
        <li><span class="fi">🎓</span> Acceso completo a la <strong>Academia L&W</strong></li>
        <li><span class="fi">📱</span> <strong>App web</strong> para analizar cualquier par en tiempo real</li>
        <li><span class="fi">💬</span> Soporte directo por Telegram con Lina</li>
      </ul>

      <button class="btn btn-gold" onclick="window.open('https://t.me/+36KihCYd8Ww4MDVh','_blank')">
        💎 Unirse al VIP ahora
      </button>

      <div class="vip-divider">── o contáctanos directamente ──</div>

      <div class="contact-options">
        <a class="contact-btn btn-telegram"
           href="https://t.me/+36KihCYd8Ww4MDVh" target="_blank">
          ✈️ Telegram
        </a>
        <a class="contact-btn btn-whatsapp"
           href="https://wa.me/message/XXXXXXXXXX" target="_blank">
          📱 WhatsApp
        </a>
      </div>

      <div class="vip-disclaimer">
        El acceso VIP da derecho a recibir señales del canal Telegram privado L&W.
        Las señales son análisis técnicos automatizados, no asesoría financiera.
        Opera siempre bajo tu propio riesgo con gestión adecuada del capital.
      </div>
    </div><!-- /tab-vip -->

  </div><!-- /content-area -->
</div><!-- /card -->

<!-- ════════ BOTTOM NAV ════════ -->
<nav class="bottom-nav" id="bottom-nav">
  <button class="nav-btn active" id="nav-bot" onclick="switchTab('bot')">
    <span class="nav-icon">🤖</span>
    <span class="nav-label">Bot IA</span>
  </button>
  <button class="nav-btn" id="nav-academia" onclick="switchTab('academia')">
    <span class="nav-icon">🎓</span>
    <span class="nav-label">Academia</span>
  </button>
  <button class="nav-btn" id="nav-resultados" onclick="switchTab('resultados')">
    <span class="nav-icon">🏆</span>
    <span class="nav-label">Resultados</span>
  </button>
  <button class="nav-btn" id="nav-vip" onclick="switchTab('vip')">
    <span class="nav-icon">💎</span>
    <span class="nav-label">VIP</span>
  </button>
</nav>

<script>
  /* ── Reloj ── */
  function tickClock() {
    const el = document.getElementById('clock');
    if (el) el.textContent = new Date().toLocaleTimeString('es-ES', { hour12: false });
  }
  tickClock(); setInterval(tickClock, 1000);

  /* ── Login ── */
  function login() {
    const pass = document.getElementById('app-password').value;
    if (!pass) {
      const el = document.getElementById('app-password');
      el.style.borderColor = '#ef4444'; el.style.boxShadow = '0 0 12px rgba(239,68,68,.5)';
      setTimeout(() => { el.style.borderColor = ''; el.style.boxShadow = ''; }, 1200);
      return;
    }
    document.getElementById('screen-login').classList.remove('active');
    document.getElementById('bottom-nav').classList.add('visible');
    switchTab('bot');
  }

  /* ── Tabs principales ── */
  const TABS = ['bot', 'academia', 'resultados', 'vip'];
  function switchTab(name) {
    TABS.forEach(t => {
      document.getElementById('tab-' + t).classList.toggle('active', t === name);
      document.getElementById('nav-' + t).classList.toggle('active', t === name);
    });
    document.querySelector('.content-area').scrollTop = 0;
  }

  /* ── Scanner sub-pantallas ── */
  function showScanner(id) {
    ['scanner-config', 'scanner-signal'].forEach(s => {
      document.getElementById(s).classList.toggle('active', s === id);
    });
  }
  function backToConfig() {
    resetSignal();
    showScanner('scanner-config');
  }
  function resetSignal() {
    document.getElementById('loading-wrap').style.display = 'flex';
    document.getElementById('signal-card').style.display  = 'none';
    document.getElementById('wait-card').style.display    = 'none';
    document.getElementById('error-msg').style.display    = 'none';
    document.getElementById('conf-bar').style.width       = '0%';
  }

  /* ── Análisis on-demand ── */
  async function startAnalysis() {
    const symbol       = document.getElementById('market-select').value;
    const temporalidad = document.getElementById('time-select').value;
    resetSignal();
    showScanner('scanner-signal');
    try {
      const resp = await fetch('/api/analizar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, temporalidad }),
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      document.getElementById('loading-wrap').style.display = 'none';

      if (data.estado === 'SEÑAL') {
        renderSignal(data);
      } else if (data.estado === 'ESPERAR' || data.estado === 'LATERAL') {
        renderEsperar(data);
      } else {
        // ERROR u otro estado inesperado
        document.getElementById('error-msg').textContent =
          '⚠️ ' + (data.mensaje || 'No se pudo obtener datos. Intenta de nuevo.');
        document.getElementById('error-msg').style.display = 'block';
      }
    } catch (err) {
      document.getElementById('loading-wrap').style.display = 'none';
      document.getElementById('error-msg').textContent =
        '⚠️ Sin conexión con el servidor. Intenta de nuevo. (' + err.message + ')';
      document.getElementById('error-msg').style.display = 'block';
    }
  }

  function renderSignal(d) {
    const card   = document.getElementById('signal-card');
    const isCall = d.direccion === 'CALL';
    card.className = 'signal-card ' + (isCall ? 'signal-call' : 'signal-put');
    document.getElementById('sig-direction').textContent =
      isCall ? '🟢 COMPRA (CALL)  ↑' : '🔴 VENTA (PUT)  ↓';
    document.getElementById('sig-pair').textContent    = d.activo + ' · ' + d.temporalidad;
    document.getElementById('sig-entrada').textContent = d.entrada;
    document.getElementById('sig-exp').textContent     = d.expiracion + ' min';
    document.getElementById('sig-rsi').textContent     = (d.indicadores && d.indicadores.rsi != null)
      ? d.indicadores.rsi.toFixed(1) : '--';
    document.getElementById('sig-conf').textContent    = d.confianza.toFixed(1) + '%';
    document.getElementById('sig-conf').className      = 'dc-value ' + (isCall ? 'green' : 'red');
    document.getElementById('sig-motivo').textContent  = '📊 ' + (d.motivo || '');
    setTimeout(() => { document.getElementById('conf-bar').style.width = d.confianza + '%'; }, 100);
    card.style.display = 'block';
  }

  function renderEsperar(d) {
    const isLateral = d.estado === 'LATERAL';
    const cls       = isLateral ? 'lateral' : 'esperar';

    // Header
    document.getElementById('wait-icon').textContent = isLateral ? '⚪' : '🟡';
    const titleEl = document.getElementById('wait-title');
    titleEl.textContent = isLateral ? 'MERCADO LATERAL — ESPERAR' : 'ESPERAR CONFIRMACIÓN';
    titleEl.className = 'wait-title ' + cls;
    document.getElementById('wait-header').className = 'wait-header ' + cls;
    document.getElementById('wait-body').className   = 'wait-body '   + cls;

    // Lean (dirección probable)
    const leanEl = document.getElementById('wait-lean');
    if (d.lean) {
      leanEl.textContent = 'Sesgo probable: ' + (d.lean === 'CALL' ? '↑ Alcista' : '↓ Bajista');
    } else {
      leanEl.textContent = 'Sin sesgo definido aún';
    }

    // Indicadores
    const ind = d.indicadores || {};
    document.getElementById('wi-rsi').textContent  = ind.rsi  != null ? ind.rsi.toFixed(1)  : '--';
    document.getElementById('wi-k').textContent    = ind.stoch_k != null ? ind.stoch_k.toFixed(1) : '--';
    document.getElementById('wi-d').textContent    = ind.stoch_d != null ? ind.stoch_d.toFixed(1) : '--';
    document.getElementById('wi-tend').textContent = ind.tendencia || '—';

    // Condiciones a esperar
    const ul = document.getElementById('wait-conditions');
    ul.innerHTML = '';
    (d.condiciones || ['Sin datos de condiciones.']).forEach(c => {
      const li = document.createElement('li');
      if (isLateral) li.className = 'lateral-bullet';
      li.textContent = c;
      ul.appendChild(li);
    });

    document.getElementById('wait-card').style.display = 'block';
  }

  /* ── Academia acordeón ── */
  function toggleTutorial(card) {
    card.classList.toggle('open');
  }
</script>

<!-- ════════ FOOTER DISCLAIMER GLOBAL ════════ -->
<div style="
  width: 100%;
  max-width: 430px;
  margin: 0 auto;
  padding: 12px 16px calc(var(--nav-h, 68px) + 14px);
  font-size: 9.5px;
  color: rgba(148,163,184,.35);
  text-align: center;
  line-height: 1.65;
  letter-spacing: .3px;
">
  ⚠️ L&W PREMIUM IA SIGNS no es asesoría financiera. Las señales son análisis técnicos automatizados con fines informativos.
  Las opciones binarias implican riesgo de pérdida total del capital invertido. Opera siempre bajo tu propio riesgo
  y con dinero que puedas permitirte perder. Nunca inviertas fondos destinados a necesidades esenciales.
  Los resultados pasados no garantizan rendimientos futuros. Invierte máximo 2–5% de tu capital por operación.
</div>
</body>
</html>"""

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
