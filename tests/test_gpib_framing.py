"""GPIB response framing, including delayed EOI and the HP boolean response '0'."""

import time
import unittest
from unittest.mock import patch

from fixtures import FixtureController, FixtureDmm

from scpi_dmm.acquisition import AcquisitionController, AcquisitionOptions
from scpi_dmm.instrument import QueryTimeout, ScpiMultimeter
from scpi_dmm.transport import AR488Transport, ConnectionOptions

try:
    from PyAR488 import AR488
except ImportError:
    try:
        from PyAR488.PyAR488 import AR488
    except ImportError:
        AR488 = None


class FramingController(FixtureController):
    """Stream a line and its EOI trailer separately, and reject overlapping queries."""

    def __init__(self, model="hp34401a", *, ending=b"\r\n", delayed_function=False,
                 initial_eot=0, initial_eot_enabled=False):
        super().__init__(FixtureDmm(model), eot=initial_eot)
        self.config["++eot_enable"] = int(initial_eot_enabled)
        self.ending, self.delayed_function = ending, delayed_function
        self.pending_query, self.trailer = None, None
        self.trailer_due, self.early_queries, self.aborts = 0, 0, 0
        self.omit_eot, self.empty_replies = set(), set()
        self.decimal_eot, self.measurement_reply = False, b"0"

    @property
    def in_waiting(self):
        if self.trailer is not None and time.monotonic() >= self.trailer_due:
            self.input.extend(self.trailer)
            self.trailer = None
        return len(self.input)

    def write(self, payload):
        command = payload.decode("ascii").strip("\r\n")
        if command == "++!":
            self.packets.append(payload)
            self.pending, self.pending_query, self.trailer = None, None, None
            self.aborts += 1
            return len(payload)
        if self.trailer is not None and not command.startswith("++"):
            self.early_queries += 1
            raise OSError(f"Next SCPI query {command} arrived before GPIB EOI completion")
        return super().write(payload)

    def emit_reply(self, command):
        assert self.pending is not None
        reply = self.measurement_reply if command == "READ?" else self.pending.encode("ascii")
        if command in self.empty_replies:
            reply = b""
        eot = bytes([self.config["++eot_char"]]) if self.config["++eot_enable"] else b""
        if eot and self.decimal_eot:
            eot = str(self.config["++eot_char"]).encode("ascii")
        if command in self.omit_eot:
            eot = b""
        if self.delayed_function and command in ("FUNC?", "SENS:FUNC?"):
            self.input.extend(reply + b"\r")
            self.trailer, self.trailer_due = b"\n" + eot, time.monotonic() + 0.03
        else:
            self.input.extend(reply + self.ending + eot)
        self.pending, self.pending_query = None, None


