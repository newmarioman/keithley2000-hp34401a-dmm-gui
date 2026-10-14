# -*- coding: utf-8 -*-

"""
Connection form with automatic COM discovery and separate interface profiles.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

import tkinter as tk
from tkinter import ttk

# Local Packages
from .connection_profiles import PROFILE_BY_MODEL, PROFILES
from .transport import ConnectionOptions
from .widgets import ResponsiveButtonBar


class ConnectionPanel(ttk.Frame):
    def __init__(self, parent, actions, body_font) -> None:
        super().__init__(parent, padding=6)
        self.columnconfigure(0, weight=1)
        self._font = body_font
        self._connected = self._busy = False
        self.fields = {}
        profile = PROFILES[0]
        self.model = self._combo(self, 0, "Model", tuple(PROFILE_BY_MODEL), profile.model)
        self.model.bind("<<ComboboxSelected>>", self._model_changed)
        self.interface = self._combo(self, 1, "Interface", ("GPIB (AR488)", "RS232"), "GPIB (AR488)")
        self.interface.bind("<<ComboboxSelected>>", self._interface_changed)
        ttk.Label(self, text="COM port").grid(row=2, column=0, sticky="w", pady=(4, 1))
        self.port = ttk.Combobox(self, values=(), width=1, state="readonly", font=body_font)
        self.port.grid(row=3, column=0, sticky="ew")
        self.port_description = ttk.Label(self, text="Scanning serial ports…", wraplength=200)
        self.port_description.grid(row=4, column=0, sticky="ew", pady=3)
        self.serial_frame = ttk.LabelFrame(self, text="RS232 parameters", padding=4)
        self.serial_frame.grid(row=5, column=0, sticky="ew", pady=4)
        self.serial_frame.columnconfigure(0, weight=1)
        for row, parameter in enumerate(profile.parameters):
            self.fields[parameter.key] = self._combo(
                self.serial_frame, row, parameter.label, parameter.choices, parameter.default
            )
        self.fields["parity"].bind("<<ComboboxSelected>>", self._parity_changed)
        self.gpib_frame = ttk.LabelFrame(self, text="AR488 controller", padding=4)
        self.gpib_frame.columnconfigure(0, weight=1)
        self.address = self._combo(
            self.gpib_frame,
            0,
            "GPIB address",
            tuple(str(i) for i in range(1, 31)),
            str(profile.gpib_address),
        )
        self.profile_hint = ttk.Label(self, text="", wraplength=200)
        self.profile_hint.grid(row=6, column=0, sticky="ew", pady=4)
        card = ttk.Frame(self)
        card.grid(row=7, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text="Timeout [s]").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.timeout_var = tk.StringVar(self, "1")
        self.timeout = ttk.Entry(card, textvariable=self.timeout_var, width=1, font=body_font)
        self.timeout.grid(row=0, column=1, sticky="ew")
        bar = ResponsiveButtonBar(self)
        bar.grid(row=8, column=0, sticky="ew", pady=6)
        self.connect_button = ttk.Button(bar, text="Connect", command=actions["connect"])
        self.disconnect_button = ttk.Button(bar, text="Disconnect", command=actions["disconnect"])
        self.refresh_button = ttk.Button(
            bar, text="Refresh ports", command=actions["refresh_ports"]
        )
        for button in (self.connect_button, self.disconnect_button, self.refresh_button):
            bar.add(button)
        self.controller = ttk.Label(self, text="Controller: —", wraplength=200)
        self.instrument = ttk.Label(self, text="Instrument: —", wraplength=200)
        for row, widget in enumerate((self.controller, self.instrument), 9):
            widget.grid(row=row, column=0, sticky="ew", pady=4)
        self.bind("<Configure>", self._resize)
        self._interface_changed()

    def _combo(self, parent, row, label, choices, default):
        card = ttk.Frame(parent)
        card.grid(row=row, column=0, sticky="ew", pady=2)
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text=label).grid(row=0, column=0, sticky="w", padx=(0, 8))
        combo = ttk.Combobox(card, values=choices, width=1, state="readonly", font=self._font)
        combo.set(default)
        combo.grid(row=0, column=1, sticky="ew")
        return combo

    def select_model(self, model):
        """Apply a profile before connecting, without changing the selected COM port."""
        if self._connected or self._busy:
            return
        profile = PROFILE_BY_MODEL[model]
        self.model.set(profile.model)
        for parameter in profile.parameters:
            combo = self.fields[parameter.key]
            combo.configure(values=parameter.choices)
            combo.set(parameter.default)
        self.address.set(str(profile.gpib_address))

    def _model_changed(self, event=None):
        self.select_model(self.model.get())

    def _parity_changed(self, event=None):
        if self._connected or self._busy:
            return
        bits = PROFILE_BY_MODEL[self.model.get()].data_bits_for(self.fields["parity"].get())
        self.fields["bytesize"].configure(values=(bits,))
        self.fields["bytesize"].set(bits)

    def _interface_changed(self, event=None):
        gpib = self.interface.get() == "GPIB (AR488)"
        self.serial_frame.grid_remove() if gpib else self.serial_frame.grid()
        self.gpib_frame.grid(
            row=5, column=0, sticky="ew", pady=4
        ) if gpib else self.gpib_frame.grid_remove()
        self.profile_hint.configure(
            text="Use the COM port of the AR488 adapter. RS232 framing options do not apply to GPIB."
            if gpib
            else "Match these parameters to the DMM front-panel RS232 configuration. Responses accept CR, LF, CR+LF and LF+CR."
        )

    def options(self) -> ConnectionOptions:
        return ConnectionOptions(
            self.port.get(),
            self.interface.get(),
            int(self.address.get()),
            int(self.fields["baudrate"].get()),
            {"None": "N", "Even": "E", "Odd": "O", "Mark": "M", "Space": "S"}[
                self.fields["parity"].get()
            ],
            float(self.fields["stopbits"].get()),
            int(self.fields["bytesize"].get()),
            self.fields["xonxoff"].get() == "ON",
            self.fields["rtscts"].get() == "ON",
            self.fields["dsrdtr"].get() == "ON",
            {"CR": "\r", "LF": "\n", "CR+LF": "\r\n", "LF+CR": "\n\r"}[
                self.fields["termination"].get()
            ],
            float(self.timeout_var.get()),
        )

    def _resize(self, event):
        for label in (self.port_description, self.controller, self.instrument, self.profile_hint):
            label.configure(wraplength=max(80, event.width - 16))

    def set_state(self, connected, busy, running):
        self._connected, self._busy = connected, busy
        state = "disabled" if connected or busy else "readonly"
        for combo in (self.model, self.interface, self.port, self.address, *self.fields.values()):
            combo.configure(state=state)
        self.timeout.configure(state="disabled" if connected or busy else "normal")
        self.connect_button.configure(
            state="normal" if not connected and not busy and self.port.get() else "disabled"
        )
        self.disconnect_button.configure(
            state="normal" if connected and not busy and not running else "disabled"
        )

    def show_status(self, status, controller="", instrument_name=""):
        self.controller.configure(text=f"Controller: {controller or '—'}")
        self.instrument.configure(
            text=f"Instrument: {instrument_name or '—'}" if status else "Instrument: —"
        )
