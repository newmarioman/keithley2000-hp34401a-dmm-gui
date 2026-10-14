# -*- coding: utf-8 -*-

"""
Responsive UI Panels for Instrument Connections and Operational Settings.

Part of the Keithley2000 / HP34401A DMM GUI project.
Distributed under the terms of the MIT license.

Author: Lorenzo Mariani <newmarioman@hotmail.it>
"""

import tkinter as tk
from collections.abc import Callable
from idlelib.tooltip import Hovertip
from tkinter import ttk

# Local Packages
from .config import ACQUISITION_INTERVALS, AppConfig
from .connection_ui import ConnectionPanel
from .layout import LayoutPolicy
from .plot import LiveTracePlot, NumericDisplay
from .settings_ui import InstrumentSettingsPanel
from .widgets import CommandRow, ResponsiveButtonBar, ScrollableFrame, TextContextMenu


class ConsolePanel(ttk.Frame):
    def __init__(self, parent, mono_font, actions, body_font) -> None:
        super().__init__(parent, padding=4)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.text = tk.Text(
            self,
            height=1,
            width=1,
            state="disabled",
            background="black",
            foreground="white",
            font=mono_font,
            wrap="word",
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, command=self.text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)
        ttk.Button(self, text="Clear console", command=self.clear).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=4
        )
        custom = CommandRow(self, "Custom command", (("Send", actions["custom"]),))
        custom.grid(row=3, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
        self.command, self.custom_button = custom.entry, custom.buttons[0]
        self.command.configure(font=body_font)
        self.command.bind("<Return>", lambda event: actions["custom"]())
        self.query_var = tk.BooleanVar(self, False)
        self.query_check = ttk.Checkbutton(self, text="Read response", variable=self.query_var)
        self.query_check.grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=2)

    def set_state(self, connected: bool, busy: bool, running: bool) -> None:
        state = "normal" if connected and not busy and not running else "disabled"
        for widget in (self.command, self.custom_button, self.query_check):
            widget.configure(state=state)

    def append(self, direction: str, text: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", f"{direction}>> {text.rstrip()}\n")
        lines = int(self.text.index("end-1c").split(".")[0])
        if lines > 2000:
            self.text.delete("1.0", f"{lines - 2000}.0")
        self.text.configure(state="disabled")
        self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class AcquisitionPanel(ttk.Frame):
    def __init__(
        self, parent, actions: dict[str, Callable], body_font, options_parent, display_font_path
    ) -> None:
        super().__init__(parent, padding=4)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.display = NumericDisplay(self, body_font, display_font_path)
        self.display.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        bar = ResponsiveButtonBar(self)
        bar.grid(row=1, column=0, sticky="ew", pady=2)
        self.start_button = ttk.Button(bar, text="Start ACQ", command=actions["start"])
        self.stop_button = ttk.Button(bar, text="Stop ACQ", command=actions["stop"])
        self.clear_plot_button = ttk.Button(bar, text="Clear plot", command=actions["clear_plot"])
        self.clear_display_button = ttk.Button(
            bar, text="Clear display", command=actions["clear_display"]
        )
        for widget in (
            self.start_button,
            self.stop_button,
            self.clear_plot_button,
            self.clear_display_button,
        ):
            bar.add(widget)
        options = ttk.LabelFrame(options_parent, text="Acquisition options", padding=4)
        options.grid(row=5, column=0, sticky="ew", pady=6)
        options.columnconfigure(0, weight=1)
        self.rate_var, self.history_var = tk.StringVar(self, "MAX"), tk.StringVar(self, "100")
        for row, (label, variable, values, key) in enumerate(
            (
                ("Interval [s]", self.rate_var, ACQUISITION_INTERVALS, "rate"),
                ("History [samples]", self.history_var, ("100", "250", "500", "1000"), "history"),
            )
        ):
            card = ttk.Frame(options, padding=2)
            card.grid(row=row, column=0, sticky="ew")
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=label).grid(row=0, column=0, sticky="w")
            combo = ttk.Combobox(
                card,
                textvariable=variable,
                values=values,
                width=1,
                state="readonly",
                font=body_font,
            )
            combo.grid(row=1, column=0, sticky="ew")
            setattr(self, key, combo)
        self.plot_var, self.logger_var = tk.BooleanVar(self, True), tk.BooleanVar(self, True)
        self.graph_check = ttk.Checkbutton(
            options, text="Graph", variable=self.plot_var, command=actions["graph"]
        )
        self.log_check = ttk.Checkbutton(options, text="File log", variable=self.logger_var)
        self.graph_check.grid(row=2, column=0, sticky="w", padx=2, pady=3)
        self.log_check.grid(row=3, column=0, sticky="w", padx=2, pady=3)
        self.plot = LiveTracePlot(self)
        self.plot.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        Hovertip(
            self.rate,
            "MAX reads as fast as the DMM allows. Numeric values are sampling intervals in seconds.",
        )
        Hovertip(
            self.history,
            "Keep the latest samples in a scrolling plot. File log records all readings.",
        )

    def set_state(self, connected: bool, busy: bool, running: bool, stopping: bool) -> None:
        idle = not busy and not running
        enabled = "normal" if connected and idle else "disabled"
        self.start_button.configure(state=enabled)
        self.stop_button.configure(state="normal" if running and not stopping else "disabled")
        for combo in (self.rate, self.history):
            combo.configure(state="readonly" if idle else "disabled")
        for widget in (
            self.graph_check,
            self.log_check,
            self.clear_plot_button,
            self.clear_display_button,
        ):
            widget.configure(state="normal" if idle else "disabled")


