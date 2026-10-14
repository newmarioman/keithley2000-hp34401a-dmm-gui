# -*- coding: utf-8 -*-

"""
Serialize complete SCPI transactions and select the driver through *IDN?.

This module encapsulates the core logical layer for managing transaction states,
sending safe ASCII instrument sequences, tracking trigger/display modes, and 
handling communication session contexts for the Keithley2000 / HP34401A DMM hardware.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager

# Local Packages
from .drivers import DriverFactory, ScpiModelDriver
from .models import ConnectionInfo, DmmDiagnostics, DmmStatus, Measurement
from .transport import ConnectionOptions, InstrumentTransport, TransportError, TransportFactory


class QueryTimeout(TimeoutError):
    """A response deadline expired, with the command kept for diagnostics."""

    def __init__(self, message: str, *, command: str = "") -> None:
        super().__init__(message)
        self.command = command


class ScpiMultimeter:
    MAX_CONSECUTIVE_TIMEOUTS = 5

    def __init__(
        self,
        transport: InstrumentTransport | None = None,
        *,
        query_timeout=1.0,
        command_delay=0.0001,
        status_delay=1.0,
        trace=None,
        activity=None,
        transport_factory=TransportFactory.create,
    ) -> None:
        self.transport = transport
        self._injected_transport = transport
        self._transport_factory = transport_factory
        self.query_timeout, self.command_delay, self.status_delay = (
            query_timeout,
            command_delay,
            status_delay,
        )
        self._trace = trace or (lambda direction, text: None)
        self._activity = activity or (lambda direction: None)
        self._lock = threading.RLock()
        self._buffer: deque[int] = deque()
        self._connected = False
        self.driver: ScpiModelDriver | None = None
        self._status = self._info = self._diagnostics = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def status(self) -> DmmStatus | None:
        return self._status

    @property
    def connection_info(self) -> ConnectionInfo | None:
        return self._info

    @contextmanager
    def _session(self, require_connection: bool = True, *, keep_connection_on_timeout: bool = False):
        with self._lock:
            if require_connection and not self._connected:
                raise TransportError("Connect to the DMM first.")
            try:
                yield
            except QueryTimeout:
                if not keep_connection_on_timeout:
                    self._release()
                raise
            except TransportError:
                self._release()
                raise

    @property
    def diagnostics(self) -> DmmDiagnostics | None:
        return self._diagnostics

    def _release(self) -> None:
        if self.transport:
            self.transport.close()
        self._connected = False
        self._status = self._info = self._diagnostics = self.driver = None
        self._buffer.clear()

    def connect(self, options: ConnectionOptions) -> DmmStatus:
        with self._session(False):
            self._release()
            self.query_timeout = options.query_timeout
            self.transport = self._injected_transport or self._transport_factory(options)
            stage = "opening the serial port"
            try:
                self.transport.open(options.port, options.address)
                controller = "Direct USB / RS232"
                if getattr(self.transport, "is_gpib", False):
                    stage = "reading the AR488 controller version"
                    self._write("++ver")
                    controller = self._read_controller_version()
                    # Configure controller mode only after the proven ++ver handshake.
                    self._configure_gpib()
                stage = "identifying the DMM with *IDN?"
                identification = self._query("*IDN?")
                self.driver = DriverFactory.detect(identification)
                stage = f"initializing {self.driver.name}"
                if not getattr(self.transport, "is_gpib", False):
                    self._write("SYST:REM")
                # Reset-on-connect.
                self._write(self.driver.reset_command)
                self._write(self.driver.display_path + " OFF")
                if self.driver.name.startswith("Keithley"):
                    self._write("FORM:ELEM READ")
                    self._write("FORM:DATA ASC")
                    self._write("INIT:CONT OFF")
                    self._write("TRIG:COUN 1")
                self._connected = True
                self._info = ConnectionInfo(
                    controller, self.driver.name, identification, options.interface
                )
                stage = "reading the current instrument settings"
                self._read_status()
                self._read_diagnostics()
                return self._status
            except Exception as exc:
                self._release()
                suffix = (
                    f", GPIB address {options.address}"
                    if options.interface == "GPIB (AR488)"
                    else f", {options.baudrate} baud"
                )
                message = f"Connection failed on {options.port}{suffix}, while {stage}: {exc}"
                if isinstance(exc, QueryTimeout):
                    raise QueryTimeout(message, command=exc.command) from exc
                raise TransportError(message) from exc

    def disconnect(self) -> None:
        with self._lock:
            try:
                if self.connected:
                    self._write(self.driver.reset_command)
                    self._write(
                        "++loc" if getattr(self.transport, "is_gpib", False) else "SYST:LOC"
                    )
            finally:
                self._release()

    def _configure_gpib(
        self, *, automatic: bool = True, eos: int = 3, assert_eoi: bool = True,
    ) -> None:
        if type(eos) is not int or not 0 <= eos <= 3:
            raise ValueError("AR488 EOS must be from 0 to 3.")
        if automatic and not assert_eoi:
            # ++eoi also controls auto-read termination. Explicit ++read eoi can still detect EOI with write EOI off.
            raise ValueError("Use manual reading when GPIB write EOI is disabled.")
        self._write("++mode 1")
        # Read-on-query keeps sending and receiving inside one firmware path, without invoking the extra addressing in AR488 0.51.29's ++read.
        self._write("++auto 2" if automatic else "++auto 0")
        self.transport.auto_query = automatic
        # GUI defaults remain EOI-only. Diagnostic profiles may append LF and independently disable EOI on writes; reads still use EOI/EOT.
        self._write(f"++eos {eos}")
        self._write(f"++eoi {int(assert_eoi)}")
        # EOT belongs to the response from AR488 to the PC, not the SCPI write.
        self._write("++eot_char 4")
        self._write("++eot_enable 1")
        self.transport.eot_byte = b"\x04"
        self.transport.requires_eot = True
        self._write(f"++read_tmo_ms {min(32000, max(100, round(self.query_timeout * 1000)))}")
        self._write("++clr")

    def refresh_status(self) -> DmmStatus:
        with self._session():
            self._read_status()
            self._read_diagnostics()
            return self._status

    def _read_status(self) -> DmmStatus:
        # Wait before the first query to avoid stale configuration readback.
        if self.status_delay:
            time.sleep(self.status_delay)
        status = self.driver.read_status(self._query)
        self._status = status
        return status

    def select_function(self, code: int) -> DmmStatus:
        with self._session():
            for command in self.driver.configure(code):
                self._write(command)
            return self._read_status()

    def set_option(self, key: str, value: str) -> DmmStatus:
        with self._session():
            self.driver.apply(key, value, self._status, self._write, self._query)
            return self._read_status()

    def reset(self) -> DmmStatus:
        with self._session():
            self._write(self.driver.reset_command)
            self._write(self.driver.display_path + " OFF")
            if self.driver.name.startswith("Keithley"):
                self._write("FORM:ELEM READ")
                self._write("FORM:DATA ASC")
                self._write("INIT:CONT OFF")
                self._write("TRIG:COUN 1")
            return self._read_status()

    def clear_errors(self) -> DmmStatus:
        with self._session():
            self._write(self.driver.clear_command)
            self._read_diagnostics(read_error=True)
            return self._read_status()

    def read_diagnostics(self) -> DmmDiagnostics:
        with self._session():
            return self._read_diagnostics(read_error=True)

    def _read_diagnostics(self, read_error=False) -> DmmDiagnostics:
        # SYST:ERR? consumes one queue entry; read it only on an explicit request.
        error = (
            self._query("SYST:ERR?")
            if read_error
            else self._diagnostics.error
            if self._diagnostics
            else "Not read"
        )
        diagnostics = DmmDiagnostics(
            int(float(self._query("*SRE?"))),
            int(float(self._query("*STB?"))),
            self._query("SYST:VERS?"),
            error,
            (
                (1, "Measurement status"),
                (4, "Error/event queue"),
                (8, "Questionable status"),
                (16, "Message available"),
                (32, "Standard event"),
                (128, "Operation status"),
            )
            if self.driver.name.startswith("Keithley")
            else (
                (4, "Error/event queue"),
                (8, "Questionable status"),
                (16, "Message available"),
                (32, "Standard event"),
            ),
        )
        self._diagnostics = diagnostics
        return diagnostics

    def read_measurements(
        self,
        timestamp: float | Callable[[], float] = 0.0,
        *,
        keep_connection_on_timeout: bool = False,
    ) -> tuple[Measurement, ...]:
        with self._session(keep_connection_on_timeout=keep_connection_on_timeout):
            trigger = self._status.values["trigger"]
            deadline = time.monotonic() + self.query_timeout if keep_connection_on_timeout else None
            if trigger == "Bus":
                self._write("INIT")
                self._write("*TRG")
                query = "FETC?"
            else:
                query = "READ?"
            try:
                with self.transport.measurements_reader() as reader:
                    reply = self._query(query, deadline, reader)
            except QueryTimeout:
                if keep_connection_on_timeout:
                    # A partial reply must not become part of the next sample.
                    self._discard_incomplete_response()
                raise

            received_at = timestamp() if callable(timestamp) else timestamp
            samples = []
            for field in reply.split(","):
                try:
                    value = float(field.strip())
                except ValueError as exc:
                    raise ValueError(f"Invalid SCPI measurement response: {reply!r}.") from exc
                if not math.isfinite(value):
                    raise ValueError(f"Non-finite SCPI measurement: {field!r}.")
                samples.append(
                    Measurement(
                        value,
                        received_at,
                        self._status.unit,
                        field.strip(),
                        abs(value) >= 9e36,
                        self._status.function_code,
                    )
                )
            return tuple(samples)


    def read_measurement(self, timestamp=0.0) -> Measurement:
        """Read one sample; acquisition uses read_measurements for complete batches."""
        if self.status and int(self.status.values["samples"]) != 1:
            raise ValueError("Use read_measurements() when Samples per trigger exceeds one.")
        return self.read_measurements(timestamp)[0]

    def send_custom(self, command: str, query: bool = False) -> str | None:
        with self._session():
            command = command.strip()
            if (
                command.startswith("++")
                or not command
                or not command.isascii()
                or any(c in command for c in "\r\n")
            ):
                raise ValueError(
                    "Enter one ASCII SCPI command without embedded line breaks or AR488 controller commands."
                )
            # Detect queries automatically so no reply is left in the device queue.
            result = self._query(command) if query or "?" in command else self._write(command)
            self._read_status()
            return result

    def trace(self, direction: str, text: str) -> None:
        """Send diagnostics through the same queued console callback as SCPI I/O."""
        self._trace(direction, text)

    def _write(self, command: str) -> None:
        self._trace("OUT", command)
        self._activity("TX")
        self.transport.write(command)
        if self.command_delay:
            time.sleep(self.command_delay)

    def _query(
        self,
        command: str,
        deadline: float | None = None,
        reader: Callable[[int], bytes] | None = None,
    ) -> str:
        # Acquisition owns its counter across complete measurement batches.
        # Other queries retry here so transient timeouts never reach a dialog.
        attempts = 1 if deadline is not None else self.MAX_CONSECUTIVE_TIMEOUTS
        gpib = getattr(self.transport, "is_gpib", False)
        automatic = gpib and self.transport.auto_query
        # AR488 auto 2 recognizes only a final '?'. Queries with parameters
        # such as SAMP:COUN? MAX require a temporary explicit-read transaction.
        manual_fallback = automatic and not command.rstrip().endswith("?")
        if manual_fallback:
            self._write("++auto 0")
            self.transport.auto_query = False
            try:
                reply = self._query(command, deadline, reader)
            except BaseException:
                try:
                    self._write("++auto 2")
                    self.transport.auto_query = True
                except TransportError:
                    self._release()
                raise
            else:
                self._write("++auto 2")
                self.transport.auto_query = True
                return reply
        interface = (
            f"GPIB (AR488, {'++auto 2' if automatic else '++read eoi'})"
            if gpib else "RS232"
        )
        for attempt in range(1, attempts + 1):
            response_deadline = deadline if deadline is not None else time.monotonic() + self.query_timeout
            self._write(command)
            if gpib and not automatic:
                self._write("++read eoi")
            try:
                if reader is None:
                    with self.transport.measurements_reader() as response_reader:
                        reply = self._read_line(response_deadline, response_reader)
                else:
                    reply = self._read_line(response_deadline, reader)
            except QueryTimeout as exc:
                timeout = QueryTimeout(f"{interface}: {command}: {exc}", command=command)
                if deadline is not None:
                    raise timeout from exc
                self.trace(
                    "WARN",
                    f"{timeout} ({attempt}/{self.MAX_CONSECUTIVE_TIMEOUTS} consecutive).",
                )
                if attempt == attempts:
                    raise QueryTimeout(
                        f"{attempt} consecutive response timeouts (timeout {self.query_timeout:g} s each). "
                        f"Last command: {command} on {interface}. Connection closed. {exc}",
                        command=command,
                    ) from exc
                self._discard_incomplete_response()
            except TransportError as exc:
                raise TransportError(f"{interface}: {command}: {exc}") from exc
            else:
                return reply

    def _discard_incomplete_response(self) -> None:
        self._buffer.clear()
        if getattr(self.transport, "is_gpib", False):
            # Request a break in either explicit or automatic receive mode.
            # Whether the request interrupts immediately depends on firmware.
            self._write("++!")
        self.transport.discard_input()

    def _read_controller_version(self) -> str:
        """Keep controller text on the original bus_read() API, with its default decoding."""
        for attempt in range(1, self.MAX_CONSECUTIVE_TIMEOUTS + 1):
            deadline = time.monotonic() + self.query_timeout
            text = ""
            while time.monotonic() < deadline:
                text += self.transport.read_text()
                if "\n" in text or "\r" in text:
                    result = text.strip("\x00\r\n ")
                    if result:
                        self._trace("IN", result)
                        self._activity("RX")
                        return result
                if len(text) > 65536:
                    raise TransportError("The AR488 controller version response is too long.")
            timeout = f"GPIB (AR488): ++ver: response timeout after {self.query_timeout:g} s"
            self.trace("WARN", f"{timeout} ({attempt}/{self.MAX_CONSECUTIVE_TIMEOUTS} consecutive).")
            if attempt == self.MAX_CONSECUTIVE_TIMEOUTS:
                raise QueryTimeout(
                    f"{attempt} consecutive controller timeouts. {timeout}. Connection closed.",
                    command="++ver",
                )
            self._discard_incomplete_response()
            self._write("++ver")

    def _read_line(self, deadline: float, reader: Callable[[int], bytes]) -> str:
        payload = bytearray()
        ending = {10, 13}
        eot = getattr(self.transport, "eot_byte", b"")
        requires_eot = getattr(self.transport, "requires_eot", False) and bool(eot)
        if requires_eot:
            ending = {eot[0]}
        elif eot:
            ending.add(eot[0])
        raw_preview = bytearray()
        received_bytes = 0
        while time.monotonic() < deadline:
            if not self._buffer:
                self._buffer.extend(reader(4096))
                if not self._buffer:
                    continue
            byte = self._buffer.popleft()
            received_bytes += 1
            if len(raw_preview) < 32:
                raw_preview.append(byte)
            # Some AR488 builds use print(uint8_t) for EOT, which emits the
            # decimal text "4" instead of byte 0x04. Accept it only AFTER a
            # line ending; a digit in the measurement must remain payload.
            decimal_eot = (
                requires_eot and eot == b"\x04" and byte == ord("4")
                and payload and payload[-1] in (10, 13)
            )
            if byte in ending or decimal_eot:
                if not payload:
                    continue
                try:
                    result = payload.decode("ascii").strip()
                except UnicodeDecodeError as exc:
                    link = "GPIB (AR488)" if getattr(self.transport, "is_gpib", False) else "RS232"
                    hint = (
                        "Check the controller and USB/GPIB data path."
                        if getattr(self.transport, "is_gpib", False)
                        else "Check the RS232 framing and connection."
                    )
                    raise TransportError(
                        f"The {link} response contains non-ASCII data: "
                        f"byte 0x{payload[exc.start]:02X} at payload offset {exc.start}. "
                        f"RX: {received_bytes} bytes; hex (first 32): "
                        f"{raw_preview.hex(' ').upper()}. {hint}"
                    ) from exc
                if not result:
                    # An empty EOI trailer or CR/LF-only frame is not a value
                    # of zero and must not reset the acquisition counter.
                    payload.clear()
                    continue
                self._trace("IN", result)
                self._activity("RX")
                return result
            payload.append(byte)
            if len(payload) > 4_000_000:
                raise TransportError("The SCPI response exceeds the supported frame size.")
        detail = ""
        if getattr(self.transport, "is_gpib", False):
            raw_hex = raw_preview.hex(" ").upper() or "none"
            expected = (
                f"EOT 0x{eot[0]:02X} (decimal 4 after CR/LF accepted)"
                if requires_eot else "CR/LF"
            )
            detail = f" RX: {received_bytes} bytes; hex (first 32): {raw_hex}; expected {expected}."
        raise QueryTimeout(
            f"DMM response timeout after {self.query_timeout:g} s. "
            f"Received {len(payload)} incomplete bytes.{detail}"
        )
