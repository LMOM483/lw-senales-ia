"""
server.py - Interfaz web L&W PREMIUM IA SIGNS
FastAPI: sirve el frontend y expone /api/analizar para ejecutar analizar_activo en tiempo real.
"""

import requests as _requests
import pandas as pd
import ta as _ta
import subprocess as _subprocess
import sys as _sys
import sqlite3 as _sqlite3
import hashlib as _hashlib
import secrets as _secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, Header, HTTPException
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
    # ── BOT DE TELEGRAM DESACTIVADO TEMPORALMENTE ──
    # Para reactivar, descomentar el bloque try/except de abajo y la sección yield/terminate.
    # try:
    #     _bot_proceso = _subprocess.Popen(
    #         [_sys.executable, "-u", "main.py"],
    #         stdout=None,
    #         stderr=None,
    #     )
    #     print(f"[LW] Bot de Telegram iniciado (PID {_bot_proceso.pid})", flush=True)
    # except Exception as e:
    #     print(f"[LW] No se pudo iniciar main.py: {e}", flush=True)
    print("[LW] Bot de Telegram DESACTIVADO — solo modo web.", flush=True)
    _init_auth()
    yield
    # if _bot_proceso and _bot_proceso.poll() is None:
    #     _bot_proceso.terminate()
    #     print("[LW] Bot de Telegram detenido")

