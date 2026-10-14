# -*- coding: utf-8 -*-

"""
Model-specific SCPI strategies derived from the manufacturers' manuals.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

# Local Packages
from .config import DIGIT_LABELS, FUNCTION_BY_CODE, FUNCTIONS
from .models import DmmStatus, SettingSpec


def numeric(text: str) -> str:
    value = float(text.strip())
    if not math.isfinite(value):
        raise ValueError(f"Non-finite SCPI response: {text!r}.")
    return format(value, ".12g")


def boolean(text: str) -> str:
    text = text.strip().upper()
    if text in ("ON", "OFF"):
        return text
    number = float(text)
    if number not in (0, 1):
        raise ValueError(f"Invalid boolean SCPI response: {text!r}.")
    return "ON" if number else "OFF"


def canonical(text: str) -> str:
    return text.strip().strip('"').strip("'").upper()


def function_code(text: str) -> int:
    value = canonical(text).replace(":", "").replace(" ", "")
    aliases = {
        "VOLT": 1,
        "VOLTDC": 1,
        "VOLTAGEDC": 1,
        "VOLTAGE": 1,
        "VOLTAC": 2,
        "VOLTAGEAC": 2,
        "CURR": 3,
        "CURRDC": 3,
        "CURRENTDC": 3,
        "CURRENT": 3,
        "CURRAC": 4,
        "CURRENTAC": 4,
        "RES": 5,
        "RESISTANCE": 5,
        "FRES": 6,
        "FRESISTANCE": 6,
        "FREQ": 7,
        "FREQUENCY": 7,
        "PER": 8,
        "PERIOD": 8,
        "TEMP": 9,
        "TEMPERATURE": 9,
        "DIOD": 10,
        "DIODE": 10,
        "CONT": 11,
        "CONTINUITY": 11,
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported measurement function reported by the DMM: {text!r}."
        ) from exc


@dataclass(frozen=True)
class SettingBinding:
    spec: SettingSpec
    path: str
    decoder: Callable[[str], str] = numeric
    arguments: tuple[tuple[str, str], ...] = ()

    def decode(self, text: str) -> str:
        result = self.decoder(text)
        for label, argument in self.arguments:
            if result == argument:
                return label
        return result

    def command(self, text: str) -> str:
        value = self.spec.validate(text)
        argument = dict(self.arguments).get(value, value)
        return f"{self.path} {argument}"


def choice(
    key: str,
    label: str,
    path: str,
    values: tuple[str, ...],
    arguments=(),
    decoder=canonical,
    hint="",
) -> SettingBinding:
    return SettingBinding(
        SettingSpec(key, label, values, hint=hint), path, decoder, tuple(arguments)
    )


def number(
    key: str,
    label: str,
    path: str,
    minimum: float,
    maximum: float,
    *,
    integer=False,
    choices=(),
    hint="",
) -> SettingBinding:
    return SettingBinding(
        SettingSpec(key, label, tuple(choices), minimum, maximum, integer, True, hint), path
    )


def on_off(key: str, label: str, path: str) -> SettingBinding:
    return choice(key, label, path, ("OFF", "ON"), decoder=boolean)


DC_VOLTAGES = (
    ("100 mV", "0.1"),
    ("1 V", "1"),
    ("10 V", "10"),
    ("100 V", "100"),
    ("1000 V", "1000"),
)
AC_VOLTAGES = (*DC_VOLTAGES[:-1], ("750 V", "750"))
DC_CURRENTS = (("10 mA", "0.01"), ("100 mA", "0.1"), ("1 A", "1"), ("3 A", "3"))
RESISTANCES = tuple(
    zip(
        ("100 Ω", "1 kΩ", "10 kΩ", "100 kΩ", "1 MΩ", "10 MΩ", "100 MΩ"),
        ("100", "1000", "10000", "100000", "1000000", "10000000", "100000000"),
    )
)


@dataclass(frozen=True)
class RangeDecoder:
    table: tuple[tuple[str, str], ...]

    def __call__(self, text: str) -> str:
        value = float(text)
        for _label, argument in self.table:
            full_scale = float(argument)
            if math.isclose(value, full_scale, rel_tol=1e-6) or 1 <= value / full_scale <= 1.21:
                return argument
        return numeric(text)


class ScpiModelDriver(ABC):
    """Describe valid controls, their SCPI paths and the actual queried state."""

    name: str
    function_query = "SENS:FUNC?"
    reset_command = "*RST"
    clear_command = "*CLS"
    display_path = "DISP"
    autozero_path = "SENS:ZERO:AUTO"
    supported_functions: tuple[int, ...]
    max_samples = 1024
    max_trigger_delay = 999999.999

    def configure(self, code: int) -> tuple[str, ...]:
        if code not in self.supported_functions:
            raise ValueError(f"{FUNCTION_BY_CODE[code].label} is not supported by {self.name}.")
        return (f"CONF:{FUNCTION_BY_CODE[code].scpi}",)

    def range_binding(self, code: int) -> SettingBinding | None:
        if code in (1, 2, 3, 4, 5, 6, 7, 8):
            table = self.ranges(code)
            return choice(
                "range",
                "Range" if code not in (7, 8) else "Threshold range",
                self.range_path(code),
                tuple(label for label, value in table),
                table,
                RangeDecoder(table),
            )
        return None

    @abstractmethod
    def ranges(self, code: int) -> tuple[tuple[str, str], ...]:
        raise NotImplementedError

    @abstractmethod
    def range_path(self, code: int) -> str:
        raise NotImplementedError

    def common_bindings(self) -> list[SettingBinding]:
        return [
            choice(
                "trigger",
                "Trigger",
                "TRIG:SOUR",
                ("Internal", "External", "Bus"),
                (("Internal", "IMM"), ("External", "EXT"), ("Bus", "BUS")),
            ),
            number(
                "trigger_delay",
                "Trigger delay [s]",
                "TRIG:DEL",
                0,
                self.max_trigger_delay,
                choices=("0", "0.1", "1"),
            ),
            on_off("trigger_auto", "Auto trigger delay", "TRIG:DEL:AUTO"),
            number(
                "samples",
                "Samples per trigger",
                "SAMP:COUN",
                1,
                self.max_samples,
                integer=True,
                choices=("1", "10", "100"),
                hint="Every returned sample is plotted and logged. The CSV timestamp is the host receive time, not a hardware timestamp.",
            ),
            on_off("autozero", "Autozero", self.autozero_path),
            on_off("display", "Instrument display", self.display_path),
        ]

    @abstractmethod
    def bindings(self, code: int, values: dict[str, str]) -> list[SettingBinding]:
        raise NotImplementedError

    def read_status(self, query: Callable[[str], str]) -> DmmStatus:
        code = function_code(query(self.function_query))
        if code not in self.supported_functions:
            raise ValueError(f"The reported function is not supported by {self.name}.")
        values = self.extra_state(query, code)
        bindings = self.bindings(code, values)
        read_keys = set(values)
        for binding in bindings:
            if not binding.spec.action and binding.path and binding.spec.key not in read_keys:
                values[binding.spec.key] = binding.decode(query(binding.path + "?"))
                read_keys.add(binding.spec.key)
        # Unit and reference-junction choices reveal additional model-specific fields.
        bindings = self.bindings(code, values)
        for binding in bindings:
            if not binding.spec.action and binding.path and binding.spec.key not in read_keys:
                values[binding.spec.key] = binding.decode(query(binding.path + "?"))
        self.finish_state(values, code)
        inputs, line = self.read_inputs(query), self.read_line(query)
        specs = tuple(binding.spec for binding in bindings)
        return DmmStatus(
            self.name,
            code,
            values,
            specs,
            inputs,
            line,
            self.unit(code, values),
            self.supported_functions,
        )

    def extra_state(self, query, code) -> dict[str, str]:
        return {}

    def finish_state(self, values: dict[str, str], code: int) -> None:
        values.setdefault("digits", "6½")

    @abstractmethod
    def read_inputs(self, query) -> str:
        raise NotImplementedError

    def read_line(self, query) -> str:
        return "Not queryable on this model"

    def unit(self, code: int, values: dict[str, str]) -> str:
        return FUNCTION_BY_CODE[code].unit

    def apply(self, key: str, value: str, status: DmmStatus, write, query) -> None:
        binding = next(
            (
                item
                for item in self.bindings(status.function_code, dict(status.values))
                if item.spec.key == key
            ),
            None,
        )
        if binding is None or not binding.path:
            raise ValueError(f"{key} is not available for this function on {self.name}.")
        write(binding.command(value))


class Keithley2000Driver(ScpiModelDriver):
    name = "Keithley 2000"
    supported_functions = tuple(int(spec.code) for spec in FUNCTIONS)
    reset_command = "SYST:PRES"
    clear_command = "SYST:CLE"
    display_path = "DISP:ENAB"
    autozero_path = "SYST:AZER:STAT"

    def ranges(self, code):
        if code in (1, 7, 8):
            return DC_VOLTAGES
        if code == 2:
            return AC_VOLTAGES
        if code in (3, 4):
            return DC_CURRENTS if code == 3 else DC_CURRENTS[1:]
        return RESISTANCES

    def range_path(self, code):
        function = FUNCTION_BY_CODE[code].scpi
        return f"SENS:{function}:THR:VOLT:RANG" if code in (7, 8) else f"SENS:{function}:RANG"

    def bindings(self, code, values):
        path = "SENS:" + FUNCTION_BY_CODE[code].scpi
        result = []
        range_binding = self.range_binding(code)
        if range_binding:
            if code not in (7, 8):
                result.append(on_off("autorange", "Autorange", range_binding.path + ":AUTO"))
            result.append(range_binding)
        if code not in (10, 11):
            result.append(
                choice(
                    "digits",
                    "Resolution [digits]",
                    path + ":DIG",
                    DIGIT_LABELS,
                    (("4½", "5"), ("5½", "6"), ("6½", "7"), ("3½", "4")),
                    numeric,
                )
            )
            result.extend(
                [
                    on_off("relative", "Relative measurement", path + ":REF:STAT"),
                    number(
                        "reference", "Reference value", path + ":REF", *self.reference_limits(code)
                    ),
                    SettingBinding(
                        SettingSpec(
                            "reference_acquire", "Use input as reference", ("Acquire",), action=True
                        ),
                        path + ":REF:ACQ",
                    ),
                ]
            )
        if code in (1, 2, 3, 4, 5, 6, 9):
            result.extend(
                [
                    number(
                        "nplc",
                        "Integration [PLC]",
                        path + ":NPLC",
                        0.01,
                        10,
                        choices=("0.01", "0.1", "1", "5", "10"),
                    ),
                    on_off("filter", "Digital filter", path + ":AVER:STAT"),
                    choice(
                        "filter_type",
                        "Filter type",
                        path + ":AVER:TCON",
                        ("Moving", "Repeat"),
                        (("Moving", "MOV"), ("Repeat", "REP")),
                    ),
                    number(
                        "filter_count",
                        "Filter count",
                        path + ":AVER:COUN",
                        1,
                        100,
                        integer=True,
                        choices=("1", "10", "100"),
                    ),
                ]
            )
        if code in (2, 4):
            result.append(
                choice(
                    "bandwidth",
                    "AC bandwidth [Hz]",
                    path + ":DET:BAND",
                    ("3", "30", "300", "3000", "30000", "300000"),
                    decoder=numeric,
                )
            )
        if code == 10:
            table = (("10 µA", "1e-05"), ("100 µA", "0.0001"), ("1 mA", "0.001"))
            result.append(
                choice(
                    "diode_current",
                    "Diode test current",
                    path + ":CURR:RANG",
                    tuple(label for label, value in table),
                    table,
                    numeric,
                )
            )
        if code == 11:
            result.append(
                number(
                    "continuity",
                    "Continuity threshold [Ω]",
                    path + ":THR",
                    1,
                    1000,
                    choices=("1", "10", "100", "1000"),
                )
            )
        if code in (1, 2):
            unit_path = "UNIT:" + FUNCTION_BY_CODE[code].scpi
            result.append(
                choice(
                    "unit",
                    "Measurement unit",
                    unit_path,
                    ("V", "dB", "dBm"),
                    (("V", "V"), ("dB", "DB"), ("dBm", "DBM")),
                )
            )
            if values.get("unit") == "dB":
                result.append(
                    number("db_reference", "dB reference [V]", unit_path + ":DB:REF", 1e-7, 1000)
                )
            if values.get("unit") == "dBm":
                result.append(
                    number(
                        "dbm_impedance",
                        "dBm impedance [Ω]",
                        unit_path + ":DBM:IMP",
                        1,
                        9999,
                        integer=True,
                    )
                )
        if code == 9:
            junction = path + ":TC:RJUN"
            result.extend(
                [
                    choice("unit", "Temperature unit", "UNIT:TEMP", ("C", "F", "K")),
                    choice("thermocouple", "Thermocouple", path + ":TC:TYPE", ("J", "K", "T")),
                    choice(
                        "junction",
                        "Reference junction",
                        junction + ":RSEL",
                        ("Simulated", "Real"),
                        (("Simulated", "SIM"), ("Real", "REAL")),
                    ),
                ]
            )
            if values.get("junction") == "Real":
                result.extend(
                    [
                        number(
                            "tc_coefficient",
                            "Junction coefficient",
                            junction + ":REAL:TCOEF",
                            -0.09999,
                            0.09999,
                        ),
                        number(
                            "tc_offset",
                            "Junction offset [V]",
                            junction + ":REAL:OFFS",
                            -0.09999,
                            0.09999,
                        ),
                    ]
                )
            else:
                # The command uses the selected temperature unit. Convert the physical 0–50°C bounds.
                limits = {"C": (0, 50), "F": (32, 122), "K": (273.15, 323.15)}[
                    values.get("unit", "C")
                ]
                result.append(
                    number(
                        "junction_temperature",
                        "Simulated junction temperature",
                        junction + ":SIM",
                        *limits,
                    )
                )
        if code not in (10, 11):
            result.extend(
                [
                    on_off("hold", "Reading hold", "SENS:HOLD:STAT"),
                    number("hold_window", "Hold window [%]", "SENS:HOLD:WIND", 0.01, 20),
                    number("hold_count", "Hold count", "SENS:HOLD:COUN", 2, 100, integer=True),
                ]
            )
        result.extend(self.common_bindings())
        return result

    def reference_limits(self, code):
        return {
            1: (-1010, 1010),
            2: (-757.5, 757.5),
            3: (-3.1, 3.1),
            4: (-3.1, 3.1),
            5: (0, 120e6),
            6: (0, 101e6),
            7: (0, 1.5e7),
            8: (0, 1),
            9: (-200, 1372),
        }[code]

    def read_inputs(self, query):
        return "Front" if boolean(query("SYST:FRSW?")) == "ON" else "Rear"

    def read_line(self, query):
        return numeric(query("SYST:LFR?")) + " Hz"

    def unit(self, code, values):
        if code in (1, 2):
            return values.get("unit", "V")
        if code == 9:
            return {"C": "°C", "F": "°F", "K": "K"}[values.get("unit", "C")]
        return super().unit(code, values)

    def apply(self, key, value, status, write, query):
        path = "SENS:" + status.function.scpi
        if key == "reference_acquire":
            write(path + ":REF:ACQ")
            write(path + ":REF:STAT ON")
            return
        super().apply(key, value, status, write, query)
        if key == "reference":
            write(path + ":REF:STAT ON")


class HP34401ADriver(ScpiModelDriver):
    name = "HP 34401A"
    function_query = "FUNC?"
    supported_functions = (1, 2, 3, 4, 5, 6, 7, 8, 10, 11)
    max_samples = 50000
    max_trigger_delay = 3600

    def ranges(self, code):
        if code == 1:
            return DC_VOLTAGES
        if code in (2, 7, 8):
            return AC_VOLTAGES
        if code in (3, 4):
            return DC_CURRENTS if code == 3 else DC_CURRENTS[-2:]
        return RESISTANCES

    def range_path(self, code):
        function = FUNCTION_BY_CODE[code].scpi
        return f"SENS:{function}:VOLT:RANG" if code in (7, 8) else f"SENS:{function}:RANG"

    def bindings(self, code, values):
        path = "SENS:" + FUNCTION_BY_CODE[code].scpi
        result = []
        range_binding = self.range_binding(code)
        if range_binding:
            result.extend(
                [on_off("autorange", "Autorange", range_binding.path + ":AUTO"), range_binding]
            )
            precision_path = path + (":APER" if code in (7, 8) else ":RES")
            result.append(
                choice(
                    "digits",
                    "Resolution [digits]",
                    "",
                    DIGIT_LABELS,
                    hint="Uses RESolution on DC/AC/ohms and APERture on frequency/period; integration readback may also change.",
                )
            )
            result.append(SettingBinding(SettingSpec("_precision", "Resolution"), precision_path))
        if code in (1, 3, 5, 6):
            result.append(
                choice(
                    "nplc",
                    "Integration [PLC]",
                    path + ":NPLC",
                    ("0.02", "0.2", "1", "10", "100"),
                    decoder=numeric,
                )
            )
        if code in (2, 4):
            result.append(
                choice(
                    "bandwidth",
                    "AC bandwidth [Hz]",
                    "SENS:DET:BAND",
                    ("3", "20", "200"),
                    decoder=numeric,
                )
            )
        if code in (7, 8):
            result.append(
                choice(
                    "aperture",
                    "Gate time [s]",
                    path + ":APER",
                    ("0.01", "0.1", "1"),
                    decoder=numeric,
                )
            )
        if code == 1:
            result.append(on_off("impedance", "Auto input impedance", "INP:IMP:AUTO"))
        if code not in (10, 11):
            result.extend(
                [
                    choice("relative", "Relative measurement", "", ("OFF", "ON")),
                    number(
                        "reference",
                        "Null reference",
                        "CALC:NULL:OFFS",
                        -self.reference_limit(code),
                        self.reference_limit(code),
                    ),
                    SettingBinding(
                        SettingSpec(
                            "reference_acquire", "Use input as reference", ("Acquire",), action=True
                        ),
                        "CALC:NULL",
                    ),
                ]
            )
        if code in (1, 2):
            result.append(
                choice(
                    "unit",
                    "Measurement unit",
                    "",
                    ("V", "dB", "dBm"),
                    hint="The HP supports one math operation at a time; dB/dBm replace Null.",
                )
            )
            if values.get("unit") == "dB":
                result.append(
                    number("db_reference", "dB reference [dBm]", "CALC:DB:REF", -200, 200)
                )
            if values.get("unit") == "dBm":
                choices = tuple(
                    str(i)
                    for i in (
                        50,
                        75,
                        93,
                        110,
                        124,
                        125,
                        135,
                        150,
                        250,
                        300,
                        500,
                        600,
                        800,
                        900,
                        1000,
                        1200,
                        8000,
                    )
                )
                result.append(
                    choice(
                        "dbm_impedance",
                        "dBm impedance [Ω]",
                        "CALC:DBM:REF",
                        choices,
                        decoder=numeric,
                    )
                )
        result.extend(
            binding
            for binding in self.common_bindings()
            if binding.spec.key != "autozero" or code in (1, 3, 5)
        )
        return result

    def extra_state(self, query, code):
        state = boolean(query("CALC:STAT?"))
        function = canonical(query("CALC:FUNC?"))
        return {
            "autozero": boolean(query("SENS:ZERO:AUTO?"))
            if code in (1, 3, 5)
            else "ON (fixed)"
            if code == 6
            else "Not applicable",
            "relative": "ON" if state == "ON" and function == "NULL" else "OFF",
            "unit": {"DB": "dB", "DBM": "dBm"}.get(function, "V")
            if state == "ON" and code in (1, 2)
            else "V",
            "_math": function if state == "ON" else "OFF",
        }

    def finish_state(self, values, code):
        if code in (1, 3, 5, 6):
            nplc = float(values["nplc"])
            values["digits"] = "4½" if nplc < 0.1 else "5½" if nplc < 5 else "6½"
        elif code in (7, 8):
            aperture = float(values["_precision"])
            values["digits"] = "4½" if aperture < 0.05 else "5½" if aperture < 0.5 else "6½"
        elif code not in (10, 11):
            selected_range = dict(self.ranges(code)).get(values["range"], values["range"])
            ratio = float(values["_precision"]) / float(selected_range)
            values["digits"] = "4½" if ratio >= 5e-5 else "5½" if ratio >= 5e-6 else "6½"
        else:
            values["digits"] = "4½"

    def read_inputs(self, query):
        value = canonical(query("ROUT:TERM?"))
        return {"FRON": "Front", "FRONT": "Front", "REAR": "Rear"}.get(value, value)

    def reference_limit(self, code):
        return {1: 1200, 2: 900, 3: 3.6, 4: 3.6, 5: 120e6, 6: 120e6, 7: 360000, 8: 4}[code]

    def unit(self, code, values):
        return values.get("unit", "V") if code in (1, 2) else super().unit(code, values)

    def apply(self, key, value, status, write, query):
        code = status.function_code
        available = {
            binding.spec.key: binding for binding in self.bindings(code, dict(status.values))
        }
        if key not in available:
            raise ValueError(f"{key} is not available for this function on {self.name}.")
        if key == "digits":
            if not status.digits_editable:
                raise ValueError("Turn Autorange OFF before changing digits on the HP 34401A.")
            available[key].spec.validate(value)
            if code in (7, 8):
                write(
                    f"SENS:{status.function.scpi}:APER "
                    + {"4½": "0.01", "5½": "0.1", "6½": "1"}[value]
                )
            else:
                current_range = float(query(self.range_path(code) + "?"))
                resolution = current_range * {"4½": 1e-4, "5½": 1e-5, "6½": 1e-6}[value]
                write(f"SENS:{status.function.scpi}:RES {resolution:.12g}")
            return
        if key == "unit":
            available[key].spec.validate(value)
            if value == "V":
                write("CALC:STAT OFF")
            else:
                write("CALC:FUNC " + {"dB": "DB", "dBm": "DBM"}[value])
                write("CALC:STAT ON")
            return
        if key == "relative":
            available[key].spec.validate(value)
            if value == "ON":
                write("CALC:FUNC NULL")
                write("CALC:STAT ON")
            elif status.values.get("relative") == "ON":
                write("CALC:STAT OFF")
            return
        if key in ("reference", "reference_acquire"):
            if key == "reference":
                offset = available[key].spec.validate(value)
            else:
                write("CALC:STAT OFF")
                write("TRIG:SOUR IMM")
                write("SAMP:COUN 1")
                try:
                    offset = numeric(query("READ?"))
                finally:
                    write(
                        "TRIG:SOUR "
                        + {"Internal": "IMM", "External": "EXT", "Bus": "BUS"}[
                            status.values["trigger"]
                        ]
                    )
                    write("SAMP:COUN " + status.values["samples"])
            write("CALC:FUNC NULL")
            write("CALC:STAT ON")
            write("CALC:NULL:OFFS " + offset)
            return
        super().apply(key, value, status, write, query)


class DriverFactory:
    @staticmethod
    def detect(identification: str) -> ScpiModelDriver:
        fields = [field.strip().upper() for field in identification.split(",")]
        if len(fields) < 2:
            raise ValueError(f"Invalid *IDN? response: {identification!r}.")
        manufacturer, model = fields[:2]
        if model == "34401A" and any(
            name in manufacturer for name in ("HEWLETT", "HP", "AGILENT", "KEYSIGHT")
        ):
            return HP34401ADriver()
        if "KEITHLEY" in manufacturer and model in ("2000", "MODEL 2000", "MODEL2000"):
            return Keithley2000Driver()
        raise ValueError(
            f"Unsupported instrument: {identification}. Expected Keithley 2000 or HP/Agilent/Keysight 34401A."
        )
