"""
bot.py - Núcleo principal - L&W Señales IA
v2: 3 estrategias combinadas (core/indicadores.py), cooldown de 2h tras pérdida,
entrada retrasada 1-3 min, reintentos HTTP con backoff, aprendizaje simple por activo.
Mantiene: sesiones, límite 8 VIP / 2 gratis, evaluación WIN/LOSS automática, anuncio y cierre.
"""

import logging
import random
import time as time_module
import requests
import pandas as pd
from datetime import datetime, timedelta
from config.config import Config
from config.database import db
from core.sessions import gestor_sesiones, TIMEZONE
from core.indicadores import evaluar_estrategias, diagnostico
from core.twelve_ws import TwelveDataPreciosWS

logger = logging.getLogger("TRADEXO")


class TradexoBot:
    def __init__(self):
        self.config = Config
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'LWSeñalesIA/2.0'})
        db.crear_tablas()
        self.log("🤖 BOT INICIALIZADO — L&W Señales IA v2")
        self.log("📡 Precio en vivo: WebSocket (REST respaldo)")

        self._pendientes = []          # señales esperando evaluación WIN/LOSS
        self._cooldown = {}            # activo -> datetime hasta cuando evitarlo (tras LOSS)
        self._tasa_acierto = {}        # activo -> tasa de acierto reciente (0.0-1.0), aprendizaje simple
        self._ultima_señal_enviada = None  # datetime de la última señal, para separar 10 min
        self._ultimo_resultado = None      # datetime del último WIN/LOSS, para pausa de 4 min tras resultado
        self._sesion_filtrada = None       # clave de la sesión ya filtrada (para no re-medir cada ciclo)
        self._activos_activos = []         # activos con movimiento de la sesión actual
        self._consecutivas_loss = 0        # LOSS seguidas en la sesión actual (freno de seguridad)
        self._sesion_pausada = False       # True si la sesión está pausada por 2 LOSS seguidos
        self._cooldown_senal_post_expiry = {}  # activo -> datetime: 5 min cooldown por par post-expiry
        self._ws_precios = TwelveDataPreciosWS(self.config.TWELVE_DATA_API_KEY)
        self._ws_precios.start()
        self._ultimo_origen_precio = "REST"

        errores = Config.validar()
        if errores:
            self.log("⚠️  Configuración incompleta:")
            for e in errores:
                self.log(f"   - {e}")

    def log(self, mensaje):
        logger.info(mensaje)

    # ── Obtención de datos con reintentos ──
    def obtener_datos_mercado(self, symbol, intervalo="5min"):
        """Devuelve un DataFrame (open, high, low, close) en orden cronológico, o None.
        intervalo por defecto '5min': alineado con expiracion M5.
        Reintenta hasta 3 veces con backoff si Twelve Data responde con error temporal."""
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol, "interval": intervalo, "apikey": self.config.TWELVE_DATA_API_KEY,
            "outputsize": 150, "format": "JSON"
        }

        espera = 1.0
        for intento in range(1, 4):
            try:
                resp = self.session.get(url, params=params, timeout=10)
                if resp.status_code in (429, 500, 502, 503, 504):
                    time_module.sleep(espera)
                    espera *= 1.5
                    continue
                if resp.status_code != 200:
                    self.log(f"❌ {symbol}: Twelve Data respondió {resp.status_code}")
                    return None

                data = resp.json()
                if "values" not in data or len(data["values"]) < 55:
                    return None

                df = pd.DataFrame(data["values"])
                for col in ("open", "high", "low", "close"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"]).sort_values("datetime").reset_index(drop=True)
                return df

            except Exception as e:
                self.log(f"❌ Error obteniendo datos {symbol} (intento {intento}/3): {e}")
                time_module.sleep(espera)
                espera *= 1.5

        return None

    def obtener_precio_actual(self, symbol):
        """Precio para entrada/cierre: WebSocket si hay tick; si no, último close REST 5min."""
        if self._ws_precios:
            p = self._ws_precios.get_precio(symbol)
            if p is not None:
                self._ultimo_origen_precio = "WS"
                return p
        df = self.obtener_datos_mercado(symbol, intervalo="5min")
        if df is None:
            self._ultimo_origen_precio = "REST"
            return None
        self._ultimo_origen_precio = "REST"
        return float(df["close"].iloc[-1])

    # ── Cooldown por activo tras una pérdida ──
    def en_cooldown(self, symbol, ahora=None):
        ahora = ahora or datetime.now(TIMEZONE)
        hasta = self._cooldown.get(symbol)
        return hasta is not None and ahora < hasta

    def aplicar_cooldown(self, symbol, ahora=None):
        ahora = ahora or datetime.now(TIMEZONE)
        self._cooldown[symbol] = ahora + timedelta(hours=self.config.COOLDOWN_HORAS_TRAS_PERDIDA)
        self.log(f"❄️  Cooldown {self.config.COOLDOWN_HORAS_TRAS_PERDIDA}h aplicado a {symbol}")

    def en_cooldown_post_expiry(self, symbol, ahora=None):
        """True si el par está en pausa de 5 min post-expiración (fix #1)."""
        ahora = ahora or datetime.now(TIMEZONE)
        hasta = self._cooldown_senal_post_expiry.get(symbol)
        return hasta is not None and ahora < hasta

    # ── Aprendizaje simple: ajusta tasa de acierto por activo (se reinicia si el bot se reinicia) ──
    def actualizar_tasa_acierto(self, symbol, gano: bool):
        alpha = 0.15
        previa = self._tasa_acierto.get(symbol, 0.5)
        self._tasa_acierto[symbol] = (1 - alpha) * previa + alpha * (1.0 if gano else 0.0)

    def _confirmar_multi_timeframe(self, symbol, direccion_m5):
        """Verifica que la tendencia M15 (EMA20) no contradiga la señal M5.
        Si EMA20 M15 esta cayendo y la senal es CALL, rechaza (y viceversa).
        Retorna True si esta confirmada o si no hay datos M15 (no bloquea)."""
        try:
            df_m15 = self.obtener_datos_mercado(symbol, intervalo="15min")
            if df_m15 is None or len(df_m15) < 30:
                return True  # sin datos M15, no bloquear

            ema20 = df_m15["close"].ewm(span=20, adjust=False).mean()
            # Comparar ultimas 3 velas M15 para confirmar tendencia
            if len(ema20) >= 3:
                tendencia_bajando = ema20.iloc[-1] < ema20.iloc[-3]
                tendencia_subiendo = ema20.iloc[-1] > ema20.iloc[-3]

                if direccion_m5 == "CALL" and tendencia_bajando:
                    return False
                if direccion_m5 == "PUT" and tendencia_subiendo:
                    return False
            return True
        except Exception:
            return True  # ante error, no bloquear

    def _confirmar_multi_timeframe_m1(self, symbol, direccion_m1):
        """Para señales M1: verifica que la EMA20 de M5 no contradiga la dirección.
        Mismo principio que M15 para M5, pero un nivel abajo: M5 confirma M1."""
        try:
            df_m5 = self.obtener_datos_mercado(symbol, intervalo="5min")
            if df_m5 is None or len(df_m5) < 25:
                return True  # sin datos M5, no bloquear

            ema20 = df_m5["close"].ewm(span=20, adjust=False).mean()
            if len(ema20) >= 3:
                tendencia_bajando = ema20.iloc[-1] < ema20.iloc[-3]
                tendencia_subiendo = ema20.iloc[-1] > ema20.iloc[-3]

                if direccion_m1 == "CALL" and tendencia_bajando:
                    return False
                if direccion_m1 == "PUT" and tendencia_subiendo:
                    return False
            return True
        except Exception:
            return True  # ante error, no bloquear

    def _proxima_apertura_m1(self, ahora):
        """Calcula la proxima apertura de vela M1 (el siguiente minuto exacto)."""
        return ahora.replace(second=0, microsecond=0) + timedelta(minutes=1)

    def _proxima_apertura_m5(self, ahora):
        """Calcula la proxima apertura de vela M5 (minutos: 00, 05, 10, 15...).
        Entra al inicio de la vela para que la expiracion M5 sea precisa."""
        min_actual = ahora.minute
        minutos_para_m5 = (5 - (min_actual % 5)) % 5
        if minutos_para_m5 == 0:
            minutos_para_m5 = 5  # si esta justo en el borde, esperar la siguiente
        return ahora + timedelta(minutes=minutos_para_m5)

    def analizar_activo(self, symbol, confianza_min, ahora=None, temporalidad="5min"):
        """Analiza un activo en la temporalidad indicada ("1min" o "5min").
        M1 → entrada en el próximo minuto, expiración 1 min.
        M5 → entrada en la próxima vela M5, expiración 5 min."""
        ahora = ahora or datetime.now(TIMEZONE)

        if self.en_cooldown(symbol, ahora):
            self.log(f"❄️  {symbol}: en cooldown, se salta este ciclo")
            return None

        df = self.obtener_datos_mercado(symbol, intervalo=temporalidad)
        if df is None:
            return None

        resultados = evaluar_estrategias(df)
        if not resultados:
            self.log(f"⏳ Sin señal ({temporalidad}): {symbol} | {diagnostico(df)}")
            return None

        direccion, confianza_base, metodo = resultados[0]

        # Confirmacion multi-timeframe según temporalidad:
        # M1 → confirmar con M5; M5 → confirmar con M15
        if temporalidad == "1min":
            if not self._confirmar_multi_timeframe_m1(symbol, direccion):
                self.log(f"❌ {symbol}: señal M1 {direccion} RECHAZADA por confirmación M5")
                return None
        else:
            if not self._confirmar_multi_timeframe(symbol, direccion):
                self.log(f"❌ {symbol}: señal M5 {direccion} RECHAZADA por confirmación M15")
                return None

        tasa = self._tasa_acierto.get(symbol, 0.5)
        bono_historial = (tasa - 0.5) * 6.0

        confianza_final = min(confianza_base + bono_historial, 95.0)

        precio_actual = float(df["close"].iloc[-1])

        label = "M1" if temporalidad == "1min" else "M5"
        self.log(
            f"📊 {symbol} [{label}]: {direccion} {confianza_final:.1f}% via {metodo} "
            f"(historial={tasa:.2f})"
        )

        if confianza_final < confianza_min:
            return None

        # Entrada y expiración según temporalidad
        if temporalidad == "1min":
            entrada = self._proxima_apertura_m1(ahora)
            expiracion_minutos = self.config.EXPIRACION_M1_MINUTOS
        else:
            entrada = self._proxima_apertura_m5(ahora)
            expiracion_minutos = self.config.EXPIRACION_MINUTOS

        return {
            "activo": symbol,
            "senal": direccion,
            "confianza": round(confianza_final, 1),
            "metodo": metodo,
            "precio": precio_actual,
            "entrada": entrada,
            "hora": ahora.strftime("%H:%M"),
            "temporalidad": label,
            "expiracion_minutos": expiracion_minutos,
        }

    def guardar_senal(self, senal):
        try:
            db.cursor.execute('''
                INSERT INTO senales (activo, direccion, confianza, rsi, precio, fecha, metodo, sesion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                senal["activo"], senal["senal"], senal["confianza"],
                0.0, senal["precio"], datetime.now().isoformat(),
                senal.get("metodo"), senal.get("sesion")
            ))
            db.conexion.commit()
            self.log(f"✅ Señal guardada: {senal['activo']} {senal['senal']}")
            return db.cursor.lastrowid
        except Exception as e:
            self.log(f"❌ Error guardando señal: {e}")
            return None

    def actualizar_resultado(self, id_senal, resultado):
        try:
            db.cursor.execute("UPDATE senales SET resultado = ? WHERE id = ?", (resultado, id_senal))
            db.conexion.commit()
        except Exception as e:
            self.log(f"❌ Error actualizando resultado: {e}")

    def evaluar_pendientes(self):
        from core.telegram import notifier
        ahora = datetime.now(TIMEZONE)
        aun_pendientes = []

        for p in self._pendientes:
            # PASO 1: si todavía no llega la hora de entrada, no hacer nada, seguir esperando
            if ahora < p["hora_entrada"]:
                aun_pendientes.append(p)
                continue

            # PASO 2: al llegar (o pasar) la hora de entrada, capturar el precio de entrada UNA vez
            if p["precio_entrada"] is None:
                precio_entrada = self.obtener_precio_actual(p["activo"])
                if precio_entrada is None:
                    aun_pendientes.append(p)  # reintentar el próximo ciclo (60s max)
                    continue
                p["precio_entrada"] = precio_entrada
                p["hora_captura_real"] = datetime.now(TIMEZONE)  # timestamp real de captura
                origen = self._ultimo_origen_precio
                retraso = (p["hora_captura_real"] - p["hora_entrada"]).total_seconds()
                self.log(
                    f"▶️  Entrada tomada {p['activo']} {p['senal']} @ {p['precio_entrada']:.5f} "
                    f"({origen}, retraso={retraso:.1f}s)"
                )

            # PASO 3: esperar hasta expira_en + 15s para que Twelve Data cierre la vela oficial
            if ahora < p["expira_en"] + timedelta(seconds=15):
                aun_pendientes.append(p)
                continue

            # PASO 4: la operacion termino — evaluar WIN/LOSS con el cierre OHLC real de Twelve Data
            # Se usa REST explícito (no WebSocket) para obtener el close de la vela ya cerrada.
            intervalo_eval = p.get("intervalo", "5min")
            df_eval = self.obtener_datos_mercado(p["activo"], intervalo=intervalo_eval)
            precio_cierre = float(df_eval["close"].iloc[-1]) if df_eval is not None else None

            if precio_cierre is None:
                p["reintentos_cierre"] = p.get("reintentos_cierre", 0) + 1
                if p["reintentos_cierre"] <= 3:
                    aun_pendientes.append(p)
                    self.log(f"⚠️  Reintento {p['reintentos_cierre']}/3: sin cierre REST {p['activo']}")
                else:
                    self.log(f"❌ Señal {p['activo']} descartada tras 3 reintentos sin datos")
                    self.actualizar_resultado(p["id"], "SIN_DATOS")
                continue

            retraso_cierre = (ahora - p["expira_en"]).total_seconds()
            self.log(
                f"📊 Evaluando {p['activo']} {p['senal']}: entrada={p['precio_entrada']:.5f} "
                f"cierre={precio_cierre:.5f} (REST {intervalo_eval}, retraso={retraso_cierre:.0f}s)"
            )
            gano = (
                precio_cierre > p["precio_entrada"] if p["senal"] == "CALL"
                else precio_cierre < p["precio_entrada"]
            )
            resultado = "WIN" if gano else "LOSS"

            self.actualizar_resultado(p["id"], resultado)
            gestor_sesiones.registrar_resultado(p["clave_sesion"], resultado)
            self.actualizar_tasa_acierto(p["activo"], gano)

            # Enviar resultado con frase motivacional al VIP
            notifier.enviar_resultado(p["activo"], p["senal"], resultado)

            # Freno de seguridad: contar LOSS consecutivas por sesión
            if gano:
                self._consecutivas_loss = 0  # WIN reinicia el contador
            else:
                self._consecutivas_loss += 1
                self.aplicar_cooldown(p["activo"], ahora)

            # Si acumula 2 LOSS seguidos, pausar la sesión
            if self._consecutivas_loss >= 2 and not self._sesion_pausada:
                self._sesion_pausada = True
                resultados_sesion = gestor_sesiones.resultados_de(p["clave_sesion"])
                self.log(f"⏸️  PAUSA TÉCNICA: 2 LOSS seguidos en sesión {p['clave_sesion']}")
                from core.telegram import notifier
                nombre_sesion_pausa = Config.SESIONES.get(p["clave_sesion"], {}).get("nombre", p["clave_sesion"])
                notifier.enviar_pausa_tecnica(
                    nombre_sesion_pausa,
                    resultados_sesion.get("ganadas", 0),
                    resultados_sesion.get("perdidas", 0)
                )

            # Cooldown 5 min por par post-expiry (fix #1): no re-analizar el mismo par
            self._cooldown_senal_post_expiry[p["activo"]] = p["expira_en"] + timedelta(minutes=5)

            self._ultimo_resultado = ahora
            self.log(
                f"🏁 Resultado {p['activo']} {p['senal']}: {resultado} "
                f"(consecutivas_loss={self._consecutivas_loss}) | "
                f"cooldown_post_expiry 5min activado"
            )

        self._pendientes = aun_pendientes

    def filtrar_activos_con_movimiento(self, activos):
        """OPCION B+: de la lista dada, descarta los PLANOS y devuelve los que tienen
        movimiento, ORDENADOS de mayor a menor volatilidad (mejores primero).
        No busca lo mas extremo por buscar riesgo; solo quita lo dormido y prioriza
        lo que tiene movimiento sano. Solo forex real (sin oro ni cripto)."""
        import ta
        UMBRAL_PLANO = 0.00030  # = 0.030%, filtra pares realmente planos
        medidos = []
        for symbol in activos:
            try:
                df = self.obtener_datos_mercado(symbol, intervalo="1min")
                if df is None or len(df) < 20:
                    continue
                atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
                vol_norm = atr.iloc[-1] / df["close"].iloc[-1]
                if vol_norm >= UMBRAL_PLANO:
                    medidos.append((symbol, vol_norm))
            except Exception:
                pass  # si falla la medicion de un par, lo saltamos
        # Ordenar de mayor a menor volatilidad (los con mas movimiento primero)
        medidos.sort(key=lambda x: x[1], reverse=True)
        resultado = [s for s, v in medidos]
        # Si TODOS salieron planos, devolver los primeros de la lista original
        return resultado if resultado else list(activos)[:5]

    def ciclo(self):
        from core.telegram import notifier
        from core.cursos import obtener_curso_hoy

        ahora = datetime.now(TIMEZONE)

        # Enviar mini curso diario al canal GRATIS (1 vez por día, antes de la primera sesión)
        if not self.config.MODO_DESARROLLO:
            if not getattr(self, "_curso_enviado_hoy", False):
                curso = obtener_curso_hoy()
                if curso:
                    notifier.enviar_curso_gratis(curso)
                    self._curso_enviado_hoy = True
                    self.log(f"📚 Mini curso enviado: {curso['titulo']}")
            # Resetear flag al cambiar de día
            if hasattr(self, "_ultimo_dia_curso") and self._ultimo_dia_curso != ahora.date():
                self._curso_enviado_hoy = False
            self._ultimo_dia_curso = ahora.date()

        self.evaluar_pendientes()

        clave_prox, datos_prox, minutos_faltan = gestor_sesiones.proxima_sesion_a_anunciar(ahora)
        if clave_prox:
            notifier.enviar_bienvenida_sesion(datos_prox["nombre"], minutos_faltan, datos_prox["activos"])
            gestor_sesiones.marcar_anunciada(clave_prox, ahora)
            self.log(f"📢 Anuncio enviado: {datos_prox['nombre']} arranca en {minutos_faltan} min")

        cerrada = gestor_sesiones.sesion_recien_cerrada(ahora)
        if cerrada:
            clave_cerrada, datos_cerrada = cerrada
            resultados = gestor_sesiones.resultados_de(clave_cerrada)
            notifier.enviar_cierre_sesion(datos_cerrada["nombre"], resultados["ganadas"], resultados["perdidas"])
            notifier.enviar_resultado_parcial(datos_cerrada["nombre"], resultados["ganadas"], resultados["perdidas"])
            self.log(f"🏁 Sesión cerrada: {datos_cerrada['nombre']} — {resultados}")

        clave_sesion, datos_sesion = gestor_sesiones.sesion_activa(ahora)
        if datos_sesion is None:
            self.log("⏸️  Fuera de sesión. Esperando...")
            self._sesion_filtrada = None  # resetear al salir de sesión
            if self._ws_precios:
                self._ws_precios.set_symbols([])
            return

        nombre_sesion = datos_sesion["nombre"]

        # OPCION B+: al entrar a una sesion nueva, revisar TODO el pool de forex
        # y quedarse con los que tienen movimiento (descarta planos automaticamente).
        # Asi Lina no elige a mano y no se pierde activos buenos que no estaban en la lista.
        if getattr(self, "_sesion_filtrada", None) != clave_sesion:
            # Nueva sesión: reiniciar contadores de seguridad
            self._consecutivas_loss = 0
            self._sesion_pausada = False
            self._cooldown_senal_post_expiry = {}   # limpiar cooldowns de sesión anterior
            pool = getattr(self.config, "POOL_FOREX", None) or datos_sesion["activos"]
            activos_buenos = self.filtrar_activos_con_movimiento(pool)
            self._activos_activos = activos_buenos[:10]
            self._sesion_filtrada = clave_sesion
            if self._ws_precios:
                self._ws_precios.set_symbols(self._activos_activos)
            self.log(f"🔍 {nombre_sesion}: operando {self._activos_activos}")
            planos = len(pool) - len(activos_buenos)
            if planos > 0:
                self.log(f"   ({planos} activos planos descartados del pool de {len(pool)})")

        # Usar solo los activos con movimiento (elegidos del pool al inicio de la sesion)
        datos_sesion = dict(datos_sesion)
        datos_sesion["activos"] = getattr(self, "_activos_activos", datos_sesion["activos"])

        # Pausa tras un resultado: dar respiro entre el WIN/LOSS y la siguiente señal
        if self._ultimo_resultado is not None:
            minutos_desde_resultado = (ahora - self._ultimo_resultado).total_seconds() / 60
            if minutos_desde_resultado < self.config.MINUTOS_PAUSA_TRAS_RESULTADO:
                faltan = round(self.config.MINUTOS_PAUSA_TRAS_RESULTADO - minutos_desde_resultado)
                self.log(f"⏸️  Pausa tras resultado ({faltan} min para la próxima señal)")
                return

        # Freno de seguridad: si la sesión está pausada por 2 LOSS seguidos, no enviar más señales
        if self._sesion_pausada:
            self.log(f"⏸️  Sesión {nombre_sesion} en pausa técnica (2 LOSS consecutivos). Retomamos en la próxima sesión.")
            return

        for activo in datos_sesion["activos"]:
            # Separación mínima entre señales: no mandar otra hasta que pasen los minutos configurados
            if self._ultima_señal_enviada is not None:
                minutos_desde_ultima = (ahora - self._ultima_señal_enviada).total_seconds() / 60
                if minutos_desde_ultima < self.config.MINUTOS_ENTRE_SEÑALES:
                    faltan = round(self.config.MINUTOS_ENTRE_SEÑALES - minutos_desde_ultima)
                    self.log(f"⏸️  Esperando separación entre señales ({faltan} min restantes)")
                    break  # no procesar más activos este ciclo

            # Cooldown 5 min post-expiry por par (fix #1): saltar si el par aún no enfría
            if self.en_cooldown_post_expiry(activo, ahora):
                hasta = self._cooldown_senal_post_expiry[activo]
                faltan = round((hasta - ahora).total_seconds() / 60, 1)
                self.log(f"⏳ {activo}: cooldown 5min post-expiry ({faltan} min restantes)")
                continue

            # Analizar en M5 primero (más fiable), luego M1 como alternativa.
            # Si M5 tiene señal la preferimos; si no, usamos M1.
            senal = None
            for temporalidad in ["5min", "1min"]:
                candidato = self.analizar_activo(
                    activo, datos_sesion["confianza_min"], ahora, temporalidad
                )
                if candidato:
                    senal = candidato
                    break  # con la primera temporalidad que dé señal válida alcanza

            if not senal:
                continue

            senal["sesion"] = nombre_sesion
            id_senal = self.guardar_senal(senal)

            enviada = False
            if gestor_sesiones.puede_enviar(clave_sesion, "gratis"):
                if notifier.enviar_senal_gratis(senal, nombre_sesion):
                    gestor_sesiones.registrar_envio(clave_sesion, "gratis")
                    enviada = True
            else:
                self.log(f"🛑 Límite GRATIS alcanzado en {nombre_sesion}")

            if gestor_sesiones.puede_enviar(clave_sesion, "vip"):
                if notifier.enviar_senal_vip(senal, nombre_sesion):
                    gestor_sesiones.registrar_envio(clave_sesion, "vip")
                    enviada = True
            else:
                self.log(f"🛑 Límite VIP alcanzado en {nombre_sesion}")

            if enviada and id_senal:
                self._ultima_señal_enviada = ahora  # marca el momento para separar la próxima
                # La operación NO empieza cuando se genera la señal, sino en la hora de ENTRADA.
                # Expiración según la temporalidad de la señal (1 min para M1, 5 min para M5).
                self._pendientes.append({
                    "id": id_senal,
                    "activo": activo,
                    "senal": senal["senal"],
                    "precio_entrada": None,  # se captura al llegar la hora de entrada, no antes
                    "hora_entrada": senal["entrada"],
                    "clave_sesion": clave_sesion,
                    "expira_en": senal["entrada"] + timedelta(minutes=senal["expiracion_minutos"]),
                    "intervalo": "1min" if senal["temporalidad"] == "M1" else "5min",
                })
                break  # solo UNA señal por ciclo, para respetar la separación entre señales


if __name__ == "__main__":
    bot = TradexoBot()
    bot.ciclo()
