"""Headless checks for acquisition, display, icons and settings synchronization."""

import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fixtures import FixtureDmm, FixtureSerial
from PIL import Image

from scpi_dmm.acquisition import AcquisitionController, AcquisitionOptions
from scpi_dmm.config import AppConfig
from scpi_dmm.dialogs import DialogService, SoundNotifier
from scpi_dmm.display import DigitalReadoutRenderer
from scpi_dmm.instrument import ScpiMultimeter
from scpi_dmm.models import DmmStatus, Measurement, SettingSpec
from scpi_dmm.ports import PortSelection, SerialPort, SerialPortDiscovery
from scpi_dmm.settings_ui import InstrumentSettingsPanel
from scpi_dmm.storage import CsvCaptureRepository
from scpi_dmm.transport import ConnectionOptions, RS232Transport


class CaptureTests(unittest.TestCase):
    def make_device(self, model="hp34401a"):
        serial = FixtureSerial(FixtureDmm(model))
        options = ConnectionOptions("COM5")
        device = ScpiMultimeter(
            RS232Transport(options, serial_factory=serial.open), status_delay=0, command_delay=0
        )
        device.connect(options)
        self.addCleanup(device.disconnect)
        return device

    def test_sample_batches_are_fully_logged_and_plot_history_is_bounded(self):
        device = self.make_device("hp34401a")
        device.set_option("samples", "10")
        device.set_option("unit", "dBm")
        complete, results = threading.Event(), []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batch.csv"
            acquisition = AcquisitionController(
                device, on_finished=lambda result: (results.append(result), complete.set())
            )
            acquisition.on_sample = lambda sample: acquisition.stop()
            acquisition.start(AcquisitionOptions(0, 2, path))
            self.assertTrue(complete.wait(2))
            acquisition.join(2)
            self.assertIsNone(results[0].error)
            self.assertEqual(results[0].samples, 10)
            self.assertEqual(acquisition.buffer.count, 10)
            self.assertEqual(len(acquisition.buffer.snapshot()), 2)
            saved = CsvCaptureRepository().load(path)
            self.assertEqual(len(saved.values), 10)
            self.assertEqual(saved.unit, "dBm")

    def test_startup_stop_is_not_lost_during_the_initial_status_read(self):
        device = self.make_device("hp34401a")
        acquisition = AcquisitionController(device)
        original = device.refresh_status
        with patch.object(
            device, "refresh_status", side_effect=lambda: (acquisition.stop(), original())[1]
        ):
            acquisition.start(AcquisitionOptions())
            acquisition.join(2)
        self.assertEqual(acquisition.buffer.count, 0)

    def test_instrument_settings_callback_sends_only_user_changes(self):
        tcl = tk.Tcl()
        panel = InstrumentSettingsPanel.__new__(InstrumentSettingsPanel)
        panel._status = SimpleNamespace(values={})
        panel._syncing, panel._enabled = False, True
        panel.variables = {"samples": tk.StringVar(tcl, "10")}
        panel._submitted = {"samples": "1"}
        calls = []
        panel._actions = {"option": lambda key, value: calls.append((key, value))}
        panel._changed("samples")
        panel._changed("samples")
        self.assertEqual(calls, [("samples", "10")])
        panel._syncing = True
        panel.variables["samples"].set("20")
        panel._changed("samples")
        self.assertEqual(len(calls), 1)
        panel._syncing = False
        panel._changed("reference_acquire", "Acquire")
        self.assertEqual(calls[-1], ("reference_acquire", "Acquire"))

    def test_dynamic_fields_bind_entry_commit_and_keep_label_combo_on_one_row(self):
        tcl = tk.Tcl()
        panel = InstrumentSettingsPanel.__new__(InstrumentSettingsPanel)
        panel.master, panel.tk, panel._font = tcl, tcl.tk, "SharedBodyFont"
        panel._status, panel._syncing, panel._enabled = SimpleNamespace(values={}), False, True
        panel._submitted, calls = {}, []
        panel._actions = {"option": lambda key, value: calls.append((key, value))}

        class Widget:
            def __init__(self, master=None, **options):
                self.options, self.bindings, self.position = options, {}, {}

            def grid(self, **options):
                self.position = options

            def bind(self, event, callback):
                self.bindings[event] = callback

        panel.fields = SimpleNamespace(winfo_children=lambda: ())
        specs = (
            SettingSpec("reference", "Reference value", minimum=-10, maximum=10, editable=True),
            SettingSpec("trigger", "Trigger", ("Internal", "External", "Bus")),
        )
        with (
            patch("scpi_dmm.settings_ui.ttk.Label", Widget),
            patch("scpi_dmm.settings_ui.ttk.Entry", Widget),
            patch("scpi_dmm.settings_ui.ttk.Combobox", Widget),
        ):
            panel._build(specs)
        for key, widget in panel.widgets.items():
            self.assertEqual(widget.position["row"], panel.labels[key].position["row"])
            self.assertEqual(widget.options["font"], "SharedBodyFont")
        entry = panel.widgets["reference"]
        panel.variables["reference"].set("1.25")
        entry.bindings["<Return>"](None)
        entry.bindings["<FocusOut>"](None)
        self.assertEqual(calls, [("reference", "1.25")])
        panel.variables["trigger"].set("External")
        panel.widgets["trigger"].bindings["<<ComboboxSelected>>"](None)
        self.assertEqual(calls[-1], ("trigger", "External"))

    def test_digits_combobox_follows_hp_autorange_readback(self):
        panel = InstrumentSettingsPanel.__new__(InstrumentSettingsPanel)
        panel._syncing, panel._enabled = False, True
        panel._signature, panel._submitted, panel.function_buttons = None, {}, {}
        tcl = tk.Tcl()
        panel.variables = {"digits": tk.StringVar(tcl, "4½")}
        calls = []
        panel._actions = {"option": lambda key, value: calls.append((key, value))}
        states = {}
        panel.widgets = {
            "digits": SimpleNamespace(configure=lambda **options: states.update(options))
        }
        panel.status_labels = {}
        for name in ("status_button", "reset_button", "clear_errors_button", "diagnostics_button"):
            setattr(panel, name, SimpleNamespace(configure=lambda **options: None))
        specs = (SettingSpec("digits", "Digits", ("4½", "5½", "6½")),)
        with patch.object(panel, "_build"):
            for model, autorange, expected in (
                ("HP 34401A", "ON", "disabled"),
                ("HP 34401A", "OFF", "readonly"),
                ("HP 34401A", "ON", "disabled"),
                ("Keithley 2000", "ON", "readonly"),
            ):
                panel.apply_status(
                    DmmStatus(model, 1, {"autorange": autorange, "digits": "6½"}, specs)
                )
                self.assertEqual(states["state"], expected)
                before = len(calls)
                panel.variables["digits"].set("4½")
                panel._changed("digits")
                self.assertEqual(len(calls), before + (expected == "readonly"))
        panel.set_state(False)
        self.assertEqual(states["state"], "disabled")


class DisplayAndIconTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def test_digital_7_fits_every_decimal_precision_and_resolution(self):
        renderer = DigitalReadoutRenderer(self.root / "fonts" / "digital-7.ttf")
        self.assertEqual(renderer._font(24).getname()[0], "Digital-7")
        for text in ("+1.23457", "+1.234568", "+1.2345679", "-12345678.1234567", "OVERLOAD"):
            for width, height in ((1, 1), (64, 24), (200, 50), (800, 100), (1600, 180)):
                image = renderer.render(text, width, height)
                self.assertLessEqual(image.width, width)
                self.assertLessEqual(image.height, height)
                if width > 64:
                    self.assertIsNotNone(image.getbbox())

    def test_attached_icon_is_valid_and_set_as_default_for_all_toplevels(self):
        config = AppConfig(self.root)
        with Image.open(config.icon_path) as image:
            self.assertEqual(image.format, "ICO")
        
        calls = []
        root = SimpleNamespace(
            iconphoto=lambda default, *images: calls.append(("root", default, len(images))),
            iconbitmap=lambda *args, **kwargs: None 
        )
        child = SimpleNamespace(
            iconphoto=lambda default, *images: calls.append(("child", default, len(images))),
            iconbitmap=lambda *args, **kwargs: None 
        )
        service = DialogService(root, config, SoundNotifier(False))
        with patch("PIL.ImageTk.PhotoImage", side_effect=lambda image, master: image.copy()):
            service.set_icon(root)
            service.set_icon(child)
        self.assertEqual(calls, [("root", True, 3), ("root", False, 3), ("child", False, 3)])
        self.assertEqual(len(service._icons), 3)


class PortsTests(unittest.TestCase):
    def test_discovery_does_not_open_ports_and_keeps_current_connection(self):
        listed = [
            SimpleNamespace(device="COM5", description="USB Serial", hwid="123"),
            SimpleNamespace(device="COM2", description="USB GPIB", hwid="456"),
        ]
        with patch("serial.tools.list_ports.comports", return_value=listed):
            ports = SerialPortDiscovery().scan()
        self.assertEqual({port.device for port in ports}, {"COM2", "COM5"})
        selection = PortSelection()
        selection.selected = "COM5"
        self.assertEqual(selection.update(ports, connected=True), "COM5")
        self.assertEqual(selection.update((), connected=True), "COM5")
        self.assertEqual(selection.update((SerialPort("COM2", "USB"),), connected=False), "COM2")


if __name__ == "__main__":
    unittest.main()
