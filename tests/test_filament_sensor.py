import importlib.util
import os
import sys
import types
import unittest


MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'moonraker_obico',
    'printer.py',
)

# printer.py uses relative imports, so load it as a member of a stub package to avoid pulling in
# the dependencies of the real moonraker_obico package.
PKG = 'obico_printer_stub_pkg'
_pkg = types.ModuleType(PKG)
_pkg.__path__ = []
sys.modules[PKG] = _pkg

_config = types.ModuleType(PKG + '.config')
_config.Config = object
sys.modules[PKG + '.config'] = _config

_version = types.ModuleType(PKG + '.version')
_version.VERSION = '0.0.0'
sys.modules[PKG + '.version'] = _version

SPEC = importlib.util.spec_from_file_location(PKG + '.printer', MODULE_PATH)
printer = importlib.util.module_from_spec(SPEC)
sys.modules[PKG + '.printer'] = printer
SPEC.loader.exec_module(printer)


class StubFilamentSensorConfig(object):
    def __init__(self, sensor_names=()):
        self.sensor_names = list(sensor_names)

    def is_monitored(self, sensor_name):
        return not self.sensor_names or sensor_name in self.sensor_names


class StubConfig(object):
    def __init__(self, sensor_names=()):
        self.filament_sensor = StubFilamentSensorConfig(sensor_names)


def sensor(filament_detected, enabled=True):
    return {'filament_detected': filament_detected, 'enabled': enabled}


def status(print_state='printing', **sensors):
    data = {
        'webhooks': {'state': 'ready'},
        'print_stats': {'state': print_state},
    }
    data.update(sensors)
    return data


RUNOUT = 'filament_switch_sensor runout'


class FilamentSensorsRanOutTestCase(unittest.TestCase):

    def setUp(self):
        self.printer_state = printer.PrinterState(app_config=StubConfig())

    def feed(self, *statuses):
        '''Feed a sequence of statuses and return the list of ran_out results, one per status.'''
        results = []
        for cur_status in statuses:
            prev_status = self.printer_state.update_status(cur_status)
            results.append(self.printer_state.filament_sensors_ran_out(prev_status))
        return results

    def test_runout_while_printing(self):
        results = self.feed(
            status(**{RUNOUT: sensor(True)}),
            status(**{RUNOUT: sensor(False)}),
        )
        self.assertEqual(results, [[], ['runout']])

    def test_runout_while_paused(self):
        # Klipper pauses the print on runout, so the runout is often only visible once paused
        results = self.feed(
            status('printing', **{'filament_motion_sensor motion': sensor(True)}),
            status('paused', **{'filament_motion_sensor motion': sensor(False)}),
        )
        self.assertEqual(results, [[], ['motion']])

    def test_disabled_sensor_is_ignored(self):
        results = self.feed(
            status(**{RUNOUT: sensor(True, enabled=False)}),
            status(**{RUNOUT: sensor(False, enabled=False)}),
        )
        self.assertEqual(results, [[], []])

    def test_unmonitored_sensor_is_ignored(self):
        self.printer_state = printer.PrinterState(app_config=StubConfig(sensor_names=['other']))
        results = self.feed(
            status(**{RUNOUT: sensor(True), 'filament_switch_sensor other': sensor(True)}),
            status(**{RUNOUT: sensor(False), 'filament_switch_sensor other': sensor(True)}),
            status(**{RUNOUT: sensor(False), 'filament_switch_sensor other': sensor(False)}),
        )
        # The unmonitored runout must not block the monitored one either
        self.assertEqual(results, [[], [], ['other']])

    def test_no_active_job(self):
        results = self.feed(
            status('standby', **{RUNOUT: sensor(True)}),
            status('standby', **{RUNOUT: sensor(False)}),
        )
        self.assertEqual(results, [[], []])

    def test_no_sensor_configured(self):
        self.assertEqual(self.feed(status(), status()), [[], []])

    def test_empty_prev_status(self):
        # e.g. moonraker-obico just (re)started, or Klippy just reconnected
        self.assertEqual(self.feed(status(**{RUNOUT: sensor(False)})), [[]])

    def test_only_the_sensor_that_ran_out_is_reported(self):
        results = self.feed(
            status(**{'filament_switch_sensor left': sensor(True), 'filament_switch_sensor right': sensor(True)}),
            status(**{'filament_switch_sensor left': sensor(True), 'filament_switch_sensor right': sensor(False)}),
        )
        self.assertEqual(results, [[], ['right']])

    def test_sensor_flipping_while_paused_is_reported_once(self):
        # The pause macro retracts the filament tail back through the sensor, flipping it a few times
        results = self.feed(
            status('printing', **{RUNOUT: sensor(True)}),
            status('paused', **{RUNOUT: sensor(False)}),
            status('paused', **{RUNOUT: sensor(True)}),
            status('paused', **{RUNOUT: sensor(False)}),
            status('paused', **{RUNOUT: sensor(True)}),
            status('paused', **{RUNOUT: sensor(False)}),
        )
        self.assertEqual(results, [[], ['runout'], [], [], [], []])

    def test_second_sensor_running_out_in_the_same_pause_is_not_reported(self):
        results = self.feed(
            status('printing', **{'filament_switch_sensor left': sensor(True), 'filament_switch_sensor right': sensor(True)}),
            status('paused', **{'filament_switch_sensor left': sensor(False), 'filament_switch_sensor right': sensor(True)}),
            status('paused', **{'filament_switch_sensor left': sensor(False), 'filament_switch_sensor right': sensor(False)}),
        )
        self.assertEqual(results, [[], ['left'], []])

    def test_reported_again_after_resume(self):
        results = self.feed(
            status('printing', **{RUNOUT: sensor(True)}),
            status('paused', **{RUNOUT: sensor(False)}),
            status('paused', **{RUNOUT: sensor(True)}),
        )
        self.assertEqual(results, [[], ['runout'], []])

        self.printer_state.clear_filament_runout_reported()  # What the app does on PrintResumed

        results = self.feed(
            status('printing', **{RUNOUT: sensor(True)}),
            status('paused', **{RUNOUT: sensor(False)}),
        )
        self.assertEqual(results, [[], ['runout']])

    def test_reported_again_after_filament_reloaded_while_printing(self):
        # pause_on_runout: False - the print keeps going and the user swaps the spool on the fly
        results = self.feed(
            status('printing', **{RUNOUT: sensor(True)}),
            status('printing', **{RUNOUT: sensor(False)}),
            status('printing', **{RUNOUT: sensor(True)}),
            status('printing', **{RUNOUT: sensor(False)}),
        )
        self.assertEqual(results, [[], ['runout'], [], ['runout']])

    def test_unrelated_status_objects_are_ignored(self):
        results = self.feed(
            status(**{'extruder': {'temperature': 200}}),
            status(**{'extruder': {'temperature': 210}}),
        )
        self.assertEqual(results, [[], []])


if __name__ == '__main__':
    unittest.main()
