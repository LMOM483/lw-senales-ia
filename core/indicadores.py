"""
core/indicadores.py - Estrategias tecnicas con CONFLUENCIA de 4 capas - L&W Senales IA
Filosofia: pocas senales pero de calidad. Solo se manda senal cuando coinciden:
  CAPA 1 (Tendencia):    EMA alineadas + slope + arbitro EMA50
  CAPA 2 (Momentum):     RSI + Estocastico (%K cruzando %D) apuntando en la misma direccion
  CAPA 3 (Volatilidad):  hay volatilidad real (ATR) y no esta agotado
  CAPA 4 (Divergencia):  divergencia RSI — confirmacion premium, suma confianza sin bloquear
Basado en la investigacion de canales que ganan: exigir confluencia = menos senales, mas aciertos.
"""
import pandas as pd
import ta
from config.config import Config


def _hay_volatilidad(df: pd.DataFrame) -> bool:
    """CAPA 3a: solo operar si hay volatilidad REAL, no en mercado plano."""
    try:
        if len(df) < 40:
            return False
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"]).average_true_range()
        atr_actual = atr.iloc[-1]
        atr_promedio = atr.rolling(20).mean().iloc[-1]
        if pd.isna(atr_actual) or pd.isna(atr_promedio) or atr_promedio == 0:
            return False
        return atr_actual >= atr_promedio * 0.80
    except Exception:
        return False


def _capa_tendencia(df: pd.DataFrame):
    """CAPA 1: direccion de la tendencia segun EMAs alineadas + slope + EMA50."""
    if len(df) < 55:
        return None
    for p in (8, 13, 21, 50):
        df[f"ema{p}"] = ta.trend.EMAIndicator(df["close"], window=p).ema_indicator()
    df["ema8_slope"] = df["ema8"].diff()
    df["ema13_slope"] = df["ema13"].diff()

    curr = df.iloc[-1]
    if pd.isna(curr["ema50"]) or pd.isna(curr["ema8_slope"]):
        return None

    precio = curr["close"]
    alcista = (curr["ema8"] > curr["ema13"] > curr["ema21"] > curr["ema50"]
               and precio > curr["ema50"]
               and curr["ema8_slope"] > 0 and curr["ema13_slope"] > 0)
    bajista = (curr["ema8"] < curr["ema13"] < curr["ema21"] < curr["ema50"]
               and precio < curr["ema50"]
               and curr["ema8_slope"] < 0 and curr["ema13_slope"] < 0)

    if alcista:
        return "CALL"
    if bajista:
        return "PUT"
    return None


def _capa_momentum(df: pd.DataFrame):
    """CAPA 2: momentum con RSI + Estocastico cruzando %K sobre %D.
    RSI rapido (window=4) con zonas restringidas para evitar sobrecompra/sobreventa.
    Estocastico DEBE tener %K cruzando %D en la direccion de la senal."""
    if len(df) < 35:
        return None
    rsi = ta.momentum.RSIIndicator(df["close"], window=Config.RSI_PERIOD).rsi()
    stoch = ta.momentum.StochasticOscillator(
        df["high"], df["low"], df["close"],
        window=14, smooth_window=3
    )
    stoch_k = stoch.stoch()
    stoch_d = stoch.stoch_signal()

    if (pd.isna(rsi.iloc[-1]) or pd.isna(stoch_k.iloc[-1]) or pd.isna(stoch_d.iloc[-1])
            or pd.isna(rsi.iloc[-2]) or pd.isna(stoch_k.iloc[-2]) or pd.isna(stoch_d.iloc[-2])):
        return None

    rsi_val = rsi.iloc[-1]
    rsi_prev = rsi.iloc[-2]
    k_actual = stoch_k.iloc[-1]
    d_actual = stoch_d.iloc[-1]
    k_prev = stoch_k.iloc[-2]
    d_prev = stoch_d.iloc[-2]

    # CALL: RSI 50-70 (fuerza alcista sin agotamiento)
    # Estocastico: %K cruza POR ENCIMA de %D (cruce alcista real) y K < 80
    cruce_k_arriba = k_prev <= d_prev and k_actual > d_actual  # cruce real K sobre D
    if 50 <= rsi_val <= 70 and rsi_val > rsi_prev and cruce_k_arriba and k_actual < 80:
        return "CALL"

    # PUT: RSI 30-50 (fuerza bajista sin rebote)
    # Estocastico: %K cruza POR DEBAJO de %D (cruce bajista real) y K > 20
    cruce_k_abajo = k_prev >= d_prev and k_actual < d_actual  # cruce real K bajo D
    if 30 <= rsi_val <= 50 and rsi_val < rsi_prev and cruce_k_abajo and k_actual > 20:
        return "PUT"
    return None


