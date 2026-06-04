#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GUI GTK para registrar en CSV las corrientes de la consola del 2m.

Se conecta al broker MQTT 192.168.0.237, suscribe:
  - oan/control/2m/consola/energia           (datos)
  - oan/control/2m/consola/energia/estado    (estado)

Y envía comandos al ESP32 en:
  - oan/control/2m/consola/energia/comando

Comandos enviados:
  START:
    {"comando": "start", "SAMPLE_INTERVAL_MS": <intervalo_en_ms>}
  STOP:
    {"comando": "stop"}
  Cambiar intervalo sin tocar START/STOP:
    {"SAMPLE_INTERVAL_MS": <intervalo_en_ms>}
"""

import paho.mqtt.client as mqtt
import time
import json
from datetime import datetime
import queue
import threading
import os
import csv
from gi.repository import Gtk, GLib
import gi
gi.require_version("Gtk", "3.0")


# -----------------------------------------------------------------------------
# CONFIGURACIÓN MQTT (coincide con tu main.py en el ESP32)
# -----------------------------------------------------------------------------

MQTT_BROKER = "192.168.0.237"
MQTT_PORT = 1883
MQTT_KEEPALIVE = 60

COMMAND_TOPIC = "oan/control/2m/consola/energia/comando"
STATUS_TOPIC = "oan/control/2m/consola/energia/estado"
DATA_TOPIC = "oan/control/2m/consola/energia"

# -----------------------------------------------------------------------------
# HILO MQTT
#   - Se conecta al broker
#   - Suscribe datos y estado
#   - Mete las mediciones en data_queue
#   - Llama status_callback cuando llega un nuevo estado
# -----------------------------------------------------------------------------


class MqttWorker(threading.Thread):
    def __init__(self, data_queue, status_callback=None):
        super().__init__(daemon=True)
        self.data_queue = data_queue
        self.status_callback = status_callback
        self.client = mqtt.Client(client_id="GUI_Consola_Energia")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self._stop_event = threading.Event()

    # Callbacks de paho-mqtt
    def on_connect(self, client, userdata, flags, rc):
        print(f"[MQTT] Conectado al broker con código rc={rc}")
        if rc == 0:
            # Suscribir a datos y estado
            client.subscribe([(DATA_TOPIC, 1), (STATUS_TOPIC, 1)])
            print(f"[MQTT] Suscrito a: {DATA_TOPIC} y {STATUS_TOPIC}")
        else:
            print("[MQTT] Error al conectar (verificar broker)")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload_str = msg.payload.decode("utf-8")
        except Exception as e:
            print(f"[MQTT] Error decodificando payload: {e}")
            return

        # Datos de corriente
        if topic == DATA_TOPIC:
            try:
                data = json.loads(payload_str)
            except Exception as e:
                print(f"[MQTT] Error parseando JSON de datos: {e}")
                return

            # Esperamos llaves: Norte, Sur, Este, Oeste
            try:
                norte = float(data.get("Norte", 0.0))
                sur = float(data.get("Sur",   0.0))
                este = float(data.get("Este",  0.0))
                oeste = float(data.get("Oeste", 0.0))
            except Exception as e:
                print(f"[MQTT] Error leyendo campos de corriente: {e}")
                return

            ts = datetime.now().isoformat()
            self.data_queue.put((ts, norte, sur, este, oeste))

        # Estado STARTED / STOPPED / OFFLINE
        elif topic == STATUS_TOPIC:
            try:
                data = json.loads(payload_str)
            except Exception as e:
                print(f"[MQTT] Error parseando JSON de estado: {e}")
                return

            estado = data.get("estado", "")
            sample_ms = data.get("SAMPLE_INTERVAL_MS", 0)

            print(
                f"[MQTT] Estado remoto: estado={estado}, SAMPLE_INTERVAL_MS={sample_ms}")

            # Avisar a la GUI (por ejemplo para mostrarlo en una etiqueta)
            if self.status_callback is not None:
                # Usar GLib.idle_add para actualizar GUI desde el hilo principal
                GLib.idle_add(self.status_callback, estado,
                              sample_ms, priority=GLib.PRIORITY_DEFAULT)

    # Métodos públicos para enviar comandos
    def send_start(self, intervalo_s):
        intervalo_ms = int(intervalo_s * 1000)
        payload = {
            "comando": "start",
            "SAMPLE_INTERVAL_MS": intervalo_ms,
        }
        print(f"[MQTT] -> START payload: {payload}")
        self.client.publish(COMMAND_TOPIC, json.dumps(payload), qos=1)

    def send_stop(self):
        payload = {
            "comando": "stop",
        }
        print(f"[MQTT] -> STOP payload: {payload}")
        self.client.publish(COMMAND_TOPIC, json.dumps(payload), qos=1)

    def send_interval(self, intervalo_s):
        intervalo_ms = int(intervalo_s * 1000)
        payload = {
            "SAMPLE_INTERVAL_MS": intervalo_ms,
        }
        print(f"[MQTT] -> CAMBIA_INTERVALO payload: {payload}")
        self.client.publish(COMMAND_TOPIC, json.dumps(payload), qos=1)

    def run(self):
        try:
            print(f"[MQTT] Conectando a {MQTT_BROKER}:{MQTT_PORT} ...")
            self.client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
        except Exception as e:
            print(f"[MQTT] Error al conectar al broker: {e}")
            return

        # loop_forever se queda en este hilo
        try:
            self.client.loop_forever()
        except Exception as e:
            print(f"[MQTT] Error en loop_forever: {e}")

    def stop(self):
        print("[MQTT] Desconectando cliente MQTT...")
        self._stop_event.set()
        try:
            self.client.disconnect()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# VENTANA PRINCIPAL GTK
# -----------------------------------------------------------------------------

class VentanaPrincipal(Gtk.Window):
    def __init__(self):
        super().__init__(title="Telescopio 2.1m - Logger de Corriente (CSV)")
        self.set_border_width(10)
        self.set_default_size(520, 300)

        # Estado de logging (ESP32 corriendo / no corriendo)
        self.logging_activo = False

        # Control de CSV
        self.directorio_salida = os.getcwd()
        self.intervalo_s = 0.5  # el ESP32 viene con SAMPLE_INTERVAL_MS = 500 por defecto
        self.csv_file = None
        self.csv_writer = None
        self.csv_habilitado = False  # si en este ciclo START/STOP estamos grabando a CSV

        # Cola de datos
        self.data_queue = queue.Queue()

        # Worker MQTT
        self.mqtt_worker = MqttWorker(
            self.data_queue, status_callback=self._actualizar_estado_remoto)
        self.mqtt_worker.start()

        # Timer GTK para leer la cola de datos y escribir CSV
        self.id_timer_gui = GLib.timeout_add(200, self._procesar_cola_datos)

        self._construir_ui()
        self.connect("destroy", self.on_destroy)

    # Construcción de la interfaz
    def _construir_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(vbox)

        # --- Intervalo ---
        frame_intervalo = Gtk.Frame(label="Intervalo de muestreo (s)")
        box_int = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        frame_intervalo.add(box_int)

        adj = Gtk.Adjustment(
            value=self.intervalo_s,
            lower=0.1,
            upper=3600.0,
            step_increment=0.1,
            page_increment=1.0
        )

        self.spin_intervalo = Gtk.SpinButton()
        self.spin_intervalo.set_adjustment(adj)
        self.spin_intervalo.set_digits(2)
        self.spin_intervalo.set_value(self.intervalo_s)

        btn_aplicar_intervalo = Gtk.Button(label="Aplicar intervalo")
        btn_aplicar_intervalo.connect("clicked", self.on_aplicar_intervalo)

        box_int.pack_start(self.spin_intervalo, False, False, 0)
        box_int.pack_start(btn_aplicar_intervalo, False, False, 0)

        # --- Directorio de salida ---
        frame_dir = Gtk.Frame(label="Directorio de salida")
        box_dir = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        frame_dir.add(box_dir)

        self.lbl_directorio = Gtk.Label(label=self.directorio_salida, xalign=0)
        btn_seleccionar_dir = Gtk.Button(label="Seleccionar...")
        btn_seleccionar_dir.connect("clicked", self.on_seleccionar_directorio)

        box_dir.pack_start(self.lbl_directorio, True, True, 0)
        box_dir.pack_start(btn_seleccionar_dir, False, False, 0)

        # --- Check para guardar CSV ---
        self.chk_guardar_csv = Gtk.CheckButton(
            label="Guardar datos en archivo CSV")
        self.chk_guardar_csv.set_active(True)  # por defecto sí guarda

        # --- Botones START/STOP ---
        box_botones = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.btn_start = Gtk.Button(label="START (MQTT)")
        self.btn_stop = Gtk.Button(label="STOP (MQTT)")
        self.btn_stop.set_sensitive(False)

        self.btn_start.connect("clicked", self.on_start)
        self.btn_stop.connect("clicked", self.on_stop)

        box_botones.pack_start(self.btn_start, True, True, 0)
        box_botones.pack_start(self.btn_stop, True, True, 0)

        # --- Estado ---
        self.lbl_status = Gtk.Label(
            label="Listo. Esperando conexión MQTT...", xalign=0)
        self.lbl_estado_remoto = Gtk.Label(
            label="Estado remoto: (desconocido)", xalign=0)

        # Empaquetar todo
        vbox.pack_start(frame_intervalo, False, False, 0)
        vbox.pack_start(frame_dir, False, False, 0)
        vbox.pack_start(self.chk_guardar_csv, False, False, 0)
        vbox.pack_start(box_botones, False, False, 0)
        vbox.pack_start(self.lbl_status, False, False, 0)
        vbox.pack_start(self.lbl_estado_remoto, False, False, 0)

    # -------------------------------------------------------------------------
    # CALLBACKS UI
    # -------------------------------------------------------------------------

    def on_aplicar_intervalo(self, button):
        nuevo_intervalo = self.spin_intervalo.get_value()
        self.intervalo_s = nuevo_intervalo
        self._set_status(f"Intervalo local = {nuevo_intervalo:.2f} s")

        # Enviar comando de cambio de intervalo al ESP32
        self.mqtt_worker.send_interval(nuevo_intervalo)

    def on_seleccionar_directorio(self, button):
        dialog = Gtk.FileChooserDialog(
            title="Seleccionar directorio de salida",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        dialog.add_button(Gtk.STOCK_OPEN, Gtk.ResponseType.OK)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            folder = dialog.get_filename()
            if folder:
                self.directorio_salida = folder
                self.lbl_directorio.set_text(folder)

        dialog.destroy()

    def on_start(self, button):
        if self.logging_activo:
            return

        # Ver si en este ciclo queremos guardar CSV o no
        self.csv_habilitado = self.chk_guardar_csv.get_active()

        if self.csv_habilitado:
            # Crear archivo CSV nuevo
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            nombre_archivo = f"corriente_2m_{ts}.csv"
            ruta_csv = os.path.join(self.directorio_salida, nombre_archivo)

            try:
                self.csv_file = open(
                    ruta_csv, "w", newline="", encoding="utf-8")
                self.csv_writer = csv.writer(self.csv_file)
                # Encabezados: ajustados a los canales que manda el ESP32
                self.csv_writer.writerow(
                    ["timestamp_iso", "Norte_A", "Sur_A", "Este_A", "Oeste_A"])
                self._set_status(f"Logging en: {ruta_csv}")
            except Exception as e:
                # Si falla abrir el archivo, NO detenemos el START MQTT
                self._set_status(f"ERROR abriendo CSV (no se grabará): {e}")
                if self.csv_file:
                    self.csv_file.close()
                    self.csv_file = None
                self.csv_writer = None
                self.csv_habilitado = False
        else:
            self._set_status("START enviado (sin guardar CSV).")

        # Marcar que el ESP32 está corriendo
        self.logging_activo = True
        self.btn_start.set_sensitive(False)
        self.btn_stop.set_sensitive(True)

        # Enviar START al ESP32 con el intervalo actual
        self.mqtt_worker.send_start(self.intervalo_s)

    def on_stop(self, button):
        if not self.logging_activo:
            return

        # Enviar STOP al ESP32
        self.mqtt_worker.send_stop()

        # Marcar que detuvimos el ciclo
        self.logging_activo = False
        self.btn_start.set_sensitive(True)
        self.btn_stop.set_sensitive(False)

        # Cerrar CSV si estaba activado
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None

        self.csv_habilitado = False
        self._set_status("Logging detenido. (MQTT STOP enviado).")

    def on_destroy(self, widget):
        # Parar MQTT y cerrar CSV
        if self.mqtt_worker is not None:
            self.mqtt_worker.stop()
        if self.csv_file:
            self.csv_file.close()
        Gtk.main_quit()

    # -------------------------------------------------------------------------
    # MANEJO DE COLA Y ESCRITURA CSV
    # -------------------------------------------------------------------------

    def _procesar_cola_datos(self):
        """
        Se llama periódicamente (cada 200 ms) desde el hilo GTK
        para vaciar la cola de datos y escribir en el CSV.
        """
        try:
            while True:
                item = self.data_queue.get_nowait()
                # item: (timestamp_iso, Norte, Sur, Este, Oeste)
                if self.logging_activo and self.csv_writer is not None and self.csv_habilitado:
                    self.csv_writer.writerow(item)
                self.data_queue.task_done()
        except queue.Empty:
            pass

        # Mantener activo el timer
        return True

    # -------------------------------------------------------------------------
    # UTILIDADES
    # -------------------------------------------------------------------------

    def _set_status(self, texto):
        print(f"[STATUS] {texto}")
        self.lbl_status.set_text(texto)

    def _actualizar_estado_remoto(self, estado, sample_interval_ms):
        texto = f"Estado remoto: {estado}  |  SAMPLE_INTERVAL_MS={sample_interval_ms}"
        self.lbl_estado_remoto.set_text(texto)

        # Si el módulo remoto reporta otro intervalo, puedes reflejarlo:
        try:
            intervalo_s = float(sample_interval_ms) / 1000.0
            self.spin_intervalo.set_value(intervalo_s)
            self.intervalo_s = intervalo_s
        except Exception:
            pass


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    win = VentanaPrincipal()
    win.show_all()
    Gtk.main()
