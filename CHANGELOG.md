# Changelog

## 0.1.2

- **Fixed: Substance 3D Painter crashed on exit after an export** (finished or cancelled).
  The application-wide event filter used to hide Painter's export progress popup handed
  every object receiving an event during the export to Python, and Painter faulted while
  tearing down afterwards. The popup is now hidden by a timer watching the top-level
  windows instead.
- Shutdown clean-up: the panel's dock is looked up live (Painter rebuilds it at start-up, so
  the handle kept since then was already dead), the quit hook can no longer run a second
  time after the plugin was closed, and the Variant Exporter window is deleted right away
  when Painter quits instead of being left for Qt.
- The "project is locked" error logged on every quit is gone: the settings are kept in the
  local copy when Painter has locked the project (closing, saving, exporting).
- Leftover `.Variant Exporter.loaded-<pid>` files are removed when the plugin closes.
- Crash diagnostics: `%LOCALAPPDATA%\em_variant_exporter\trace.log` (plugin lifecycle) and
  `crash-trace.txt` (Python stacks on a native crash).

## 0.1.1

- Exporting over files that already exist asks what to do: **Cancel**, **Increment**
  (number the new files after the existing ones, e.g. `_0004` when `_0001`..`_0003` are
  there) or **Overwrite**.

## 0.1.0

First release.

- Export every variation (a layer or group inside the picked groups, or individually picked
  layers) one by one, each with its own number (`_0001`, `_0002`…).
- Variations list: grouped, reorderable by drag & drop, Reverse / Reset, skip variations,
  Start number with its own padding (`001`, `00005`…), *Skip counts* option.
- Export settings: output directory, prefix / file name / suffix, export template, per-map
  suffix, subfolder and *Per variation* switch, file name / prefix / suffix override per map,
  file type, bit depth, padding, dithering, several sizes (each in its own subfolder).
- *Variant Exporter* window (File › Export Variants…) with Variations / Export tabs, plus a
  docked panel. Settings are saved per project.
- Installs the bundled EM export presets and project template into the user's shelf.
