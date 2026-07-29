"""
main.py - Punto de entrada - L&W Señales IA
Corregido: loop continuo (revisa cada 60 seg) + mensaje de bienvenida una vez por dia.
Ejecutas: python main.py
Detienes: CTRL + C
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import time
import os
from datetime import datetime
from core.bot import TradexoBot
from core.logger import setup_logger

logger = setup_logger()
SEGUNDOS_ENTRE_CICLOS = 60
ARCHIVO_BIENVENIDA = "database/.bienvenida_enviada"


def ya_envio_bienvenida_hoy():
    """Verifica si la bienvenida ya se envio hoy."""
    try:
        if not os.path.exists(ARCHIVO_BIENVENIDA):
            return False
        with open(ARCHIVO_BIENVENIDA, "r") as f:
            fecha_guardada = f.read().strip()
        return fecha_guardada == datetime.now().strftime("%Y-%m-%d")
    except Exception:
        return False


def marcar_bienvenida_enviada():
    """Guarda la fecha de hoy como ya enviada."""
    try:
        os.makedirs(os.path.dirname(ARCHIVO_BIENVENIDA), exist_ok=True)
        with open(ARCHIVO_BIENVENIDA, "w") as f:
            f.write(datetime.now().strftime("%Y-%m-%d"))
    except Exception:
        pass


def main():
    logger.info("=" * 60)
    logger.info("L&W Senales IA - BOT INICIADO (loop continuo)")
    logger.info(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("CTRL+C para detener")
    logger.info("=" * 60)

    bot = TradexoBot()

    if not ya_envio_bienvenida_hoy():
        try:
            from core.telegram import notifier
            notifier.enviar_bienvenida_inicio()
            marcar_bienvenida_enviada()
            logger.info("Mensaje de bienvenida enviado a los canales")
        except Exception as e:
            logger.error(f"No se pudo enviar la bienvenida: {e}")
    else:
        logger.info("Bienvenida ya enviada hoy, se omite")

    try:
        while True:
            try:
                bot.ciclo()
            except Exception as e:
                logger.error(f"Error en ciclo: {e}")
            time.sleep(SEGUNDOS_ENTRE_CICLOS)
    except KeyboardInterrupt:
        if getattr(bot, "_ws_precios", None):
            bot._ws_precios.stop()
        logger.warning("Bot detenido por el usuario")


if __name__ == "__main__":
    main()
