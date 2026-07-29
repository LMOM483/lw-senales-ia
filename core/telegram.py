"""
core/telegram.py - Gestión profesional de canales GRATIS y VIP - L&W Señales IA
Corregido: chat_id_vip y chat_id_gratis ahora vienen de .env y son distintos.
"""

import random
import requests
from config.config import Config

# Frases motivacionales/educativas para WIN (disciplina y consistencia)
FRASES_WIN = [
    "La disciplina paga más que la suerte.",
    "Un buen trader no apuesta, ejecuta un plan.",
    "La constancia vence al talento desordenado.",
    "Cada WIN confirma que el proceso funciona.",
    "Ganar con sistema es diferente a ganar por azar.",
    "La paciencia tiene su recompensa en el trading.",
    "No es suerte, es estrategia aplicada con calma.",
    "El éxito viene de repetir lo correcto, no de una sola jugada.",
    "Controlar la emoción es la verdadera ganancia.",
    "Un paso a la vez, sin prisa pero sin pausa.",
]

# Frases educativas para LOSS (gestión de riesgo, sin incitar a recuperar)
FRASES_LOSS = [
    "Una pérdida controlada es parte del plan.",
    "Nunca persigas recuperar con apuestas grandes.",
    "El mercado no siempre coopera, eso es normal.",
    "Gestionar pérdidas es más importante que buscar ganancias.",
    "La próxima oportunidad ya está en camino.",
    "Lo importante es proteger el capital, no venganza.",
    "Loss no es fracaso, es aprendizaje con datos.",
    "El mercado premia la paciencia, no la prisa.",
    "Cada LOSS reduce el tamaño de la próxima operación.",
    "Disciplina hoy, resultados mañana.",
]


# Nombres como aparecen en Quotex (para que el usuario los encuentre fácil en la plataforma)
NOMBRE_QUOTEX = {
    "XAU/USD": "XAU/USD (ORO)",
    "XAG/USD": "XAG/USD (PLATA)",
    "BTC/USD": "BTC/USD (BITCOIN)",
    "ETH/USD": "ETH/USD (ETHEREUM)",
    "BNB/USD": "BNB/USD (BINANCE COIN)",
    "SOL/USD": "SOL/USD (SOLANA)",
    "XRP/USD": "XRP/USD (RIPPLE)",
}


def nombre_para_quotex(activo: str) -> str:
    """Devuelve el nombre del activo aclarando cómo se llama en Quotex si aplica."""
    return NOMBRE_QUOTEX.get(activo, activo)


