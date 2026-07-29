# 📋 COMANDOS L&W SEÑALES IA — Guía rápida

Guarda este documento. Aquí están TODOS los comandos que usas, organizados.
Recuerda: en CMD, pega un comando, dale Enter, espera a que termine, y luego el siguiente.

---

## 🚀 PARA ARRANCAR EL BOT (lo de cada día)

1. Abrir la carpeta del proyecto:
```
cd Desktop\tradexo-quotex-pro
```

2. Instalar dependencias (una vez, o cuando Vera te mande archivos nuevos):
```
pip install -r requirements.txt
```

3. (Opcional) Probar que el precio en tiempo real conecta — NO arranca el bot:
```
python probar_websocket.py
```
Si ves precios unos 20 segundos, el WebSocket funciona. Si no, el bot igual puede correr usando REST.

4. VER QUÉ ACTIVOS ESTÁN BUENOS (¡correr ANTES de cada sesión!):
```
python analizar_activos.py
```
Te muestra el ranking de volatilidad. EXCELENTE y BUENO = operar.
PLANO = mercado flojo, mejor esperar.

5. Arrancar el bot:
```
python main.py
```
Al iniciar verás una línea sobre "Precio en vivo: WebSocket". Entrada y resultados usan ese precio; si falla, REST como antes.

6. Para DETENER el bot:
```
CTRL + C
```
(mantén presionada la tecla CTRL y pulsa C)

---

## 🔍 VERIFICAR QUE LOS ACTIVOS FUNCIONAN

Comprueba que todos los activos responden con tu cuenta de Twelve Data:
```
python verificar_activos.py
```
Úsalo si agregaste activos nuevos o si alguno da error.

---

## 📂 CUANDO VERA TE MANDA ARCHIVOS NUEVOS (ZIP)

1. Detén el bot:
```
CTRL + C
```

2. Entra a la carpeta:
```
cd Desktop\tradexo-quotex-pro
```

3. Encuentra dónde quedó el archivo descargado (por la carpeta doble):
```
dir /s /b %USERPROFILE%\Downloads\indicadores.py
```
(cambia "indicadores.py" por el archivo que busques: bot.py, config.py, etc.)

4. Copia el archivo a su lugar. Ejemplos (ajusta la ruta según el paso 3):

Para archivos del motor (van a la carpeta core):
```
copy /Y "C:\Users\RaidenShg\Downloads\L_W_XXXX\L_W_XXXX\core\indicadores.py" core\indicadores.py
```
```
copy /Y "C:\Users\RaidenShg\Downloads\L_W_XXXX\L_W_XXXX\core\bot.py" core\bot.py
```

Para config (va a la carpeta config):
```
copy /Y "C:\Users\RaidenShg\Downloads\L_W_XXXX\L_W_XXXX\config\config.py" config\config.py
```

Para archivos sueltos (main.py, analizar_activos.py van a la raíz):
```
copy /Y "C:\Users\RaidenShg\Downloads\L_W_XXXX\L_W_XXXX\main.py" main.py
```

⚠️ NOTA: cambia "L_W_XXXX" por el nombre real del ZIP que te mandó Vera.
El resultado debe decir "1 archivo(s) copiado(s)".

---

## ✅ VERIFICAR QUE UN CAMBIO SE INSTALÓ BIEN

Buscar una palabra dentro de un archivo (para confirmar que es la versión nueva):
```
findstr "CONFLUENCIA" core\indicadores.py
```
```
findstr "EXPIRACION_MINUTOS" config\config.py
```
Si muestra líneas = el cambio está puesto. Si no muestra nada = no se instaló.

---

## 🔧 VER O EDITAR CONFIGURACIÓN

Ver el contenido del .env (tus credenciales):
```
type config\.env
```

Ver los activos configurados:
```
findstr "activos" config\config.py
```

Ver los tiempos del bot:
```
findstr "MINUTOS" config\config.py
```

---

## 🆘 SI ALGO FALLA

- "no se reconoce como comando" → revisa que no se pegara una letra de más (ej: "cdir" en vez de "dir")
- "no puede encontrar la ruta" → probablemente estás pegando dos comandos juntos, o hay carpeta doble. Usa el "dir /s /b" para hallar la ruta real.
- "no such file main.py" → no estás en la carpeta correcta. Corre primero: cd Desktop\tradexo-quotex-pro
- El bot arranca pero no manda señales → normal si el mercado está plano. Corre analizar_activos.py para ver si hay volatilidad.

---

## 📝 RECORDATORIOS DE ORO (decisiones que tomamos)

- SOLO mercado real. NUNCA OTC (aunque el cripto brille en volatilidad).
- El oro (XAU/USD) sí es mercado real y suele estar EXCELENTE.
- Análisis en velas de 1 min, operación M2 (2 min), entrada 2 min después.
- Pocas señales buenas > muchas señales flojas.
- Anota cada señal (hora, par, compra/venta, resultado real) para afinar con datos.
- Ningún bot gana el 100%. Las pérdidas son parte del juego.
