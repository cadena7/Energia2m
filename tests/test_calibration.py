"""Pruebas en PC: python3 -m unittest discover -s tests -v."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from calibration import CalibrationStore, validate_offsets

# Valores de prueba, no usados como respaldo en el firmware.
DEFAULT_OFFSETS = {'Norte': 1.5696, 'Sur': 1.5685, 'Este': 1.5650, 'Oeste': 1.5668}
DEFAULT_DEADBANDS = {'Norte': 0.35, 'Sur': 0.35, 'Este': 0.30, 'Oeste': 0.30}

def seed_config(path):
    Path(path).write_text(json.dumps({'version': 2, 'offsets': DEFAULT_OFFSETS,
                                    'deadbands': DEFAULT_DEADBANDS}))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, 'config.json')
        seed_config(self.path)
        self.store = CalibrationStore(self.path)
        self.updated = dict(DEFAULT_OFFSETS, Norte=1.5712, Oeste=1.5634)

    def test_first_boot_and_persistence_after_recreation(self):
        self.assertEqual(self.store.offsets, DEFAULT_OFFSETS)
        self.assertEqual(self.store.source, 'saved')
        self.assertTrue(self.store.save(self.updated))
        reboot = CalibrationStore(self.path)
        self.assertEqual(reboot.offsets, self.updated)
        self.assertEqual(reboot.source, 'saved')
        self.assertEqual(reboot.warning, '')

    def test_unchanged_save_does_not_write(self):
        self.assertFalse(self.store.save(DEFAULT_OFFSETS))
        with patch('builtins.open', wraps=open) as opened:
            self.assertFalse(self.store.save(DEFAULT_OFFSETS))
        self.assertTrue(all(call.args[1] == 'r' for call in opened.call_args_list))

    def test_invalid_offsets_rejected_without_writing(self):
        for value in (None, True, '1.56', 0, -1, 3.33, float('nan'), float('inf')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.store.save(dict(DEFAULT_OFFSETS, Norte=value))
        for value in (None, [], {}, dict(DEFAULT_OFFSETS, Extra=1.5)):
            with self.assertRaises(ValueError):
                validate_offsets(value)
        self.assertEqual(os.listdir(self.directory.name), ['config.json'])

    def test_corrupt_primary_loads_previous_backup_and_can_be_repaired(self):
        self.store.save(DEFAULT_OFFSETS)
        self.store.save(self.updated)
        Path(self.path).write_text('{broken')
        reboot = CalibrationStore(self.path)
        self.assertEqual(reboot.offsets, DEFAULT_OFFSETS)
        self.assertEqual(reboot.source, 'backup')
        self.assertTrue(reboot.warning)
        reboot.save(self.updated)
        self.assertEqual(CalibrationStore(self.path).offsets, self.updated)
        self.assertEqual(reboot._read(reboot.backup)['offsets'], DEFAULT_OFFSETS)

    def test_write_failure_keeps_previous_offsets(self):
        self.store.save(DEFAULT_OFFSETS)
        with patch('builtins.open', side_effect=OSError(28, 'full')):
            with self.assertRaises(OSError):
                self.store.save(self.updated)
        self.assertEqual(self.store.offsets, DEFAULT_OFFSETS)
        self.assertEqual(CalibrationStore(self.path).offsets, DEFAULT_OFFSETS)

    def test_interruption_before_promotion_recovers_backup_and_retry_repairs(self):
        self.store.save(DEFAULT_OFFSETS)
        rename = os.rename

        def fail_promotion(source, destination):
            if source == self.store.temporary:
                raise OSError(5, 'simulated interruption')
            rename(source, destination)

        with patch('calibration.os.rename', side_effect=fail_promotion):
            with self.assertRaises(OSError):
                self.store.save(self.updated)
        self.assertEqual(self.store.offsets, DEFAULT_OFFSETS)
        reboot = CalibrationStore(self.path)
        self.assertEqual(reboot.source, 'backup')
        self.assertEqual(reboot.offsets, DEFAULT_OFFSETS)
        # Incluso guardar los valores anteriores debe reparar el archivo ausente.
        self.assertTrue(self.store.save(DEFAULT_OFFSETS))
        self.assertEqual(CalibrationStore(self.path).source, 'saved')

    def test_missing_files_stop_startup(self):
        os.remove(self.path)
        with self.assertRaisesRegex(ValueError, 'Mediciones y MQTT detenidos'):
            CalibrationStore(self.path)

    def test_corrupt_files_and_orphan_temporary_stop_startup(self):
        Path(self.path).write_text('[]')
        Path(self.store.backup).write_text('{"version":2,"offsets":{}}')
        seed_config(self.store.temporary)
        with self.assertRaisesRegex(ValueError, 'Mediciones y MQTT detenidos'):
            CalibrationStore(self.path)

    def test_legacy_file_without_deadbands_is_rejected(self):
        Path(self.path).write_text(json.dumps({'version': 1, 'offsets': self.updated}))
        with self.assertRaisesRegex(ValueError, 'Mediciones y MQTT detenidos'):
            CalibrationStore(self.path)

    def test_missing_deadbands_is_rejected(self):
        Path(self.path).write_text(json.dumps({'version': 2, 'offsets': self.updated}))
        with self.assertRaisesRegex(ValueError, 'Mediciones y MQTT detenidos'):
            CalibrationStore(self.path)

    def test_deadbands_persist_and_are_preserved_by_offset_only_clients(self):
        deadbands = dict(DEFAULT_DEADBANDS, Norte=0, Oeste=0.12)
        self.store.save(DEFAULT_OFFSETS, deadbands)
        reboot = CalibrationStore(self.path)
        self.assertEqual(reboot.deadbands, deadbands)
        reboot.save(self.updated)
        self.assertEqual(CalibrationStore(self.path).deadbands, deadbands)
        self.assertEqual(CalibrationStore(self.path).offsets, self.updated)

    def test_invalid_deadbands_do_not_save_either_group(self):
        self.store.save(DEFAULT_OFFSETS)
        before = Path(self.path).read_bytes()
        invalid = [None, {}, [], dict(DEFAULT_DEADBANDS, Extra=0.1)]
        invalid += [dict(DEFAULT_DEADBANDS, Norte=value)
                    for value in (True, '0.2', -0.1, 20.1, float('nan'), float('inf'))]
        for deadbands in invalid:
            with self.subTest(deadbands=deadbands):
                with self.assertRaises(ValueError):
                    self.store.save(self.updated, deadbands)
        self.assertEqual(Path(self.path).read_bytes(), before)
        self.assertEqual(self.store.offsets, DEFAULT_OFFSETS)
        self.assertEqual(self.store.deadbands, DEFAULT_DEADBANDS)

    def test_backup_recovers_offsets_and_deadbands_together(self):
        deadbands = dict(DEFAULT_DEADBANDS, Norte=0.2)
        self.store.save(self.updated, deadbands)
        self.store.save(DEFAULT_OFFSETS, DEFAULT_DEADBANDS)
        Path(self.path).write_text('{broken')
        reboot = CalibrationStore(self.path)
        self.assertEqual(reboot.offsets, self.updated)
        self.assertEqual(reboot.deadbands, deadbands)
        self.assertEqual(reboot.source, 'backup')


def load_firmware():
    machine = types.ModuleType('machine')
    machine.Pin = lambda pin: pin
    machine.ADC = lambda *args, **kwargs: types.SimpleNamespace(read=lambda: 1930)
    machine.WDT = lambda **kwargs: types.SimpleNamespace(feed=lambda: None)
    mqtt = types.ModuleType('mqtt_as')
    mqtt.config = {}
    mqtt.MQTTClient = type('MQTTClient', (), {'__init__': lambda self, config: None})
    spec = importlib.util.spec_from_file_location('firmware_test', Path(__file__).resolve().parents[1] / 'main.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'machine': machine, 'mqtt_as': mqtt, 'ujson': json}):
        spec.loader.exec_module(module)
    return module


class FirmwareStartupTests(unittest.TestCase):
    def test_missing_config_starts_web_configuration_without_mqtt(self):
        with patch('calibration.open', side_effect=OSError(2, 'missing'), create=True):
            fw = load_firmware()
        self.assertFalse(fw.calibration.ready)
        self.assertIsNone(fw.client)
        self.assertIsNone(fw.sensor_norte.offset)
        self.assertFalse(fw.measurements_ready)
        fw.mqtt_callback(b'command', b'{"comando":"start"}', False)
        self.assertFalse(fw.publishing_enabled)


class FirmwareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fw = load_firmware()
        config_path = os.path.join(self.directory.name, 'config.json')
        seed_config(config_path)
        self.fw.calibration = CalibrationStore(config_path)
        self.fw.maintain_memory = lambda: None
        self.updated = dict(DEFAULT_OFFSETS, Norte=1.5732)

    async def test_web_creates_missing_config_and_enables_measurements(self):
        os.remove(self.fw.calibration.path)
        self.fw.calibration = CalibrationStore(self.fw.calibration.path, allow_unconfigured=True)
        for sensor in self.fw.sensors.values():
            sensor.offset = sensor.deadband = None
        data, status = await self.post({'offsets': self.updated})
        self.assertEqual(status, 400)
        with patch.object(self.fw.calibration, '_sync', side_effect=OSError(28, 'full')):
            self.assertEqual((await self.post({'offsets': self.updated, 'deadbands': DEFAULT_DEADBANDS}))[1], 500)
        self.assertFalse(self.fw.calibration.ready)
        self.assertIsNone(self.fw.sensor_norte.offset)
        data, status = await self.post({'offsets': self.updated, 'deadbands': DEFAULT_DEADBANDS})
        self.assertEqual(status, 200)
        self.assertTrue(data['ready'])
        self.assertEqual(self.fw.sensor_norte.offset, self.updated['Norte'])
        self.assertEqual(CalibrationStore(self.fw.calibration.path).offsets, self.updated)
        self.assertFalse(self.fw.measurements_ready)

    async def test_unconfigured_cycle_only_reads_voltage(self):
        os.remove(self.fw.calibration.path)
        self.fw.calibration = CalibrationStore(self.fw.calibration.path, allow_unconfigured=True)
        async def one_cycle(milliseconds):
            if milliseconds:
                raise asyncio.CancelledError()
            await asyncio.sleep(0)
        with patch.object(asyncio, 'sleep_ms', one_cycle, create=True):
            with self.assertRaises(asyncio.CancelledError):
                await self.fw.sampling_task()
        self.assertFalse(self.fw.measurements_ready)
        self.assertTrue(all(not points for points in self.fw.history.values()))
        self.assertIsNotNone(self.fw.sensor_norte.voltage)
        data = json.loads((await self.fw.api_calibration(types.SimpleNamespace(method='GET')))[0])
        self.assertFalse(data['ready'])
        self.assertEqual(data['offsets'], {})

    async def test_mqtt_waits_without_constructing_client_when_unconfigured(self):
        os.remove(self.fw.calibration.path)
        self.fw.calibration = CalibrationStore(self.fw.calibration.path, allow_unconfigured=True)

        async def stop_wait(milliseconds):
            raise asyncio.CancelledError()

        with patch.object(self.fw, 'MQTTClient') as client_type:
            with patch.object(asyncio, 'sleep_ms', stop_wait, create=True):
                with self.assertRaises(asyncio.CancelledError):
                    await self.fw.mqtt_task()
            client_type.assert_not_called()

    async def post(self, data):
        response = await self.fw.api_calibration(types.SimpleNamespace(method='POST', json=data))
        return json.loads(response[0]), response[1]

    async def test_api_save_applies_without_reboot_and_resets_history(self):
        for history in self.fw.history.values():
            history.append(4.0)
        self.fw.measurements_ready = True
        data, status = await self.post({'offsets': self.updated})
        self.assertEqual(status, 200)
        self.assertEqual(data['offsets'], self.updated)
        self.assertEqual(self.fw.sensor_norte.offset, self.updated['Norte'])
        self.assertTrue(all(not points for points in self.fw.history.values()))
        self.assertFalse(self.fw.measurements_ready)
        self.assertEqual(CalibrationStore(self.fw.calibration.path).offsets, self.updated)

    async def test_invalid_and_failed_save_do_not_change_active_calibration(self):
        for payload in (None, [], {'offsets': dict(self.updated, Norte=True)}):
            self.assertEqual((await self.post(payload))[1], 400)
        with patch.object(self.fw.calibration, 'save', side_effect=OSError(28, 'full')):
            self.assertEqual((await self.post({'offsets': self.updated}))[1], 500)
        self.assertEqual(self.fw.sensor_norte.offset, DEFAULT_OFFSETS['Norte'])

    async def test_config_change_waits_for_measurement_cycle(self):
        await self.fw.measurement_lock.acquire()
        pending = asyncio.create_task(self.post({'offsets': self.updated}))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.assertEqual(self.fw.sensor_norte.offset, DEFAULT_OFFSETS['Norte'])
        self.fw.measurement_lock.release()
        self.assertEqual((await pending)[1], 200)

    async def test_voltage_is_filtered_and_available_even_with_deadband(self):
        async def yield_adc(milliseconds):
            await asyncio.sleep(0)

        sensor = self.fw.sensor_norte
        samples = iter([0] * 10 + [1930] * 21 + [4095] * 10)
        sensor.adc.read = lambda: next(samples)
        with patch.object(asyncio, 'sleep_ms', yield_adc, create=True):
            current = await sensor.read_current()
        self.assertEqual(current, 0.0)
        self.assertAlmostEqual(sensor.voltage, 1930 * (3.33 / 4095))
        response = await self.fw.api_calibration(types.SimpleNamespace(method='GET'))
        self.assertEqual(json.loads(response[0])['voltages']['Norte'], sensor.voltage)
        self.assertIsNone(json.loads(response[0])['voltages']['Sur'])

    async def test_first_new_sample_uses_saved_offsets(self):
        await self.post({'offsets': self.updated})

        async def one_cycle(milliseconds):
            if milliseconds:
                raise asyncio.CancelledError()
            await asyncio.sleep(0)

        self.fw.sensor_norte.adc.read = lambda: 2200
        with patch.object(asyncio, 'sleep_ms', one_cycle, create=True):
            with self.assertRaises(asyncio.CancelledError):
                await self.fw.sampling_task()
        expected = (2200 * (3.33 / 4095) - self.updated['Norte']) / self.fw.SENSITIVITY_HSTS016L
        self.assertAlmostEqual(self.fw.history['Norte'][0], expected)
        self.assertTrue(self.fw.measurements_ready)
        self.assertTrue(all(len(points) == 1 for points in self.fw.history.values()))

    async def test_deadband_only_change_applies_without_reboot(self):
        deadbands = dict(DEFAULT_DEADBANDS, Norte=0)
        self.fw.history['Norte'].append(0.0)
        self.fw.measurements_ready = True
        data, status = await self.post({'offsets': DEFAULT_OFFSETS, 'deadbands': deadbands})
        self.assertEqual(status, 200)
        self.assertEqual(data['deadbands'], deadbands)
        self.assertEqual(self.fw.sensor_norte.deadband, 0)
        self.assertEqual(self.fw.sensor_sur.deadband, 0.35)
        self.assertEqual(self.fw.history['Norte'], [])
        self.assertFalse(self.fw.measurements_ready)
        self.assertEqual(CalibrationStore(self.fw.calibration.path).deadbands, deadbands)
        async def yield_adc(milliseconds):
            await asyncio.sleep(0)
        with patch.object(asyncio, 'sleep_ms', yield_adc, create=True):
            current = await self.fw.sensor_norte.read_current()
        self.assertNotEqual(current, 0.0)
        self.assertAlmostEqual(current, (1930 * (3.33 / 4095) - DEFAULT_OFFSETS['Norte']) / 0.03125)
        response = await self.fw.api_calibration(types.SimpleNamespace(method='GET'))
        self.assertEqual(json.loads(response[0])['deadbands'], deadbands)

    async def test_invalid_or_failed_deadband_save_keeps_active_values(self):
        bad = dict(DEFAULT_DEADBANDS, Norte=-1)
        self.assertEqual((await self.post({'offsets': self.updated, 'deadbands': bad}))[1], 400)
        self.assertEqual((await self.post({'offsets': self.updated, 'deadbands': None}))[1], 400)
        with patch.object(self.fw.calibration, 'save', side_effect=OSError(28, 'full')):
            self.assertEqual((await self.post({'offsets': self.updated, 'deadbands': dict(DEFAULT_DEADBANDS, Norte=0)}))[1], 500)
        self.assertEqual(self.fw.sensor_norte.offset, DEFAULT_OFFSETS['Norte'])
        self.assertEqual(self.fw.sensor_norte.deadband, DEFAULT_DEADBANDS['Norte'])


if __name__ == '__main__':
    unittest.main()
