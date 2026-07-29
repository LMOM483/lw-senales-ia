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
    # Ajustado 2026-07-29 con datos reales de 219 señales:
    # Removidos activos con <50% win rate: EUR/USD(42.9%), EUR/AUD(43.8%),
    # EUR/CHF(45.0%), GBP/USD(46.7%), USD/CAD(33.3%)
    # Priorizados activos con >60% win rate: AUD/JPY(77.8%), USD/JPY(70%),
    # CAD/JPY(66.7%), NZD/USD(60%), USD/CHF(60%)
    POOL_FOREX = [
        "AUD/JPY", "USD/JPY", "CAD/JPY", "NZD/USD", "USD/CHF",
        "EUR/JPY", "GBP/JPY", "EUR/GBP", "GBP/CHF", "NZD/JPY",
    ]

    # ==================== SESIONES (M1, sin CRYPTO NIGHT) ====================
    # Ajuste 2026-07-29: EUROPA GOLD subió confianza_min a 82% (16.7% win rate previo).
    # Activos de sesión alineados con los mejores del análisis histórico.
    SESIONES = {
        'europa': {
            'inicio': time(6, 0),
            'fin': time(10, 0),
            'nombre': 'EUROPA GOLD',
            'confianza_min': 82.0,  # subido de 78 a 82 por bajo win rate histórico
            'activos': ["EUR/JPY", "GBP/JPY", "EUR/GBP", "USD/CHF", "GBP/CHF"]
        },
        'ny': {
            'inicio': time(10, 0),
            'fin': time(13, 0),
            'nombre': 'NY POWER',
            'confianza_min': 78.0,
            'activos': ["USD/JPY", "AUD/JPY", "CAD/JPY", "NZD/USD", "USD/CHF"]
        },
        'overlap': {
            'inicio': time(13, 0),
            'fin': time(17, 0),
            'nombre': 'OVERLAP L&W',
            'confianza_min': 78.0,
            'activos': ["AUD/JPY", "USD/JPY", "NZD/USD", "GBP/JPY", "EUR/JPY"]
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
