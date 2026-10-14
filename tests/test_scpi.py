"""Verify model dispatch, complete serial frames and the actual PyAR488 driver."""

import unittest
from unittest.mock import patch

from fixtures import FixtureController, FixtureDmm, FixtureSerial

from scpi_dmm.drivers import DriverFactory, HP34401ADriver, Keithley2000Driver
from scpi_dmm.instrument import QueryTimeout, ScpiMultimeter
from scpi_dmm.transport import AR488Transport, ConnectionOptions, RS232Transport

try:
    from PyAR488 import AR488
except ImportError:
    try:
        from PyAR488.PyAR488 import AR488
    except ImportError:
        AR488 = None


class SerialProtocolTests(unittest.TestCase):
    def make_device(self, model, **serial_options):
        fixture = FixtureSerial(FixtureDmm(model), **serial_options)
        options = ConnectionOptions("COM5")
        transport = RS232Transport(options, serial_factory=fixture.open)
        instrument = ScpiMultimeter(transport, status_delay=0, command_delay=0)
        instrument.connect(options)
        self.addCleanup(instrument.disconnect)
        return instrument, fixture

    def test_every_model_function_queries_only_supported_scpi(self):
        for model in ("hp34401a", "keithley2000"):
            device, serial = self.make_device(model)
            for code in device.driver.supported_functions:
                status = device.select_function(code)
                self.assertEqual(status.function_code, code)
                self.assertEqual(device.read_measurements(2)[0].unit, status.unit)
            self.assertFalse(any(command.startswith("++") for command in serial.device.commands))
            self.assertIn("SYST:REM", serial.device.commands)
            if model == "hp34401a":
                self.assertNotIn("SYST:LFR?", serial.device.commands)
                self.assertNotIn("SENS:VOLT:DC:DIG?", serial.device.commands)

    def test_all_receive_terminators_and_fragment_sizes(self):
        for ending in (b"\r", b"\n", b"\r\n", b"\n\r"):
            for fragment in (1, 2, 13, 4096):
                device, serial = self.make_device(model="hp34401a", ending=ending, fragment=fragment)
                self.assertAlmostEqual(device.read_measurements()[0].value, 1.084147098)
                self.assertEqual(device.refresh_status().inputs, "Front")

    def test_command_termination_uses_actual_selected_bytes(self):
        for ending in ("\r", "\n", "\r\n", "\n\r"):
            serial = FixtureSerial(FixtureDmm("keithley2000"))
            options = ConnectionOptions("COM5", termination=ending)
            transport = RS232Transport(options, serial_factory=serial.open)
            transport.open("COM5")
            transport.write("*IDN?\n")
            self.assertEqual(serial.packets, [("*IDN?" + ending).encode("ascii")])
            transport.close()

    def test_rs232_parameters_preserve_original_default_profile(self):
        _, serial = self.make_device("hp34401a")
        self.assertEqual(
            {
                key: serial.options[key]
                for key in (
                    "baudrate",
                    "parity",
                    "stopbits",
                    "bytesize",
                    "xonxoff",
                    "rtscts",
                    "dsrdtr",
                )
            },
            {
                "baudrate": 19200,
                "parity": "N",
                "stopbits": 1,
                "bytesize": 8,
                "xonxoff": False,
                "rtscts": False,
                "dsrdtr": False,
            },
        )
        self.assertEqual(serial.packets[0], b"*IDN?\r")

    def test_setting_write_and_readback_are_specific_to_each_model(self):
        hp, hp_serial = self.make_device("hp34401a")
        keithley, keithley_serial = self.make_device("keithley2000")
        hp.set_option("autorange", "OFF")
        self.assertEqual(keithley.set_option("range", "100 mV").values["range"], "100 mV")
        self.assertEqual(keithley.status.values["autorange"], "OFF")

    def test_hp_digits_are_blocked_until_autorange_is_off(self):
        hp, serial = self.make_device("hp34401a")
        self.assertEqual(hp.status.values["autorange"], "ON")
        before = list(serial.device.commands)
        with self.assertRaisesRegex(ValueError, "Autorange OFF"):
            hp.set_option("digits", "4½")
        self.assertEqual(serial.device.commands, before)
        self.assertTrue(hp.connected)
        hp.set_option("autorange", "OFF")
        self.assertEqual(hp.set_option("digits", "4½").digits, "4½")
        hp.set_option("autorange", "ON")
        before = list(serial.device.commands)
        with self.assertRaisesRegex(ValueError, "Autorange OFF"):
            hp.set_option("digits", "6½")
        self.assertEqual(serial.device.commands, before)
        keithley, _ = self.make_device("keithley2000")
        self.assertEqual(keithley.status.values["autorange"], "ON")
        self.assertEqual(keithley.set_option("digits", "4½").digits, "4½")

    def test_bandwidth_and_auto_zero_paths_differ(self):
        hp, hp_serial = self.make_device("hp34401a")
        keithley, k_serial = self.make_device("keithley2000")
        hp.select_function(2)
        keithley.select_function(2)
        hp.set_option("bandwidth", "20")
        keithley.set_option("bandwidth", "30")
        keithley.set_option("autozero", "OFF")
        self.assertIn("SENS:DET:BAND 20", hp_serial.device.commands)
        self.assertIn("SENS:VOLT:AC:DET:BAND 30", k_serial.device.commands)
        self.assertIn("SYST:AZER:STAT OFF", k_serial.device.commands)

    def test_invalid_or_unsupported_settings_send_no_commands(self):
        hp, serial = self.make_device("hp34401a")
        for key, value in (
            ("nplc", "3"),
            ("samples", "0"),
            ("samples", "1.5"),
            ("filter", "ON"),
            ("trigger_delay", "3601"),
            ("range", "1200 V"),
        ):
            before = len(serial.device.commands)
            with self.assertRaises(ValueError):
                hp.set_option(key, value)
            self.assertEqual(len(serial.device.commands), before)
        with self.assertRaises(ValueError):
            hp.select_function(9)

    def test_temperature_junction_and_voltage_math_branches(self):
        k, serial = self.make_device("keithley2000")
        status = k.select_function(9)
        self.assertIn("junction_temperature", {spec.key for spec in status.specs})
        status = k.set_option("junction", "Real")
        self.assertNotIn("junction_temperature", {spec.key for spec in status.specs})
        self.assertIn("tc_coefficient", {spec.key for spec in status.specs})
        k.set_option("tc_coefficient", "0.0002")
        k.set_option("tc_offset", "0.05463")
        self.assertIn("SENS:TEMP:TC:RJUN:REAL:TCOEF 0.0002", serial.device.commands)
        self.assertIn("SENS:TEMP:TC:RJUN:REAL:OFFS 0.05463", serial.device.commands)
        k.select_function(1)
        status = k.set_option("unit", "dBm")
        self.assertEqual(status.unit, "dBm")
        k.set_option("dbm_impedance", "600")
        self.assertIn("UNIT:VOLT:DC:DBM:IMP 600", serial.device.commands)

    def test_hp_math_order_null_and_frequency_gate(self):
        hp, serial = self.make_device("hp34401a")
        status = hp.set_option("reference", "0.25")
        self.assertEqual(status.values["relative"], "ON")
        commands = serial.device.commands
        index = commands.index("CALC:NULL:OFFS 0.25")
        self.assertEqual(commands[index - 2 : index], ["CALC:FUNC NULL", "CALC:STAT ON"])
        self.assertEqual(hp.set_option("unit", "dB").unit, "dB")
        self.assertEqual(hp.status.values["relative"], "OFF")
        self.assertEqual(hp.select_function(7).digits, "5½")
        hp.set_option("autorange", "OFF")

    def test_queries_are_read_automatically_and_binary_data_rejected(self):
        hp, _ = self.make_device("hp34401a")
        self.assertEqual(hp.send_custom("SYST:VERS?"), "1991.0")
        for command in ("++ver", "*IDN?\n*RST", "VOLT:DC:RANG µ"):
            with self.assertRaises(ValueError):
                hp.send_custom(command)

    def test_batch_is_complete_with_receive_time_and_bus_trigger(self):
        hp, serial = self.make_device("hp34401a")
        hp.set_option("samples", "10")
        hp.set_option("trigger", "Bus")
        samples = hp.read_measurements(lambda: 3.14)
        self.assertEqual(len(samples), 10)
        self.assertTrue(all(sample.timestamp == 3.14 for sample in samples))
        self.assertEqual(serial.device.commands[-3:], ["INIT", "*TRG", "FETC?"])

    def test_timeout_closes_session_and_reports_incomplete_frame(self):
        hp, serial = self.make_device("hp34401a")
        hp.query_timeout = 0.02
        with patch.object(serial.device, "execute", return_value=None):
            with self.assertRaises(QueryTimeout):
                hp.refresh_status()
        self.assertFalse(hp.connected)
        self.assertTrue(serial.closed)

    def test_status_wait_occurs_before_any_query(self):
        hp, serial = self.make_device("hp34401a")
        hp.status_delay = 1
        before = len(serial.device.commands)
        with patch("scpi_dmm.instrument.time.sleep") as sleep:
            sleep.side_effect = lambda seconds: self.assertEqual(
                len(serial.device.commands), before
            )
            hp.refresh_status()
        sleep.assert_called_once_with(1)

    def test_error_queue_is_consumed_only_by_explicit_diagnostics(self):
        hp, serial = self.make_device("hp34401a")
        hp.refresh_status()
        hp.set_option("display", "ON")
        self.assertNotIn("SYST:ERR?", serial.device.commands)
        hp.read_diagnostics()
        self.assertIn("SYST:ERR?", serial.device.commands)


