# -*- coding: utf-8 -*-

"""
Software SCPI instrument for inspecting both models without hardware.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

import math
import time

# Local Packages
from .drivers import HP34401ADriver, Keithley2000Driver, function_code
from .transport import InstrumentTransport


class SimulatedTransport(InstrumentTransport):
    is_gpib = False
    eot_byte = b""

    def __init__(self, model="keithley2000", sample_delay=0.015) -> None:
        self.driver = HP34401ADriver() if model == "hp34401a" else Keithley2000Driver()
        self.model, self.sample_delay = model, sample_delay
        self._open = False
        self._buffer = bytearray()
        self.history: list[str] = []
        self._reset()

    def _reset(self):
        self.code, self.index = 1, 0
        self.state = {
            "CALC:STAT": "0",
            "CALC:FUNC": "NULL",
            "CALC:NULL:OFFS": "0",
            "CALC:DB:REF": "0",
            "CALC:DBM:REF": "600",
            "TRIG:SOUR": "IMM",
            "TRIG:DEL": "0",
            "TRIG:DEL:AUTO": "1",
            "SAMP:COUN": "1",
            "SENS:ZERO:AUTO": "1",
            "SYST:AZER:STAT": "1",
            "DISP": "1",
            "DISP:ENAB": "1",
            "SENS:DET:BAND": "20",
            "INP:IMP:AUTO": "0",
            "UNIT:TEMP": "C",
        }
        for code in self.driver.supported_functions:
            for binding in self.driver.bindings(code, {}):
                path, spec = binding.path, binding.spec
                if not path or spec.action or path in self.state:
                    continue
                if path.endswith(":DIG"):
                    value = "6" if code in (2, 4, 9) else "7"
                elif path.endswith(":NPLC"):
                    value = "10" if self.model == "hp34401a" else "1"
                elif path.endswith(":RES"):
                    value = (
                        "0.00001" if code in (1, 2) else "0.000001" if code in (3, 4) else "0.0001"
                    )
                elif path.endswith(":APER"):
                    value = "0.1"
                elif path.endswith(":RANG:AUTO"):
                    value = "1"
                elif path.endswith(":RANG"):
                    ranges = self.driver.ranges(code) if code not in (10, 11) else ()
                    value = ranges[min(2, len(ranges) - 1)][1] if ranges else "0.001"
                elif path.endswith(":STAT"):
                    value = "0"
                elif path.endswith(":SIM"):
                    value = "23"
                elif path.endswith(":WIND"):
                    value = "1"
                elif path.endswith(":COUN"):
                    value = "5" if ":HOLD:" in path else "10"
                elif path.endswith(":DET:BAND"):
                    value = "30"
                elif path.endswith(":THR"):
                    value = "10"
                elif spec.choices:
                    value = dict(binding.arguments).get(spec.choices[0], spec.choices[0])
                else:
                    value = "0"
                self.state[path] = value
        # Branch-specific controls are queried after selecting their parent setting.
        for function in ("VOLT:DC", "VOLT:AC"):
            self.state.update(
                {
                    f"UNIT:{function}": "V",
                    f"UNIT:{function}:DB:REF": "1",
                    f"UNIT:{function}:DBM:IMP": "75",
                }
            )
        self.state.update(
            {"SENS:TEMP:TC:RJUN:REAL:TCOEF": "0.0002", "SENS:TEMP:TC:RJUN:REAL:OFFS": "0.05463"}
        )

    def open(self, port: str, address: int = 0) -> None:
        self._open = True
        self._buffer.clear()
        self._reset()

    @property
    def is_open(self):
        return self._open

    def write(self, command: str) -> None:
        if not self._open:
            raise RuntimeError("The simulated port is closed.")
        self.history.append(command)
        command = command.upper().strip().lstrip(":")
        if command == "*IDN?":
            reply = (
                "HEWLETT-PACKARD,34401A,0,11-5-2"
                if self.model == "hp34401a"
                else "KEITHLEY INSTRUMENTS INC.,MODEL 2000,1234567,A20"
            )
        elif command in ("FUNC?", "SENS:FUNC?"):
            from .config import FUNCTION_BY_CODE

            reply = '"' + FUNCTION_BY_CODE[self.code].scpi + '"'
        elif command in ("READ?", "FETC?"):
            time.sleep(self.sample_delay)
            count = int(self.state["SAMP:COUN"])
            values = []
            for _ in range(count):
                self.index += 1
                value = 1 + math.sin(self.index * 0.15) * 0.15
                if self.state["CALC:STAT"] in ("1", "ON") and self.state["CALC:FUNC"] == "NULL":
                    value -= float(self.state["CALC:NULL:OFFS"])
                values.append(f"{value:+.9E}")
            reply = ",".join(values)
        elif command == "SYST:FRSW?":
            reply = "1"
        elif command == "ROUT:TERM?":
            reply = "FRON"
        elif command == "SYST:LFR?":
            reply = "50"
        elif command == "SYST:VERS?":
            reply = "1991.0"
        elif command in ("*SRE?", "*STB?"):
            reply = "0"
        elif command == "SYST:ERR?":
            reply = '0,"No error"'
        elif command.endswith("?"):
            path = command[:-1]
            if path not in self.state:
                raise ValueError(f"Unsupported simulated query: {command}")
            reply = self.state[path]
        elif command in ("*RST", "SYST:PRES"):
            self._reset()
            return
        elif command.startswith("CONF:"):
            self.code = function_code(command[5:])
            self.state["CALC:STAT"] = "0"
            self.state["TRIG:SOUR"] = "IMM"
            self.state["SAMP:COUN"] = "1"
            return
        elif command in (
            "*CLS",
            "SYST:CLE",
            "SYST:REM",
            "SYST:LOC",
            "INIT",
            "*TRG",
            "FORM:ELEM READ",
            "FORM:DATA ASC",
            "INIT:CONT OFF",
            "TRIG:COUN 1",
        ):
            return
        elif command.endswith(":REF:ACQ"):
            self.state[command[:-4]] = "1"
            return
        else:
            path, separator, value = command.partition(" ")
            if not separator:
                raise ValueError(f"Unsupported simulated command: {command}")
            self.state[path] = value
            if path.endswith(":RANG") and path + ":AUTO" in self.state:
                self.state[path + ":AUTO"] = "0"
            if self.model == "hp34401a" and path.endswith(":NPLC"):
                base = path[:-5]
                ratios = {0.02: 1e-4, 0.2: 1e-5, 1: 3e-6, 10: 1e-6, 100: 3e-7}
                self.state[base + ":RES"] = str(
                    float(self.state[base + ":RANG"]) * ratios[float(value)]
                )
            if self.model == "hp34401a" and path.endswith(":RES"):
                base = path[:-4]
                if base + ":NPLC" in self.state:
                    ratio = float(value) / float(self.state[base + ":RANG"])
                    self.state[base + ":NPLC"] = (
                        "0.02" if ratio >= 5e-5 else "0.2" if ratio >= 5e-6 else "10"
                    )
            return
        self._buffer.extend((reply + "\r\n").encode("ascii"))

    def read(self, size: int = 1) -> bytes:
        result = bytes(self._buffer[:size])
        del self._buffer[: len(result)]
        return result

    def read_available(self, size=1):
        return self.read(size)

    def close(self) -> None:
        self._open = False
        self._buffer.clear()