class ApplicationView(ttk.Frame):
    def __init__(
        self, root: tk.Tk, config: AppConfig, actions: dict[str, Callable], themes
    ) -> None:
        super().__init__(root, padding=4)
        self.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.themes = themes
        self._compact, self._controls_open, self._font_size = None, True, 10
        self._layout_key = None
        self._flash_ids = {}
        toolbar = ResponsiveButtonBar(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.controls_button = ttk.Button(toolbar, text="Controls", command=self.toggle_controls)
        self.analysis_button = ttk.Button(
            toolbar, text="Data analysis", command=actions["analysis"]
        )
        self.folder_button = ttk.Button(toolbar, text="Folder", command=actions["folder"])
        self.configuration_button = ttk.Button(
            toolbar, text="Config", command=actions["configuration"]
        )
        self.help_button = ttk.Button(toolbar, text="Help", command=actions["help"])
        for button in (
            self.controls_button,
            self.analysis_button,
            self.folder_button,
            self.configuration_button,
            self.help_button,
        ):
            toolbar.add(button)
        self.body = ttk.Frame(self)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.sidebar = ttk.Frame(self.body)
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        self.sidebar.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(self.sidebar)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.connection_page, self.settings_page = (
            ScrollableFrame(self.notebook),
            ScrollableFrame(self.notebook),
        )
        self.console = ConsolePanel(self.notebook, themes.mono_font, actions, themes.body_font)
        for page, label in (
            (self.connection_page, "Connection"),
            (self.settings_page, "Settings"),
            (self.console, "Console"),
        ):
            self.notebook.add(page, text=label)
        self.connection = ConnectionPanel(self.connection_page.content, actions, themes.body_font)
        self.connection.grid(row=0, column=0, sticky="ew")
        self.settings = InstrumentSettingsPanel(
            self.settings_page.content, actions, themes.body_font
        )
        self.settings.grid(row=0, column=0, sticky="ew")
        self.acquisition = AcquisitionPanel(
            self.body, actions, themes.body_font, self.settings, config.display_font_path
        )
        footer = ttk.Frame(self)
        footer.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        footer.columnconfigure(0, weight=1)
        self.status = ttk.Label(footer, text="Ready", wraplength=300)
        self.status.grid(row=0, column=0, sticky="ew", padx=4)
        indicators = ttk.Frame(footer)
        indicators.grid(row=0, column=1, sticky="e")
        self.indicators = {}
        for column, direction in enumerate(("LINK", "TX", "RX", "ACQ")):
            block = ttk.Frame(indicators)
            block.grid(row=0, column=column, padx=3)
            canvas = tk.Canvas(block, width=9, height=9, highlightthickness=0)
            canvas.grid(row=0, column=0, padx=(0, 3))
            rectangle = canvas.create_rectangle(0, 0, 9, 9, fill="black", outline="")
            ttk.Label(block, text=direction).grid(row=0, column=1)
            self.indicators[direction] = canvas, rectangle
        self.context_menu = TextContextMenu(root, self.console.clear)
        self.bind("<Configure>", self._resize)
        self._apply_layout(LayoutPolicy(config.width, config.height, self._dpi()))

    def _dpi(self) -> float:
        return max(1.0, self.winfo_fpixels("1i") / 96)

    def toggle_controls(self) -> None:
        self._controls_open = not self._controls_open
        self._apply_layout(LayoutPolicy(self.winfo_width(), self.winfo_height(), self._dpi()))

    def _resize(self, event) -> None:
        policy = LayoutPolicy(event.width, event.height, self._dpi())
        self.themes.resize_fonts(policy.font_size)
        if policy.font_size != self._font_size:
            self._font_size = policy.font_size
            self.acquisition.plot.renderer.set_font_size(policy.font_size)
            self.acquisition.plot.canvas.draw_idle()
        self._apply_layout(policy)
        self.status.configure(wraplength=max(80, event.width - 230))
        self.acquisition.display.fit()

    def _apply_layout(self, policy: LayoutPolicy) -> None:
        key = (policy.compact, self._controls_open)
        if key == self._layout_key:
            if policy.compact:
                self.sidebar.configure(height=policy.compact_controls_height)
            else:
                self.sidebar.configure(width=policy.sidebar_width)
            return
        self._layout_key, self._compact = key, policy.compact
        if not self._controls_open:
            self.sidebar.grid_remove()
        for index in range(2):
            self.body.columnconfigure(index, weight=0, minsize=0)
            self.body.rowconfigure(index, weight=0, minsize=0)
        self.controls_button.configure(text="Hide controls" if self._controls_open else "Controls")
        if policy.compact:
            self.body.columnconfigure(0, weight=1)
            self.body.rowconfigure(1, weight=1)
            if self._controls_open:
                self.sidebar.configure(width=1, height=policy.compact_controls_height)
                self.sidebar.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
            self.acquisition.grid(row=1, column=0, sticky="nsew")
        else:
            self.body.rowconfigure(0, weight=1)
            self.body.columnconfigure(1, weight=1)
            if self._controls_open:
                self.sidebar.configure(width=policy.sidebar_width, height=1)
                self.sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
            self.acquisition.grid(row=0, column=1, sticky="nsew")

    def set_state(self, connected: bool, busy: bool, running: bool, stopping: bool) -> None:
        self.connection.set_state(connected, busy, running)
        self.settings.set_state(connected and not busy and not running)
        self.acquisition.set_state(connected, busy, running, stopping)
        self.console.set_state(connected, busy, running)
        self.configuration_button.configure(
            state="normal" if not busy and not running else "disabled"
        )
        self.analysis_button.configure(state="normal" if not busy else "disabled")
        for name, active, color in (("LINK", connected, "lawngreen"), ("ACQ", running, "magenta")):
            canvas, rectangle = self.indicators[name]
            canvas.itemconfigure(rectangle, fill=color if active else "black")

    def flash(self, direction: str) -> None:
        canvas, rectangle = self.indicators[direction]
        canvas.itemconfigure(rectangle, fill="blue" if direction == "TX" else "orange")
        if direction in self._flash_ids:
            self.after_cancel(self._flash_ids[direction])
        self._flash_ids[direction] = self.after(80, lambda: self._end_flash(direction))

    def _end_flash(self, direction: str) -> None:
        canvas, rectangle = self.indicators[direction]
        canvas.itemconfigure(rectangle, fill="black")
        self._flash_ids.pop(direction, None)