@unittest.skipIf(AR488 is None, "PyAR488 is required for driver integration tests")
class GpibProtocolTests(unittest.TestCase):
    def test_actual_pyar488_driver_for_both_models_and_eot_characters(self):
        for model in ("hp34401a", "keithley2000"):
            for eot in (0, 4):
                controller = FixtureController(FixtureDmm(model), eot)
                transport = AR488Transport()
                device = ScpiMultimeter(transport, status_delay=0, command_delay=0)
                with (
                    patch.object(AR488.serial, "Serial", controller.open),
                    patch.object(AR488.time, "sleep"),
                ):
                    try:
                        status = device.connect(
                            ConnectionOptions("COM5", interface="GPIB (AR488)", address=23)
                        )
                        if model == "hp34401a":
                            device.set_option("autorange", "OFF")
                        self.assertEqual(len(device.read_measurements()), 1)
                        packets = controller.packets
                        self.assertIn(b"++ver\n", packets)
                        self.assertIn(b"*IDN?\n", packets)
                        self.assertNotIn(b"++read eoi\n", packets)
                        self.assertEqual(controller.config["++auto"], 2)
                        self.assertEqual(controller.config["++eos"], 3)
                        self.assertEqual(controller.config["++eoi"], 1)
                        self.assertFalse(any(b"\n\n" in packet for packet in packets))
                        self.assertNotIn("SYST:REM", controller.device.commands)
                        self.assertEqual(controller.config["++addr"], 23)
                    finally:
                        device.disconnect()
                self.assertTrue(controller.closed)


class DriverFactoryTests(unittest.TestCase):
    def test_brand_variants_and_model_2000_are_recognized(self):
        for brand in ("HEWLETT-PACKARD", "AGILENT TECHNOLOGIES", "KEYSIGHT TECHNOLOGIES"):
            self.assertIsInstance(DriverFactory.detect(brand + ",34401A,0,1"), HP34401ADriver)
        for model in ("MODEL 2000", "2000"):
            self.assertIsInstance(
                DriverFactory.detect("KEITHLEY INSTRUMENTS INC.," + model + ",0,A20"),
                Keithley2000Driver,
            )
        for response in ("invalid", "FLUKE,8840A,0,1", "KEITHLEY,2001,0,1", "UNKNOWN,34401A,0,1"):
            with self.assertRaises(ValueError):
                DriverFactory.detect(response)


if __name__ == "__main__":
    unittest.main()
