# L&W Señales IA — Documento Maestro del Proyecto

## Qué es este proyecto

Bot de señales de trading para opciones binarias (Quotex) que envía señales a dos canales de Telegram: uno GRATIS y uno VIP ($20/mes). Opera por sesiones horarias (NO 24/7), usa datos reales de Twelve Data (plan pago Grow $79/mes) y 3 estrategias técnicas combinadas. La dueña es Lina, sin experiencia en programación — todas las explicaciones deben ser paso a paso, simples, sin jerga innecesaria, en español.

## Regla de comunicación con Lina

- NO hacerle preguntas técnicas abiertas; proponer la mejor opción con justificación breve y ejecutar
- Todo cambio se prueba ANTES de darse por terminado
- Explicar qué se hizo en 2-3 líneas máximo después de cada cambio
- NUNCA prometer ganancias ni win rates inventados; la transparencia es la marca del canal

## Estructura del proyecto

```
tradexo-quotex-pro/
├── main.py                  # Punto de entrada, loop continuo cada 60s
├── config/
│   ├── config.py            # Config central: sesiones, límites, umbrales
│   ├── database.py          # SQLite (tabla senales con resultado WIN/LOSS)
│   └── .env                 # Credenciales (NUNCA tocar ni exponer ni subir a git)
├── core/
│   ├── bot.py               # Núcleo: análisis, envío, evaluación WIN/LOSS
│   ├── indicadores.py       # 3 estrategias: RSI+Estocástico, EMA+SAR, Bollinger+MACD
│   ├── sessions.py          # Gestor de sesiones, límites, anuncios, cierres
│   ├── telegram.py          # Envío a canales GRATIS y VIP
│   └── logger.py            # Logging a archivo
├── database/tradexo.db      # Histórico de señales con resultados
└── logs/                    # Logs de ejecución
```

## Estado actual (funcionando y verificado en producción)

- Datos reales de Twelve Data plan Grow (símbolos con barra: EUR/USD, no EURUSD)
- 4 sesiones: EUROPA GOLD (6-10), NY POWER (10-13), OVERLAP L&W (13-17), CRYPTO NIGHT (20-23:59), zona America/Sao_Paulo
- Umbrales confianza: 75/76/74/76
- Límite 8 señales VIP / 2 gratis por sesión
- Separación entre señales (actualmente 8 min — CAMBIAR A 10, ver tareas)
- Entrada retrasada 1-3 min, expiración M5
- Evaluación WIN/LOSS automática comparando precio a los 5 min
- Cooldown 2h por activo tras pérdida
- Mensaje de resultado ✅/❌ al VIP tras cada evaluación
- Anuncio 10 min antes de cada sesión, resumen al cierre con efectividad
- Señales confirmadas ganando en Quotex real

## TAREAS PENDIENTES (ejecutar en orden, probar cada una)

### 1. Separación entre señales: 8 → 10 minutos
En `config/config.py`, cambiar `MINUTOS_ENTRE_SEÑALES = 8` a `= 10`.

### 2. Mensaje de bienvenida al encender el bot
Al arrancar `main.py`, enviar UNA vez a ambos canales un mensaje de bienvenida con la marca L&W que incluya los horarios de las 4 sesiones y una línea de gestión de riesgo ("Invierte máximo 2-5% de tu capital por operación. Opera bajo tu propio riesgo"). NO debe reenviarse en cada reinicio del mismo día (guardar fecha del último envío en la base de datos o archivo).

### 3. Mensajes cortos motivacionales/educativos tras WIN/LOSS
En `core/telegram.py`, método `enviar_resultado`: agregar al final del mensaje UNA línea corta rotativa (lista de ~10 frases para WIN y ~10 para LOSS). Educativas y sobrias, ej. WIN: "La disciplina paga más que la suerte." LOSS: "Una pérdida controlada es parte del plan. Nunca persigas recuperar con apuestas grandes." PROHIBIDO: frases que inciten a apostar más, martingala, o "recuperar lo perdido".

### 4. Freno de seguridad: 2 pérdidas consecutivas
En `core/bot.py`: si una sesión acumula 2 LOSS consecutivos (sin WIN entre medio):
- Pausar el envío de señales por el resto de esa sesión
- Registrar en el log un diagnóstico: valores de RSI/EMA/MACD de las señales perdidas
- Enviar al VIP un mensaje sobrio: "Pausa técnica de la sesión tras 2 señales negativas. Protegemos tu capital. Retomamos en la próxima sesión."
- El contador se reinicia en cada sesión nueva
- NO ajustar parámetros automáticamente — el ajuste de estrategias se hace manualmente con revisión semanal de datos

### 5. Revisión semanal de parámetros (crear script)
Crear `revision_semanal.py` que lea `database/tradexo.db` y muestre: total señales, WIN/LOSS por sesión, por activo, por estrategia (método), y efectividad. Esto es la base para ajustar umbrales con datos reales, no con intuición.

### 6. Aprovechar plan pago Twelve Data
- Batch requests: pedir los 3 activos de la sesión en UNA llamada (Twelve Data soporta símbolos separados por coma)
- Confirmación multi-timeframe: antes de enviar señal M1, verificar que la tendencia en velas de 5min no la contradiga (EMA20 de 5min a favor de la dirección)

## Reglas técnicas fijas (NO cambiar sin autorización de Lina)

- Twelve Data devuelve velas de más nueva a más vieja: SIEMPRE invertir a orden cronológico antes de calcular indicadores
- RSI se calcula con las últimas 14+1 velas exactas, no todo el historial
- Los chat_id de Telegram están en .env: GRATIS y VIP son DISTINTOS
- El precio NO se muestra en el mensaje VIP
- Formato de mensajes ya aprobado por Lina — no rediseñar sin pedirlo
- No operar fines de semana (OTC de Quotex no corresponde a datos de Twelve Data)
- Nunca hardcodear credenciales en el código; todo por .env

## Comandos útiles

- Correr el bot: `python main.py` (detener: CTRL+C)
- Ver señales guardadas: consultar tabla `senales` en `database/tradexo.db`
- Los logs quedan en `logs/`
