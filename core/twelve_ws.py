"""
twelve_ws.py - Precio en vivo via WebSocket de Twelve Data (solo ticks).
REST sigue usandose para velas e indicadores en bot.py.
Incluye reconexion automatica si la conexion se cae.
"""
import json
import logging
import threading
import time as time_module
from typing import List, Optional, Set

import websocket

logger = logging.getLogger(__name__)

WS_URL = "wss://ws.twelvedata.com/v1/quotes/price"
MAX_INTENTOS_RECONEXION = 5
ESPERA_RECONEXION = 10


class TwelveDataPreciosWS:
    """Cliente WebSocket en hilo daemon: cache symbol -> ultimo precio."""

    def __init__(self, api_key: str):
        self._api_key = (api_key or "").strip()
        self._precios: dict = {}
        self._lock = threading.Lock()
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._symbols_actuales: Set[str] = set()
        self._pending_symbols: Optional[List[str]] = None
        self._connected = False
        self._stopping = False

    def start(self) -> None:
        if not self._api_key:
            logger.warning("TwelveDataPreciosWS: sin API key, no se conecta")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stopping = False
        self._thread = threading.Thread(
            target=self._run_forever_con_reconexion,
            name="TwelveDataWS",
            daemon=True,
        )
        self._thread.start()

    def _run_forever_con_reconexion(self) -> None:
        intentos = 0
        while not self._stopping and intentos < MAX_INTENTOS_RECONEXION:
            try:
                url = f"{WS_URL}?apikey={self._api_key}"
                self._ws = websocket.WebSocketApp(
                    url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                logger.info(f"WebSocket: intento de conexion {intentos + 1}/{MAX_INTENTOS_RECONEXION}")
                self._ws.run_forever(ping_interval=10, ping_timeout=5)
            except Exception as e:
                logger.warning(f"WebSocket: excepcion en run_forever: {e}")

            if self._stopping:
                break

            self._connected = False
            intentos += 1
            if intentos < MAX_INTENTOS_RECONEXION:
                logger.info(f"WebSocket: reconexion en {ESPERA_RECONEXION}s (intento {intentos}/{MAX_INTENTOS_RECONEXION})")
                time_module.sleep(ESPERA_RECONEXION)

        if not self._stopping:
            logger.warning("WebSocket: se agotaron los intentos de reconexion")

    def _on_open(self, ws) -> None:
        self._connected = True
        logger.info("WebSocket Twelve Data: conectado")
        pending = self._pending_symbols
        if pending is not None:
            self._send_subscribe(pending)

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if data.get("event") == "price":
            symbol = data.get("symbol")
            price = data.get("price")
            if symbol and price is not None:
                with self._lock:
                    self._precios[symbol] = float(price)
        elif data.get("event") == "subscribe-status":
            status = data.get("status")
            if status and status != "ok":
                logger.warning("WebSocket subscribe-status: %s fails=%s", status, data.get("fails"))

    def _on_error(self, ws, error) -> None:
        if not self._stopping:
            logger.warning("WebSocket Twelve Data error: %s", error)

    def _on_close(self, ws, *args) -> None:
        self._connected = False
        if not self._stopping:
            logger.info("WebSocket Twelve Data: conexion cerrada, reconectando...")

    def _send_subscribe(self, symbols: List[str]) -> None:
        if not self._ws or not self._connected:
            return
        if not symbols:
            self._symbols_actuales = set()
            return
        uniq = list(dict.fromkeys(symbols))
        msg = {"action": "subscribe", "params": {"symbols": ",".join(uniq)}}
        try:
            self._ws.send(json.dumps(msg))
            self._symbols_actuales = set(uniq)
            logger.info("WebSocket suscrito a: %s", ", ".join(uniq))
        except Exception as e:
            logger.warning("No se pudo suscribir WS: %s", e)

    def set_symbols(self, symbols: List[str]) -> None:
        """Actualiza suscripcion a los pares de la sesion (max ~10). Lista vacia = fuera de sesion."""
        uniq = list(dict.fromkeys(symbols or []))
        nuevo = set(uniq)
        if nuevo == self._symbols_actuales:
            return
        self._pending_symbols = uniq
        if not uniq:
            self._symbols_actuales = set()
            self._pending_symbols = []
            return
        if self._connected:
            self._send_subscribe(uniq)
        else:
            self._symbols_actuales = set()

    def get_precio(self, symbol: str) -> Optional[float]:
        with self._lock:
            p = self._precios.get(symbol)
            return p if p is not None else None

    def stop(self) -> None:
        self._stopping = True
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
