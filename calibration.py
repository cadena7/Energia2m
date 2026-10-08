"""Calibración persistente, compatible con MicroPython y Python de escritorio."""

import os
try:
    import ujson as json
except ImportError:
    import json


CHANNELS = ('Norte', 'Sur', 'Este', 'Oeste')
MAX_OFFSET_V = 3.33
MAX_DEADBAND_A = 20.0
_UNSET = object()


def validate_offsets(offsets):
    if not isinstance(offsets, dict) or len(offsets) != len(CHANNELS):
        raise ValueError('Se requieren los offsets de los cuatro canales.')
    result = {}
    for channel in CHANNELS:
        value = offsets.get(channel)
        # La comparación también rechaza NaN e infinitos; bool no es un voltaje.
        if (type(value) not in (int, float)
                or not 0 < value < MAX_OFFSET_V):
            raise ValueError('Offset de {}: debe ser mayor que 0 y menor que 3.33 V.'.format(channel))
        result[channel] = float(value)
    return result


def validate_deadbands(deadbands):
    if not isinstance(deadbands, dict) or len(deadbands) != len(CHANNELS):
        raise ValueError('Se requieren las zonas muertas de los cuatro canales.')
    result = {}
    for channel in CHANNELS:
        value = deadbands.get(channel)
        if type(value) not in (int, float) or not 0 <= value <= MAX_DEADBAND_A:
            raise ValueError('Zona muerta de {}: debe estar entre 0 y 20 A.'.format(channel))
        result[channel] = float(value)
    return result


class CalibrationStore:
    def __init__(self, path='config.json', allow_unconfigured=False):
        self.path = path
        self.backup = path + '.bak'
        self.temporary = path + '.tmp'
        self.warning = ''
        for filename, source in ((self.path, 'saved'), (self.backup, 'backup')):
            try:
                saved = self._read(filename)
                self.offsets = saved['offsets']
                self.deadbands = saved['deadbands']
                self.source = source
                if source == 'backup':
                    self.warning = 'Se recuperó el respaldo. Guarda para restaurar la configuración principal.'
                return
            except (OSError, ValueError, TypeError):
                pass
        self.offsets = {}
        self.deadbands = {}
        self.source = 'missing'
        self.warning = ('Falta config.json o no es válido y no hay un respaldo válido. '
                        'Mediciones y MQTT detenidos. Abre la web y crea la configuración '
                        'en el panel de calibración con Guardar y aplicar.')
        if not allow_unconfigured:
            raise ValueError(self.warning)

    @property
    def ready(self):
        return self.source in ('saved', 'backup')

    def _read(self, filename):
        with open(filename, 'r') as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or data.get('version') != 2:
            raise ValueError('Formato de configuración inválido.')
        return {
            'version': data['version'],
            'offsets': validate_offsets(data.get('offsets')),
            'deadbands': validate_deadbands(data.get('deadbands')),
        }

    @staticmethod
    def _remove(filename):
        try:
            os.remove(filename)
        except OSError as error:
            if error.args[0] != 2:  # ENOENT: no existe.
                raise

    @staticmethod
    def _sync():
        sync = getattr(os, 'sync', None)
        if sync is not None:
            sync()

    def save(self, offsets, deadbands=_UNSET):
        offsets = validate_offsets(offsets)
        deadbands = validate_deadbands(self.deadbands if deadbands is _UNSET else deadbands)
        document = json.dumps({'version': 2, 'offsets': offsets, 'deadbands': deadbands})
        # Aplicar exactamente los valores que recuperará el firmware al arrancar,
        # incluso si su serializador limita los decimales de los float.
        saved = json.loads(document)
        offsets = validate_offsets(saved['offsets'])
        deadbands = validate_deadbands(saved['deadbands'])
        if (self.source == 'saved' and offsets == self.offsets
                and deadbands == self.deadbands):
            try:
                if self._read(self.path) == saved:
                    return False
            except (OSError, ValueError, TypeError):
                pass

        with open(self.temporary, 'w') as stream:
            stream.write(document)
        self._sync()
        if self._read(self.temporary) != saved:
            raise OSError('No se pudo verificar la configuración escrita.')

        # Nunca sustituir un respaldo válido por un archivo principal corrupto.
        try:
            self._read(self.path)
            valid_primary = True
        except (OSError, ValueError, TypeError):
            valid_primary = False
        if valid_primary:
            self._remove(self.backup)
            os.rename(self.path, self.backup)
        else:
            self._remove(self.path)
        self._sync()
        os.rename(self.temporary, self.path)
        self._sync()

        # Solo confirmar el estado en RAM después de terminar la escritura.
        self.offsets = offsets
        self.deadbands = deadbands
        self.source = 'saved'
        self.warning = ''
        return True
