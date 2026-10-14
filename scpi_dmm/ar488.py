# -*- coding: utf-8 -*-

"""
PyAR488 transport with explicit ASCII serial writes and deadline-aware reads.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import contextmanager


class TransportError(RuntimeError):
    pass


class InstrumentTransport(ABC):
    eot_byte: bytes = b""
    requires_eot: bool = False
    auto_query: bool = False

    @abstractmethod
    def open(self, port: str, address: int) -> None:
        pass

    @abstractmethod
    def write(self, command: str) -> None:
        pass

    @abstractmethod
    def read(self, size: int = 1) -> bytes:
        pass

    def read_text(self) -> str:
        return self.read(4096).decode("utf-8", errors="replace")

    def read_available(self, size: int = 1) -> bytes:
        return self.read(size)

    @contextmanager
    def measurements_reader(self):
        """Yield a reader that cooperates with the instrument's response deadline."""
        yield self.read

    def discard_input(self) -> None:
        """Discard queued bytes from an incomplete response before retrying."""
        while self.read_available(4096):
            pass

    @abstractmethod
    def close(self) -> None:
        pass


class AR488Transport(InstrumentTransport):
    is_gpib = True
    """Use PyAR488 to open the controller, with explicit ASCII serial framing.

    Controller text uses bus_read(); ordinary read() uses bus_read(decode=False).
    During DMM queries, a non-blocking serial reader lets the instrument enforce
    one total response deadline, even for incomplete or slowly arriving lines.
    """

    def __init__(
        self, timeout: float = 1.0, *, adapter_factory: Callable | None = None,
        wire_trace: Callable[[str, bytes], None] | None = None,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Serial timeout must be a finite positive number.")
        self.timeout = timeout
        self._wire_trace = wire_trace
        self._adapter_factory = adapter_factory
        self._interface = None
        self._buffer = bytearray()

    @property
    def is_open(self) -> bool:
        return self._interface is not None

    def open(self, port: str, address: int) -> None:
        self.close()
        if not port.strip() or type(address) is not int or not 1 <= address <= 30:
            raise ValueError("Select a serial port and a GPIB address from 1 to 30.")
        factory = self._adapter_factory
        if factory is None:
            try:
                # Use the same import preference as the working Wavetek.
                try:
                    from PyAR488 import AR488
                except ImportError:
                    from PyAR488.PyAR488 import AR488
            except ImportError as exc:
                raise TransportError(
                    "PyAR488 is not installed. Run: python -m pip install -r requirements.txt"
                ) from exc
            factory = AR488
        try:
            self._interface = factory(port, timeout=self.timeout, debug=False)
            self._interface.address(address)
            # The constructor already read this character. Do not add queries
            # or modify serial timeouts before the first ++ver exchange.
            character = getattr(self._interface, "_eot_char", "")
            self.eot_byte = character.encode("latin-1") if character else b""
            if len(self.eot_byte) > 1:
                raise ValueError("The controller EOT character must be exactly one byte.")
        except Exception as exc:
            self.close()
            raise TransportError(f"Could not open AR488 on {port}, GPIB {address}: {exc}") from exc

    def write(self, command: str) -> None:
        if self._interface is None:
            raise TransportError("The AR488 port is not open.")
        if not isinstance(command, str):
            raise ValueError("Instrument commands must be ASCII strings.")
        # Own the USB command ending explicitly. PyAR488.bus_write() appends
        # LF+CR; the GUI sends exactly one LF to AR488's line parser instead.
        command = command.rstrip("\r\n")
        if not command or any(char in command for char in "\r\n"):
            raise ValueError("Send one ASCII command without embedded line breaks.")
        if not all(32 <= ord(char) <= 126 for char in command):
            raise ValueError("Instrument commands must contain printable ASCII characters.")
        payload = command.encode("ascii") + b"\n"
        try:
            self._record_wire("TX", payload)
            written = self._interface._ser.write(payload)
            if written != len(payload):
                raise TransportError("Incomplete AR488 serial write.")
        except Exception as exc:
            raise TransportError(f"AR488 write error: {exc}") from exc

    def _record_wire(self, direction: str, payload: bytes) -> None:
        if self._wire_trace is not None and payload:
            self._wire_trace(direction, payload)

    def read_text(self) -> str:
        if self._interface is None:
            raise TransportError("The AR488 port is not open.")
        try:
            if self._buffer:
                response = bytes(self._buffer)
                self._buffer.clear()
            else:
                response = self._interface.bus_read()
                raw = response.encode("latin-1") if isinstance(response, str) else bytes(response or b"")
                self._record_wire("RX", raw)
            return response.decode("utf-8") if isinstance(response, bytes) else str(response or "")
        except Exception as exc:
            raise TransportError(f"AR488 read error: {exc}") from exc

    def read(self, size: int = 1) -> bytes:
        if self._interface is None:
            raise TransportError("The AR488 port is not open.")
        try:
            if not self._buffer:
                response = self._interface.bus_read(decode=False)
                if isinstance(response, str):
                    response = response.encode("latin-1")
                self._record_wire("RX", bytes(response or b""))
                self._buffer.extend(response or b"")
            result = bytes(self._buffer[:size])
            del self._buffer[: len(result)]
            return result
        except Exception as exc:
            raise TransportError(f"AR488 read error: {exc}") from exc

    def read_available(self, size: int = 1) -> bytes:
        if self._interface is None:
            raise TransportError("The AR488 port is not open.")
        if self._buffer:
            result = bytes(self._buffer[:size])
            del self._buffer[: len(result)]
            return result
        serial = getattr(self._interface, "_ser", None)
        if serial is None:
            return b""
        try:
            waiting = serial.in_waiting
            result = bytes(serial.read(min(size, waiting))) if waiting else b""
            self._record_wire("RX", result)
            return result
        except Exception as exc:
            raise TransportError(f"AR488 read error: {exc}") from exc

    @contextmanager
    def measurements_reader(self):
        if self._interface is None:
            raise TransportError("The AR488 port is not open.")
        serial = self._interface._ser
        original_timeout = serial.timeout
        try:
            serial.timeout = 0
            yield self.read_available
        finally:
            serial.timeout = original_timeout

    def discard_input(self) -> None:
        if self._interface is None:
            raise TransportError("The AR488 port is not open.")
        self._buffer.clear()
        try:
            self._interface._ser.reset_input_buffer()
        except Exception as exc:
            raise TransportError(f"AR488 input reset error: {exc}") from exc

    def close(self) -> None:
        interface, self._interface = self._interface, None
        self._buffer.clear()
        self.eot_byte = b""
        self.requires_eot = False
        self.auto_query = False
        if interface is not None:
            try:
                close = getattr(interface, "close", None)
                if callable(close):
                    close()
                else:
                    interface._ser.close()
            except Exception:
                # Keep cleanup idempotent and preserve the original I/O error.
                pass


# Compatibility name for the same hardware transport.
PyAR488Transport = AR488Transport
