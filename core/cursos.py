"""
cursos.py - Mini cursos educativos para el canal GRATIS (embudo VIP).
5 cursos que rotan de lunes a viernes. Texto puro, sin imágenes por ahora.
"""
from datetime import date

CURSOS = {
    0: {  # Lunes
        "titulo": "GESTIÓN DE RIESGO",
        "contenido": [
            "📚 <b>MINI CURSO L&W — Gestión de Riesgo</b>\n"
            "Tema: Cómo proteger tu capital en opciones binarias.\n\n"
            "1️⃣ <b>Nunca arriesgues más del 2-5%</b> de tu capital en una sola operación.\n"
            "Si tienes $100, opera máximo $5 por señal.\n\n"
            "2️⃣ <b>Establece un límite diario de pérdidas.</b>\n"
            "Si pierdes 3 operaciones seguidas, PARA. El mercado estará ahí mañana.\n\n"
            "3️⃣ <b>No dupliques tras una pérdida.</b>\n"
            "La martingala funciona en teoría, pero en la práctica destruye cuentas.\n\n"
            "4️⃣ <b>Gana lo mismo, pierde lo mismo.</b>\n"
            "Si ganas $5 en una WIN, no arriesgues $10 en la siguiente.\n\n"
            "💡 La gestión de riesgo es la diferencia entre un trader profesional y un apostador.\n\n"
            "🔒 ¿Quieres señales con gestión de riesgo automática? "
            "<a href='https://t.me/+36KihCYd8Ww4MDVh'>VIP $20/mes</a>"
        ],
    },
    1: {  # Martes
        "titulo": "CÓMO LEER EL RSI",
        "contenido": [
            "📚 <b>MINI CURSO L&W — El RSI Explicado</b>\n"
            "Tema: Cómo leer el Índice de Fuerza Relativa.\n\n"
            "El RSI es un indicador que mide la <b>fuerza</b> del movimiento del precio.\n"
            "Va de 0 a 100.\n\n"
            "🟢 <b>RSI > 70 = Sobrecompra</b>\n"
            "El precio subió demasiado, demasiado rápido. Puede rebotar hacia abajo.\n\n"
            "🔴 <b>RSI < 30 = Sobreventa</b>\n"
            "El precio bajó demasiado, demasiado rápido. Puede rebotar hacia arriba.\n\n"
            "⚖️ <b>RSI 40-60 = Neutro</b>\n"
            "El mercado está indeciso. No es buen momento para operar.\n\n"
            "Nuestro bot usa RSI de 7 periodos (más sensible que el clásico de 14).\n"
            "Detecta cambios de momentum antes que otros indicadores.\n\n"
            "🔒 En VIP usamos RSI + Estocástico + EMA para confirmar cada señal. "
            "<a href='https://t.me/+36KihCYd8Ww4MDVh'>Ver estrategia completa</a>"
        ],
    },
    2: {  # Miércoles
        "titulo": "TENDENCIAS CON EMAs",
        "contenido": [
            "📚 <b>MINI CURSO L&W — Tendencias con EMAs</b>\n"
            "Tema: Cómo identificar la dirección del mercado.\n\n"
            "EMA = Media Móvil Exponencial. Es una línea que suaviza el precio.\n"
            "Si el precio está <b>ARRIBA</b> de la EMA → tendencia alcista 🟢\n"
            "Si el precio está <b>DEBAJO</b> de la EMA → tendencia bajista 🔴\n\n"
            "🎯 <b>La regla de oro:</b>\n"
            "Cuando las EMAs se alinean en orden (8 > 13 > 21 > 50),\n"
            "la tendencia es fuerte y consistente.\n\n"
            "⚡ <b>El cruce EMA 9/21:</b>\n"
            "Cuando la EMA9 cruza POR ENCIMA de la EMA21 → señal de compra.\n"
            "Cuando la EMA9 cruza POR DEBAJO de la EMA21 → señal de venta.\n"
            "Es el cruce más usado en scalping de 1 minuto.\n\n"
            "Nuestro bot exige que las 4 EMAs estén alineadas + pendientes positivas.\n"
            "Esto filtra el 80% de las señales falsas.\n\n"
            "🔒 En VIP analizamos tendencia en M1 + confirmación en M5. "
            "<a href='https://t.me/+36KihCYd8Ww4MDVh'>Acceso VIP</a>"
        ],
    },
    3: {  # Jueves
        "titulo": "GESTIÓN EMOCIONAL",
        "contenido": [
            "📚 <b>MINI CURSO L&W — Psicología del Trading</b>\n"
            "Tema: Por qué la emoción es tu peor enemigo.\n\n"
            "1️⃣ <b>La codicia después de una WIN:</b>\n"
            "\"Gané, voy a poner el doble\" → así se pierde todo.\n"
            "Una WIN no significa que inviertas más. Sigue el plan.\n\n"
            "2️⃣ <b>La venganza tras una LOSS:</b>\n"
            "\"Perdí, tengo que recuperar\" → así se destruyen cuentas.\n"
            "El mercado no sabe que perdiste. No te debe nada.\n\n"
            "3️⃣ <b>El miedo a perder oportunidades:</b>\n"
            "\"No operé y subió\" → FOMO.\n"
            "Siempre habrá otra señal. No corras.\n\n"
            "4️⃣ <b>La overtrading:</b>\n"
            "Operar por aburrimiento o por ansiedad.\n"
            "Calidad > Cantidad. Mejor 2 señales buenas que 10 mediocres.\n\n"
            "💡 <b>Regla simple:</b> Si sientes emoción fuerte antes de operar, NO operes.\n"
            "El trading es frío, metódico y aburrido. Y eso es exactamente lo que lo hace rentable.\n\n"
            "🔒 Nuestro bot opera sin emociones. Señales técnicas puras. "
            "<a href='https://t.me/+36KihCYd8Ww4MDVh'>VIP $20/mes</a>"
        ],
    },
    4: {  # Viernes
        "titulo": "CÓMO FUNCIONA EL BOT",
        "contenido": [
            "📚 <b>MINI CURSO L&W — La Estrategia del Bot</b>\n"
            "Tema: Cómo analiza y genera cada señal.\n\n"
            "Nuestro bot usa <b>3 capas de análisis</b> antes de enviar cualquier señal:\n\n"
            "🔹 <b>CAPA 1 — Tendencia:</b>\n"
            "Verifica que las EMAs estén alineadas y con pendiente positiva.\n"
            "Si las EMAs no están de acuerdo → NO hay señal.\n\n"
            "🔹 <b>CAPA 2 — Momentum:</b>\n"
            "RSI + Estocástico deben apuntar en la misma dirección.\n"
            "El Estocástico DEBE estar cruzando (no pegado al extremo).\n\n"
            "🔹 <b>CAPA 3 — Volatilidad + MACD:</b>\n"
            "ATR verifica que haya movimiento real (no mercado plano).\n"
            "MACD confirma la dirección. Si está en contra → NO hay señal.\n\n"
            "Además, filtramos velas de rechazo y exigimos cruces recientes de EMA.\n\n"
            "📊 Resultado: pocas señales, pero de ALTA calidad.\n\n"
            "🔒 En VIP recibes cada señal con su análisis técnico completo. "
            "<a href='https://t.me/+36KihCYd8Ww4MDVh'>Acceso VIP $20/mes</a>"
        ],
    },
}


def obtener_curso_hoy():
    """Devuelve el curso del día según el día de la semana (0=Lunes, 4=Viernes)."""
    dia = date.today().weekday()
    if dia not in CURSOS:
        return None  # fin de semana no hay curso
    return CURSOS[dia]


def obtener_curso_por_dia(dia_semana):
    """Devuelve un curso específico por día de la semana (0-4)."""
    return CURSOS.get(dia_semana)
