# -*- coding: utf-8 -*-

"""
Validated connection profiles and interchangeable RS232/GPIB transports.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

import math
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from .ar488 import AR488Transport, InstrumentTransport, TransportError


@dataclass(frozen=True)
class ConnectionOptions:
    port: str
    interface: str = "RS232"
    address: int = 22
    baudrate: int = 19200
    parity: str = "N"
    stopbits: float = 1
    bytesize: int = 8
    xonxoff: bool = False
    rtscts: bool = False
    dsrdtr: bool = False
    termination: str = "\r"
    query_timeout: float = 1.0

    def __post_init__(self) -> None:
        if not self.port.strip():
            raise ValueError("Select an available serial port.")
        if self.interface not in ("RS232", "GPIB (AR488)"):
            raise ValueError("Select RS232 or GPIB (AR488).")
        if type(self.address) is not int or not 1 <= self.address <= 30:
            raise ValueError("The GPIB address must be from 1 to 30.")
        if type(self.baudrate) is not int or not 50 <= self.baudrate <= 115200:
            raise ValueError("Select a valid baud rate.")
        if (
            self.parity not in ("N", "E", "O", "M", "S")
            or self.stopbits not in (1, 1.5, 2)
            or self.bytesize not in (5, 6, 7, 8)
        ):
            raise ValueError("Invalid serial framing parameters.")
        if self.termination not in ("\r", "\n", "\r\n", "\n\r"):
            raise ValueError("Select CR, LF, CR+LF, or LF+CR as the command terminator.")
        if not math.isfinite(self.query_timeout) or not 0.1 <= self.query_timeout <= 3600:
            raise ValueError("Response timeout must be from 0.1 to 3600 seconds.")


class RS232Transport(InstrumentTransport):
    """Send ASCII commands followed by the selected real CR/LF bytes."""

    is_gpib = False
    eot_byte = b""

    def __init__(
        self, options: ConnectionOptions, *, serial_factory: Callable | None = None
    ) -> None:
        self.options = options
        self._factory = serial_factory
        self._serial = None

    def open(self, port: str, address: int = 0) -> None:
        self.close()
        factory = self._factory
        if factory is None:
            try:
                import serial
            except ImportError as exc:
                raise TransportError(
                    "Install pyserial with: python -m pip install -r requirements.txt"
                ) from exc
            factory = serial.Serial
        try:
            self._serial = factory(
                port=port,
                baudrate=self.options.baudrate,
                parity=self.options.parity,
                stopbits=self.options.stopbits,
                bytesize=self.options.bytesize,
                xonxoff=self.options.xonxoff,
                rtscts=self.options.rtscts,
                dsrdtr=self.options.dsrdtr,
                timeout=0.1,
                write_timeout=self.options.query_timeout,
            )
            self._serial.reset_input_buffer()
        except Exception as exc:
            self.close()
            raise TransportError(f"Could not open RS232 on {port}: {exc}") from exc

    @property
    def is_open(self) -> bool:
        return self._serial is not None

    def write(self, command: str) -> None:
        if self._serial is None:
            raise TransportError("The RS232 port is not open.")
        command = command.rstrip("\r\n")
        if not command or not command.isascii() or any(c in command for c in "\r\n"):
            raise ValueError("Send one ASCII command without embedded line breaks.")
        try:
            payload = (command + self.options.termination).encode("ascii")
            written = self._serial.write(payload)
            if written != len(payload):
                raise TransportError("Incomplete serial write.")
        except Exception as exc:
            raise TransportError(f"RS232 write error: {exc}") from exc

    def read(self, size: int = 1) -> bytes:
        if self._serial is None:
            raise TransportError("The RS232 port is not open.")
        try:
            waiting = self._serial.in_waiting
            return bytes(self._serial.read(min(size, waiting) if waiting else 1))
        except Exception as exc:
            raise TransportError(f"RS232 read error: {exc}") from exc

    def read_available(self, size: int = 1) -> bytes:
        if self._serial is None:
            raise TransportError("The RS232 port is not open.")
        return (
            bytes(self._serial.read(min(size, self._serial.in_waiting)))
            if self._serial.in_waiting
            else b""
        )

    @contextmanager
    def measurements_reader(self):
        if self._serial is None:
            raise TransportError("The RS232 port is not open.")
        serial = self._serial
        original_timeout = serial.timeout
        try:
            # The instrument owns the total response deadline, including fragments.
            serial.timeout = 0
            yield self.read
        finally:
            serial.timeout = original_timeout

    def discard_input(self) -> None:
        if self._serial is None:
            raise TransportError("The RS232 port is not open.")
        try:
            self._serial.reset_input_buffer()
        except Exception as exc:
            raise TransportError(f"RS232 input reset error: {exc}") from exc


    def close(self) -> None:
        serial, self._serial = self._serial, None
        if serial is not None:
            try:
                serial.close()
            except Exception:
                pass


class TransportFactory:
    @staticmethod
    def create(options: ConnectionOptions) -> InstrumentTransport:
        return AR488Transport() if options.interface == "GPIB (AR488)" else RS232Transport(options)


__all__ = [
    "AR488Transport",
    "ConnectionOptions",
    "InstrumentTransport",
    "RS232Transport",
    "TransportError",
    "TransportFactory",
]
