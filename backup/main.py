# main.py
# Medidor de Energia de la Consola del 2m
# © 2026 — Desarrollado por Edgar Omar Cadena Zepeda (IA-UNAM Ensenada)
# 6 de mayo de 2026

import machine
import asyncio
import time
import gc
from mqtt_as import MQTTClient, config
import ujson
from lib.microdot.microdot import Microdot, send_file


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
DEADBAND_A = 0.3


class HSTS016L:
    def __init__(self, adc_pin, offset=1.56, deadband=DEADBAND_A):
        self.adc = machine.ADC(machine.Pin(adc_pin), atten=3)
        self.offset = offset
        self.pin = adc_pin
        self.deadband = deadband

    async def read_current(self, n=41, trim=10):

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

        # ---------------------------------------------------------
        # 📌 DESCOMENTAR ESTE PRINT PARA CALIBRAR OFFSET DEL SENSOR
        #
        # print("Sensor ADC Pin({}): V_ADC a 0A = {:.7f}".format(self.pin, v))
        #
        # Usa esos valores (con corriente cero) para definir nuevos offsets:
        # sensor_norte = HSTS016L(32, offset=VALOR_MEDIDO)
        # ---------------------------------------------------------

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


sensor_norte = HSTS016L(32, offset=1.5696, deadband=0.35)
sensor_sur = HSTS016L(35, offset=1.5685, deadband=0.35)
sensor_este = HSTS016L(36, offset=1.5650, deadband=0.30)
sensor_oeste = HSTS016L(39, offset=1.5668, deadband=0.30)


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
client = MQTTClient(config)


async def safe_publish(topic, payload, *, retain=False, qos=1, timeout_s=3):
    pub_task = client.publish(topic, payload, retain=retain, qos=qos)
    await asyncio.wait_for(pub_task, timeout_s)


# =========================
# MICRODOT
# =========================
app = Microdot()


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
        return send_file('static/' + filename)
    except OSError:
        return 'Not found', 404


@app.route('/')
async def index(request):
    html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Medidor de Energía Consola 2m</title>
    <script src="/static/Chart.min.js"></script>
</head>
<body>
    <h1>Medidor de Energía Consola 2m</h1>

    <!-- ===== VALORES NUMÉRICOS ===== -->
    <div style="display:flex; gap:40px; font-size:22px; margin-bottom:15px;">
        <div style="color:red;">
            <strong>Norte:</strong> <span id="valN">0.00</span> A
        </div>
        <div style="color:blue;">
            <strong>Sur:</strong> <span id="valS">0.00</span> A
        </div>
        <div style="color:green;">
            <strong>Este:</strong> <span id="valE">0.00</span> A
        </div>
        <div style="color:purple;">
            <strong>Oeste:</strong> <span id="valO">0.00</span> A
        </div>
    </div>

    <canvas id="chart" width="400" height="200"></canvas>

    <footer style="margin-top:25px; text-align:center; color:#666; font-size:13px;">
        © 2026 — Desarrollado por <strong>Edgar Omar Cadena Zepeda</strong><br>
        Instituto de Astronomía — UNAM, Ensenada
    </footer>

    <script>
        let ctx = document.getElementById('chart').getContext('2d');
        let chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    { label: 'Norte (A)', data: [], borderColor: 'red', borderWidth:1, yAxisID: 'y' },
                    { label: 'Sur (A)',   data: [], borderColor: 'blue', borderWidth:1, yAxisID: 'y' },
                    { label: 'Este (A)',  data: [], borderColor: 'green', borderWidth:1, yAxisID: 'y' },
                    { label: 'Oeste (A)', data: [], borderColor: 'purple', borderWidth:1, yAxisID: 'y2' }
                ]
            },
            options: {
                        animation: false, // <-- Desactiva todas las animaciones
                        responsive: true,
                        scales: {
                            x: {
                                title: { display:true, text:'Tiempo (s)' }
                            },
                            y: {
                                position: 'left',
                                title:{ display:true, text:'Corriente (A)' },
                                beginAtZero: false,
                                ticks: { suggestedMin: -10, suggestedMax: 10 }
                            },
                            y2: {
                                display: true,
                                position: 'right',
                                beginAtZero: false,
                                ticks: { suggestedMin: -10, suggestedMax: 10 },
                                grid: {
                                    drawOnChartArea: false
                                }
                            }
                        }
                    }
        });

        async function update() {
                try {
                    let r = await fetch('/api/history');
                    if (!r.ok) return;
                    let d = await r.json();

                    let dt = Number(d.SAMPLE_INTERVAL_MS || 500) / 1000.0;
                    let last = d.Norte.length - 1;
                    chart.data.labels = d.Norte.map((_, index) => ((index - last) * dt).toFixed(1) + "s");
                    chart.data.datasets[0].data = d.Norte;
                    chart.data.datasets[1].data = d.Sur;
                    chart.data.datasets[2].data = d.Este;
                    chart.data.datasets[3].data = d.Oeste;

                    // ===== ACTUALIZAR VALORES NUMÉRICOS =====
                    if (d.Norte.length > 0) {
                        document.getElementById("valN").innerText = d.Norte[last].toFixed(2);
                        document.getElementById("valS").innerText = d.Sur[last].toFixed(2);
                        document.getElementById("valE").innerText = d.Este[last].toFixed(2);
                        document.getElementById("valO").innerText = d.Oeste[last].toFixed(2);
                    }

                    // ===== SINCRONIZAR EJES IZQUIERDO Y DERECHO =====
                    let allValues = [
                        ...d.Norte,
                        ...d.Sur,
                        ...d.Este,
                        ...d.Oeste
                    ];

                    if (allValues.length > 0) {
                        let min = Math.min(...allValues);
                        let max = Math.max(...allValues);

                        // Margen visual
                        let padding = (max - min) * 0.1 || 1;

                        chart.options.scales.y.min = min - padding;
                        chart.options.scales.y.max = max + padding;

                        chart.options.scales.y2.min = min - padding;
                        chart.options.scales.y2.max = max + padding;
                    }

                    chart.update('none');

                } catch(e){}
            }

        setInterval(update, 1000);
    </script>
</body>
</html>
"""
    return html, 200, {'Content-Type': 'text/html'}


@app.route('/api/history')
async def api_history(request):
    payload = {
        "SAMPLE_INTERVAL_MS": sample_interval_ms,
        "Norte": history["Norte"],
        "Sur": history["Sur"],
        "Este": history["Este"],
        "Oeste": history["Oeste"]
    }
    return ujson.dumps(payload), 200, {'Content-Type': 'application/json'}


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
    while True:
        try:
            maintain_memory()
            wdt.feed()

            sensor_values["Norte"] = await sensor_norte.read_current()
            sensor_values["Sur"] = await sensor_sur.read_current()
            sensor_values["Este"] = await sensor_este.read_current()
            sensor_values["Oeste"] = await sensor_oeste.read_current()
            append_history()

            await asyncio.sleep_ms(sample_interval_ms)

        except Exception as e:
            print("Sensor error:", e)
            await asyncio.sleep(1)


async def mqtt_task():
    global last_status_sent

    while True:
        try:
            maintain_memory()
            wdt.feed()

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
                    payload = ujson.dumps({
                        "Norte": round(sensor_values["Norte"], 2),
                        "Sur":   round(sensor_values["Sur"], 2),
                        "Este":  round(sensor_values["Este"], 2),
                        "Oeste": round(sensor_values["Oeste"], 2)
                    })

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
