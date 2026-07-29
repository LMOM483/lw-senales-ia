"""
debug_momentum.py - Muestra valores exactos de RSI y Estocastico para diagnosticar
por qué Capa 2 (Momentum) bloquea siempre.
Correr: python debug_momentum.py
"""
import sys, io
try:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
except Exception:
    pass

import requests
import pandas as pd
import ta
from config.config import Config

ACTIVOS = ["AUD/JPY", "USD/JPY", "NZD/USD", "USD/CHF", "CAD/JPY"]
INTERVALOS = ["1min", "5min"]

def obtener_df(simbolo, intervalo):
    simbolo_api = simbolo.replace("/", "")
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={simbolo_api}&interval={intervalo}&outputsize=150"
        f"&apikey={Config.TWELVE_DATA_API_KEY}"
    )
    r = requests.get(url, timeout=15)
    data = r.json()
    if data.get("status") == "error" or "values" not in data:
        print(f"  ERROR API: {data.get('message','?')}")
        return None
    values = data["values"][::-1]  # invertir: mas antigua primero
    df = pd.DataFrame(values)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col])
    return df

def diagnostico_completo(simbolo, intervalo):
    df = obtener_df(simbolo, intervalo)
    if df is None:
        return

    print(f"\n{'='*60}")
    print(f"  {simbolo}  [{intervalo}]  ({len(df)} velas)")
    print(f"{'='*60}")

    # === CAPA 1: EMAs ===
    for p in (8, 13, 21, 50):
        df[f"ema{p}"] = ta.trend.EMAIndicator(df["close"], window=p).ema_indicator()
    df["ema8_slope"] = df["ema8"].diff()
    df["ema13_slope"] = df["ema13"].diff()
    curr = df.iloc[-1]
    precio = curr["close"]

    print(f"\n[CAPA 1 - Tendencia]")
    print(f"  Precio:  {precio:.5f}")
    print(f"  EMA8:    {curr['ema8']:.5f}   slope={curr['ema8_slope']:+.6f}")
    print(f"  EMA13:   {curr['ema13']:.5f}  slope={curr['ema13_slope']:+.6f}")
    print(f"  EMA21:   {curr['ema21']:.5f}")
    print(f"  EMA50:   {curr['ema50']:.5f}")
    alcista = (curr["ema8"] > curr["ema13"] > curr["ema21"] > curr["ema50"]
               and precio > curr["ema50"]
               and curr["ema8_slope"] > 0 and curr["ema13_slope"] > 0)
    bajista = (curr["ema8"] < curr["ema13"] < curr["ema21"] < curr["ema50"]
               and precio < curr["ema50"]
               and curr["ema8_slope"] < 0 and curr["ema13_slope"] < 0)
    dir_t = "CALL" if alcista else ("PUT" if bajista else "NINGUNA")
    print(f"  Orden alcista: {curr['ema8']:.5f} > {curr['ema13']:.5f} > {curr['ema21']:.5f} > {curr['ema50']:.5f} = {curr['ema8']>curr['ema13']>curr['ema21']>curr['ema50']}")
    print(f"  => Tendencia: {dir_t}")

    # === CAPA 2: Momentum ===
    rsi = ta.momentum.RSIIndicator(df["close"], window=Config.RSI_PERIOD).rsi()
    stoch = ta.momentum.StochasticOscillator(df["high"], df["low"], df["close"], window=14, smooth_window=3)
    stoch_k = stoch.stoch()
    stoch_d = stoch.stoch_signal()

    rsi_val = rsi.iloc[-1]
    rsi_prev = rsi.iloc[-2]
    k_actual = stoch_k.iloc[-1]
    d_actual = stoch_d.iloc[-1]
    k_prev = stoch_k.iloc[-2]
    d_prev = stoch_d.iloc[-2]

    cruce_k_arriba = k_prev <= d_prev and k_actual > d_actual
    cruce_k_abajo  = k_prev >= d_prev and k_actual < d_actual

    print(f"\n[CAPA 2 - Momentum]")
    print(f"  RSI ({Config.RSI_PERIOD}):  actual={rsi_val:.2f}  prev={rsi_prev:.2f}  subiendo={rsi_val > rsi_prev}")
    print(f"  Stoch %K:  actual={k_actual:.2f}  prev={k_prev:.2f}")
    print(f"  Stoch %D:  actual={d_actual:.2f}  prev={d_prev:.2f}")
    print(f"  Cruce K>D (alcista): prev({k_prev:.2f}<={d_prev:.2f}) y actual({k_actual:.2f}>{d_actual:.2f}) = {cruce_k_arriba}")
    print(f"  Cruce K<D (bajista): prev({k_prev:.2f}>={d_prev:.2f}) y actual({k_actual:.2f}<{d_actual:.2f}) = {cruce_k_abajo}")

    # Verificar condiciones CALL
    call_rsi_ok = 50 <= rsi_val <= 70 and rsi_val > rsi_prev
    call_stoch_ok = cruce_k_arriba and k_actual < 80
    put_rsi_ok  = 30 <= rsi_val <= 50 and rsi_val < rsi_prev
    put_stoch_ok  = cruce_k_abajo and k_actual > 20

    print(f"\n  Para CALL:")
    print(f"    RSI en 50-70 y subiendo: {call_rsi_ok}  (RSI={rsi_val:.2f})")
    print(f"    Cruce K arriba y K<80:   {call_stoch_ok}")
    print(f"    => Momentum CALL: {call_rsi_ok and call_stoch_ok}")

    print(f"\n  Para PUT:")
    print(f"    RSI en 30-50 y bajando:  {put_rsi_ok}  (RSI={rsi_val:.2f})")
    print(f"    Cruce K abajo y K>20:    {put_stoch_ok}")
    print(f"    => Momentum PUT:  {put_rsi_ok and put_stoch_ok}")

    # Historial últimas 5 velas para ver si hubo cruces recientes
    print(f"\n  Historial Estocástico (últimas 6 velas):")
    print(f"  {'i':>4}  {'%K':>7}  {'%D':>7}  {'K>D':>5}")
    for i in range(-6, 0):
        k = stoch_k.iloc[i]
        d = stoch_d.iloc[i]
        print(f"  {i:>4}  {k:>7.2f}  {d:>7.2f}  {'SI' if k>d else 'NO':>5}")

    print(f"\n  Historial RSI (últimas 6 velas):")
    for i in range(-6, 0):
        print(f"  {i:>4}  RSI={rsi.iloc[i]:.2f}")

if __name__ == "__main__":
    print("Diagnostico de Momentum - L&W Senales IA")
    print(f"API Key configurada: {'SI' if Config.TWELVE_DATA_API_KEY else 'NO'}")
    for activo in ACTIVOS:
        for intervalo in INTERVALOS:
            try:
                diagnostico_completo(activo, intervalo)
            except Exception as e:
                print(f"ERROR en {activo} {intervalo}: {e}")
    print("\n\nDiagnostico completo.")
