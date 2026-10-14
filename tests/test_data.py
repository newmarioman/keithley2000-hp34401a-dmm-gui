"""Files, scientific plot rendering and scrolling trace checks."""

import math
import tempfile
import unittest
from contextlib import closing
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from PIL import Image

from scpi_dmm.analysis import PLOT_TYPES, AnalysisRenderer, DataAnalyzer, PlotOptions
from scpi_dmm.config import AppConfig, UserSettings
from scpi_dmm.models import Measurement, TraceBuffer
from scpi_dmm.plot import LiveTraceRenderer
from scpi_dmm.storage import (
    CaptureData,
    CsvCaptureLogger,
    CsvCaptureRepository,
    OutputManager,
    SettingsRepository,
)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_settings_round_trip(self):
        settings = UserSettings(self.folder, "clam", "mario")
        repository = SettingsRepository(AppConfig(self.folder))
        repository.save(settings)
        self.assertEqual(repository.load(), settings)
        self.assertEqual(
            (self.folder / "settings.ini").read_text().splitlines(),
            [f"Program Path: {self.folder}", "Theme Style: clam", "Audio : mario"],
        )

    def test_output_folder_is_safe_on_windows_and_csv_can_be_saved_and_loaded(self):
        manager = OutputManager(AppConfig(self.folder))
        preferences = UserSettings(self.folder / "Users" / "Nome utente")
        csv_path = manager.capture_path(preferences, "V")
        with closing(CsvCaptureLogger(csv_path, "V")) as logger:
            logger.append(Measurement(1.25, 0.1, "V"))
        capture = CsvCaptureRepository().load(csv_path)
        self.assertEqual(capture.values, (1.25,))
        self.assertEqual(csv_path.parent, preferences.program_path / "Keithley2000_HP34401A_DMM" / "OUTPUT")
        png_path = manager.plot_path(preferences, "Line", "Reading: 1 / V")
        self.assertTrue(png_path.parent.is_dir())
        relative = csv_path.relative_to(preferences.program_path)
        self.assertEqual(PureWindowsPath(str(relative)).parts[:2], ("Keithley2000_HP34401A_DMM", "OUTPUT"))
        for component in relative.parts:
            self.assertFalse(any(character in component for character in '<>:"/\\|?*'))

    def test_cp1252_preferences_and_corrupt_file_fallback(self):
        output = self.folder / "café"
        output.mkdir()
        config = AppConfig(self.folder)
        config.settings_path.write_bytes(
            f"Program Path: {output}\nTheme Style: clam\nAudio : chime".encode("cp1252")
        )
        self.assertEqual(SettingsRepository(config).load().program_path, output)
        config.settings_path.write_text("incorrect")
        self.assertTrue(SettingsRepository(config).load().program_path.is_dir())

    def test_bad_csv_rows_and_empty_capture_are_rejected(self):
        path = self.folder / "bad.csv"
        for payload in (
            "wrong,header\n1,2",
            "MEASURE[V],TIMESTAMP[s]\n",
            "MEASURE[V],TIMESTAMP[s]\nnan,0",
            "MEASURE[V],TIMESTAMP[s]\n1,-1",
            "MEASURE[V],TIMESTAMP[s]\n1,2,3",
        ):
            path.write_text(payload)
            with self.assertRaises(ValueError):
                CsvCaptureRepository().load(path)

    def test_logger_does_not_overwrite_or_mix_units(self):
        path = self.folder / "capture.csv"
        logger = CsvCaptureLogger(path, "Ω")
        try:
            logger.append(Measurement(1234, 0, "Ω"))
            with self.assertRaises(ValueError):
                logger.append(Measurement(1, 1, "V"))
            with self.assertRaises(FileExistsError):
                CsvCaptureLogger(path, "Ω")
        finally:
            logger.close()
        data = CsvCaptureRepository().load(path)
        self.assertEqual(data.values, (1234,))
        self.assertEqual(data.unit, "Ω")

    def test_output_names_are_portable_and_existing_png_is_preserved(self):
        manager, preferences = OutputManager(AppConfig(self.folder)), UserSettings(self.folder)
        self.assertEqual(manager.sanitize_name(' Ω:/?*" '), "Ohm_____")
        first = manager.plot_path(preferences, "Line", "Example")
        first.write_bytes(b"original")
        second = manager.plot_path(preferences, "Line", "Example")
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), b"original")


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.data = CaptureData((1.0, 2.0, 2.0, 3.0, 4.0), (0.0, 1.0, 2.0, 3.0, 4.0), "V")
        self.renderer = AnalysisRenderer()

    def test_statistics_use_sample_standard_deviation(self):
        stats = DataAnalyzer().statistics(self.data)
        self.assertEqual(
            (stats.count, stats.minimum, stats.maximum, stats.mean, stats.median), (5, 1, 4, 2.4, 2)
        )
        self.assertAlmostEqual(stats.std, math.sqrt(1.3))

    def test_all_five_plot_types_render_independently(self):
        for kind in PLOT_TYPES:
            for vertical in (False, True):
                axes = self.renderer.render(
                    self.data,
                    PlotOptions(
                        kind=kind,
                        mean=True,
                        std=True,
                        median=True,
                        mode=True,
                        minimum=True,
                        maximum=True,
                        kde=True,
                        quartiles=True,
                        vertical=vertical,
                    ),
                )
                FigureCanvasAgg(self.renderer.figure).draw()
                self.assertEqual(len(self.renderer.figure.axes), 1)
                self.assertIn("V", axes.get_xlabel() + axes.get_ylabel())

    def test_constant_and_single_reading_render_without_density_failure(self):
        for values in ((2.0,), (2.0, 2.0, 2.0)):
            data = CaptureData(values, tuple(float(i) for i in range(len(values))), "A")
            for kind in PLOT_TYPES:
                self.renderer.render(data, PlotOptions(kind=kind, kde=True))
                FigureCanvasAgg(self.renderer.figure).draw()

    def test_histogram_percentage_sums_to_100(self):
        axes = self.renderer.render(
            self.data, PlotOptions(kind="Histogram", percentage=True, bins=10)
        )
        self.assertAlmostEqual(sum(p.get_height() for p in axes.patches), 100)

    def test_grid_checkbox_really_disables_the_grid(self):
        for enabled in (True, False):
            axes = self.renderer.render(self.data, PlotOptions(grid=enabled))
            FigureCanvasAgg(self.renderer.figure).draw()
            self.assertEqual(any(line.get_visible() for line in axes.get_xgridlines()), enabled)
            self.assertEqual(any(line.get_visible() for line in axes.get_ygridlines()), enabled)

    def test_axis_bounds_are_validated_and_applied(self):
        for bounds in ((1, 1), (2, 1), (float("nan"), 3), (-1, float("inf"))):
            with self.assertRaises(ValueError):
                PlotOptions(x_limits=bounds)
        axes = self.renderer.render(self.data, PlotOptions(x_limits=(0, 5), y_limits=(-1, 5)))
        self.assertEqual(axes.get_xlim(), (0, 5))
        self.assertEqual(axes.get_ylim(), (-1, 5))

    def test_line_statistics_and_extrema_use_x_y_or_intersected_bounds(self):
        data = CaptureData(
            (-100.0, 4.0, 1.0, 3.0, 50.0, 2.0, -40.0), (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0), "V"
        )
        for x_limits, y_limits, count, mean, std, minimum, maximum in (
            ((1, 3), None, 3, 8 / 3, math.sqrt(7 / 3), (2, 1), (1, 4)),
            (None, (1, 4), 4, 2.5, math.sqrt(5 / 3), (2, 1), (1, 4)),
            ((2, 5), (2, 10), 2, 2.5, math.sqrt(0.5), (5, 2), (3, 3)),
        ):
            with self.subTest(x=x_limits, y=y_limits):
                axes = self.renderer.render(
                    data,
                    PlotOptions(
                        x_limits=x_limits,
                        y_limits=y_limits,
                        minimum=True,
                        maximum=True,
                        mean=True,
                        std=True,
                    ),
                )
                stats = self.renderer.last_statistics
                self.assertEqual(stats.count, count)
                self.assertAlmostEqual(stats.mean, mean)
                self.assertAlmostEqual(stats.std, std)
                self.assertEqual(stats.minimum, minimum[1])
                self.assertEqual(stats.maximum, maximum[1])
                for point, expected in zip(axes.lines[1:3], (minimum, maximum)):
                    self.assertEqual((point.get_xdata()[0], point.get_ydata()[0]), expected)
                np.testing.assert_array_equal(axes.lines[3].get_ydata(), [mean, mean])
                self.assertEqual(
                    axes.get_legend_handles_labels()[1], [f"Mean: {mean:.6g}", f"SD: {std:.6g}"]
                )
                np.testing.assert_array_equal(axes.lines[0].get_ydata(), data.values)
                FigureCanvasAgg(self.renderer.figure).draw()

    def test_line_limits_include_edges_and_one_visible_sample_has_zero_sd(self):
        axes = self.renderer.render(
            self.data,
            PlotOptions(
                x_limits=(3, 4), y_limits=(4, 5), minimum=True, maximum=True, mean=True, std=True
            ),
        )
        stats = self.renderer.last_statistics
        self.assertEqual(
            (stats.count, stats.minimum, stats.maximum, stats.mean, stats.std), (1, 4, 4, 4, 0)
        )
        self.assertEqual(axes.lines[1].get_xdata()[0], 4)
        self.assertEqual(axes.lines[2].get_xdata()[0], 4)
        FigureCanvasAgg(self.renderer.figure).draw()

    def test_empty_line_selection_reports_no_samples_without_full_capture_markers(self):
        for x_limits, y_limits in (((10, 11), None), (None, (10, 11)), ((0, 1), (3, 4))):
            with self.subTest(x=x_limits, y=y_limits):
                axes = self.renderer.render(
                    self.data,
                    PlotOptions(
                        x_limits=x_limits,
                        y_limits=y_limits,
                        minimum=True,
                        maximum=True,
                        mean=True,
                        std=True,
                    ),
                )
                self.assertIsNone(self.renderer.last_statistics)
                self.assertEqual(len(axes.lines), 1)
                self.assertEqual(len(axes.patches), 0)
                self.assertIsNone(axes.get_legend())
                self.assertIn("No samples", axes.texts[0].get_text())
                FigureCanvasAgg(self.renderer.figure).draw()

    def test_resetting_line_limits_restores_all_statistics_and_other_types_keep_full_data(self):
        original = DataAnalyzer().statistics(self.data)
        self.renderer.render(self.data, PlotOptions(x_limits=(0, 1), y_limits=(0, 3)))
        self.assertEqual(self.renderer.last_statistics.count, 2)
        self.renderer.render(self.data, PlotOptions())
        self.assertEqual(self.renderer.last_statistics, original)
        for kind in ("Histogram", "KDE", "Box", "Violin"):
            self.renderer.render(
                self.data, PlotOptions(kind=kind, x_limits=(0, 1), y_limits=(0, 3))
            )
            self.assertEqual(self.renderer.last_statistics, original)

    def test_png_export_restores_screen_figure_and_uses_300_dpi(self):
        self.renderer.render(self.data, PlotOptions())
        original = self.renderer.figure.get_size_inches().copy()
        original_axes = self.renderer.figure.axes[0]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                self.renderer.figure, "set_size_inches", wraps=self.renderer.figure.set_size_inches
            ) as screen_resize,
        ):
            path = self.renderer.save_png(Path(directory) / "figure.png")
            screen_resize.assert_not_called()
            with Image.open(path) as picture:
                self.assertGreater(picture.width, 2000)
                self.assertGreater(picture.height, 1500)
                self.assertAlmostEqual(picture.info["dpi"][0], 300, places=2)
        np.testing.assert_array_equal(self.renderer.figure.get_size_inches(), original)
        self.assertIs(self.renderer.figure.axes[0], original_axes)


