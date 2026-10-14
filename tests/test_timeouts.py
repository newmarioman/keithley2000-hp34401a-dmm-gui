"""Timeout regressions through real RS232 and PyAR488 transport code, without hardware."""

import tempfile
import time
import unittest
from collections import Counter, deque
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fixtures import FixtureController, FixtureDmm, FixtureSerial

from scpi_dmm.acquisition import AcquisitionController, AcquisitionOptions
from scpi_dmm.app import DmmApplication
from scpi_dmm.config import AppConfig
from scpi_dmm.instrument import QueryTimeout, ScpiMultimeter
from scpi_dmm.storage import CsvCaptureRepository
from scpi_dmm.transport import AR488Transport, ConnectionOptions, RS232Transport, TransportError

try:
    from PyAR488 import AR488
except ImportError:
    try:
        from PyAR488.PyAR488 import AR488
    except ImportError:
        AR488 = None


class ReplyScript:
    """'ok' keeps a real fixture response; None/bytes replace it with silence/fragments."""

    def setup_script(self):
        self.plans, self.attempts = {}, Counter()

    def set_replies(self, command, replies):
        self.plans[command] = deque(replies)
        self.attempts[command] = 0

    def next_reply(self, command):
        self.attempts[command] += 1
        if not self.plans[command]:
            raise AssertionError(f"Unexpected extra attempt for {command}")
        return self.plans[command].popleft()


class ScriptedSerial(ReplyScript, FixtureSerial):
    def __init__(self, device):
        super().__init__(device)
        self.setup_script()

    def write(self, payload):
        written = super().write(payload)
        command = payload.decode("ascii").strip("\r\n")
        if command in self.plans:
            reply = self.next_reply(command)
            if reply != "ok":
                self.input[:] = reply or b""
        return written


class ScriptedController(ReplyScript, FixtureController):
    def __init__(self, device):
        super().__init__(device)
        self.setup_script()

    def emit_reply(self, command):
        if command in self.plans:
            reply = self.next_reply(command)
            if reply != "ok":
                self.input.extend(reply or b"")
                self.pending, self.pending_query = None, None
                return
        super().emit_reply(command)

    def write(self, payload):
        command = payload.decode("ascii").strip("\r\n")
        written = super().write(payload)
        if command == "++ver" and command in self.plans:
            reply = self.next_reply(command)
            if reply != "ok":
                self.input[:] = reply or b""
        return written


