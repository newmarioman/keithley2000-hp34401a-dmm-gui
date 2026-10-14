# -*- coding: utf-8 -*-

"""
Build only the settings supported by the detected model and active function.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

import tkinter as tk
from idlelib.tooltip import Hovertip
from tkinter import ttk

# Local Packages
from .config import FUNCTIONS
from .widgets import ResponsiveButtonBar


class InstrumentSettingsPanel(ttk.Frame):
    def __init__(self, parent, actions, body_font):
        super().__init__(parent, padding=6)
        self.columnconfigure(0, weight=1)
        self._actions, self._font = actions, body_font
        self._syncing, self._enabled, self._status = False, False, None
        self._signature, self._submitted = None, {}
        self.measurement_info = ttk.LabelFrame(self, text="Measurement status", padding=5)
        self.measurement_info.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.measurement_info.columnconfigure(0, weight=1)
        self.status_labels = {}
        for row, name in enumerate(
            ("Inputs", "Line", "Autozero", "Autorange", "Trigger", "Resolution", "Processing")
        ):
            label = ttk.Label(self.measurement_info, text=f"{name}: —", wraplength=240)
            label.grid(row=row, column=0, sticky="ew", pady=1)
            self.status_labels[name] = label
        self.functions = ResponsiveButtonBar(self)
        self.functions.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.function_buttons = {}
        for spec in FUNCTIONS:
            button = tk.Button(
                self.functions,
                text=spec.label,
                font=body_font,
                relief="groove",
                background="#f0f0f0",
                activebackground="#d9f8df",
                command=lambda code=int(spec.code): actions["function"](code),
            )
            self.function_buttons[int(spec.code)] = button
            self.functions.add(button)
        self.fields = ttk.LabelFrame(self, text="Function settings", padding=4)
        self.fields.grid(row=2, column=0, sticky="ew")
        self.fields.columnconfigure(1, weight=1)
        self.variables, self.widgets, self.labels = {}, {}, {}
        bar = ResponsiveButtonBar(self)
        bar.grid(row=3, column=0, sticky="ew", pady=6)
        self.status_button = ttk.Button(bar, text="Read status", command=actions["status"])
        self.reset_button = ttk.Button(bar, text="Reset", command=actions["reset"])
        self.clear_errors_button = ttk.Button(
            bar, text="Clear errors", command=actions["clear_errors"]
        )
        self.diagnostics_button = ttk.Button(
            bar, text="Read diagnostics", command=actions["diagnostics"]
        )
        for widget in (
            self.status_button,
            self.reset_button,
            self.clear_errors_button,
            self.diagnostics_button,
        ):
            bar.add(widget)
        diagnostics = ttk.LabelFrame(self, text="SCPI diagnostics", padding=5)
        diagnostics.grid(row=4, column=0, sticky="ew", pady=4)
        diagnostics.columnconfigure(0, weight=1)
        self.diagnostic_labels = {}
        for row, name in enumerate(
            ("SRQ Mask", "SRQ events", "Status byte", "SCPI version", "Error queue")
        ):
            label = ttk.Label(diagnostics, text=f"{name}: —", wraplength=240)
            label.grid(row=row, column=0, sticky="ew", pady=1)
            self.diagnostic_labels[name] = label
        self.bind("<Configure>", self._resize)

    def _build(self, specs):
        for child in self.fields.winfo_children():
            child.destroy()
        self.variables, self.widgets, self.labels = {}, {}, {}
        for row, spec in enumerate(item for item in specs if not item.key.startswith("_")):
            label = ttk.Label(self.fields, text=spec.label, wraplength=125)
            label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            self.labels[spec.key] = label
            variable = tk.StringVar(self)
            self.variables[spec.key] = variable
            if spec.action:
                widget = ttk.Button(
                    self.fields,
                    text=spec.choices[0],
                    command=lambda key=spec.key: self._changed(key, "Acquire"),
                )
            elif spec.choices:
                widget = ttk.Combobox(
                    self.fields,
                    textvariable=variable,
                    values=spec.choices,
                    width=1,
                    font=self._font,
                )
                widget.bind("<<ComboboxSelected>>", lambda event, key=spec.key: self._changed(key))
            else:
                widget = ttk.Entry(self.fields, textvariable=variable, width=1, font=self._font)
            if spec.editable or not spec.choices:
                widget.bind("<Return>", lambda event, key=spec.key: self._changed(key))
                widget.bind("<FocusOut>", lambda event, key=spec.key: self._changed(key))
            widget.grid(row=row, column=1, sticky="ew", pady=3)
            self.widgets[spec.key] = widget
            if spec.hint:
                Hovertip(widget, spec.hint)

    def _changed(self, key, action=None):
        if self._syncing or not self._enabled or self._status is None:
            return
        if key == "digits" and not self._status.digits_editable:
            return
        value = action or self.variables[key].get().strip()
        if not action and value == self._submitted.get(key):
            return
        self._submitted[key] = value
        self._actions["option"](key, value)

    def apply_status(self, status):
        self._syncing = True
        try:
            self._status = status
            if status:
                signature = (status.model, status.function_code, status.specs)
                if signature != self._signature:
                    self._build(status.specs)
                    self._signature = signature
                self._submitted = dict(status.values)
                for key, variable in self.variables.items():
                    variable.set(status.values.get(key, ""))
                values = {
                    "Inputs": status.inputs,
                    "Line": status.line_frequency,
                    "Autozero": status.values.get("autozero", "—"),
                    "Autorange": status.values.get("autorange", "Fixed / manual"),
                    "Trigger": status.values.get("trigger", "—"),
                    "Resolution": status.digits,
                    "Processing": ("Math " + status.values.get("_math", "OFF"))
                    if "_math" in status.values
                    else "Relative "
                    + status.values.get("relative", "OFF")
                    + "; filter "
                    + status.values.get("filter", "N/A"),
                }
                for name, label in self.status_labels.items():
                    label.configure(text=f"{name}: {values[name]}")
            else:
                for label, name in ((label, name) for name, label in self.status_labels.items()):
                    label.configure(text=f"{name}: —")
            for code, button in self.function_buttons.items():
                button.configure(
                    background="#90ee90" if status and code == status.function_code else "#f0f0f0"
                )
        finally:
            self._syncing = False
        self.set_state(self._enabled)

    def apply_diagnostics(self, diagnostics):
        values = (
            {
                "SRQ Mask": str(diagnostics.srq_mask),
                "SRQ events": ", ".join(diagnostics.srq_events) or "None",
                "Status byte": str(diagnostics.status_byte),
                "SCPI version": diagnostics.scpi_version,
                "Error queue": diagnostics.error,
            }
            if diagnostics
            else {}
        )
        for name, label in self.diagnostic_labels.items():
            label.configure(text=f"{name}: {values.get(name, '—')}")

    def set_state(self, enabled):
        self._enabled = enabled
        for code, button in self.function_buttons.items():
            button.configure(
                state="normal"
                if enabled and self._status and code in self._status.supported_functions
                else "disabled"
            )
        specs = {spec.key: spec for spec in self._status.specs} if self._status else {}
        for key, widget in self.widgets.items():
            spec = specs.get(key)
            state = (
                "disabled"
                if not enabled
                or spec is None
                or key == "digits"
                and not self._status.digits_editable
                else "readonly"
                if spec.choices and not spec.editable and not spec.action
                else "normal"
            )
            widget.configure(state=state)
        for widget in (
            self.status_button,
            self.reset_button,
            self.clear_errors_button,
            self.diagnostics_button,
        ):
            widget.configure(state="normal" if enabled else "disabled")

    def _resize(self, event):
        for label in (*self.status_labels.values(), *self.diagnostic_labels.values()):
            label.configure(wraplength=max(100, event.width - 30))
        for label in self.labels.values():
            label.configure(wraplength=max(60, min(145, round(event.width * 0.4))))
