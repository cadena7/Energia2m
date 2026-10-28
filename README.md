# Medidor de Energia Consola 2m

Firmware MicroPython para un ESP32 que mide la corriente de cuatro lineas de la consola del telescopio de 2m usando sensores HSTS016L. El equipo publica mediciones por MQTT bajo demanda, mantiene una interfaz web local con grafica en tiempo real y puede ser controlado desde una GUI GTK para registrar datos en CSV.

## Funcionamiento

- `boot.py` configura Ethernet con IP fija `192.168.0.90`.
- `main.py` lee cuatro sensores de corriente en los ADC del ESP32:
  - Norte: GPIO 32
  - Sur: GPIO 35
  - Este: GPIO 36
  - Oeste: GPIO 39
- La medicion se mantiene activa aunque el broker MQTT no este disponible.
- La publicacion MQTT solo se activa cuando llega un comando `start`.
- La pagina web en `http://192.168.0.90/` muestra valores numericos y una grafica historica.

## Red Y MQTT

Configuracion principal:

- Broker MQTT: `192.168.0.237`
- Cliente: `ESP32_Consola_Energia`
- IP Ethernet del ESP32: `192.168.0.90`
- Gateway: `192.168.0.254`
- DNS: `8.8.8.8`

Topicos:

- Comandos: `oan/control/2m/consola/energia/comando`
- Estado: `oan/control/2m/consola/energia/estado`
- Mediciones: `oan/control/2m/consola/energia`

Comandos aceptados:

```json
{"comando": "start", "SAMPLE_INTERVAL_MS": 500}
```

```json
{"comando": "stop"}
```

```json
{"SAMPLE_INTERVAL_MS": 1000}
```

El intervalo minimo permitido por el firmware es `250 ms`.

Payload de medicion:

```json
{
  "Norte": 0.0,
  "Sur": 0.0,
  "Este": 0.0,
  "Oeste": 0.0
}
```

Payload de estado:

```json
{
  "SAMPLE_INTERVAL_MS": 500,
  "estado": "STARTED"
}
```

## Interfaz Web

Endpoints disponibles:

- `/`: pagina principal con grafica Chart.js.
- `/api/history`: historial JSON con `SAMPLE_INTERVAL_MS`, `Norte`, `Sur`, `Este` y `Oeste`.
- `/api/calibration` (`GET`): offsets y zonas muertas activos, voltajes filtrados y avisos de recuperacion.
- `/api/calibration` (`POST`): valida, guarda y aplica offsets y zonas muertas de los cuatro canales.
- `/static/Chart.min.js`: biblioteca Chart.js local.

Al final de la pagina, debajo de la grafica y del pie de pagina, aparece el panel
plegable **Configuracion · Calibracion de canales**, cerrado inicialmente.
Mientras esta abierto consulta los voltajes aproximadamente una vez por segundo,
sin sobrescribir los campos que se estan editando. La grafica y el panel esperan
a que termine cada consulta antes de programar la siguiente. Las consultas se
pausan al ocultar/cerrar la pestaña y se reanudan al volver (incluido atras/adelante).
Cada lectura tiene un timeout de 8 s y reintentos con espera progresiva hasta 15 s.
Cuando falta comunicacion, las lecturas numericas muestran `—` y aparece un aviso
de reconexion; la grafica conserva su ultimo historial hasta recuperar datos.

El historial se limita a `MAX_HISTORY = 40` muestras para cuidar la RAM del ESP32.
La página muestra por separado el estado de la consulta de datos y el de la
gráfica. Los errores indican red, timeout, HTTP, JSON o historial inválido;
los fallos al iniciar o dibujar la gráfica conservan las lecturas numéricas y
se detallan también en la consola del navegador. `static/Chart.min.js` es la
distribución UMD 4.5.0, que expone `Chart` al cargarla como script normal.
La grafica calcula sus etiquetas de tiempo en el navegador como una ventana relativa de los ultimos 40 puntos; el punto mas reciente se muestra como `0.0s`.

## Protecciones

- HTTP: hasta 6 conexiones activas; 5 s para recibir la peticion, 5 s para su
  manejador, 15 s para enviar la respuesta y 1 s para terminar el cierre.
- Las conexiones y archivos se cierran tambien al interrumpir descargas, enviar
  `HEAD`, recibir peticiones incompletas o cancelar tareas. Las conexiones ociosas
  expiran sin dejar al servidor esperando indefinidamente.
