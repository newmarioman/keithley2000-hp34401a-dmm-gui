# -*- coding: utf-8 -*-

"""
Immutable Instrument Definitions and Application Preference Models.

This module defines the specifications for measurement functions, range 
mappings, digit resolutions, and trigger commands for the 
Keithley2000 / HP34401A DMM, along with the application runtime 
configuration and user settings models.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from types import MappingProxyType


class MeasurementFunction(IntEnum):
    VOLTAGE_DC = 1
    VOLTAGE_AC = 2
    CURRENT_DC = 3
    CURRENT_AC = 4
    RESISTANCE_2W = 5
    RESISTANCE_4W = 6
    FREQUENCY = 7
    PERIOD = 8
    TEMPERATURE = 9
    DIODE = 10
    CONTINUITY = 11


@dataclass(frozen=True)
class FunctionSpec:
    code: MeasurementFunction
    label: str
    unit: str
    scpi: str


FUNCTIONS = (
    FunctionSpec(MeasurementFunction.VOLTAGE_DC, "Voltage DC", "V", "VOLT:DC"),
    FunctionSpec(MeasurementFunction.VOLTAGE_AC, "Voltage AC", "V", "VOLT:AC"),
    FunctionSpec(MeasurementFunction.CURRENT_DC, "Current DC", "A", "CURR:DC"),
    FunctionSpec(MeasurementFunction.CURRENT_AC, "Current AC", "A", "CURR:AC"),
    FunctionSpec(MeasurementFunction.RESISTANCE_2W, "Resistance 2w", "Ω", "RES"),
    FunctionSpec(MeasurementFunction.RESISTANCE_4W, "Resistance 4w", "Ω", "FRES"),
    FunctionSpec(MeasurementFunction.FREQUENCY, "Frequency", "Hz", "FREQ"),
    FunctionSpec(MeasurementFunction.PERIOD, "Period", "s", "PER"),
    FunctionSpec(MeasurementFunction.TEMPERATURE, "Temperature", "°C", "TEMP"),
    FunctionSpec(MeasurementFunction.DIODE, "Diode", "V", "DIOD"),
    FunctionSpec(MeasurementFunction.CONTINUITY, "Continuity", "Ω", "CONT"),
)
FUNCTION_BY_CODE = MappingProxyType({int(item.code): item for item in FUNCTIONS})
ACQUISITION_INTERVALS = (
    "MAX",
    *(f"{i / 10:g}" for i in range(1, 10)),
    *(str(i) for i in range(1, 3601)),
)
DIGIT_LABELS = ("4½", "5½", "6½")


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path = Path(__file__).resolve().parents[1]
    tool_name: str = "Keithley2000 / HP34401A DMM"
    author: str = "Lorenzo Mariani"
    version_major: int = 1
    version_minor: int = 0
    version_patch: int = 0
    versione_tag: str = "-dev"
    version: str = str(version_major) + "." + str(version_minor) + "." + str(version_patch) + versione_tag
    width: int = 1400
    height: int = 788
    query_timeout: float = 1.0
    command_delay: float = 0.0001
    status_delay: float = 1.0
    plot_interval_ms: int = 80
    output_folder_name: str = "Keithley2000_HP34401A_DMM"
    theme_styles: tuple[str, ...] = (
        "winnative",
        "clam",
        "alt",
        "default",
        "classic",
        "vista",
        "xpnative",
    )
    audio_themes: tuple[str, ...] = ("big-sur", "chime", "mario", "material", "zelda")

    @property
    def settings_path(self) -> Path:
        return self.root_dir / "settings.ini"

    @property
    def display_font_path(self) -> Path:
        return self.root_dir / "fonts" / "digital-7.ttf"

    @property
    def icon_path(self) -> Path:
        return self.root_dir / "assets" / "DMM_GUI.ico"


@dataclass
class UserSettings:
    program_path: Path = field(default_factory=Path.home)
    theme_style: str = "vista"
    audio_theme: str = "big-sur"
