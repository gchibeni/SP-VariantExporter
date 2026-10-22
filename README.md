## SP VariantExporter

A lightweight plugin for **Adobe Substance 3D Painter** that exports every color variation of a texture set in one click — numbered, organized and with your layer stack left exactly as it was.

> [!NOTE]
> **SP Variant Exporter is in its first release (v0.1.0).** Everything below is up and running, and has been tested on Windows with Painter 12. See the [Changelog](CHANGELOG.md) for what's new.

<br>

## :sparkles: Highlights

- **One click, every variation** — no more hiding, unhiding and renaming exports by hand.
- **Non-destructive** — layer visibility is always restored after an export, even if it fails.
- **Saved per project** — every setting is stored with the project and comes back when you reopen it.
- **Fits right in** — Painter's own colors, a docked panel and a **File › Export Variants…** entry.
- **Batteries included** — bundled export presets and a project template are installed into your shelf automatically.

<br>

## :jigsaw: Features

### Variations

- **Pick groups or layers** — select the groups holding your variations (every layer or group inside becomes one) or the variation layers themselves, then click **Use selected**.
- **Reorder freely** — drag & drop, **Reverse** or **Reset** the order.
- **Skip what you don't need** — untick any variation; with **Skip counts** on, the others keep their numbers.
- **Smart numbering** — the start number also sets the padding: `001`, `0001`, `00005`…

### Export

- **Flexible file names** — output folder, prefix, file name and suffix, with Painter tokens such as `$textureSet`. Underscores are added for you.
- **Per-map control** — export on/off, suffix, subfolder (nested paths like `PBR/Ocean` work) and whether the map changes **per variation** (e.g. albedo) or is exported **once** (normal, roughness…).
- **Per-map overrides** — give any map its own file name, prefix or suffix.
- **Any export template** — including the bundled `!EM-OPT` and `!EM-PDP` presets.
- **Formats & sizes** — file type, bit depth, padding (infinite by default), dithering, and several sizes at once, each in its own subfolder.
- **Quiet exports** — progress is shown inline instead of Painter's export popup.

<br>

## :card_index_dividers: Output Example

With the prefix `T`, the file name `$textureSet`, two variations and the albedo set **per variation**:

```
Export/
├── T_Boat_alb_0001.png      # one per variation
├── T_Boat_alb_0002.png
├── T_Boat_nrm.png           # exported once
└── T_Boat_rgh.png
```

With more than one size selected, each size goes into its own subfolder (`2048/`, `1024/`…).

<br>

## :hammer_and_wrench: Requirements

- **Adobe Substance 3D Painter 11.0** or newer (Python API 0.3.4+).
- **Windows.** macOS should work but is untested.

<br>

## :package: Installation

The repository **is** the plugin, so you can clone it straight into Painter's user plugins folder:

```bash
cd "%USERPROFILE%\Documents\Adobe\Adobe Substance 3D Painter\python\plugins"
git clone https://github.com/gchibeni/SP-Variant-Exporter.git "Variant Exporter"
```

Or download the ZIP from GitHub, extract it into the same folder and rename the extracted folder to **`Variant Exporter`**.

Then, in Painter, click **Python › Reload Plugins Folder** and tick **Variant Exporter** in the same menu.

> [!TIP]
> The folder name is the name Painter shows in its **Python** menu. Painter remembers the plugin once ticked and starts it automatically from then on. To update a cloned copy, run `git pull` and untick / tick it in the **Python** menu.

<br>

## :rocket: Usage

1. Open **File › Export Variants…** (or use the docked **Variant Exporter** panel).
2. In the layer stack, select the group(s) holding your variations — or the variation layers — and click **Use selected**.
3. Untick the variations you don't want, reorder them and set the start number.
4. Click **Next…** and set up the output, maps, format and sizes on the **Export** tab.
5. Click **Export N variations…** — the folder button opens the output directory when it's done.

<br>

## :file_folder: Project Structure

```
├── __init__.py          Plugin entry: dock panel, File menu entry, Painter events
├── core.py              Export logic: variations, file names, export calls
├── ui.py                Panel, Variant Exporter window and its Export tab
├── theme.py             Colors, stylesheet and line icons
├── dock_placement.py    Remembers where the panel is docked
├── assets_install.py    Copies the bundled presets / template into the user's shelf
├── self_update.py       Reloads the plugin in place when its files are updated
├── version.py           Plugin version
└── assets/
    ├── export-presets/  Bundled export presets (.spexp)
    └── templates/       Bundled project template (.spt)
```

To release a new version, update `version.py` and `CHANGELOG.md`.

<br>

## :scroll: License

Released under the [MIT License](LICENSE).

<br>

#### NOW you are all set. Happy exporting!
