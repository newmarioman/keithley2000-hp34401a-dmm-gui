# -*- coding: utf-8 -*-

"""
Model-specific serial choices and the requested connection defaults.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class SerialParameter:
    key: str
    label: str
    choices: tuple[str, ...]
    default: str


@dataclass(frozen=True)
class ConnectionProfile:
    model: str
    gpib_address: int
    parameters: tuple[SerialParameter, ...]
    parity_data_bits: tuple[tuple[str, str], ...]

    def data_bits_for(self, parity: str) -> str:
        return dict(self.parity_data_bits)[parity]


BAUD_RATES = ("300", "600", "1200", "2400", "4800", "9600", "19200")
PROFILES = (
    ConnectionProfile(
        "Keithley 2000",
        16,
        (
            SerialParameter("baudrate", "Baud rate", BAUD_RATES, "19200"),
            SerialParameter("parity", "Parity", ("None",), "None"),
            SerialParameter("bytesize", "Data bits", ("8",), "8"),
            SerialParameter("stopbits", "Stop bits", ("1",), "1"),
            SerialParameter("termination", "Command terminator", ("CR", "CR+LF", "LF+CR"), "CR"),
            SerialParameter("xonxoff", "XON / XOFF", ("OFF", "ON"), "OFF"),
            SerialParameter("rtscts", "RTS / CTS", ("OFF",), "OFF"),
            SerialParameter("dsrdtr", "DSR / DTR", ("OFF",), "OFF"),
        ),
        (("None", "8"),),
    ),
    ConnectionProfile(
        "HP 34401A",
        22,
        (
            SerialParameter("baudrate", "Baud rate", BAUD_RATES[:-1], "9600"),
            SerialParameter("parity", "Parity", ("None", "Even", "Odd"), "None"),
            SerialParameter("bytesize", "Data bits", ("8",), "8"),
            SerialParameter("stopbits", "Stop bits", ("1", "2"), "1"),
            SerialParameter("termination", "Command terminator", ("LF", "CR+LF"), "CR+LF"),
            SerialParameter("xonxoff", "XON / XOFF", ("OFF",), "OFF"),
            SerialParameter("rtscts", "RTS / CTS", ("OFF",), "OFF"),
            SerialParameter("dsrdtr", "DSR / DTR", ("OFF", "ON"), "OFF"),
        ),
        (("None", "8"), ("Even", "7"), ("Odd", "7")),
    ),
)
PROFILE_BY_MODEL = MappingProxyType({profile.model: profile for profile in PROFILES})