def _cruce_reciente_ema(df: pd.DataFrame, direccion: str) -> bool:
    """Detecta si hubo un CRUCE reciente de EMA9 sobre EMA21 (o viceversa) en las ultimas velas."""
    try:
        if len(df) < 25:
            return False
        ema9 = ta.trend.EMAIndicator(df["close"], window=9).ema_indicator()
        ema21 = ta.trend.EMAIndicator(df["close"], window=21).ema_indicator()
        for i in range(1, 4):
            antes = ema9.iloc[-i-1] - ema21.iloc[-i-1]
            ahora = ema9.iloc[-i] - ema21.iloc[-i]
            if pd.isna(antes) or pd.isna(ahora):
                continue
            if direccion == "CALL" and antes <= 0 and ahora > 0:
                return True
            if direccion == "PUT" and antes >= 0 and ahora < 0:
                return True
        return False
    except Exception:
        return False


def _capa_macd(df: pd.DataFrame):
    """CAPA 2b (apoyo): confirma con MACD."""
    if len(df) < 35:
        return None
    macd = ta.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
    macd_linea = macd.macd()
    macd_senal = macd.macd_signal()
    if pd.isna(macd_linea.iloc[-1]) or pd.isna(macd_senal.iloc[-1]):
        return None
    if macd_linea.iloc[-1] > macd_senal.iloc[-1]:
        return "CALL"
    if macd_linea.iloc[-1] < macd_senal.iloc[-1]:
        return "PUT"
    return None


def _vela_rechazo_contraria(df: pd.DataFrame, direccion: str) -> bool:
    """Detecta si la vela anterior tiene mecha larga en la direccion OPUESTA a la senal."""
    if len(df) < 3:
        return True
    try:
        vela = df.iloc[-2]
        body = abs(vela["close"] - vela["open"])
        wick_sup = vela["high"] - max(vela["close"], vela["open"])
        wick_inf = min(vela["close"], vela["open"]) - vela["low"]

        if body == 0:
            return True

        ratio_sup = wick_sup / body
        ratio_inf = wick_inf / body

        if direccion == "CALL" and ratio_sup > 1.5:
            return True
        if direccion == "PUT" and ratio_inf > 1.5:
            return True
        return False
    except Exception:
        return True


def _vela_confirmacion_fuerte(df: pd.DataFrame, direccion: str) -> bool:
    """Fix #4: la ULTIMA vela (la que confirma la señal) debe cerrar con cuerpo real
    a favor de la tendencia y sin mecha de rechazo dominante en la dirección contraria.

    Requisitos:
    - El cuerpo (|close - open|) debe ser >= 35% del rango total (high - low)
    - La vela debe cerrar EN la dirección de la señal (close > open para CALL)
    - La mecha contraria NO debe superar el 80% del cuerpo

    Devuelve True si la vela es válida (señal pasa), False si bloquea.
    """
    try:
        if len(df) < 2:
            return True
        vela = df.iloc[-1]
        rango = vela["high"] - vela["low"]
        if rango == 0:
            return True   # vela doji — no bloquear, dejar que otras capas decidan
        cuerpo = abs(vela["close"] - vela["open"])

        # Condición 1: cuerpo mínimo del 35% del rango
        if cuerpo / rango < 0.35:
            return False

        # Condición 2: cierre en la dirección correcta
        if direccion == "CALL" and vela["close"] <= vela["open"]:
            return False
        if direccion == "PUT" and vela["close"] >= vela["open"]:
            return False

        # Condición 3: mecha contraria no mayor al 80% del cuerpo
        if cuerpo > 0:
            mecha_sup = vela["high"] - max(vela["close"], vela["open"])
            mecha_inf = min(vela["close"], vela["open"]) - vela["low"]
            if direccion == "CALL" and mecha_inf > cuerpo * 0.80:
                return False   # mecha bajista larga en señal alcista
            if direccion == "PUT" and mecha_sup > cuerpo * 0.80:
                return False   # mecha alcista larga en señal bajista

        return True
    except Exception:
        return True   # ante error, no bloquear