- Las peticiones se limitan a 1 KB de cuerpo, 2 KB por linea y 8 KB de encabezados
  acumulados. La lectura limita la memoria antes de aceptar una linea completa.
- El HTML y los archivos estaticos se envian por bloques, para reducir las copias
  grandes en RAM cuando hay varias conexiones.
- Si falla el servidor, se cierra el listener anterior antes de volver a abrirlo.
- Watchdog configurado a `300000 ms`.
- Reinicio automatico si la RAM libre baja de `10000 bytes`.
- Timeout Ethernet de `30000 ms` en `boot.py`; si no conecta, el ESP32 se reinicia.
- La ruta `/static/...` rechaza nombres inseguros para evitar lectura de archivos fuera de `static/`.
- La medicion de sensores esta desacoplada de MQTT, asi que la web sigue actualizandose aunque el broker este caido.

## Archivos Principales

- `boot.py`: configuracion de red Ethernet.
- `main.py`: lectura de sensores, servidor web, MQTT y watchdog.
- `calibration.py`: validacion, carga y guardado persistente de offsets y zonas muertas.
- `static/Chart.min.js`: libreria para la grafica web.
- `static/index.html`: pagina principal, enviada por bloques.
- `static/dashboard.js` y `static/polling.js`: grafica, tiempos limite y reconexion.
- `static/calibration.js` y `static/calibration.css`: panel de calibracion.
- `config.json`: configuracion cargada al arrancar o creada desde la web, actualizada al guardar; `config.json.bak` conserva la anterior.
- `gtk/logguer_energia2m_ui.py`: GUI de escritorio para controlar START/STOP y guardar CSV.
- `lib/microdot/microdot.py`: servidor web Microdot.
- `lib/mqtt_as/__init__.mpy`: cliente MQTT asincrono.
- `backup/`: copia local de respaldo del firmware.
- `sensor datasheet.pdf`: hoja de datos del sensor.
- `ESP32-GATEWAY-GPIOs-Rev.F-up.pdf`: referencia de pines de la tarjeta.

## Calibracion

Los offsets y deadbands se cargan exclusivamente de `config.json` (formato `version: 2`). El archivo incluido sirve como punto de partida para una instalación nueva; calibra cada sensor antes de usar sus mediciones. No hay valores de calibración de respaldo en el código Python.

Para recalibrar, abrir el panel al final de la interfaz web, verificar que no circule
corriente y observar el voltaje medido de cada canal. Este voltaje es el resultado
del promedio ADC filtrado y la conversion `VCC / ADC_RESOLUTION`, antes de restar
el offset o aplicar el deadband; es una referencia del ADC, no una medicion
independiente con multimetro. Editar los offsets en voltios y las zonas muertas en
amperes, y pulsar **Guardar y aplicar**. Ambos grupos se guardan juntos.

La zona muerta (deadband) se aplica simetricamente: `0.35` fuerza a cero cualquier
corriente entre `-0.35 A` y `+0.35 A`, incluidos los extremos. `0` la desactiva.
El rango de ajuste permitido es de 0 a 20 A por canal. Los valores del JSON incluido
son Norte/Sur `0.35 A` y Este/Oeste `0.30 A`.

El ESP32 valida los ocho valores (offsets finitos mayores que 0 y menores que
3.33 V, y zonas muertas finitas entre 0 y 20 A), escribe y verifica `config.json.tmp`, conserva la configuracion anterior
valida en `config.json.bak` y promueve el temporal a `config.json`. Usa `os.sync()`
cuando esta disponible. Solo escribe si hay cambios o es necesario crear/reparar
el archivo principal. Si el guardado falla, informa el error y mantiene la calibracion
activa. La copia de respaldo ayuda a recuperar guardados interrumpidos; la
resistencia a cortes de energia tambien depende del sistema de archivos de la placa.

Los cambios se aplican entre ciclos completos de medicion, sin reiniciar. Se limpia
el historial y MQTT espera una muestra completa con la nueva calibracion. El formato
de los mensajes MQTT permanece igual. Al arrancar se carga el archivo principal;
si falta o es invalido se intenta el respaldo. Si ambos fallan, se muestra un error
en la consola USB y en la web: falta config.json o no es valido. La web permanece
activa en modo configuracion, con el panel abierto y sus ocho campos vacios.
Solo se leen voltajes ADC filtrados; no se calculan corrientes ni se conecta MQTT.
Completa los offsets y deadbands y pulsa Guardar y aplicar para crear el JSON y
habilitar las mediciones sin reiniciar. MQTT se conecta; la publicacion requiere start.
La interfaz muestra un aviso si se uso respaldo.

