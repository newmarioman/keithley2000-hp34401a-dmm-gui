"""Independent serial/GPIB fixtures with explicit SCPI command contracts."""

import math


class FixtureDmm:
    def __init__(self, model):
        self.model = model
        self.commands = []
        self.reset()

    def reset(self):
        self.function = "VOLT:DC"
        self.count = 0
        self.state = {
            "DISP": "1",
            "DISP:ENAB": "1",
            "SENS:ZERO:AUTO": "1",
            "SYST:AZER:STAT": "1",
            "TRIG:SOUR": "IMM",
            "TRIG:DEL": "0",
            "TRIG:DEL:AUTO": "1",
            "SAMP:COUN": "1",
            "TRIG:COUN": "1",
            "INIT:CONT": "0",
            "CALC:STAT": "0",
            "CALC:FUNC": "NULL",
            "CALC:NULL:OFFS": "0",
            "CALC:DB:REF": "0",
            "CALC:DBM:REF": "600",
            "INP:IMP:AUTO": "0",
            "SENS:DET:BAND": "20",
        }
        ranges = {
            "VOLT:DC": "10",
            "VOLT:AC": "750",
            "CURR:DC": "0.01",
            "CURR:AC": "3",
            "RES": "1000",
            "FRES": "1000",
        }
        for function, nominal in ranges.items():
            base = "SENS:" + function
            self.state.update({base + ":RANG": nominal, base + ":RANG:AUTO": "1"})
            if self.model == "hp34401a":
                self.state[base + ":RES"] = str(float(nominal) * 1e-6)
                if function not in ("VOLT:AC", "CURR:AC"):
                    self.state[base + ":NPLC"] = "10"
            elif self.model == "keithley2000":
                self.state.update(
                    {
                        base + ":NPLC": "1",
                        base + ":DIG": "7",
                        base + ":REF": "0",
                        base + ":REF:STAT": "0",
                        base + ":AVER:STAT": "0",
                        base + ":AVER:TCON": "MOV",
                        base + ":AVER:COUN": "10",
                    }
                )
                if function in ("VOLT:AC", "CURR:AC"):
                    self.state[base + ":DET:BAND"] = "30"
        for function in ("FREQ", "PER"):
            base = "SENS:" + function
            if self.model == "hp34401a":
                self.state.update(
                    {
                        base + ":VOLT:RANG": "10",
                        base + ":VOLT:RANG:AUTO": "1",
                        base + ":APER": "0.1",
                    }
                )
            elif self.model == "keithley2000":
                self.state.update(
                    {
                        base + ":THR:VOLT:RANG": "10",
                        base + ":DIG": "7",
                        base + ":REF": "0",
                        base + ":REF:STAT": "0",
                    }
                )
        if self.model == "keithley2000":
            self.state.update(
                {
                    "SENS:TEMP:DIG": "6",
                    "SENS:TEMP:NPLC": "1",
                    "SENS:TEMP:REF": "0",
                    "SENS:TEMP:REF:STAT": "0",
                    "SENS:TEMP:AVER:STAT": "0",
                    "SENS:TEMP:AVER:TCON": "REP",
                    "SENS:TEMP:AVER:COUN": "10",
                    "UNIT:TEMP": "C",
                    "SENS:TEMP:TC:TYPE": "J",
                    "SENS:TEMP:TC:RJUN:RSEL": "SIM",
                    "SENS:TEMP:TC:RJUN:SIM": "23",
                    "SENS:TEMP:TC:RJUN:REAL:TCOEF": "0.0002",
                    "SENS:TEMP:TC:RJUN:REAL:OFFS": "0.05463",
                    "SENS:DIOD:CURR:RANG": "0.001",
                    "SENS:CONT:THR": "10",
                    "SENS:HOLD:STAT": "0",
                    "SENS:HOLD:WIND": "1",
                    "SENS:HOLD:COUN": "5",
                }
            )
            for function in ("VOLT:DC", "VOLT:AC"):
                self.state.update(
                    {
                        "UNIT:" + function: "V",
                        "UNIT:" + function + ":DB:REF": "1",
                        "UNIT:" + function + ":DBM:IMP": "75",
                    }
                )
        # Remove commands which exist only on the other instrument.
        if self.model == "hp34401a":
            for path in ("DISP:ENAB", "SYST:AZER:STAT", "INIT:CONT"):
                self.state.pop(path)
        elif self.model == "keithley2000":
            for path in (
                "DISP",
                "SENS:ZERO:AUTO",
                "SENS:DET:BAND",
                "INP:IMP:AUTO",
                "CALC:NULL:OFFS",
                "CALC:DB:REF",
                "CALC:DBM:REF",
            ):
                self.state.pop(path)

    def execute(self, command):
        if ";" in command:
            replies = [reply for unit in command.split(";")
                       if (reply := self.execute(unit)) is not None]
            return ";".join(replies) if replies else None
        self.commands.append(command)
        if command == "*IDN?":
            return (
                "HEWLETT-PACKARD,34401A,0,11-5-2"
                if self.model == "hp34401a"
                else "KEITHLEY INSTRUMENTS INC.,MODEL 2000,123456,A20"
            )
        if command == ("FUNC?" if self.model == "hp34401a" else "SENS:FUNC?"):
            return '"' + self.function + '"'
        if command == "ROUT:TERM?" and self.model == "hp34401a":
            return "FRON"
        if command == "SYST:FRSW?" and self.model == "keithley2000":
            return "1"
        if command == "SYST:LFR?" and self.model == "keithley2000":
            return "50"
        if command in ("*SRE?", "*STB?"):
            return "0"
        if command == "SYST:VERS?":
            return "1991.0"
        if command == "SYST:ERR?":
            return '0,"No error"'
        if command in ("*RST", "SYST:PRES"):
            assert command == ("*RST" if self.model == "hp34401a" else "SYST:PRES")
            self.reset()
            return
        if command in (
            "SYST:REM",
            "SYST:LOC",
            "*CLS",
            "SYST:CLE",
            "FORM:ELEM READ",
            "FORM:DATA ASC",
            "INIT",
            "*TRG",
        ):
            if command.startswith("FORM:"):
                assert self.model == "keithley2000"
            return
        if command in ("READ?", "FETC?"):
            self.count += 1
            return ",".join(
                f"{(1 + math.sin(self.count + n / 10) / 10):+.9E}"
                for n in range(int(self.state["SAMP:COUN"]))
            )
        if command.startswith("CONF:"):
            self.function = command[5:]
            self.state["CALC:STAT"] = "0"
            return
        if command.endswith(":REF:ACQ") and self.model == "keithley2000":
            self.state[command[:-4]] = "0.1234567"
            return
        if command.endswith("?"):
            path = command[:-1]
            assert path in self.state, f"Undocumented {self.model} query: {command}"
            return self.state[path]
        path, separator, value = command.partition(" ")
        assert separator and path in self.state, f"Undocumented {self.model} command: {command}"
        self.state[path] = value
        if path + ":AUTO" in self.state and path.endswith(":RANG"):
            self.state[path + ":AUTO"] = "0"
        if self.model == "hp34401a" and path.endswith(":RES"):
            base = path[:-4]
            if base + ":NPLC" in self.state:
                ratio = float(value) / float(self.state[base + ":RANG"])
                self.state[base + ":NPLC"] = (
                    "0.02" if ratio >= 5e-5 else "0.2" if ratio >= 5e-6 else "10"
                )


