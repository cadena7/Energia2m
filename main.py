# main.py
# Medidor de Energia de la Consola del 2m
# © 2026 — Desarrollado por Edgar Omar Cadena Zepeda (IA-UNAM Ensenada)
# 14 de septiembre de 2026

import machine
import asyncio
import time
import gc
from mqtt_as import MQTTClient, config
import ujson
from lib.microdot.microdot import Microdot, Request, send_file
from calibration import CalibrationStore


# =========================
# AJUSTES DEL SISTEMA
# =========================
SAMPLE_INTERVAL_MS = 500
MIN_INTERVAL_MS = 250

COMMAND_TOPIC = 'oan/control/2m/consola/energia/comando'
STATUS_TOPIC = 'oan/control/2m/consola/energia/estado'
MEAS_TOPIC = 'oan/control/2m/consola/energia'

publishing_enabled = False
sample_interval_ms = SAMPLE_INTERVAL_MS

status_state = 'STOPPED'
last_status_sent = None

# --- PROTECCIÓN RAM ---
RAM_CRITICAL = 10000  # 10 KB


def maintain_memory():
    gc.collect()
    free = gc.mem_free()
    if free < RAM_CRITICAL:
        print("🚨 RAM crítica:", free, "Reiniciando...")
        time.sleep(1)
        machine.reset()


# =========================
# CONFIGURACIÓN DE SENSORES
# =========================
VCC = 3.33
ADC_RESOLUTION = 4095

IPN_HSTS016L = 20
SENSITIVITY_HSTS016L = 0.625 / IPN_HSTS016L


class HSTS016L:
    def __init__(self, adc_pin, offset, deadband):
        self.adc = machine.ADC(machine.Pin(adc_pin), atten=3)
        self.offset = offset
        self.pin = adc_pin
        self.deadband = deadband
        self.voltage = None

    async def read_voltage(self, n=41, trim=10):

        # ---------------------------------------------------------
        # 📌 FILTRO ANTI-PICOS
        #
        # Se toman varias muestras ADC, se ordenan y se eliminan
        # las más altas y más bajas antes de promediar.
        #
        # Esto ayuda a eliminar ruido espurio del ADC del ESP32
        # y falsos picos de corriente.
        # ---------------------------------------------------------

        samples = []

        for _ in range(n):
            samples.append(self.adc.read())
            await asyncio.sleep_ms(0)

        samples.sort()

        # Elimina picos bajos y altos
        if len(samples) > (2 * trim):
            samples = samples[trim:-trim]

        avg = sum(samples) / len(samples)

        v = avg * (VCC / ADC_RESOLUTION)
        self.voltage = v  # Voltaje filtrado, antes de offset y deadband.

        # ---------------------------------------------------------
        # 📌 DESCOMENTAR ESTE PRINT PARA CALIBRAR OFFSET DEL SENSOR
        #
        # print("Sensor ADC Pin({}): V_ADC a 0A = {:.7f}".format(self.pin, v))
        #
        # Con corriente cero, registra varias lecturas para calcular
        # el offset con estadística. El voltaje ya tiene el filtro
        # anti-picos; todavía no se aplica el offset ni el deadband.
        # Guarda los offsets calculados en el panel web de calibración
        # (Guardar y aplicar), para que persistan en config.json.
        # ---------------------------------------------------------

        return v

    async def read_current(self, n=41, trim=10):
        if self.offset is None or self.deadband is None:
            raise ValueError('Sensor sin calibración válida.')
        v = await self.read_voltage(n, trim)
        I = (v - self.offset) / SENSITIVITY_HSTS016L

        # ---------------------------------------------------------
        # 📌 DEADBAND
        #
        # Corrientes pequeñas alrededor de cero se fuerzan a 0 A
        # para evitar ruido residual.
        # ---------------------------------------------------------

        if -self.deadband <= I <= self.deadband:
            I = 0.0

        return I


calibration = CalibrationStore(allow_unconfigured=True)
if calibration.warning:
    print('[Calibración]', calibration.warning)
sensor_norte = HSTS016L(32, offset=calibration.offsets.get('Norte'), deadband=calibration.deadbands.get('Norte'))
sensor_sur = HSTS016L(35, offset=calibration.offsets.get('Sur'), deadband=calibration.deadbands.get('Sur'))
sensor_este = HSTS016L(36, offset=calibration.offsets.get('Este'), deadband=calibration.deadbands.get('Este'))
sensor_oeste = HSTS016L(39, offset=calibration.offsets.get('Oeste'), deadband=calibration.deadbands.get('Oeste'))
sensors = {'Norte': sensor_norte, 'Sur': sensor_sur,
           'Este': sensor_este, 'Oeste': sensor_oeste}