class LivePlotTests(unittest.TestCase):
    def test_line_is_reused_and_time_window_scrolls(self):
        renderer, trace = LiveTraceRenderer(), TraceBuffer(5)
        line = renderer.line
        for index in range(20):
            trace.append(Measurement(index - 18, index / 10, "V"))
            renderer.update(trace.snapshot(), "V")
        self.assertIs(renderer.line, line)
        self.assertEqual(len(renderer.axes.lines), 1)
        self.assertGreater(renderer.axes.get_xlim()[0], 1.4)
        self.assertGreater(renderer.axes.get_xlim()[1], 1.9)
        self.assertEqual(list(renderer.line.get_ydata()), [-3, -2, -1, 0, 1])

    def test_zero_negative_values_and_overload_are_handled(self):
        renderer = LiveTraceRenderer()
        renderer.update((Measurement(0, 0, "V"),), "V")
        self.assertLess(renderer.axes.get_ylim()[0], 0)
        self.assertGreater(renderer.axes.get_ylim()[1], 0)
        renderer.update(
            (Measurement(-2, 1, "V"), Measurement(9.99999e9, 2, "V", overload=True)), "V"
        )
        self.assertTrue(np.isnan(renderer.line.get_ydata()[-1]))
        self.assertLess(renderer.axes.get_ylim()[1], 0)

    def test_resizing_changes_rendering_without_mutating_samples(self):
        renderer = LiveTraceRenderer()
        readings = tuple(Measurement(math.sin(i / 4), i / 10, "V") for i in range(30))
        renderer.update(readings, "V")
        for width, height in ((4, 2), (12, 6)):
            renderer.figure.set_size_inches(width, height)
            FigureCanvasAgg(renderer.figure).draw()
            self.assertEqual(list(renderer.line.get_xdata()), [s.timestamp for s in readings])
        renderer.clear()
        self.assertEqual(len(renderer.line.get_xdata()), 0)


if __name__ == "__main__":
    unittest.main()
