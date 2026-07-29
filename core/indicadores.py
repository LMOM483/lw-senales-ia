"""
core/indicadores.py - Estrategias tecnicas con CONFLUENCIA de 3 capas - L&W Senales IA
Filosofia: pocas senales pero de calidad. Solo se manda senal cuando coinciden:
  CAPA 1 (Tendencia): EMA alineadas + slope + arbitro EMA50
  CAPA 2 (Momentum): RSI + Estocastico (%K cruzando %D) apuntando en la misma direccion
  CAPA 3 (Volatilidad/timing): hay volatilidad real (ATR) y no esta agotado
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


def evaluar_estrategias(df: pd.DataFrame) -> list:
    """CONFLUENCIA DE 3 CAPAS: solo devuelve senal si las capas coinciden.
    MACD es OBLIGATORIO. Vela anterior no puede ser de rechazo contrario.
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

    hay_cruce = _cruce_reciente_ema(df.copy(), direccion)

    if hay_cruce:
        confianza = 90.0
        metodo = "MACD + cruce EMA (premium)"
    else:
        confianza = 82.0
        metodo = "MACD + tendencia + momentum"

    return [(direccion, confianza, metodo)]


def diagnostico(df: pd.DataFrame) -> str:
    """Muestra el estado de cada capa, para ver por que hay o no hay senal."""
    try:
        vol = _hay_volatilidad(df.copy())
        t = _capa_tendencia(df.copy())
        m = _capa_momentum(df.copy())
        mc = _capa_macd(df.copy())
        rechazo = _vela_rechazo_contraria(df.copy(), t) if t else "N/A"
        macd_ok = "OK" if mc == t else "FALTA"
        return (f"Vol={'SI' if vol else 'NO'} | T={t or '-'} | M={m or '-'} | "
                f"MACD={mc or '-'}({macd_ok}) | Rechazo={rechazo}")
    except Exception as e:
        return f"(diagnostico no disponible: {e})"