measurement_lock = asyncio.Lock()
measurements_ready = False


sensor_values = {
    "Norte": 0.0,
    "Sur":   0.0,
    "Este":  0.0,
    "Oeste": 0.0
}


# =========================
# HISTORIAL
# =========================
MAX_HISTORY = 40
history = {
    "Norte": [],
    "Sur": [],
    "Este": [],
    "Oeste": []
}


# =========================
# CALLBACK MQTT
# =========================
def mqtt_callback(topic, msg, retained):
    global publishing_enabled, sample_interval_ms, status_state

    try:
        data = ujson.loads(msg.decode())
    except Exception as e:
        print("[MQTT] Comando JSON inválido:", e)
        return

    if not calibration.ready:
        return

    cmd = data.get("comando") or data.get("command")
    if isinstance(cmd, str):
        c = cmd.lower()
        if c == "start":
            publishing_enabled = True
            status_state = "STARTED"
        elif c == "stop":
            publishing_enabled = False
            status_state = "STOPPED"

    interval = data.get("SAMPLE_INTERVAL_MS")
    if isinstance(interval, int) and interval > 0:
        if interval < MIN_INTERVAL_MS:
            interval = MIN_INTERVAL_MS
        sample_interval_ms = interval


# =========================
# CONFIG MQTT
# =========================
config['server'] = '192.168.0.237'
config['ssid'] = ''
config['password'] = ''
config['client_id'] = 'ESP32_Consola_Energia'
config['subs_cb'] = mqtt_callback
config['keepalive'] = 60
config['qos'] = 1
config['will'] = (
    STATUS_TOPIC,
    '{"estado":"OFFLINE"}',
    True,
    1
)

MQTTClient.DEBUG = True

WDT_TIMEOUT_MS = 300_000
wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)


async def on_connect(client):
    await client.subscribe(COMMAND_TOPIC, 1)
    print("[MQTT] Suscrito a:", COMMAND_TOPIC)


config['connect_coro'] = on_connect
client = None


async def safe_publish(topic, payload, *, retain=False, qos=1, timeout_s=3):
    pub_task = client.publish(topic, payload, retain=retain, qos=qos)
    await asyncio.wait_for(pub_task, timeout_s)


# =========================
# MICRODOT
# =========================
app = Microdot()
# Calibration fits comfortably in 1 KB; keep incoming requests bounded.
Request.max_content_length = 1024
Request.max_body_length = 1024
Request.max_readline = 2048
Request.max_header_length = 8192


def is_safe_static_filename(filename):
    if not filename or filename.startswith('/') or filename.startswith('\\'):
        return False
    if '..' in filename.split('/'):
        return False

    allowed = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.'
    for ch in filename:
        if ch not in allowed:
            return False
    return True


@app.route('/static/<path:filename>')
async def static(request, filename):
    if not is_safe_static_filename(filename):
        return 'Forbidden', 403
    try:
        resp = send_file('static/' + filename)
        resp.headers['Connection'] = 'close'
        resp.headers['Cache-Control'] = ('public, max-age=86400'
                                         if filename == 'Chart.min.js' else 'no-cache')
        return resp
    except OSError:
        return 'Not found', 404


@app.route('/')
async def index(request):
    # Stream the HTML in small chunks instead of encoding a full copy per client.
    response = send_file('static/index.html', content_type='text/html; charset=utf-8')
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/api/history')
async def api_history(request):
    payload = {
        "SAMPLE_INTERVAL_MS": sample_interval_ms,
        "calibration_ready": calibration.ready,
        "warning": calibration.warning,
        "Norte": history["Norte"],
        "Sur": history["Sur"],
        "Este": history["Este"],
        "Oeste": history["Oeste"]
    }
    return ujson.dumps(payload), 200, {
        'Content-Type': 'application/json',
        'Connection': 'close',
        'Cache-Control': 'no-store'
    }


def calibration_response(payload, status=200):
    return ujson.dumps(payload), status, {
        'Content-Type': 'application/json',
        'Connection': 'close',
        'Cache-Control': 'no-store'
    }


