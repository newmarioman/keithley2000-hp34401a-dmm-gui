"""Connection profile changes without hardware access or a desktop display."""

import unittest

from fixtures import FixtureDmm, FixtureSerial

from scpi_dmm.connection_profiles import PROFILES
from scpi_dmm.connection_ui import ConnectionPanel
from scpi_dmm.instrument import ScpiMultimeter
from scpi_dmm.transport import RS232Transport


class Control:
    def __init__(self, value=""):
        self.value, self.options = value, {}

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def configure(self, **options):
        self.options.update(options)


class ConnectionProfileTests(unittest.TestCase):
    def make_panel(self):
        panel = ConnectionPanel.__new__(ConnectionPanel)
        panel._connected, panel._busy = False, False
        panel.model, panel.interface, panel.port = Control(), Control("RS232"), Control("COM5")
        panel.address, panel.timeout_var = Control(), Control("10")
        panel.fields = {parameter.key: Control() for parameter in PROFILES[0].parameters}
        for name in ("timeout", "connect_button", "disconnect_button"):
            setattr(panel, name, Control())
        panel.select_model("HP 34401A")
        return panel

    def test_model_selection_sets_requested_defaults_and_limits(self):
        panel = self.make_panel()
        for model, expected, address, maximum in (
            ("HP 34401A", ("9600", "None", "8", "1", "CR+LF", "OFF", "OFF", "OFF"), "22", 9600),
            ("Keithley 2000", ("19200", "None", "8", "1", "CR", "OFF", "OFF", "OFF"), "16", 19200),
            ("HP 34401A", ("9600", "None", "8", "1", "CR+LF", "OFF", "OFF", "OFF"), "22", 9600),
        ):
            panel.model.set(model)
            panel._model_changed()
            self.assertEqual(tuple(control.get() for control in panel.fields.values()), expected)
            self.assertEqual(panel.address.get(), address)
            self.assertEqual(max(map(int, panel.fields["baudrate"].options["values"])), maximum)
            for control in panel.fields.values():
                self.assertIn(control.get(), control.options["values"])
            self.assertEqual(panel.port.get(), "COM5")
            self.assertEqual(panel.timeout_var.get(), "10")
        panel.interface.set("GPIB (AR488)")
        panel.select_model("Keithley 2000")
        self.assertEqual(panel.options().address, 16)
        self.assertEqual(panel.interface.get(), "GPIB (AR488)")

    def test_parity_updates_hp_data_bits_and_model_switch_clears_old_settings(self):
        panel = self.make_panel()
        for parity, bits in (("Even", "7"), ("Odd", "7"), ("None", "8")):
            panel.fields["parity"].set(parity)
            panel._parity_changed()
            self.assertEqual(panel.fields["bytesize"].get(), bits)
            self.assertEqual(panel.fields["bytesize"].options["values"], (bits,))
        panel.fields["parity"].set("Odd")
        panel._parity_changed()
        panel.fields["dsrdtr"].set("ON")
        panel.select_model("Keithley 2000")
        self.assertEqual(panel.fields["parity"].get(), "None")
        self.assertEqual(panel.fields["bytesize"].get(), "8")
        self.assertEqual(panel.fields["stopbits"].options["values"], ("1",))
        for key in ("rtscts", "dsrdtr"):
            self.assertEqual(panel.fields[key].options["values"], ("OFF",))
            self.assertEqual(panel.fields[key].get(), "OFF")

    def test_model_changes_are_disabled_during_connection_and_background_work(self):
        panel = self.make_panel()
        for connected, busy in ((True, False), (False, True)):
            panel.set_state(connected, busy, False)
            self.assertEqual(panel.model.options["state"], "disabled")
            panel.select_model("Keithley 2000")
            self.assertEqual(panel.model.get(), "HP 34401A")
            self.assertEqual(panel.address.get(), "22")
        panel.set_state(False, False, False)
        self.assertEqual(panel.model.options["state"], "readonly")
        panel.select_model("Keithley 2000")
        self.assertEqual(panel.model.get(), "Keithley 2000")

    def test_profiles_reach_serial_port_with_correct_defaults_and_actual_terminator_bytes(self):
        panel = self.make_panel()
        for model, fixture_model, baudrate, terminator in (
            ("HP 34401A", "hp34401a", 9600, b"\r\n"),
            ("Keithley 2000", "keithley2000", 19200, b"\r"),
        ):
            panel.select_model(model)
            serial = FixtureSerial(FixtureDmm(fixture_model))
            options = panel.options()
            device = ScpiMultimeter(
                RS232Transport(options, serial_factory=serial.open), status_delay=0, command_delay=0
            )
            try:
                self.assertEqual(device.connect(options).model, model)
                self.assertEqual(serial.options["baudrate"], baudrate)
                self.assertEqual(serial.options["parity"], "N")
                self.assertEqual(serial.options["bytesize"], 8)
                self.assertEqual(serial.options["stopbits"], 1)
                for key in ("xonxoff", "rtscts", "dsrdtr"):
                    self.assertFalse(serial.options[key])
                self.assertEqual(serial.packets[0], b"*IDN?" + terminator)
            finally:
                device.disconnect()


if __name__ == "__main__":
    unittest.main()
