"""
core/sessions.py - Control de sesión activa + límite de señales + anuncio previo + cierre con resumen.
"""
from datetime import datetime, timedelta
import pytz
from config.config import Config

TIMEZONE = pytz.timezone(Config.TIMEZONE)


class GestorSesiones:
    def __init__(self):
        self._contador = {}          # "clave:canal" -> cantidad enviada
        self._resultados = {}        # "clave" -> {"ganadas": N, "perdidas": N}
        self._anunciadas = {}        # "clave" -> fecha (str) en que ya se anunció
        self._cerradas = {}          # "clave" -> fecha (str) en que ya se cerró
        self._ultima_sesion_activa = None  # para detectar el momento exacto en que una sesión termina

    def sesion_activa(self, ahora=None):
        """Devuelve (clave, datos) de la sesión activa, o (None, None) si no hay ninguna."""
        ahora = ahora or datetime.now(TIMEZONE)
        hora_actual = ahora.time()
        for clave, datos in Config.SESIONES.items():
            if datos["inicio"] <= hora_actual <= datos["fin"]:
                return clave, datos
        return None, None

    def puede_enviar(self, clave_sesion: str, canal: str) -> bool:
        contador_key = f"{clave_sesion}:{canal}"
        enviadas = self._contador.get(contador_key, 0)
        limite = Config.MAX_SEÑALES_VIP_POR_SESION if canal == "vip" else Config.MAX_SEÑALES_GRATIS_POR_SESION
        return enviadas < limite

    def registrar_envio(self, clave_sesion: str, canal: str):
        contador_key = f"{clave_sesion}:{canal}"
        self._contador[contador_key] = self._contador.get(contador_key, 0) + 1

    def registrar_resultado(self, clave_sesion: str, resultado: str):
        """resultado: 'WIN' o 'LOSS'"""
        if clave_sesion not in self._resultados:
            self._resultados[clave_sesion] = {"ganadas": 0, "perdidas": 0}
        if resultado == "WIN":
            self._resultados[clave_sesion]["ganadas"] += 1
        else:
            self._resultados[clave_sesion]["perdidas"] += 1

    def resultados_de(self, clave_sesion: str) -> dict:
        return self._resultados.get(clave_sesion, {"ganadas": 0, "perdidas": 0})

    def resumen(self):
        return dict(self._contador)

    # ── Anuncio previo (10 min antes de que abra la sesión) ──
    def proxima_sesion_a_anunciar(self, ahora=None, minutos_antes=None):
        """Si hay una sesión que arranca en <= minutos_antes y aún no fue anunciada hoy, la devuelve."""
        ahora = ahora or datetime.now(TIMEZONE)
        minutos_antes = minutos_antes if minutos_antes is not None else Config.ANUNCIO_MINUTOS_ANTES
        hoy = ahora.date().isoformat()

        for clave, datos in Config.SESIONES.items():
            if self._anunciadas.get(clave) == hoy:
                continue  # ya se anunció hoy

            inicio_hoy = ahora.replace(
                hour=datos["inicio"].hour, minute=datos["inicio"].minute, second=0, microsecond=0
            )
            faltan = (inicio_hoy - ahora).total_seconds() / 60

            if 0 <= faltan <= minutos_antes:
                return clave, datos, round(faltan)
        return None, None, None

    def marcar_anunciada(self, clave_sesion: str, ahora=None):
        ahora = ahora or datetime.now(TIMEZONE)
        self._anunciadas[clave_sesion] = ahora.date().isoformat()

    # ── Cierre de sesión (justo cuando termina) ──
    def sesion_recien_cerrada(self, ahora=None):
        """Detecta la transición activa->inactiva. Devuelve (clave, datos) de la que se acaba de cerrar, o None."""
        ahora = ahora or datetime.now(TIMEZONE)
        clave_actual, _ = self.sesion_activa(ahora)
        hoy = ahora.date().isoformat()

        cerrada = None
        if self._ultima_sesion_activa and clave_actual != self._ultima_sesion_activa:
            clave_previa = self._ultima_sesion_activa
            if self._cerradas.get(clave_previa) != hoy:
                cerrada = (clave_previa, Config.SESIONES[clave_previa])
                self._cerradas[clave_previa] = hoy

        self._ultima_sesion_activa = clave_actual
        return cerrada


gestor_sesiones = GestorSesiones()