@unittest.skipIf(AR488 is None, "PyAR488 is required for GPIB framing tests")
class GpibFramingTests(unittest.TestCase):
    def device(self, controller):
        def factory(port, **options):
            with (
                patch.object(AR488.serial, "Serial", controller.open),
                patch.object(AR488.time, "sleep", lambda seconds: None),
            ):
                return AR488(port, **options)

        events = []
        device = ScpiMultimeter(
            AR488Transport(adapter_factory=factory), command_delay=0, status_delay=0,
            trace=lambda direction, text: events.append((direction, text)),
        )
        self.addCleanup(device.disconnect)
        device.connect(ConnectionOptions("COM5", interface="GPIB (AR488)", address=3,
                                         query_timeout=0.1))
        return device, events

    def test_connection_waits_for_gpib_completion_after_function_before_math_query(self):
        controller = FramingController(delayed_function=True, initial_eot_enabled=True)
        device, events = self.device(controller)
        self.assertTrue(device.connected)
        self.assertEqual(controller.early_queries, 0)
        self.assertIsNone(controller.trailer)
        self.assertIn(("IN", "0"), events)  # CALC:STAT? OFF is a valid response.
        self.assertFalse(any(direction == "WARN" for direction, _ in events))

    def test_numeric_zero_and_eoi_only_responses_work_for_both_models(self):
        for model in ("hp34401a", "keithley2000"):
            with self.subTest(model=model):
                controller = FramingController(model, ending=b"")
                device, events = self.device(controller)
                sample = device.read_measurement()
                self.assertEqual(sample.value, 0)
                self.assertFalse(sample.overload)
                self.assertTrue(device.connected)
                self.assertFalse(any(direction == "WARN" for direction, _ in events))

    def test_saved_disabled_or_ascii_digit_eot_is_replaced_with_safe_framing(self):
        for enabled in (False, True):
            with self.subTest(eot_enabled=enabled):
                controller = FramingController(initial_eot=ord("0"), initial_eot_enabled=enabled)
                device, events = self.device(controller)
                self.assertEqual(controller.config["++eot_enable"], 1)
                self.assertEqual(controller.config["++eot_char"], 4)
                self.assertEqual(device.transport.eot_byte, b"\x04")
                self.assertEqual(device.status.model, "HP 34401A")
                self.assertTrue(device.connected)

    def test_line_without_eot_times_out_with_raw_bytes_then_recovers_after_abort(self):
        controller = FramingController()
        device, events = self.device(controller)
        device.query_timeout = 0.01
        controller.omit_eot.add("READ?")
        with self.assertRaises(QueryTimeout) as caught:
            device.read_measurements(keep_connection_on_timeout=True)
        self.assertIn("30 0D 0A", str(caught.exception))
        self.assertIn("EOT 0x04", str(caught.exception))
        self.assertIn(("OUT", "++!"), events)
        self.assertEqual(controller.aborts, 1)
        self.assertTrue(device.connected)
        controller.omit_eot.clear()
        self.assertEqual(device.read_measurement().value, 0)

    def test_empty_eot_is_not_accepted_as_numeric_zero(self):
        controller = FramingController()
        device, events = self.device(controller)
        device.query_timeout = 0.01
        controller.empty_replies.add("READ?")
        with self.assertRaises(QueryTimeout):
            device.read_measurements(keep_connection_on_timeout=True)
        self.assertTrue(device.connected)

    def test_decimal_eot_after_crlf_does_not_truncate_digits_in_real_responses(self):
        controller = FramingController()
        controller.decimal_eot = True
        device, events = self.device(controller)
        for reply in (b"0", b"4", b"14", b"+1.444E+00", b"-4.14"):
            with self.subTest(reply=reply):
                controller.measurement_reply = reply
                self.assertEqual(device.read_measurement().value, float(reply))
        self.assertTrue(device.connected)
        self.assertFalse(any(direction == "WARN" for direction, _ in events))

    def test_digit_four_in_incomplete_data_is_not_a_completion_marker(self):
        controller = FramingController()
        device, events = self.device(controller)
        device.query_timeout = 0.01
        controller.ending = b""
        controller.measurement_reply = b"14"
        controller.omit_eot.add("READ?")
        with self.assertRaises(QueryTimeout) as caught:
            device.read_measurements(keep_connection_on_timeout=True)
        self.assertIn("31 34", str(caught.exception))
        self.assertTrue(device.connected)

    def test_five_lines_without_eot_close_connection_after_five_timeouts(self):
        controller = FramingController()
        device, events = self.device(controller)
        device.query_timeout = 0.01
        controller.omit_eot.add("READ?")
        results = []
        acquisition = AcquisitionController(device, on_finished=results.append)
        acquisition.start(AcquisitionOptions())
        acquisition.join(2)
        self.assertFalse(acquisition.running)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0].error, QueryTimeout)
        self.assertEqual(results[0].samples, 0)
        self.assertEqual(sum(direction == "WARN" for direction, _ in events), 5)
        self.assertFalse(device.connected)
        self.assertTrue(controller.closed)


if __name__ == "__main__":
    unittest.main()