def _divergencia_rsi(df: pd.DataFrame, direccion: str) -> bool:
    """CAPA 4 (confirmacion premium): detecta divergencia RSI clasica en las ultimas velas.

    Divergencia ALCISTA (CALL):
      Precio hace un minimo MAS BAJO que el minimo anterior,
      pero el RSI en ese mismo punto es MAS ALTO -> mercado perdiendo fuerza bajista.

    Divergencia BAJISTA (PUT):
      Precio hace un maximo MAS ALTO que el maximo anterior,
      pero el RSI en ese mismo punto es MAS BAJO -> mercado perdiendo fuerza alcista.

    No bloquea la senal si no hay divergencia — solo suma confianza cuando la detecta.
    Ventana de busqueda: ultimas 25 velas, requiere al menos 2 pivotes comparables.
    """
    try:
        if len(df) < 30:
            return False

        rsi = ta.momentum.RSIIndicator(df["close"], window=Config.RSI_PERIOD).rsi()

        # Trabajar con las ultimas 25 velas
        precio = df["close"].iloc[-25:].reset_index(drop=True)
        rsi_vals = rsi.iloc[-25:].reset_index(drop=True)

        if rsi_vals.isna().sum() > 3:
            return False

        if direccion == "CALL":
            # Divergencia alcista: buscar 2 minimos locales de precio
            minimos = []
            for i in range(1, len(precio) - 1):
                if precio.iloc[i] < precio.iloc[i - 1] and precio.iloc[i] < precio.iloc[i + 1]:
                    if not pd.isna(rsi_vals.iloc[i]):
                        minimos.append((precio.iloc[i], rsi_vals.iloc[i]))
            if len(minimos) >= 2:
                m1_precio, m1_rsi = minimos[-2]   # minimo mas antiguo
                m2_precio, m2_rsi = minimos[-1]   # minimo mas reciente
                # Divergencia: precio baja, RSI sube
                if m2_precio < m1_precio and m2_rsi > m1_rsi:
                    return True

        elif direccion == "PUT":
            # Divergencia bajista: buscar 2 maximos locales de precio
            maximos = []
            for i in range(1, len(precio) - 1):
                if precio.iloc[i] > precio.iloc[i - 1] and precio.iloc[i] > precio.iloc[i + 1]:
                    if not pd.isna(rsi_vals.iloc[i]):
                        maximos.append((precio.iloc[i], rsi_vals.iloc[i]))
            if len(maximos) >= 2:
                m1_precio, m1_rsi = maximos[-2]   # maximo mas antiguo
                m2_precio, m2_rsi = maximos[-1]   # maximo mas reciente
                # Divergencia: precio sube, RSI baja
                if m2_precio > m1_precio and m2_rsi < m1_rsi:
                    return True

        return False
    except Exception:
        return False


def evaluar_estrategias(df: pd.DataFrame) -> list:
    """CONFLUENCIA DE 4 CAPAS: solo devuelve senal si las 3 capas base coinciden.
    Capa 4 (divergencia RSI) es opcional — suma confianza sin bloquear.
    Devuelve [(direccion, confianza, metodo)] o []."""

    if not _hay_volatilidad(df.copy()):
        return []

    dir_tendencia = _capa_tendencia(df.copy())
    if dir_tendencia is None:
        return []

    dir_momentum = _capa_momentum(df.copy())
    if dir_momentum is None:
        return []

    if dir_tendencia != dir_momentum:
        return []

    direccion = dir_tendencia

    dir_macd = _capa_macd(df.copy())
    if dir_macd != direccion:
        return []

    if _vela_rechazo_contraria(df.copy(), direccion):
        return []

    if not _vela_confirmacion_fuerte(df.copy(), direccion):
        return []

    hay_cruce      = _cruce_reciente_ema(df.copy(), direccion)
    hay_divergencia = _divergencia_rsi(df.copy(), direccion)

    # Confianza base segun cruce de EMA
    if hay_cruce:
        confianza = 90.0
        metodo = "MACD + cruce EMA (premium)"
    else:
        confianza = 82.0
        metodo = "MACD + tendencia + momentum"

    # Capa 4: divergencia RSI suma +4% y etiqueta el metodo como ULTRA
    if hay_divergencia:
        confianza = min(confianza + 4.0, 95.0)
        metodo += " + divergencia RSI"

    return [(direccion, confianza, metodo)]


def diagnostico(df: pd.DataFrame) -> str:
    """Muestra el estado de cada capa, para ver por que hay o no hay senal."""
    try:
        vol = _hay_volatilidad(df.copy())
        t = _capa_tendencia(df.copy())
        m = _capa_momentum(df.copy())
        mc = _capa_macd(df.copy())
        rechazo = _vela_rechazo_contraria(df.copy(), t) if t else "N/A"
        vela_ok = _vela_confirmacion_fuerte(df.copy(), t) if t else "N/A"
        macd_ok = "OK" if mc == t else "FALTA"
        div = _divergencia_rsi(df.copy(), t) if t else False
        return (f"Vol={'SI' if vol else 'NO'} | T={t or '-'} | M={m or '-'} | "
                f"MACD={mc or '-'}({macd_ok}) | Rechazo={rechazo} | "
                f"VelaFuerte={'SI' if vela_ok else 'NO'} | "
                f"DivRSI={'SI +4%' if div else 'NO'}")
    except Exception as e:
        return f"(diagnostico no disponible: {e})"