app = FastAPI(title="L&W PREMIUM IA SIGNS", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Modelo de entrada del endpoint ──────────────────────────────────────────

class AnalisisRequest(BaseModel):
    symbol: str
    temporalidad: str

class LoginRequest(BaseModel):
    email: str
    password: str

# ── Auth: SQLite users + sesión única anti-share ─────────────────────────────

_AUTH_SALT = "lw_senales_ia_2026"   # salt fijo de aplicación

def _hash_pw(password: str) -> str:
    return _hashlib.pbkdf2_hmac(
        "sha256", password.encode(), _AUTH_SALT.encode(), 100_000
    ).hex()

def _db_conn():
    return _sqlite3.connect(Config.DATABASE_FILE, check_same_thread=False)

def _init_auth():
    """Crea tabla users e inserta usuario demo si no existe."""
    with _db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_active     INTEGER DEFAULT 1,
                session_token TEXT
            )
        """)
        exists = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("demo@lwsenales.com",)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (email, password_hash, is_active) VALUES (?,?,1)",
                ("demo@lwsenales.com", _hash_pw("LW2026!"))
            )
            print("[LW-Auth] Usuario demo creado: demo@lwsenales.com / LW2026!")

def _verificar_token(x_session_token: str = Header(None)):
    """Dependency FastAPI: valida token de sesión. Lanza 401 si no es válido."""
    if not x_session_token:
        raise HTTPException(status_code=401, detail="No autenticado")
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE session_token = ? AND is_active = 1",
            (x_session_token,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Sesión inválida o abierta en otro dispositivo")
    return x_session_token

@app.post("/api/login")
def api_login(req: LoginRequest):
    email = req.email.strip().lower()
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash, is_active FROM users WHERE email = ?", (email,)
        ).fetchone()
    if not row or not row[2] or row[1] != _hash_pw(req.password):
        return JSONResponse({"ok": False, "error": "Email o contraseña incorrectos."}, status_code=401)
    token = _secrets.token_urlsafe(32)
    with _db_conn() as conn:
        conn.execute("UPDATE users SET session_token = ? WHERE id = ?", (token, row[0]))
    return JSONResponse({"ok": True, "token": token})

@app.post("/api/logout")
def api_logout(token: str = Depends(_verificar_token)):
    with _db_conn() as conn:
        conn.execute("UPDATE users SET session_token = NULL WHERE session_token = ?", (token,))
    return JSONResponse({"ok": True})

# ── Función de datos (sync, reutiliza la misma lógica del bot) ───────────────

_IND_VACIO = {"rsi": None, "stoch_k": None, "stoch_d": None, "tendencia": "—", "macd": None}

def _obtener_df(symbol: str, intervalo: str):
    """Descarga velas de Twelve Data.
    Retorna (df, None) en éxito, (None, 'rate_limit') en 429, (None, 'error') en otro fallo."""
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
        if resp.status_code == 429:
            return None, "rate_limit"
        if resp.status_code != 200:
            return None, "error"
        data = resp.json()
        if "values" not in data or len(data["values"]) < 55:
            return None, "error"
        df = pd.DataFrame(data["values"])
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("datetime").reset_index(drop=True)
        return df, None
    except Exception:
        return None, "error"

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
        # Estocástico — mensaje según posición actual de K vs D
        if k_act >= 80:
            condiciones.append(f"Estocástico sobrecomprado (%K {k_act:.0f}) — esperar retroceso bajo 80")
        elif not ind["cruce_arriba"]:
            if k_act > d_act:
                # K ya está por encima de D pero el cruce no ocurrió en esta vela
                condiciones.append(
                    f"Estocástico en zona de compra (%K {k_act:.0f} > %D {d_act:.0f}) — aguardar señal de cruce en esta vela"
                )
            else:
                condiciones.append(
                    f"Estocástico: %K {k_act:.0f} < %D {d_act:.0f} — esperar que %K cruce por encima de %D"
                )
    else:  # PUT
        if rsi > 50:
            condiciones.append(f"RSI = {rsi:.1f} — necesita bajar de 50 para confirmar presión vendedora")
        elif rsi < 30:
            condiciones.append(f"RSI = {rsi:.1f} — sobreventa, esperar rebote sobre 30")
        elif rsi > rsi_prev:
            condiciones.append(f"RSI = {rsi:.1f} subiendo — esperar que vuelva a bajar")
        # Estocástico — mensaje según posición actual de K vs D
        if k_act <= 20:
            condiciones.append(f"Estocástico sobrevendido (%K {k_act:.0f}) — esperar rebote sobre 20")
        elif not ind["cruce_abajo"]:
            if k_act < d_act:
                # K ya está por debajo de D pero el cruce no ocurrió en esta vela
                condiciones.append(
                    f"Estocástico en zona de venta (%K {k_act:.0f} < %D {d_act:.0f}) — aguardar señal de cruce en esta vela"
                )
            else:
                condiciones.append(
                    f"Estocástico: %K {k_act:.0f} > %D {d_act:.0f} — esperar que %K cruce por debajo de %D"
                )

    # MACD
    if dir_macd != dir_t:
        condiciones.append(f"MACD apunta {dir_macd} pero tendencia es {dir_t} — esperar alineación")

    if not condiciones:
        condiciones.append("Condiciones casi listas — aguardar cierre de la vela actual")

    return condiciones, "ESPERAR"


# ── Endpoint de análisis ─────────────────────────────────────────────────────

@app.post("/api/analizar")
def analizar_mercado(req: AnalisisRequest, _tok: str = Depends(_verificar_token)):
    """Análisis on-demand: siempre devuelve estado SEÑAL, ESPERAR o LATERAL con valores reales."""
    symbol       = req.symbol.strip()
    temporalidad = req.temporalidad
    intervalo    = "1min" if temporalidad == "M1" else "5min"
    exp_min      = 1 if temporalidad == "M1" else 5

    # 1. Descargar datos
    df, api_err = _obtener_df(symbol, intervalo)
    if api_err == "rate_limit":
        return JSONResponse({
            "ok": True,
            "estado": "ERROR",
            "mensaje": "⚠️ Límite de peticiones a Twelve Data. Espera unos segundos e intenta de nuevo.",
            "condiciones": ["La API de datos está temporalmente saturada. Reintentar en 15-30 segundos."],
            "indicadores": _IND_VACIO,
        })
    if df is None:
        return JSONResponse({
            "ok": True,
            "estado": "ERROR",
            "mensaje": f"No se obtuvieron datos para {symbol}. Verifica conexión o intenta de nuevo.",
            "condiciones": ["Sin datos del servidor. Intenta de nuevo en unos segundos."],
            "indicadores": _IND_VACIO,
        })

    # 2. Calcular indicadores
    try:
        ind = _calcular_indicadores(df.copy())
    except Exception as e:
        return JSONResponse({
            "ok": True,
            "estado": "ERROR",
            "mensaje": f"Error calculando indicadores: {e}",
            "condiciones": ["Error interno al procesar los datos. Intenta de nuevo."],
            "indicadores": _IND_VACIO,
        })

    # Bloque de indicadores siempre completo (valores redondeados, nunca None en los numéricos)
    def _ind_bloque(inc_macd: bool = False):
        bloque = {
            "rsi":       round(float(ind["rsi"]),   1),
            "stoch_k":   round(float(ind["k_act"]), 1),
            "stoch_d":   round(float(ind["d_act"]), 1),
            "tendencia": ind["tendencia"] or "—",
        }
        if inc_macd:
            bloque["macd"] = ind["macd"] or "—"
        return bloque

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
            "confianza": round(float(confianza), 1),
            "entrada": entrada.strftime("%H:%M:%S"),
            "expiracion": exp_min,
            "activo": symbol,
            "temporalidad": temporalidad,
            "motivo": _construir_motivo(ind, direccion, metodo),
            "indicadores": _ind_bloque(),
        })

    # 4. Sin señal: explicar qué falta
    condiciones, estado = _que_esperar(ind)

    lean = None
    if ind["tendencia"] and ind["macd"] == ind["tendencia"]:
        lean = ind["tendencia"]

    return JSONResponse({
        "ok": True,
        "estado": estado,
        "lean": lean,
        "condiciones": condiciones,
        "indicadores": _ind_bloque(inc_macd=True),
    })


# ── Endpoint Escáner de mejores activos ──────────────────────────────────────
_scanner_cache: dict = {}   # intervalo → {"ts": float, "data": list}

@app.get("/api/top-assets")
def top_assets(intervalo: str = "5min", _tok: str = Depends(_verificar_token)):
    """Escanea todos los activos del pool y los devuelve ordenados por confluencia.
    Cache de 60 s para no saturar Twelve Data."""
    ahora_ts = datetime.now().timestamp()
    cached = _scanner_cache.get(intervalo)
    if cached and ahora_ts - cached["ts"] < 30:
        return JSONResponse({"status": "success", "cached": True, "top_assets": cached["data"]})

    resultados = []
    hubo_rate_limit = False
    for simbolo in Config.POOL_FOREX:
        try:
            df, api_err = _obtener_df(simbolo, intervalo)
            if api_err == "rate_limit":
                hubo_rate_limit = True
                resultados.append({
                    "symbol": simbolo,
                    "confluence": None,
                    "signal": "API_LIMIT",
                    "confirmed": False,
                    "status_label": "⚠️ Sin datos — Reintentar",
                    "reason": "Límite de peticiones alcanzado",
                })
                continue
            if df is None:
                continue
            ind = _calcular_indicadores(df.copy())
            senales = evaluar_estrategias(df)

            if senales:
                dir_, conf, metodo = senales[0]
                resultados.append({
                    "symbol": simbolo,
                    "confluence": round(float(conf), 1),
                    "signal": dir_,
                    "confirmed": True,   # 4/4 capas superadas
                    "status_label": "🟢 ENTRADA CONFIRMADA" if dir_ == "CALL" else "🔴 ENTRADA CONFIRMADA",
                    "reason": _construir_motivo(ind, dir_, metodo),
                })
                continue

            # Sin señal: puntuación parcial por capas que sí pasaron
            score = 0
            partes = []
            dir_t = ind["tendencia"]

            if dir_t:
                score += 25
                partes.append("Tendencia " + ("alcista" if dir_t == "CALL" else "bajista"))
            if dir_t and ind["macd"] == dir_t:
                score += 20
                partes.append("MACD confirma")
            rsi = ind["rsi"]
            if dir_t == "CALL" and 50 <= rsi <= 70:
                score += 20
                partes.append(f"RSI {rsi:.0f} en zona")
            elif dir_t == "PUT" and 30 <= rsi <= 50:
                score += 20
                partes.append(f"RSI {rsi:.0f} en zona")
            if dir_t == "CALL" and ind["cruce_arriba"]:
                score += 15
                partes.append("Estocástico cruzó al alza")
            elif dir_t == "PUT" and ind["cruce_abajo"]:
                score += 15
                partes.append("Estocástico cruzó a la baja")

            if score >= 45 and dir_t:
                label  = "🟡 SEÑAL EN FORMACIÓN"
                signal = dir_t
            else:
                label  = "⚪ MERCADO LATERAL"
                signal = "LATERAL"

            resultados.append({
                "symbol": simbolo,
                "confluence": float(score),
                "signal": signal,
                "confirmed": False,   # no llegó a 4/4 capas
                "status_label": label,
                "reason": " + ".join(partes) if partes else "Sin confluencia técnica",
            })
        except Exception:
            continue

    # Ordenar: confluence None al final (activos con rate_limit)
    resultados.sort(key=lambda x: x["confluence"] if x["confluence"] is not None else -1, reverse=True)
    _scanner_cache[intervalo] = {"ts": ahora_ts, "data": resultados}
    return JSONResponse({
        "status": "success",
        "cached": False,
        "rate_limited": hubo_rate_limit,
        "top_assets": resultados,
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

    /* ══ SCANNER HOME ══ */
    .home-hdr { display:flex; justify-content:space-between; align-items:center;
      padding:12px 0 14px; margin-bottom:4px; border-bottom:1px solid rgba(255,255,255,.06); }
    .home-hdr-title { font-family:'Orbitron',sans-serif; font-size:10px; color:var(--purple);
      font-weight:700; letter-spacing:.5px; }
    .home-refresh-info { font-size:9px; color:var(--muted); margin-top:3px; }
    .time-toggle { display:flex; border-radius:8px; overflow:hidden;
      border:1px solid rgba(255,255,255,.1); flex-shrink:0; }
    .time-toggle-btn { padding:5px 13px; font-size:11px; font-weight:600; border:none;
      cursor:pointer; background:transparent; color:rgba(148,163,184,.55); transition:all .15s; }
    .time-toggle-btn.active { background:var(--purple); color:#fff; }

    /* Best opp — señal confirmada */
    .boc-wrap { border-radius:15px; overflow:hidden; margin-bottom:14px; }
    .boc-wrap.call { background:linear-gradient(135deg,rgba(34,197,94,.1),rgba(34,197,94,.04));
      border:1.5px solid rgba(34,197,94,.35); }
    .boc-wrap.put  { background:linear-gradient(135deg,rgba(239,68,68,.1),rgba(239,68,68,.04));
      border:1.5px solid rgba(239,68,68,.35); }
    .boc-lbl { font-size:9px; font-weight:700; letter-spacing:1.2px; color:var(--muted);
      padding:12px 14px 0; }
    .boc-dir { font-family:'Orbitron',sans-serif; font-size:18px; font-weight:900;
      padding:4px 14px 0; line-height:1.2; }
    .boc-dir.call { color:#4ade80; } .boc-dir.put { color:#f87171; }
    .boc-meta { padding:6px 14px; display:flex; gap:10px; font-size:11px; color:var(--muted); }
    .boc-conf { font-weight:700; }
    .boc-conf.call { color:#4ade80; } .boc-conf.put { color:#f87171; }
    .boc-reason { padding:0 14px 10px; font-size:11px; color:rgba(148,163,184,.65); line-height:1.5; }
    .btn-ver-senal { display:block; margin:0 14px 14px; padding:13px; border-radius:11px; border:none;
      font-size:13px; font-weight:800; cursor:pointer; color:#fff; transition:opacity .15s; }
    .btn-ver-senal.call { background:linear-gradient(135deg,#15803d,#22c55e);
      box-shadow:0 0 20px rgba(34,197,94,.3); }
    .btn-ver-senal.put  { background:linear-gradient(135deg,#b91c1c,#ef4444);
      box-shadow:0 0 20px rgba(239,68,68,.3); }
    .btn-ver-senal:hover { opacity:.86; }

    /* Best opp — esperando */
    .bow-wrap { padding:16px 18px; border-radius:15px; margin-bottom:14px; text-align:center;
      background:rgba(148,163,184,.04); border:1px solid rgba(148,163,184,.14); }
    .bow-icon { font-size:26px; margin-bottom:6px; }
    .bow-title { font-family:'Orbitron',sans-serif; font-size:10px; color:var(--muted);
      font-weight:700; letter-spacing:.5px; margin-bottom:5px; }
    .bow-sub { font-size:11px; color:rgba(148,163,184,.5); line-height:1.6; }

    /* Home pill (activos lista) */
    .home-pill { display:flex; align-items:center; justify-content:space-between;
      padding:12px 14px; border-radius:12px; margin-bottom:7px;
      background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.07);
      cursor:pointer; transition:background .15s,border-color .15s,transform .1s; }
    .home-pill:hover  { background:rgba(168,85,247,.08); border-color:rgba(168,85,247,.28); }
    .home-pill:active { transform:scale(.98); }
    .hp-left  { flex:1; min-width:0; }
    .hp-symbol { font-size:14px; font-weight:800; color:#e2e8f0; letter-spacing:.3px; }
    .hp-reason { font-size:10px; color:var(--muted); margin-top:2px;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:200px; }
    .hp-right { text-align:right; flex-shrink:0; margin-left:10px; }
    .hp-badge { font-size:12px; font-weight:800; padding:3px 9px; border-radius:7px;
      display:inline-block; margin-bottom:3px; }
    .hp-badge.call { background:rgba(34,197,94,.13); color:#4ade80; border:1px solid rgba(34,197,94,.28); }
    .hp-badge.put  { background:rgba(239,68,68,.13);  color:#f87171; border:1px solid rgba(239,68,68,.28); }
    .hp-badge.wait { background:rgba(234,179,8,.12);  color:#fbbf24; border:1px solid rgba(234,179,8,.25); }
    .hp-badge.flat { background:rgba(148,163,184,.07); color:rgba(148,163,184,.55); border:1px solid rgba(148,163,184,.18); }
    .hp-label { font-size:9px; color:var(--muted); }
    .home-loading-wrap { text-align:center; padding:32px 0 16px; }
    .home-loading-wrap .spinner { margin:0 auto 12px; }
    .home-loading-wrap div { font-size:11px; color:var(--muted); }

    /* ══ MÓDULO ESCÁNER DE ACTIVOS ══ */
    .scanner-module {
      margin-bottom: 16px; border-radius: 14px; overflow: hidden;
      border: 1px solid rgba(168,85,247,.22);
    }
    .scanner-module-header {
      padding: 11px 14px; background: rgba(168,85,247,.07);
      display: flex; justify-content: space-between; align-items: center;
    }
    .scanner-module-title {
      font-size: 11px; font-weight: 700; color: var(--purple);
      font-family: 'Orbitron', sans-serif; letter-spacing: .3px;
    }
    .btn-scan {
      font-size: 11px; padding: 7px 13px; border-radius: 8px; border: none;
      background: linear-gradient(135deg, var(--purple), var(--cyan));
      color: #fff; font-weight: 700; cursor: pointer;
      transition: opacity .15s; white-space: nowrap;
    }
    .btn-scan:hover  { opacity: .82; }
    .btn-scan:disabled { opacity: .45; cursor: wait; }
    .asset-list { padding: 8px 8px 2px; }
    .asset-pill {
      display: flex; align-items: center; justify-content: space-between;
      padding: 10px 11px; border-radius: 10px; margin-bottom: 6px;
      background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.07);
      cursor: pointer; transition: background .15s, border-color .15s; gap: 8px;
    }
    .asset-pill:hover { background: rgba(168,85,247,.09); border-color: rgba(168,85,247,.3); }
    .asset-pill:last-child { margin-bottom: 0; }
    .ap-left { flex: 1; min-width: 0; }
    .ap-symbol { font-size: 13px; font-weight: 700; color: #e2e8f0; }
    .ap-reason { font-size: 10px; color: var(--muted); margin-top: 2px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .ap-right { text-align: right; flex-shrink: 0; }
    .ap-badge {
      font-size: 12px; font-weight: 800; padding: 3px 9px; border-radius: 6px;
      display: inline-block; margin-bottom: 3px;
    }
    .ap-badge.call { background: rgba(34,197,94,.13); color: #4ade80; border: 1px solid rgba(34,197,94,.28); }
    .ap-badge.put  { background: rgba(239,68,68,.13);  color: #f87171; border: 1px solid rgba(239,68,68,.28); }
    .ap-badge.wait { background: rgba(234,179,8,.12);  color: #fbbf24; border: 1px solid rgba(234,179,8,.25); }
    .ap-badge.flat { background: rgba(148,163,184,.07); color: rgba(148,163,184,.6); border: 1px solid rgba(148,163,184,.18); }
    .ap-label { font-size: 9px; color: var(--muted); line-height: 1.3; }
    .scan-empty { padding: 12px; text-align: center; font-size: 11px; color: var(--muted); }
    .scan-loading-txt { padding: 14px; text-align: center; font-size: 11px; color: var(--muted); }
    .scan-cache-note { font-size: 9px; color: rgba(148,163,184,.35); text-align: right;
      padding: 0 10px 8px; }

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
      <div style="text-align:center;margin-bottom:22px;">
        <div style="font-family:'Orbitron',sans-serif;font-size:15px;font-weight:900;
             background:linear-gradient(135deg,var(--purple),var(--cyan));
             -webkit-background-clip:text;-webkit-text-fill-color:transparent;
             background-clip:text;letter-spacing:1px;">L&W PREMIUM IA SIGNS</div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px;">Acceso exclusivo para miembros</div>
      </div>
      <div class="form-group">
        <label>Email</label>
        <input type="email" id="login-email" placeholder="tu@email.com"
               onkeydown="if(event.key==='Enter') doLogin()">
      </div>
      <div class="form-group">
        <label>Contraseña</label>
        <input type="password" id="login-password" placeholder="••••••••"
               onkeydown="if(event.key==='Enter') doLogin()">
      </div>
      <div id="login-error"
           style="display:none;color:#f87171;font-size:12px;margin-bottom:12px;
                  padding:10px 12px;background:rgba(239,68,68,.07);
                  border:1px solid rgba(239,68,68,.25);border-radius:8px;"></div>
      <button class="btn btn-primary" id="btn-login" onclick="doLogin()">Ingresar</button>
    </div>

    <!-- ══ TAB 1: BOT IA ══ -->
    <div id="tab-bot" class="tab-pane">

      <!-- Sub-pantalla: SCANNER HOME (pantalla principal automática) -->
      <div id="scanner-home" class="scanner-screen active">

        <!-- Header: título + toggle temporalidad + contador -->
        <div class="home-hdr">
          <div>
            <div class="home-hdr-title">📡 ESCÁNER EN VIVO</div>
            <div class="home-refresh-info" id="home-refresh-info">Cargando...</div>
          </div>
          <div class="time-toggle">
            <button class="time-toggle-btn active" id="home-btn-m5" onclick="setHomeTime('M5')">M5</button>
            <button class="time-toggle-btn" id="home-btn-m1" onclick="setHomeTime('M1')">M1</button>
          </div>
        </div>

        <!-- 🔥 MEJOR OPORTUNIDAD ACTUAL (se rellena dinámicamente) -->
        <div id="home-best-opp"></div>

        <!-- Spinner mientras carga -->
        <div class="home-loading-wrap" id="home-loading">
          <div class="spinner"></div>
          <div>Escaneando los 10 pares principales...</div>
        </div>

        <!-- Lista de todos los activos -->
        <div id="home-asset-list" style="display:none"></div>

        <div class="disclaimer" style="margin-top:14px;">
          Análisis técnico en tiempo real · Mercado Forex oficial · Actualización cada 30 s.
        </div>
      </div>

      <!-- Sub-pantalla: resultado del análisis individual -->
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

        <button class="btn btn-secondary" style="margin-top:18px" onclick="backToHome()">← Volver al escáner</button>

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

    <!-- ══ TAB 4: SOPORTE ══ -->
    <div id="tab-soporte" class="tab-pane">
      <div class="section-title">💬 Soporte L&W</div>

      <div style="text-align:center;margin-bottom:20px;padding:18px;
           background:linear-gradient(135deg,rgba(34,197,94,.08),rgba(168,85,247,.08));
           border:1px solid rgba(34,197,94,.2);border-radius:16px;">
        <div style="font-size:28px;margin-bottom:6px;">✅</div>
        <div style="font-family:'Orbitron',sans-serif;font-size:12px;color:#4ade80;
             font-weight:700;letter-spacing:.5px;">MEMBRESÍA ACTIVA</div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px;">
          Tienes acceso completo a señales VIP, Academia y App web.
        </div>
      </div>

      <div style="font-size:12px;color:var(--muted);margin-bottom:14px;text-align:center;">
        ¿Tienes alguna duda o necesitas ayuda? Contáctanos directamente:
      </div>

      <div class="contact-options" style="margin-bottom:14px;">
        <a class="contact-btn btn-whatsapp"
           href="https://wa.me/message/XXXXXXXXXX" target="_blank"
           style="font-size:13px;padding:15px 10px;">
          💬 WhatsApp
        </a>
        <a class="contact-btn btn-telegram"
           href="https://t.me/+36KihCYd8Ww4MDVh" target="_blank"
           style="font-size:13px;padding:15px 10px;">
          ✈️ Telegram
        </a>
      </div>

      <div style="font-size:10px;color:rgba(148,163,184,.4);text-align:center;
           margin-bottom:20px;line-height:1.6;">
        Horario de soporte: Lun–Vie 9:00–18:00 (America/Sao Paulo).<br>
        Las señales son análisis técnicos automatizados, no asesoría financiera.
      </div>

      <button class="btn btn-secondary" onclick="doLogout()"
              style="width:100%;font-size:12px;padding:11px;">
        🚪 Cerrar sesión
      </button>
    </div><!-- /tab-soporte -->

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
  <button class="nav-btn" id="nav-soporte" onclick="switchTab('soporte')">
    <span class="nav-icon">💬</span>
    <span class="nav-label">Soporte</span>
  </button>
</nav>

<script>
  /* ── Reloj ── */
  function tickClock() {
    const el = document.getElementById('clock');
    if (el) el.textContent = new Date().toLocaleTimeString('es-ES', { hour12: false });
  }
  tickClock(); setInterval(tickClock, 1000);

  /* ── Token helpers ── */
  const TOKEN_KEY = 'lw_token';
  function getToken()    { return localStorage.getItem(TOKEN_KEY) || ''; }
  function saveToken(t)  { localStorage.setItem(TOKEN_KEY, t); }
  function clearToken()  { localStorage.removeItem(TOKEN_KEY); }
  function apiHeaders()  {
    return { 'Content-Type': 'application/json', 'X-Session-Token': getToken() };
  }

  /* ── Login / Logout ── */
  async function doLogin() {
    const email    = (document.getElementById('login-email').value    || '').trim();
    const password = (document.getElementById('login-password').value || '');
    const errEl    = document.getElementById('login-error');
    const btnEl    = document.getElementById('btn-login');
    errEl.style.display = 'none';
    if (!email || !password) {
      errEl.textContent   = 'Ingresa tu email y contraseña.';
      errEl.style.display = 'block'; return;
    }
    btnEl.disabled = true; btnEl.textContent = 'Verificando...';
    try {
      const resp = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      const data = await resp.json();
      if (data.ok) {
        saveToken(data.token);
        enterApp();
      } else {
        errEl.textContent   = data.error || 'Credenciales incorrectas.';
        errEl.style.display = 'block';
      }
    } catch (e) {
      errEl.textContent   = 'Error de conexión. Intenta de nuevo.';
      errEl.style.display = 'block';
    } finally {
      btnEl.disabled = false; btnEl.textContent = 'Ingresar';
    }
  }

  async function doLogout() {
    try {
      await fetch('/api/logout', { method: 'POST', headers: apiHeaders() });
    } catch (_) {}
    clearToken();
    showLogin('Sesión cerrada correctamente.');
  }

  function enterApp() {
    document.getElementById('screen-login').classList.remove('active');
    document.getElementById('bottom-nav').classList.add('visible');
    switchTab('bot');
    startScannerHome();
  }

  function showLogin(msg) {
    const TABS_ALL = ['bot', 'academia', 'resultados', 'soporte'];
    TABS_ALL.forEach(t => document.getElementById('tab-' + t).classList.remove('active'));
    document.getElementById('bottom-nav').classList.remove('visible');
    document.getElementById('screen-login').classList.add('active');
    if (msg) {
      const errEl = document.getElementById('login-error');
      errEl.textContent   = msg;
      errEl.style.display = 'block';
      errEl.style.color   = msg.startsWith('⚠️') ? '#f87171' : '#4ade80';
    }
  }

  function handle401() {
    clearToken();
    showLogin('⚠️ Tu sesión se ha abierto en otro dispositivo. Vuelve a iniciar sesión.');
  }

  /* ── Arranque: restaurar sesión si existe token ── */
  (function initSession() {
    if (getToken()) { enterApp(); }
  })();

  /* ── Tabs principales ── */
  const TABS = ['bot', 'academia', 'resultados', 'soporte'];
  function switchTab(name) {
    TABS.forEach(t => {
      document.getElementById('tab-' + t).classList.toggle('active', t === name);
      document.getElementById('nav-' + t).classList.toggle('active', t === name);
    });
    document.querySelector('.content-area').scrollTop = 0;
  }

  /* ── Scanner sub-pantallas ── */
  function showScanner(id) {
    ['scanner-home', 'scanner-signal'].forEach(s => {
      document.getElementById(s).classList.toggle('active', s === id);
    });
  }
  function backToHome() {
    resetSignal();
    showScanner('scanner-home');
  }
  function resetSignal() {
    document.getElementById('loading-wrap').style.display = 'flex';
    document.getElementById('signal-card').style.display  = 'none';
    document.getElementById('wait-card').style.display    = 'none';
    document.getElementById('error-msg').style.display    = 'none';
    document.getElementById('conf-bar').style.width       = '0%';
  }

  /* ── Análisis on-demand (desde home o desde cualquier activo) ── */
  async function startAnalysis(symbol, temporalidad) {
    if (!symbol || !temporalidad) return;
    resetSignal();
    showScanner('scanner-signal');
    try {
      const resp = await fetch('/api/analizar', {
        method: 'POST',
        headers: apiHeaders(),
        body: JSON.stringify({ symbol, temporalidad }),
      });
      if (resp.status === 401) { handle401(); return; }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      document.getElementById('loading-wrap').style.display = 'none';

      if (data.estado === 'SEÑAL') {
        renderSignal(data);
      } else if (data.estado === 'ESPERAR' || data.estado === 'LATERAL') {
        renderEsperar(data);
      } else {
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

  /* ── Helper seguro para formatear números (evita crash con undefined/null) ── */
  const formatNum = (val, dec = 1) =>
    (val !== undefined && val !== null && !isNaN(Number(val)))
      ? Number(val).toFixed(dec)
      : '--';

  function renderSignal(d) {
    const card   = document.getElementById('signal-card');
    const isCall = d.direccion === 'CALL';
    const ind    = d.indicadores || {};
    card.className = 'signal-card ' + (isCall ? 'signal-call' : 'signal-put');
    document.getElementById('sig-direction').textContent =
      isCall ? '🟢 COMPRA (CALL)  ↑' : '🔴 VENTA (PUT)  ↓';
    document.getElementById('sig-pair').textContent    = (d.activo || '') + ' · ' + (d.temporalidad || '');
    document.getElementById('sig-entrada').textContent = d.entrada || '--:--:--';
    document.getElementById('sig-exp').textContent     = (d.expiracion != null ? d.expiracion : '--') + ' min';
    document.getElementById('sig-rsi').textContent     = formatNum(ind.rsi);
    document.getElementById('sig-conf').textContent    = formatNum(d.confianza) + '%';
    document.getElementById('sig-conf').className      = 'dc-value ' + (isCall ? 'green' : 'red');
    document.getElementById('sig-motivo').textContent  = '📊 ' + (d.motivo || '');
    const conf = Number(d.confianza) || 0;
    setTimeout(() => { document.getElementById('conf-bar').style.width = conf + '%'; }, 100);
    card.style.display = 'block';
  }

  function renderEsperar(d) {
    const isLateral = d.estado === 'LATERAL';
    const cls       = isLateral ? 'lateral' : 'esperar';
    const ind       = d.indicadores || {};

    document.getElementById('wait-icon').textContent = isLateral ? '⚪' : '🟡';
    const titleEl = document.getElementById('wait-title');
    titleEl.textContent = isLateral ? 'MERCADO LATERAL — ESPERAR' : 'ESPERAR CONFIRMACIÓN';
    titleEl.className = 'wait-title ' + cls;
    document.getElementById('wait-header').className = 'wait-header ' + cls;
    document.getElementById('wait-body').className   = 'wait-body '   + cls;

    const leanEl = document.getElementById('wait-lean');
    leanEl.textContent = d.lean
      ? 'Sesgo probable: ' + (d.lean === 'CALL' ? '↑ Alcista' : '↓ Bajista')
      : 'Sin sesgo definido aún';

    document.getElementById('wi-rsi').textContent  = formatNum(ind.rsi);
    document.getElementById('wi-k').textContent    = formatNum(ind.stoch_k);
    document.getElementById('wi-d').textContent    = formatNum(ind.stoch_d);
    document.getElementById('wi-tend').textContent = ind.tendencia || '—';

    const ul = document.getElementById('wait-conditions');
    ul.innerHTML = '';
    (d.condiciones || ['Sin datos de condiciones disponibles.']).forEach(c => {
      const li = document.createElement('li');
      if (isLateral) li.className = 'lateral-bullet';
      li.textContent = c;
      ul.appendChild(li);
    });

    document.getElementById('wait-card').style.display = 'block';
  }

  /* ══ SCANNER-FIRST: escáner automático en tiempo real ══ */
  let _homeTime      = 'M5';      // temporalidad activa en el home
  let _homeTimer     = null;      // setInterval del auto-refresh (30 s)
  let _homeCountdown = null;      // setInterval del contador visual
  let _homeNextScan  = 0;         // timestamp del próximo escaneo

  function setHomeTime(t) {
    _homeTime = t;
    document.getElementById('home-btn-m5').classList.toggle('active', t === 'M5');
    document.getElementById('home-btn-m1').classList.toggle('active', t === 'M1');
    // Escanear de inmediato con la nueva temporalidad
    _stopHomeTimers();
    scanHome();
  }

  function startScannerHome() {
    _stopHomeTimers();
    scanHome();
    // Auto-refresh cada 30 s
    _homeTimer = setInterval(scanHome, 30000);
  }

  function _stopHomeTimers() {
    if (_homeTimer)     { clearInterval(_homeTimer);     _homeTimer = null; }
    if (_homeCountdown) { clearInterval(_homeCountdown); _homeCountdown = null; }
  }

  function _startCountdown() {
    _homeNextScan = Date.now() + 30000;
    if (_homeCountdown) clearInterval(_homeCountdown);
    _homeCountdown = setInterval(() => {
      const secs = Math.max(0, Math.round((_homeNextScan - Date.now()) / 1000));
      const el = document.getElementById('home-refresh-info');
      if (el) el.textContent = 'Actualiza en ' + secs + ' s · ' + _homeTime;
      if (secs === 0) clearInterval(_homeCountdown);
    }, 1000);
  }

  async function scanHome() {
    const intervalo = _homeTime === 'M1' ? '1min' : '5min';
    // Mostrar spinner, ocultar lista
    document.getElementById('home-loading').style.display = 'block';
    document.getElementById('home-asset-list').style.display = 'none';
    const infoEl = document.getElementById('home-refresh-info');
    if (infoEl) infoEl.textContent = 'Escaneando... · ' + _homeTime;

    try {
      const resp = await fetch('/api/top-assets?intervalo=' + intervalo,
                               { headers: apiHeaders() });
      if (resp.status === 401) { handle401(); return; }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      if (data.status === 'success') {
        renderBestOpp(data.top_assets);
        renderHomeAssets(data.top_assets);
        document.getElementById('home-loading').style.display = 'none';
        document.getElementById('home-asset-list').style.display = 'block';
        _startCountdown();
      }
    } catch (e) {
      document.getElementById('home-loading').style.display = 'none';
      document.getElementById('home-best-opp').innerHTML =
        '<div class="bow-wrap"><div class="bow-icon">⚠️</div>' +
        '<div class="bow-title">SIN CONEXIÓN</div>' +
        '<div class="bow-sub">No se pudo contactar el servidor.<br>Reintentando en 30 s.</div></div>';
      if (infoEl) infoEl.textContent = 'Error · Reintentando...';
    }
  }

  function renderBestOpp(assets) {
    const el = document.getElementById('home-best-opp');
    if (!el) return;
    // Buscar el primer activo confirmado (4/4 capas)
    const best = (assets || []).find(a => a.confirmed === true);
    if (!best) {
      // No hay señal confirmada ahora
      el.innerHTML =
        '<div class="bow-wrap">' +
          '<div class="bow-icon">🔍</div>' +
          '<div class="bow-title">BUSCANDO OPORTUNIDAD</div>' +
          '<div class="bow-sub">El escáner monitorea los 10 pares en tiempo real.<br>' +
          'Te avisará cuando haya una entrada de 4/4 capas.</div>' +
        '</div>';
      return;
    }
    const isCall = best.signal === 'CALL';
    const cls    = isCall ? 'call' : 'put';
    const dir    = isCall ? '🟢 CALL — COMPRA ↑' : '🔴 PUT — VENTA ↓';
    const conf   = best.confluence != null ? Number(best.confluence).toFixed(0) + '%' : '—';
    el.innerHTML =
      '<div class="boc-wrap ' + cls + '">' +
        '<div class="boc-lbl">🔥 MEJOR OPORTUNIDAD ACTUAL</div>' +
        '<div class="boc-dir ' + cls + '">' + dir + '</div>' +
        '<div class="boc-meta">' +
          '<span>' + (best.symbol || '') + ' · ' + _homeTime + '</span>' +
          '<span class="boc-conf ' + cls + '">' + conf + ' confluencia</span>' +
        '</div>' +
        '<div class="boc-reason">' + (best.reason || '') + '</div>' +
        '<button class="btn-ver-senal ' + cls + '" onclick="startAnalysis(\'' +
          (best.symbol || '').replace(/'/g, '') + '\',\'' + _homeTime + '\')">⚡ Ver Señal Completa</button>' +
      '</div>';
  }

  function renderHomeAssets(assets) {
    const listEl = document.getElementById('home-asset-list');
    if (!listEl) return;
    listEl.innerHTML = '';
    if (!assets || assets.length === 0) {
      listEl.innerHTML = '<div style="text-align:center;font-size:11px;color:var(--muted);padding:16px 0;">Sin datos disponibles.</div>';
      return;
    }
    // Pequeño título de sección
    const titleDiv = document.createElement('div');
    titleDiv.style.cssText = 'font-size:9px;font-weight:700;color:var(--muted);letter-spacing:1.2px;text-transform:uppercase;margin-bottom:8px;padding-top:4px;';
    titleDiv.textContent = 'TODOS LOS PARES';
    listEl.appendChild(titleDiv);

    assets.forEach(a => {
      const isCall      = a.signal === 'CALL';
      const isPut       = a.signal === 'PUT';
      const isConfirmed = a.confirmed === true;
      const isApiLimit  = a.signal === 'API_LIMIT';
      const badgeCls = (isCall && isConfirmed) ? 'call'
                     : (isPut  && isConfirmed) ? 'put'
                     : ((isCall || isPut) && !isConfirmed) ? 'wait'
                     : 'flat';
      const confLabel = (a.confluence != null && a.confluence > 0)
        ? Number(a.confluence).toFixed(0) + '%' : '—';

      const pill = document.createElement('div');
      pill.className = 'home-pill';
      pill.setAttribute('data-symbol', a.symbol || '');
      pill.innerHTML =
        '<div class="hp-left">' +
          '<div class="hp-symbol">' + (a.symbol || '') + '</div>' +
          '<div class="hp-reason">' + (a.reason || '') + '</div>' +
        '</div>' +
        '<div class="hp-right">' +
          '<div class="hp-badge ' + (isApiLimit ? 'flat' : badgeCls) + '">' +
            (isApiLimit ? '⚠️' : confLabel) +
          '</div>' +
          '<div class="hp-label">' + (a.status_label || '') + '</div>' +
        '</div>';
      if (!isApiLimit) {
        pill.addEventListener('click', (e) => {
          const sym = e.currentTarget.getAttribute('data-symbol');
          if (sym) startAnalysis(sym, _homeTime);
        });
      }
      listEl.appendChild(pill);
    });
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
