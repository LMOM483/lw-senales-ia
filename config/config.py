"""
config.py - Configuración centralizada - L&W Señales IA
Corregido: sin secretos hardcodeados, límites por sesión (no por día).
"""

import os
from dotenv import load_dotenv
from datetime import time

import pathlib as _pathlib
_env_config = _pathlib.Path(__file__).parent / ".env"
_env_root   = _pathlib.Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_env_config if _env_config.exists() else _env_root)


class Config:
    """Clase con TODA la configuración del sistema"""

    # ==================== APIS Y TELEGRAM (solo desde .env, sin respaldo hardcodeado) ====================
    TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID_GRATIS = os.getenv("TELEGRAM_CHAT_ID_GRATIS", "")
    TELEGRAM_CHAT_ID_VIP = os.getenv("TELEGRAM_CHAT_ID_VIP", "")

    # ==================== TRADING ====================
    MODO_DESARROLLO = os.getenv("MODO_DESARROLLO", "true").lower() == "true"  # False = activar canal GRATIS
    # Límites POR SESIÓN (no por día completo)
    MAX_SEÑALES_VIP_POR_SESION = 8
    MAX_SEÑALES_GRATIS_POR_SESION = 2
    ANUNCIO_MINUTOS_ANTES = 10
    EXPIRACION_MINUTOS = 5        # M5: expiración 5 minutos
    EXPIRACION_M1_MINUTOS = 1    # M1: expiración 1 minuto
    MINUTOS_ENTRE_SEÑALES = 10
    MINUTOS_PAUSA_TRAS_RESULTADO = 4
    COOLDOWN_HORAS_TRAS_PERDIDA = 2
    ENTRADA_DELAY_MIN_MINUTOS = 1
    ENTRADA_DELAY_MAX_MINUTOS = 3

    # ==================== ZONA HORARIA ====================
    TIMEZONE = "America/Sao_Paulo"

    # ==================== INDICADORES (usados por core/indicadores.py) ====================
    RSI_PERIOD = 7

    # ==================== POOL DE FOREX REAL ====================
    # Todos los pares de forex de mercado real que el bot puede considerar.
    # NO incluye oro (XAU) por la guerra, NI cripto (es OTC en Quotex).
    # El bot elige automaticamente de esta lista los que tengan movimiento
    # en cada sesion, y descarta los planos. Lina ya no elige a mano.
    POOL_FOREX = [
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF",
        "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "CAD/JPY",
        "EUR/AUD", "EUR/CHF", "GBP/CHF", 
    ]

    # ==================== SESIONES (M1, sin CRYPTO NIGHT) ====================
    # Activos amplios para que Lina elija con analizar_activos.py segun el dia.
    # CRYPTO NIGHT eliminada: cripto es OTC en Quotex (regla firme: solo mercado real)
    SESIONES = {
        'europa': {
            'inicio': time(6, 0),
            'fin': time(10, 0),
            'nombre': 'EUROPA GOLD',
            'confianza_min': 78.0,
            'activos': ["EUR/USD", "GBP/USD", "EUR/GBP", "USD/CHF", "EUR/JPY", "XAU/USD"]
        },
        'ny': {
            'inicio': time(10, 0),
            'fin': time(13, 0),
            'nombre': 'NY POWER',
            'confianza_min': 78.0,
            'activos': ["USD/JPY", "AUD/USD", "GBP/JPY", "CAD/JPY", "XAU/USD"]
        },
        'overlap': {
            'inicio': time(13, 0),
            'fin': time(17, 0),
            'nombre': 'OVERLAP L&W',
            'confianza_min': 78.0,
            'activos': ["EUR/GBP", "USD/CHF", "EUR/AUD", "GBP/JPY"]
        }
    }

    # ==================== LOGGING ====================
    LOG_LEVEL = "INFO"
    LOG_FILE = "logs/tradexo.log"

    # ==================== DATABASE ====================
    DATABASE_FILE = "database/tradexo.db"

    @classmethod
    def validar(cls):
        """Devuelve lista de problemas de configuración. Vacía = todo OK."""
        errores = []
        if not cls.TELEGRAM_BOT_TOKEN:
            errores.append("TELEGRAM_BOT_TOKEN vacío en .env")
        if not cls.TELEGRAM_CHAT_ID_GRATIS:
            errores.append("TELEGRAM_CHAT_ID_GRATIS vacío en .env")
        if not cls.TELEGRAM_CHAT_ID_VIP:
            errores.append("TELEGRAM_CHAT_ID_VIP vacío en .env")
        if cls.TELEGRAM_CHAT_ID_GRATIS and cls.TELEGRAM_CHAT_ID_GRATIS == cls.TELEGRAM_CHAT_ID_VIP:
            errores.append("TELEGRAM_CHAT_ID_GRATIS y TELEGRAM_CHAT_ID_VIP son iguales — deben ser distintos")
        if not cls.TWELVE_DATA_API_KEY:
            errores.append("TWELVE_DATA_API_KEY vacío (bot correrá en modo prueba con datos simulados)")
        return errores