class TimeoutTests(unittest.TestCase):
    interfaces = ("RS232", "GPIB (AR488)")
    models = ("hp34401a", "keithley2000")

    @contextmanager
    def device(self, interface, model="hp34401a", timeout=0.01):
        events = []
        options = ConnectionOptions("COM5", interface=interface)
        if interface == "RS232":
            serial = ScriptedSerial(FixtureDmm(model))
            transport = RS232Transport(options, serial_factory=serial.open)
        else:
            if AR488 is None:
                self.skipTest("Install PyAR488 to run GPIB integration tests")
            serial = ScriptedController(FixtureDmm(model))
            transport = AR488Transport()
        device = ScpiMultimeter(
            transport, status_delay=0, command_delay=0,
            trace=lambda direction, text: events.append((direction, text)),
        )
        try:
            if interface == "RS232":
                device.connect(options)
            else:
                with (
                    patch.object(AR488.serial, "Serial", serial.open),
                    patch.object(AR488.time, "sleep", lambda seconds: None),
                ):
                    device.connect(options)
            device.query_timeout = timeout
            events.clear()
            yield device, serial, events
        finally:
            device.disconnect()

    def acquire(self, device, *, stop_after=None, log_path=None, trace_stop=None, join_timeout=2):
        results = []
        acquisition = AcquisitionController(device, on_finished=results.append)
        if stop_after is not None:
            acquisition.on_sample = lambda sample: (
                acquisition.stop() if acquisition.buffer.count >= stop_after else None
            )
        original_trace = device.trace

        def report(direction, text):
            original_trace(direction, text)
            if direction == "WARN" and trace_stop and trace_stop in text:
                acquisition.stop()

        with patch.object(device, "trace", side_effect=report):
            acquisition.start(AcquisitionOptions(log_path=log_path))
            acquisition.join(join_timeout)
        if acquisition.running:
            acquisition.stop()
            acquisition.join(2)
            self.fail("Acquisition did not finish within the test deadline")
        self.assertEqual(len(results), 1, "Exactly one completion must be delivered")
        return acquisition, results[0]

    @staticmethod
    def warnings(events):
        return [text for direction, text in events if direction == "WARN"]

    def assert_command(self, warnings, command, interface):
        for text in warnings:
            self.assertIn(command, text)
            self.assertIn("timeout", text.lower())
            self.assertIn("GPIB" if interface != "RS232" else "RS232", text)
            if interface != "RS232":
                self.assertIn("++auto 2", text)

    def test_four_timeouts_then_a_valid_read_keep_connection_and_skip_failed_samples(self):
        for interface in self.interfaces:
            for model in self.models:
                with self.subTest(interface=interface, model=model), self.device(interface, model) as (device, serial, events):
                    serial.set_replies("READ?", [None] * 4 + ["ok"])
                    acquisition, result = self.acquire(device, stop_after=1)
                    self.assertIsNone(result.error)
                    self.assertTrue(device.connected)
                    self.assertFalse(serial.closed)
                    self.assertEqual(result.samples, 1)
                    self.assertEqual(acquisition.buffer.count, 1)
                    self.assertEqual(serial.attempts["READ?"], 5)
                    warnings = self.warnings(events)
                    self.assertEqual(len(warnings), 4)
                    self.assert_command(warnings, "READ?", interface)
                    self.assertTrue(all(f"({n}/5 consecutive)" in text for n, text in enumerate(warnings, 1)))

    def test_fifth_timeout_closes_both_interfaces_for_both_models(self):
        for interface in self.interfaces:
            for model in self.models:
                with self.subTest(interface=interface, model=model), self.device(interface, model) as (device, serial, events):
                    serial.set_replies("READ?", [None] * 5)
                    _, result = self.acquire(device)
                    self.assertIsInstance(result.error, QueryTimeout)
                    self.assertEqual(result.error.command, "READ?")
                    self.assertIn("5 consecutive", str(result.error))
                    self.assertEqual(result.samples, 0)
                    self.assertEqual(serial.attempts["READ?"], 5)
                    self.assertFalse(device.connected)
                    self.assertTrue(serial.closed)
                    self.assertIsNone(device.status)
                    self.assertEqual(len(self.warnings(events)), 5)

    def test_success_resets_counter_instead_of_accumulating_timeouts(self):
        for interface in self.interfaces:
            for model in self.models:
                with self.subTest(interface=interface, model=model), self.device(interface, model) as (device, serial, events):
                    serial.set_replies("READ?", [None, "ok"] + [None] * 4 + ["ok"])
                    _, result = self.acquire(device, stop_after=2)
                    self.assertIsNone(result.error)
                    self.assertEqual(result.samples, 2)
                    self.assertTrue(device.connected)
                    warnings = self.warnings(events)
                    self.assertEqual(len(warnings), 5)
                    self.assertEqual(
                        [text.split(" consecutive)")[0].rsplit("(", 1)[1] for text in warnings],
                        ["1/5", "1/5", "2/5", "3/5", "4/5"],
                    )

    def test_bus_trigger_warns_about_fetc_rather_than_read(self):
        for interface in self.interfaces:
            for model in self.models:
                with self.subTest(interface=interface, model=model), self.device(interface, model) as (device, serial, events):
                    device.set_option("trigger", "Bus")
                    events.clear()
                    serial.set_replies("FETC?", [None] * 5)
                    _, result = self.acquire(device)
                    self.assertIsInstance(result.error, QueryTimeout)
                    self.assertEqual(result.error.command, "FETC?")
                    warnings = self.warnings(events)
                    self.assert_command(warnings, "FETC?", interface)
                    self.assertNotIn("READ?", str(result.error))
                    self.assertEqual(serial.device.commands.count("INIT"), 5)
                    self.assertEqual(serial.device.commands.count("*TRG"), 5)

    def test_incomplete_reply_is_discarded_before_the_next_valid_sample(self):
        for interface in self.interfaces:
            with self.subTest(interface=interface), self.device(interface) as (device, serial, events):
                serial.set_replies("READ?", [b"+999", "ok"])
                acquisition, result = self.acquire(device, stop_after=1)
                self.assertIsNone(result.error)
                self.assertAlmostEqual(acquisition.buffer.snapshot()[0].value, 1.090929743)
                self.assertIn("4 incomplete bytes", self.warnings(events)[0])
                self.assertFalse(serial.input)

    def test_total_deadline_is_not_extended_by_partial_fragments(self):
        for interface in self.interfaces:
            with self.subTest(interface=interface), self.device(interface, timeout=1) as (device, serial, events):
                serial.set_replies("READ?", [b"123456789" * 20])
                serial.fragment = 1
                original_read, original_timeout = serial.read, serial.timeout
                clock = SimpleNamespace(now=0.0)

                def read_fragment(size):
                    self.assertEqual(serial.timeout, 0)
                    clock.now += 0.2
                    return original_read(size)

                with (
                    patch("scpi_dmm.instrument.time.monotonic", side_effect=lambda: clock.now),
                    patch("scpi_dmm.instrument.time.sleep"),
                    patch.object(serial, "read", side_effect=read_fragment),
                ):
                    with self.assertRaises(QueryTimeout) as caught:
                        device.read_measurements(keep_connection_on_timeout=True)
                self.assertAlmostEqual(clock.now, 1.0)
                self.assertEqual(caught.exception.command, "READ?")
                self.assertTrue(device.connected)
                self.assertEqual(serial.timeout, original_timeout)

    def test_status_and_custom_queries_retry_without_intermediate_errors(self):
        for interface in self.interfaces:
            for model in self.models:
                with self.subTest(interface=interface, model=model), self.device(interface, model) as (device, serial, events):
                    command = device.driver.function_query
                    serial.set_replies(command, [None] * 4 + ["ok"])
                    device.refresh_status()
                    self.assertTrue(device.connected)
                    self.assertEqual(serial.attempts[command], 5)
                    self.assertEqual(len(self.warnings(events)), 4)
                    self.assert_command(self.warnings(events), command, interface)
                    serial.plans.pop(command)
                    events.clear()
                    serial.set_replies("SYST:VERS?", [None, "ok"])
                    self.assertEqual(device.send_custom("SYST:VERS?"), "1991.0")
                    self.assertEqual(len(self.warnings(events)), 1)

    def test_five_status_timeouts_close_connection(self):
        for interface in self.interfaces:
            with self.subTest(interface=interface), self.device(interface) as (device, serial, events):
                command = device.driver.function_query
                serial.set_replies(command, [None] * 5)
                with self.assertRaises(QueryTimeout) as caught:
                    device.refresh_status()
                self.assertEqual(caught.exception.command, command)
                self.assertEqual(serial.attempts[command], 5)
                self.assertEqual(len(self.warnings(events)), 5)
                self.assertFalse(device.connected)
                self.assertTrue(serial.closed)

    def test_standalone_measurement_uses_five_attempts_too(self):
        for interface in self.interfaces:
            with self.subTest(interface=interface), self.device(interface) as (device, serial, events):
                serial.set_replies("READ?", [None] * 5)
                with self.assertRaises(QueryTimeout):
                    device.read_measurement()
                self.assertEqual(serial.attempts["READ?"], 5)
                self.assertEqual(len(self.warnings(events)), 5)
                self.assertFalse(device.connected)

    def test_manual_stop_before_fifth_timeout_remains_normal_and_fifth_still_disconnects(self):
        for interface in self.interfaces:
            for stop_at in (4, 5):
                with self.subTest(interface=interface, stop_at=stop_at), self.device(interface) as (device, serial, events):
                    serial.set_replies("READ?", [None] * stop_at)
                    _, result = self.acquire(device, trace_stop=f"({stop_at}/5 consecutive)")
                    self.assertEqual(serial.attempts["READ?"], stop_at)
                    self.assertEqual(device.connected, stop_at < 5)
                    if stop_at < 5:
                        self.assertIsNone(result.error)
                    else:
                        self.assertIsInstance(result.error, QueryTimeout)

    def test_failed_local_command_does_not_mask_the_final_timeout_or_prevent_close(self):
        for interface in self.interfaces:
            with self.subTest(interface=interface), self.device(interface) as (device, serial, events):
                serial.set_replies("READ?", [None] * 5)
                original_write = serial.write
                local = "SYST:LOC" if interface == "RS232" else "++loc"

                def write(payload):
                    if payload.decode("ascii").strip("\r\n") == local:
                        raise OSError("Simulated local-command failure")
                    return original_write(payload)

                with patch.object(serial, "write", side_effect=write):
                    _, result = self.acquire(device)
                self.assertIsInstance(result.error, QueryTimeout)
                self.assertTrue(serial.closed)
                self.assertFalse(device.connected)

