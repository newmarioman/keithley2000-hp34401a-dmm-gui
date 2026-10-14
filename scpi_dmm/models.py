# -*- coding: utf-8 -*-

"""
Instrument Snapshots and Bounded Acquisition Data Independent of Tkinter.

This module contains the core thread-safe models and containers for processing
the Keithley2000 / HP34401A status, active connection metadata, individual parsed measurements,
and the rolling historical TraceBuffer for plotting.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

import math
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

# Local Packages
from .config import FUNCTION_BY_CODE, FunctionSpec


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    integer: bool = False
    editable: bool = False
    hint: str = ""
    action: bool = False

    def validate(self, text: str) -> str:
        text = str(text).strip()
        if text in self.choices:
            return text
        if self.choices and not self.editable:
            raise ValueError(f"Select a valid value for {self.label}.")
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError(f"{self.label} requires a number.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{self.label} requires a finite number.")
        if self.integer and not number.is_integer():
            raise ValueError(f"{self.label} requires an integer.")
        if (
            self.minimum is not None
            and number < self.minimum
            or self.maximum is not None
            and number > self.maximum
        ):
            raise ValueError(f"{self.label} must be between {self.minimum:g} and {self.maximum:g}.")
        return str(int(number)) if self.integer else format(number, ".12g")


@dataclass(frozen=True)
class DmmStatus:
    model: str
    function_code: int
    values: Mapping[str, str]
    specs: tuple[SettingSpec, ...]
    inputs: str = "Unknown"
    line_frequency: str = "Not queryable on this model"
    unit: str = "V"
    supported_functions: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @property
    def function(self) -> FunctionSpec:
        return FUNCTION_BY_CODE[self.function_code]

    @property
    def digits(self) -> str:
        return self.values.get("digits", "6½")

    @property
    def digits_editable(self) -> bool:
        return self.model != "HP 34401A" or self.values.get("autorange") != "ON"


@dataclass(frozen=True)
class DmmDiagnostics:
    srq_mask: int
    status_byte: int
    scpi_version: str
    error: str = "Not read"
    event_bits: tuple[tuple[int, str], ...] = (
        (4, "Error/event queue"),
        (8, "Questionable status"),
        (16, "Message available"),
        (32, "Standard event"),
    )

    @property
    def srq_events(self) -> tuple[str, ...]:
        return tuple(label for bit, label in self.event_bits if self.srq_mask & bit)


@dataclass(frozen=True)
class ConnectionInfo:
    controller_name: str
    instrument_name: str
    identification: str
    interface: str


@dataclass(frozen=True)
class Measurement:
    value: float
    timestamp: float
    unit: str
    raw: str = ""
    overload: bool = False
    function_code: int | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or not math.isfinite(self.timestamp) or self.timestamp < 0:
            raise ValueError("Measurements require a finite value and a nonnegative timestamp.")

    @property
    def function_label(self) -> str:
        spec = FUNCTION_BY_CODE.get(self.function_code)
        return spec.label if spec else "Measurement"

    @property
    def display_text(self) -> str:
        return f"{self.format_value()} {self.unit}"

    def format_value(self) -> str:
        if self.overload:
            return "OVERLOAD"
        formatted = f"{self.value:+.{10}f}"
        if "." in formatted:
            parts = formatted.split(".")
            integer_part = parts[0]
            decimal_part = parts[1].rstrip("0")
            if decimal_part:
                return f"{integer_part}.{decimal_part}"
            return integer_part
        return formatted


class TraceBuffer:
    """Keep a bounded scrolling trace and count every acquired sample."""

    def __init__(self, capacity: int = 100) -> None:
        if type(capacity) is not int or not 2 <= capacity <= 10000:
            raise ValueError("Plot history must contain from 2 to 10000 samples.")
        self.capacity = capacity
        self._samples: deque[Measurement] = deque(maxlen=capacity)
        self._count = 0
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def append(self, sample: Measurement) -> None:
        with self._lock:
            self._samples.append(sample)
            self._count += 1

    def snapshot(self) -> tuple[Measurement, ...]:
        with self._lock:
            return tuple(self._samples)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()
            self._count = 0