class FixtureSerial:
    def __init__(self, device, ending=b"\r\n", fragment=4096):
        self.device, self.ending, self.fragment = device, ending, fragment
        self.options, self.packets, self.input = {}, [], bytearray()
        self.closed = False
        self.timeout = None

    def open(self, **options):
        self.options = options
        self.timeout = options.get("timeout")
        self.closed = False
        return self

    @property
    def in_waiting(self):
        return len(self.input)

    def reset_input_buffer(self):
        self.input.clear()

    def write(self, payload):
        self.packets.append(payload)
        command = payload.decode("ascii").strip("\r\n")
        result = self.device.execute(command)
        if result is not None:
            self.input.extend(result.encode("ascii") + self.ending)
        return len(payload)

    def read(self, size):
        result = bytes(self.input[: min(size, self.fragment)])
        del self.input[: len(result)]
        return result

    def close(self):
        self.closed = True


class FixtureController(FixtureSerial):
    """Run the real PyAR488 package against a byte-level serial adapter."""

    def __init__(self, device, eot=0):
        super().__init__(device)
        self.config = {
            "++addr": 1,
            "++mode": 1,
            "++eoi": 1,
            "++eos": 0,
            "++eot_enable": 1,
            "++eot_char": eot,
            "++eor": 0,
            "++idn": 0,
            "++auto": 0,
            "++read_tmo_ms": 1000,
        }
        self.pending, self.pending_query, self.identified = None, None, False

    def emit_reply(self, command):
        assert self.pending is not None
        eot = bytes([self.config["++eot_char"]]) if self.config["++eot_enable"] else b""
        self.input.extend(self.pending.encode("ascii") + b"\r\n" + eot)
        self.pending, self.pending_query = None, None

    def write(self, payload):
        self.packets.append(payload)
        command = payload.decode("ascii").strip("\r\n")
        if command in ("++ver", "++ver real"):
            self.identified = True
            self.input.extend(b"AR488 GPIB controller, ver. 0.51.29\r\n")
        elif command == "++read eoi":
            self.emit_reply(self.pending_query)
        elif command in ("++clr", "++loc", "++!", "++unt", "++unl"):
            if command == "++!":
                self.pending = None
                self.pending_query = None
        elif command.startswith("++"):
            path, _, value = command.partition(" ")
            if value:
                if path == "++mode":
                    assert self.identified, "Controller mode must not be changed before ++ver."
                self.config[path] = int(value)
            else:
                assert path in self.config, command
                self.input.extend(str(self.config[path]).encode("ascii") + b"\r\n")
        else:
            result = self.device.execute(command)
            if result is not None:
                assert self.pending is None, "A query response was overwritten."
                self.pending = result
                self.pending_query = command
                if self.config["++auto"] == 2 and command.endswith("?"):
                    self.emit_reply(command)
        return len(payload)

    def readline(self):
        end = self.input.find(b"\n")
        return self.read(end + 1 if end >= 0 else len(self.input))
