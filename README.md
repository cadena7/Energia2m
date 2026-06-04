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
- `/static/Chart.min.js`: biblioteca Chart.js local.

El historial se limita a `MAX_HISTORY = 40` muestras para cuidar la RAM del ESP32.
La grafica calcula sus etiquetas de tiempo en el navegador como una ventana relativa de los ultimos 40 puntos; el punto mas reciente se muestra como `0.0s`.

## Protecciones

- Watchdog configurado a `300000 ms`.
- Reinicio automatico si la RAM libre baja de `10000 bytes`.
- Timeout Ethernet de `30000 ms` en `boot.py`; si no conecta, el ESP32 se reinicia.
- La ruta `/static/...` rechaza nombres inseguros para evitar lectura de archivos fuera de `static/`.
- La medicion de sensores esta desacoplada de MQTT, asi que la web sigue actualizandose aunque el broker este caido.

## Archivos Principales

- `boot.py`: configuracion de red Ethernet.
- `main.py`: lectura de sensores, servidor web, MQTT y watchdog.
- `static/Chart.min.js`: libreria para la grafica web.
- `gtk/logguer_energia2m_ui.py`: GUI de escritorio para controlar START/STOP y guardar CSV.
- `lib/microdot/microdot.py`: servidor web Microdot.
- `lib/mqtt_as/__init__.mpy`: cliente MQTT asincrono.
- `backup/`: copia local de respaldo del firmware.
- `sensor datasheet.pdf`: hoja de datos del sensor.
- `ESP32-GATEWAY-GPIOs-Rev.F-up.pdf`: referencia de pines de la tarjeta.

## Calibracion

Cada sensor usa un offset calibrado cerca de 1.56 V y un deadband por canal:

```python
sensor_norte = HSTS016L(32, offset=1.5696, deadband=0.35)
sensor_sur = HSTS016L(35, offset=1.5685, deadband=0.35)
sensor_este = HSTS016L(36, offset=1.5650, deadband=0.30)
sensor_oeste = HSTS016L(39, offset=1.5668, deadband=0.30)
```

Para recalibrar, medir la salida del sensor con corriente cero y actualizar el `offset` correspondiente. El codigo incluye un `print` comentado dentro de `read_current()` para facilitar esa medicion.

La lectura usa un filtro anti-picos con `n=41` y `trim=10`: toma varias muestras ADC, las ordena, descarta los extremos bajos y altos, y promedia el bloque central antes de convertir a amperes.

## Pruebas Recomendadas

1. Cargar `boot.py`, `main.py`, `static/`, `lib/microdot/` y `lib/mqtt_as/` al ESP32.
2. Reiniciar con Ethernet conectado y confirmar la IP `192.168.0.90`.
3. Abrir `http://192.168.0.90/` y verificar que la grafica se actualiza.
4. Apagar temporalmente el broker MQTT y confirmar que la pagina web sigue actualizando lecturas.
5. Encender el broker y enviar `{"comando":"start","SAMPLE_INTERVAL_MS":500}`.
6. Verificar publicaciones en `oan/control/2m/consola/energia`.
7. Enviar `{"comando":"stop"}` y confirmar que deja de publicar, pero la web sigue midiendo.
8. Probar que `/static/Chart.min.js` funciona y que rutas como `/static/../main.py` son rechazadas.

## Notas De Operacion

- El firmware esta pensado para MicroPython en ESP32 con Ethernet LAN8710.
- Si el ESP32 se reinicia repetidamente, revisar primero conexion Ethernet, broker MQTT, offsets de sensores y memoria libre.
- La GUI GTK usa el mismo broker y topicos que el firmware, por lo que no requiere cambios mientras se mantenga el formato JSON actual.