def test_io_error_is_not_counted_as_a_response_timeout(self):
    for interface in self.interfaces:
        with self.subTest(interface=interface), self.device(interface) as (device, serial, events):
            serial.set_replies("READ?", ["ok"])
            original_read = serial.read
            def read(size):
                if getattr(serial, 'last_write', '').startswith("READ?"):
                    raise OSError("Simulated USB failure")
                return original_read(size)
            with patch.object(serial, "read", side_effect=read):
                _, result = self.acquire(device)
            self.assertIsInstance(result.error, TransportError)
            self.assertEqual(self.warnings(events), [])
            self.assertFalse(device.connected)

    def test_non_ascii_reply_reports_command_and_byte_and_disconnects_both_interfaces(self):
        for interface in self.interfaces:
            for model in self.models:
                with self.subTest(interface=interface, model=model), self.device(interface, model) as (device, serial, events):
                    ending = b"\r\n\x04" if interface != "RS232" else b"\r\n"
                    serial.set_replies("READ?", [b"+1.2\xf9" + ending])
                    with self.assertRaises(TransportError) as caught:
                        device.read_measurements(keep_connection_on_timeout=True)
                    self.assertIn("READ?:", str(caught.exception))
                    self.assertIn("byte 0xF9", str(caught.exception))
                    self.assertEqual(serial.attempts["READ?"], 1)
                    self.assertFalse(self.warnings(events))
                    self.assertFalse(device.connected)
                    self.assertTrue(serial.closed)

    def test_csv_keeps_only_valid_samples_and_is_closed_on_timeout(self):
        for interface in self.interfaces:
            with self.subTest(interface=interface), self.device(interface) as (device, serial, events):
                serial.set_replies("READ?", [None, "ok"] + [None] * 5)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "capture.csv"
                    _, result = self.acquire(device, log_path=path)
                    self.assertIsInstance(result.error, QueryTimeout)
                    saved = CsvCaptureRepository().load(path)
                    self.assertEqual(len(saved.values), 1)
                    self.assertEqual(result.samples, 1)
                    path.unlink()  # Also checks release of the file on Windows.

    def test_gui_shows_no_dialog_for_recovered_timeouts_and_one_for_the_fifth(self):
        for interface in self.interfaces:
            for fatal in (False, True):
                with self.subTest(interface=interface, fatal=fatal), self.device(interface) as (device, serial, events):
                    serial.set_replies("READ?", [None] * 5 if fatal else [None] * 4 + ["ok"])
                    _, result = self.acquire(device, stop_after=None if fatal else 1)
                    app = DmmApplication.__new__(DmmApplication)
                    app._closing = app.busy = False
                    app.instrument = device
                    app.view = SimpleNamespace(status=Mock(), console=Mock())
                    app.dialogs = Mock()
                    app._update_live, app._sync_from_instrument, app._run_task = Mock(), Mock(), Mock()
                    app._acquisition_finished(result)
                    if fatal:
                        app.dialogs.error.assert_called_once()
                        self.assertEqual(app.dialogs.error.call_args.args[0], "Communication timeout")
                        self.assertIn("5 consecutive", app.dialogs.error.call_args.args[1])
                    else:
                        app.dialogs.error.assert_not_called()

    def test_gui_status_operation_shows_only_the_final_timeout_dialog(self):
        for interface in self.interfaces:
            for fatal in (False, True):
                with self.subTest(interface=interface, fatal=fatal), self.device(interface) as (device, serial, events):
                    command = device.driver.function_query
                    serial.set_replies(command, [None] * 5 if fatal else [None] * 4 + ["ok"])
                    app = DmmApplication.__new__(DmmApplication)
                    app._closing = app.busy = False
                    app.acquisition = SimpleNamespace(running=False)
                    app.view = SimpleNamespace(status=Mock(), console=Mock())
                    app.dialogs = Mock()
                    app._refresh_state, app._sync_from_instrument = Mock(), Mock()

                    def submit(operation, succeeded, failed):
                        try:
                            result = operation()
                        except Exception as exc:
                            failed(exc)
                        else:
                            succeeded(result)

                    app.executor = SimpleNamespace(submit=submit)
                    app._run_task("Reading status", device.refresh_status)
                    self.assertFalse(app.busy)
                    if fatal:
                        app.dialogs.error.assert_called_once()
                        self.assertEqual(app.dialogs.error.call_args.args[0], "Communication timeout")
                    else:
                        app.dialogs.error.assert_not_called()

    def test_new_acquisition_and_reconnection_start_with_a_fresh_counter(self):
        for interface in self.interfaces:
            with self.subTest(interface=interface), self.device(interface) as (device, serial, events):
                for failures in (4, 1):
                    events.clear()
                    serial.set_replies("READ?", [None] * failures + ["ok"])
                    _, result = self.acquire(device, stop_after=1)
                    self.assertIsNone(result.error)
                    self.assertIn("(1/5 consecutive)", self.warnings(events)[0])
                device.disconnect()
                options = ConnectionOptions("COM5", interface=interface)
                if interface == "RS232":
                    device.connect(options)
                else:
                    with (
                        patch.object(AR488.serial, "Serial", serial.open),
                        patch.object(AR488.time, "sleep", lambda seconds: None),
                    ):
                        device.connect(options)
                device.query_timeout = 0.01
                events.clear()
                serial.set_replies("READ?", [None] * 4 + ["ok"])
                _, result = self.acquire(device, stop_after=1)
                self.assertIsNone(result.error)
                self.assertTrue(device.connected)
                self.assertEqual(len(self.warnings(events)), 4)

    def test_defaults_and_real_one_second_timeout_on_both_interfaces(self):
        self.assertEqual(AppConfig().query_timeout, 1)
        self.assertEqual(ConnectionOptions("COM5").query_timeout, 1)
        self.assertEqual(ScpiMultimeter().query_timeout, 1)
        for interface in self.interfaces:
            with self.subTest(interface=interface), self.device(interface, timeout=1) as (device, serial, events):
                if interface != "RS232":
                    self.assertEqual(serial.config["++read_tmo_ms"], 1000)
                serial.set_replies("READ?", [None] * 5)
                start = time.monotonic()
                _, result = self.acquire(device, join_timeout=8)
                elapsed = time.monotonic() - start
                self.assertIsInstance(result.error, QueryTimeout)
                self.assertEqual(serial.attempts["READ?"], 5)
                self.assertGreaterEqual(elapsed, 4.9)
                self.assertLess(elapsed, 7.5)

    @unittest.skipIf(AR488 is None, "Install PyAR488 for controller timeout checks")
    def test_controller_version_also_retries_five_times_and_closes(self):
        serial = ScriptedController(FixtureDmm("hp34401a"))
        serial.set_replies("++ver", [None] * 5)
        events = []
        device = ScpiMultimeter(
            AR488Transport(), status_delay=0, command_delay=0,
            trace=lambda direction, text: events.append((direction, text)),
        )
        with (
            patch.object(AR488.serial, "Serial", serial.open),
            patch.object(AR488.time, "sleep", lambda seconds: None),
        ):
            with self.assertRaises(QueryTimeout) as caught:
                device.connect(ConnectionOptions("COM5", interface="GPIB (AR488)", query_timeout=0.1))
        self.assertEqual(caught.exception.command, "++ver")
        self.assertEqual(serial.attempts["++ver"], 5)
        self.assertEqual(len(self.warnings(events)), 5)
        self.assertTrue(serial.closed)
        self.assertFalse(device.connected)


if __name__ == "__main__":
    unittest.main()