class TelegramNotifier:
    """Gestor profesional de Telegram"""

    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id_gratis = Config.TELEGRAM_CHAT_ID_GRATIS
        self.chat_id_vip = Config.TELEGRAM_CHAT_ID_VIP
        self.url_base = f"https://api.telegram.org/bot{self.token}"

    def enviar_mensaje(self, texto, chat_id):
        """Envía mensaje a Telegram. En MODO_DESARROLLO ignora el canal GRATIS."""
        from config.config import Config
        if Config.MODO_DESARROLLO and chat_id == self.chat_id_gratis:
            return True
        if not self.token or not chat_id:
            print(f"[MODO PRUEBA - falta token o chat_id] Se enviaría a '{chat_id}':\n{texto}\n")
            return True
        try:
            url = f"{self.url_base}/sendMessage"
            payload = {"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                print(f"✅ Enviado a Telegram (chat_id {chat_id})")
            else:
                print(f"❌ Telegram respondió {resp.status_code}: {resp.text}")
            return resp.status_code == 200
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

    def enviar_senal_gratis(self, senal, nombre_sesion):
        emoji = "🟢" if senal["senal"] == "CALL" else "🔴"
        texto = "COMPRA ↑" if senal["senal"] == "CALL" else "VENTA ↓"
        temporalidad = senal.get("temporalidad", "M5")
        exp_min = senal.get("expiracion_minutos", Config.EXPIRACION_MINUTOS)
        mensaje = (
            f"🚀 <b>L&W Señales IA — GRATIS</b>\n"
            f"Sesión: {nombre_sesion}\n\n"
            f"📊 <b>{nombre_para_quotex(senal['activo'])}</b>\n"
            f"{emoji} {texto}\n"
            f"⏰ Entrada: {senal['entrada'].strftime('%H:%M')}\n"
            f"⏱️ Temporalidad: <b>{temporalidad}</b> ({exp_min} min)\n\n"
            f"🔒 <i>¿Ganó o perdió? Resultado y análisis solo para VIP.</i>\n"
            f"<a href='https://t.me/+36KihCYd8Ww4MDVh'>✅ ACCESO VIP $20/mes</a>"
        )
        return self.enviar_mensaje(mensaje, self.chat_id_gratis)

    def enviar_senal_vip(self, senal, nombre_sesion):
        emoji = "🟢" if senal["senal"] == "CALL" else "🔴"
        texto = "COMPRA ↑" if senal["senal"] == "CALL" else "VENTA ↓"
        temporalidad = senal.get("temporalidad", "M5")
        exp_min = senal.get("expiracion_minutos", Config.EXPIRACION_MINUTOS)
        mensaje = (
            f"🚀 <b>L&W Señales IA — VIP</b>\n"
            f"Sesión: {nombre_sesion}\n\n"
            f"📊 <b>{nombre_para_quotex(senal['activo'])}</b>\n"
            f"{emoji} {texto}\n"
            f"⏰ Entrada: <b>{senal['entrada'].strftime('%H:%M')}</b>\n"
            f"⏱️ Temporalidad: <b>{temporalidad}</b> ({exp_min} min)\n"
            f"🎯 Confianza técnica: <b>{senal['confianza']:.1f}%</b>\n"
            f"🔎 Método: {senal['metodo']}\n\n"
            f"<i>La confianza es una medida técnica, no una garantía de resultado.</i>"
        )
        return self.enviar_mensaje(mensaje, self.chat_id_vip)

    def enviar_bienvenida_inicio(self):
        """Mensaje que se envía UNA vez cuando el bot arranca, con horarios y gestión de riesgo."""
        mensaje = (
            f"🚀 <b>L&W Señales IA — ACTIVO</b>\n\n"
            f"¡Bienvenidos! El sistema está en línea y listo para operar.\n\n"
            f"🕐 <b>HORARIOS DE SESIONES (hora Brasil UTC-3):</b>\n"
            f"🌅 EUROPA GOLD — 06:00 a 10:00\n"
            f"🏙️ NY POWER — 10:00 a 13:00\n"
            f"🔥 OVERLAP L&W — 13:00 a 17:00\n\n"
            f"📊 Cada señal indica par, dirección, hora de entrada y temporalidad.\n"
            f"⏱️ Espera la hora de entrada exacta antes de operar.\n\n"
            f"⚠️ <b>GESTIÓN DE RIESGO:</b> invierte máximo 2-5% de tu capital por operación. "
            f"Nunca persigas pérdidas. Opera bajo tu propio riesgo.\n\n"
            f"💎 <a href='https://t.me/+36KihCYd8Ww4MDVh'>ACCESO VIP $20/mes</a>"
        )
        self.enviar_mensaje(mensaje, self.chat_id_gratis)
        self.enviar_mensaje(mensaje, self.chat_id_vip)

    def enviar_bienvenida_sesion(self, nombre_sesion, minutos_faltantes, activos):
        """Aviso previo, antes de que abra la sesión."""
        lista_activos = ", ".join(activos)
        mensaje_gratis = (
            f"⏰ <b>L&W Señales IA</b>\n\n"
            f"La sesión <b>{nombre_sesion}</b> arranca en <b>{minutos_faltantes} min</b>.\n"
            f"Prepárate — activos de hoy: {lista_activos}\n\n"
            f"💎 <i>¿Quieres todas las señales de la sesión?</i>\n"
            f"<a href='https://t.me/+36KihCYd8Ww4MDVh'>✅ ACCESO VIP $20/mes</a>"
        )
        mensaje_vip = (
            f"⏰ <b>L&W Señales IA — VIP</b>\n\n"
            f"La sesión <b>{nombre_sesion}</b> arranca en <b>{minutos_faltantes} min</b>.\n"
            f"Prepárate — activos de hoy: {lista_activos}"
        )
        self.enviar_mensaje(mensaje_gratis, self.chat_id_gratis)
        self.enviar_mensaje(mensaje_vip, self.chat_id_vip)

    def enviar_cierre_sesion(self, nombre_sesion, ganadas, perdidas):
        """Resumen al cerrar la sesión."""
        total = ganadas + perdidas
        efectividad = round((ganadas / total * 100), 1) if total > 0 else 0
        mensaje = (
            f"🏁 <b>Sesión {nombre_sesion} — CERRADA</b>\n\n"
            f"✅ Ganadas: <b>{ganadas}</b>\n"
            f"❌ Perdidas: <b>{perdidas}</b>\n"
            f"📊 Efectividad: <b>{efectividad}%</b>\n\n"
            f"Próxima sesión: te avisamos 10 min antes."
        )
        self.enviar_mensaje(mensaje, self.chat_id_gratis)
        self.enviar_mensaje(mensaje, self.chat_id_vip)

    def enviar_resultado(self, activo, direccion, resultado):
        """Mensaje individual de resultado con frase motivacional rotativa."""
        frase = random.choice(FRASES_WIN if resultado == "WIN" else FRASES_LOSS)
        if resultado == "WIN":
            mensaje = (
                f"✅ <b>L&W — SEÑAL GANADA</b> 🏆\n\n"
                f"📊 {nombre_para_quotex(activo)} {direccion}\n\n"
                f"💡 <i>{frase}</i>"
            )
        else:
            mensaje = (
                f"❌ <b>L&W — SEÑAL PERDIDA</b>\n\n"
                f"📊 {nombre_para_quotex(activo)} {direccion}\n\n"
                f"💡 <i>{frase}</i>"
            )
        return self.enviar_mensaje(mensaje, self.chat_id_vip)

    def enviar_pausa_tecnica(self, nombre_sesion, ganadas, perdidas):
        """Mensaje de pausa técnica tras 2 LOSS consecutivos."""
        mensaje = (
            f"⏸️ <b>L&W — PAUSA TÉCNICA</b>\n\n"
            f"Sesión <b>{nombre_sesion}</b> pausada tras 2 señales negativas.\n\n"
            f"✅ Ganadas: {ganadas} | ❌ Perdidas: {perdidas}\n\n"
            f"🛡️ Protegemos tu capital. Retomamos en la próxima sesión."
        )
        self.enviar_mensaje(mensaje, self.chat_id_vip)

    def enviar_curso_gratis(self, curso):
        """Envía mini curso educativo al canal GRATIS (embudo VIP)."""
        if not curso:
            return False
        contenido = "\n\n".join(curso["contenido"])
        self.enviar_mensaje(contenido, self.chat_id_gratis)

    def enviar_resultado_parcial(self, nombre_sesion, ganadas, perdidas):
        """Resultado parcial al canal GRATIS al cierre de sesión (prueba social, sin detalles)."""
        total = ganadas + perdidas
        efectividad = round((ganadas / total * 100), 1) if total > 0 else 0
        barras = "✅" * ganadas + "❌" * perdidas
        mensaje = (
            f"📊 <b>L&W — RESULTADO {nombre_sesion}</b>\n\n"
            f"{barras}\n\n"
            f"✅ {ganadas} ganadas | ❌ {perdidas} perdidas\n"
            f"📈 Efectividad: <b>{efectividad}%</b>\n\n"
            f"🔒 <i>Cada señal con análisis técnico completo solo en VIP.</i>\n"
            f"<a href='https://t.me/+36KihCYd8Ww4MDVh'>ACCESO VIP $20/mes</a>"
        )
        self.enviar_mensaje(mensaje, self.chat_id_gratis)

    def test_conexion(self):
        mensaje = "✅ Bot L&W Señales IA activado\n\n📱 Señales GRATIS por sesión\n💎 Señales VIP por sesión"
        return self.enviar_mensaje(mensaje, self.chat_id_gratis)


notifier = TelegramNotifier()