Ejemplo de archivo que puedes subir o crear desde la web:

```json
{
  "version": 2,
  "offsets": {"Norte": 1.5696, "Sur": 1.5685, "Este": 1.5650, "Oeste": 1.5668},
  "deadbands": {"Norte": 0.35, "Sur": 0.35, "Este": 0.30, "Oeste": 0.30}
}
```

Los archivos con `version: 1` ya no se aceptan: actualizalos a `version: 2` e incluye los deadbands de los cuatro canales explícitamente. Los clientes que envian solo `offsets` conservan las zonas muertas activas cargadas del JSON.

Los valores persisten tras reiniciar o desconectar la alimentacion. Para actualizar
el programa conservando la calibracion, mantener `config.json` y `config.json.bak`;
no borrar toda la flash.

La lectura usa un filtro anti-picos con `n=41` y `trim=10`: toma varias muestras ADC, las ordena, descarta los extremos bajos y altos, y promedia el bloque central antes de convertir a amperes.

## Pruebas Recomendadas

1. Cargar `boot.py`, `main.py`, `calibration.py`, `config.json` (opcional: puede crearse desde la web), `static/`, `lib/microdot/` y `lib/mqtt_as/` al ESP32.
2. Reiniciar con Ethernet conectado y confirmar la IP `192.168.0.90`.
3. Abrir `http://192.168.0.90/` y verificar que la grafica se actualiza.
4. Apagar temporalmente el broker MQTT y confirmar que la pagina web sigue actualizando lecturas.
5. Encender el broker y enviar `{"comando":"start","SAMPLE_INTERVAL_MS":500}`.
6. Verificar publicaciones en `oan/control/2m/consola/energia`.
7. Enviar `{"comando":"stop"}` y confirmar que deja de publicar, pero la web sigue midiendo.
8. Probar que `/static/Chart.min.js` funciona y que rutas como `/static/../main.py` son rechazadas.
9. Abrir el panel inferior, comprobar voltajes con corriente cero, guardar un offset y una zona muerta y verificar el cambio sin reiniciar.
10. Reiniciar y despues apagar/encender el ESP32; comprobar que se recuperan los offsets y las zonas muertas guardados.

Pruebas automatizadas en PC, con hardware simulado:

```sh
python3 -m unittest discover -s tests -v
```

Cubren persistencia, respaldo, interrupcion del guardado, validacion, errores de
escritura, rechazo de archivos incompletos o antiguos, ajuste/desactivacion de deadband,
aplicacion entre ciclos, voltaje filtrado antes del deadband y limpieza
HTTP ante desconexiones, esperas y cancelaciones.

Pruebas adicionales en PC:

```sh
node --test tests/test_polling.cjs
node --test tests/test_dashboard.cjs
python3 tests/stress_http.py
```

La prueba TCP local ejecuta 100 ciclos (mas de 1100 conexiones) de carga de pagina,
archivos, consultas y cierres interrumpidos, comprobando que no queden archivos
abiertos ni conexiones activas. Usa sensores simulados y tiempos limite acortados;
no sustituye la prueba de RAM, Ethernet y sockets en la placa real.

Para instalar esta revision, actualizar **`main.py`, `calibration.py`, toda la
carpeta `static/` y `lib/microdot/microdot.py`**. Esta copia de Microdot contiene las
correcciones locales de cierre y tiempos limite. Conservar `config.json` y su
respaldo existentes. En Chrome, recargar sin cache una vez tras la actualizacion.

Validacion pendiente en el ESP32: abrir/cerrar y recargar la pagina repetidamente,
probar varias pestañas, interrumpir/restablecer Ethernet y dejarla varias horas
activa. Comprobar la recuperacion automatica, la RAM libre y que las mediciones
continuen con MQTT disponible y tambien sin broker. Las pruebas locales no
garantizan ausencia de fallos de red, hardware o firmware en la placa.

## Notas De Operacion

- El firmware esta pensado para MicroPython en ESP32 con Ethernet LAN8710.
- Si el ESP32 se reinicia repetidamente, revisar primero conexion Ethernet, broker MQTT, offsets de sensores y memoria libre.
- La GUI GTK usa el mismo broker y topicos que el firmware, por lo que no requiere cambios mientras se mantenga el formato JSON actual.