@app.route('/api/calibration', methods=['GET', 'POST'])
async def api_calibration(request):
    global measurements_ready
    if request.method == 'GET':
        async with measurement_lock:
            return calibration_response({
                'offsets': calibration.offsets,
                'deadbands': calibration.deadbands,
                'voltages': {name: sensor.voltage for name, sensor in sensors.items()},
                'warning': calibration.warning,
                'ready': calibration.ready,
            })
    try:
        data = request.json
        if not isinstance(data, dict):
            raise ValueError('Se requiere un objeto JSON con offsets.')
        async with measurement_lock:
            previous = calibration.offsets
            previous_deadbands = calibration.deadbands
            # Los clientes anteriores pueden seguir enviando solo offsets.
            written = calibration.save(data.get('offsets'),
                                       data.get('deadbands', calibration.deadbands))
            changed = (previous != calibration.offsets
                       or previous_deadbands != calibration.deadbands)
            if changed:
                for name, sensor in sensors.items():
                    sensor.offset = calibration.offsets[name]
                    sensor.deadband = calibration.deadbands[name]
                    history[name].clear()
                # MQTT espera el primer ciclo completo con los nuevos offsets.
                measurements_ready = False
            return calibration_response({
                'offsets': calibration.offsets,
                'deadbands': calibration.deadbands,
                'ready': calibration.ready,
                'message': ('Calibración guardada y aplicada. No es necesario reiniciar.'
                            if written else 'La calibración ya estaba guardada y aplicada.'),
            })
    except (ValueError, TypeError) as error:
        return calibration_response({'error': str(error)}, 400)
    except OSError as error:
        print('[Calibración] Error al guardar:', error)
        return calibration_response({
            'error': 'No se pudo completar el guardado. La calibración activa no cambió; vuelve a intentarlo.'
        }, 500)


def build_status_payload():
    return ujson.dumps({
        "SAMPLE_INTERVAL_MS": sample_interval_ms,
        "estado": status_state
    })


# =========================
# TAREA PRINCIPAL
# =========================
def append_history():
    history["Norte"].append(sensor_values["Norte"])
    history["Sur"].append(sensor_values["Sur"])
    history["Este"].append(sensor_values["Este"])
    history["Oeste"].append(sensor_values["Oeste"])

    # SIN slicing (evita fragmentación)
    if len(history["Norte"]) > MAX_HISTORY:
        for k in history:
            history[k].pop(0)


async def sampling_task():
    global measurements_ready
    while True:
        try:
            maintain_memory()
            wdt.feed()

            async with measurement_lock:
                measurements_ready = False
                if calibration.ready:
                    for name, sensor in sensors.items():
                        sensor_values[name] = await sensor.read_current()
                    append_history()
                    measurements_ready = True
                else:
                    # Solo voltajes ADC para configurar el cero; no son corrientes.
                    for sensor in sensors.values():
                        await sensor.read_voltage()

            await asyncio.sleep_ms(sample_interval_ms)

        except Exception as e:
            print("Sensor error:", e)
            await asyncio.sleep(1)


async def mqtt_task():
    global last_status_sent, client

    while True:
        try:
            maintain_memory()
            wdt.feed()

            if not calibration.ready:
                await asyncio.sleep_ms(500)
                continue
            if client is None:
                client = MQTTClient(config)
            print("Intentando conectar al broker...")
            await client.connect()
            print("MQTT conectado.")
            last_status_sent = None

            while True:
                maintain_memory()
                wdt.feed()

                now_state = (status_state, sample_interval_ms)
                if now_state != last_status_sent:
                    await safe_publish(
                        STATUS_TOPIC,
                        build_status_payload(),
                        retain=True,
                        qos=1
                    )
                    last_status_sent = now_state

                if publishing_enabled:
                    async with measurement_lock:
                        payload = (ujson.dumps({
                            name: round(sensor_values[name], 2) for name in sensors
                        }) if measurements_ready else None)
                    if payload is not None:
                        await safe_publish(
                            MEAS_TOPIC,
                            payload,
                            retain=False,
                            qos=1
                        )

                await asyncio.sleep_ms(sample_interval_ms)

        except Exception as e:
            print("MQTT error:", e)
            try:
                if client is not None:
                    await client.disconnect()
            except Exception as disconnect_error:
                print("MQTT disconnect error:", disconnect_error)
            await asyncio.sleep(5)


async def web_task():
    while True:
        try:
            await app.start_server(port=80)
        except Exception as e:
            print("Web error:", e)
        finally:
            # Release the old listener before rebinding after an error.
            if app.server is not None:
                try:
                    app.server.close()
                    await asyncio.wait_for(app.server.wait_closed(), 2)
                except Exception as close_error:
                    print('Web close error:', close_error)
                app.server = None
        maintain_memory()
        await asyncio.sleep(3)


def el_main():
    loop = asyncio.get_event_loop()
    loop.create_task(sampling_task())
    loop.create_task(mqtt_task())
    loop.create_task(web_task())
    loop.run_forever()


if __name__ == '__main__':
    el_main()
