# Archivos que debes subir al ESP32

Con MicroPython instalado en la placa, copia los siguientes archivos a su sistema de archivos. Conserva los nombres y las carpetas exactamente como aparecen aquí. La raíz `/` es la del microcontrolador; no subas la carpeta completa `Energia2m_HSTS016L` dentro de otra carpeta.

## Estructura obligatoria en el micro

```text
/
├── boot.py
├── main.py
├── calibration.py
├── config.json                 (opcional: también se puede crear desde la web)
├── lib/
│   ├── microdot/
│   │   └── microdot.py
│   └── mqtt_as/
│       └── __init__.mpy
└── static/
    ├── index.html
    ├── Chart.min.js
    ├── polling.js
    ├── dashboard.js
    ├── calibration.js
    └── calibration.css
```

| Archivo o carpeta del proyecto | Destino en el ESP32 | Función |
| --- | --- | --- |
| `boot.py` | `/boot.py` | Inicializa Ethernet y configura la IP `192.168.0.90`. |
| `main.py` | `/main.py` | Lee sensores y ejecuta MQTT, el servidor web y el watchdog. |
| `calibration.py` | `/calibration.py` | Carga, valida y guarda la calibración de los cuatro canales. |
| `lib/microdot/microdot.py` | `/lib/microdot/microdot.py` | Servidor web con las correcciones locales de conexiones y tiempos límite. Usa la copia de este proyecto. |
| `lib/mqtt_as/__init__.mpy` | `/lib/mqtt_as/__init__.mpy` | Cliente MQTT asíncrono. Copia el archivo binario sin modificarlo. |
| Toda la carpeta `static/` | `/static/` | Página web, gráfica y panel de calibración: los seis archivos del árbol anterior. |

## Qué hacer con `config.json`

En una instalación nueva puedes subir `config.json` a `/config.json`, o **crearlo desde la web**. Debe usar `version: 2` e incluir offsets y deadbands válidos de los cuatro canales. El archivo del proyecto sirve como punto de partida; calibra después cada sensor con corriente cero.

Los valores de calibración se obtienen exclusivamente del JSON. Si el principal falta o es inválido, se intenta `config.json.bak`. Si ninguno es válido, se muestra un error en la consola USB y en la web. Las mediciones de corriente y MQTT quedan detenidos, pero la web sigue disponible en `http://192.168.0.90/`. El panel se abre automáticamente, con offsets y deadbands vacíos. Introduce los ocho valores y pulsa **Guardar y aplicar**: se crea el JSON y las mediciones se habilitan sin reiniciar. MQTT se conecta, pero publicar mediciones sigue requiriendo el comando `start`. Mientras configuras, se leen únicamente los voltajes ADC filtrados para ayudarte a determinar los offsets con corriente cero.

Al pulsar **Guardar y aplicar** en el panel web, se actualiza `config.json` y se conserva la configuración anterior válida en `config.json.bak`.

**Si estás actualizando una placa ya calibrada, conserva su `config.json` y `config.json.bak`.** No los sustituyas por el archivo del proyecto ni borres toda la flash, porque perderías la calibración guardada. `config.json.tmp` es un archivo temporal gestionado por el programa; no tienes que subirlo.

## Archivos que se quedan en la computadora

No necesitas subir al microcontrolador:

- `gtk/`: programa de escritorio para controlar MQTT y guardar CSV.
- `tests/`: pruebas que se ejecutan en la computadora.
- `backup/`: copias de respaldo; usa los archivos actuales de la raíz y de `static/`.
- Los archivos PDF: documentación del sensor y de la tarjeta.
- `README.md` y `sube.md`: documentación.

## Orden de carga y comprobación

1. Conecta la placa por USB y abre su sistema de archivos con tu herramienta de carga de MicroPython.
2. Detén el programa en ejecución antes de copiar los archivos.
3. Crea las carpetas `/lib/microdot`, `/lib/mqtt_as` y `/static` si no existen.
4. Sube las bibliotecas, los seis archivos de `static/` y `calibration.py`; después sube `config.json` (opcional en instalación nueva), `boot.py` y `main.py` a la raíz.
5. Conecta Ethernet y reinicia la placa. `boot.py` espera la conexión y reinicia el ESP32 si no conecta en 30 segundos.
6. Desde una computadora con acceso a esa red, abre `http://192.168.0.90/`. Comprueba las lecturas, la gráfica y el panel de calibración. Tras actualizar, recarga el navegador sin caché.

Para actualizar esta revisión en una placa que ya funciona, sube **`main.py`, `calibration.py`, toda la carpeta `static/` y `lib/microdot/microdot.py`**. Si `boot.py` y `lib/mqtt_as/__init__.mpy` ya coinciden con los de este proyecto, puedes conservarlos.

Los módulos `machine`, `network`, `asyncio`, `time`, `gc`, `os` y `ujson` los proporciona el firmware MicroPython; no son archivos que debas copiar desde este proyecto. El firmware debe admitir Ethernet LAN8710 y ser compatible con el archivo compilado `.mpy` incluido. Si aparece un error de incompatibilidad de `.mpy`, habrá que usar una versión de `mqtt_as` compilada para ese firmware o su código fuente compatible.
