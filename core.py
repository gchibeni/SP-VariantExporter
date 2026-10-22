"""Variation export logic, independent from the UI.

A "variation" is a direct child (layer or group) of one of the selected groups.
For each variation, every other variation is hidden, the variation is shown and
the "per variation" maps are exported with a numbered suffix (_0001, _0002...).
Maps not flagged "per variation" (normal, roughness...) are exported only once.
Visibility is always restored at the end, even on error or cancel.
"""

import copy
import os
import re
from dataclasses import dataclass, field, asdict, replace
from typing import Callable, List, Optional

import substance_painter.export as sp_export
import substance_painter.layerstack as sp_layers
import substance_painter.textureset as sp_textureset

FILE_FORMATS = ["png", "tga", "jpeg", "tiff", "exr", "psd", "bmp", "webp"]
BIT_DEPTHS = ["8", "16", "16f", "32f"]
PADDINGS = ["infinite", "diffusion", "color", "transparent", "passthrough"]
PADDINGS_WITH_DISTANCE = {"diffusion", "color", "transparent"}
SIZES = [256, 512, 1024, 2048, 4096, 8192]

# Template map names that are colour variations by default.
_VARIANT_HINTS = ("basecolor", "base_color", "albedo", "diffuse", "color")
# Short names only count as a whole part of the name ("_alb", "_alb_coat", not "global").
_VARIANT_TOKENS = ("alb", "col", "bc")


# --------------------------------------------------------------------------- data

@dataclass
class MapSetting:
    key: str              # fileName of the map in the template (unique per template)
    enabled: bool = True
    suffix: str = ""
    per_variation: bool = False
    folder: Optional[str] = None   # subfolder for this map, "" = none (None: saved before it existed)
    name: str = ""                 # file name override for this map, "" = use the main file name
    prefix: str = ""               # prefix override for this map, "" = use the main prefix
    name_suffix: str = ""          # suffix override for this map, "" = use the main suffix


@dataclass
class OutputSetting:
    size: Optional[int] = 2048   # pixels, None = texture set resolution
    folder: str = ""             # subfolder; auto-named only when there are several sizes
    enabled: bool = True         # switched off = kept in the list but not exported


@dataclass
class ExportSettings:
    export_dir: str = ""
    base_name: str = "$textureSet"
    prefix: str = ""        # added before every file name
    name_suffix: str = ""   # added after the file name, before the map suffix
    template_url: str = ""
    file_format: str = "png"     # "" = template default
    bit_depth: str = ""          # "" = template default
    dithering: bool = False
    padding: str = "infinite"
    dilation: int = 16
    start_index: int = 1
    digits: int = 4
    group_uids: List[int] = field(default_factory=list)
    skipped_uids: List[int] = field(default_factory=list)
    reverse_order: bool = False   # legacy, replaced by order_uids
    order_uids: List[int] = field(default_factory=list)   # custom variation order, [] = stack
    count_skipped: bool = True   # skipped variations still use up their number
    saved_at: float = 0.0   # time.time() of the last save: picks the newest copy on load
    maps: List[MapSetting] = field(default_factory=list)
    outputs: List[OutputSetting] = field(default_factory=lambda: [OutputSetting()])

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        d = dict(d)
        known = ExportSettings.__dataclass_fields__
        d = {k: v for k, v in d.items() if k in known}
        d["maps"] = [MapSetting(**m) for m in d.get("maps", [])]
        d["outputs"] = [OutputSetting(**o) for o in d.get("outputs", [])] or [OutputSetting()]
        return ExportSettings(**d)


@dataclass
class Variation:
    uid: int
    name: str
    group_name: str
    is_group: bool = False


class ExportCancelled(Exception):
    pass


# --------------------------------------------------------------------------- layer stack

class LayerSet:
    """Layers picked one by one, handled like a group whose children are those layers:
    each one is a variation, and exporting one hides the other picked layers only."""

    UID = -1
    NAME = "Selected layers"

    def __init__(self, layers):
        self.layers = list(layers)

    def uid(self):
        return self.UID

    def get_name(self):
        return self.NAME

    def sub_layers(self):
        return list(self.layers)

    def get_stack(self):
        return self.layers[0].get_stack()

    def parents(self):
        """Groups holding the picked layers: they must be visible for the export to see them."""
        seen, result = set(), []
        for layer in self.layers:
            parent = layer.get_parent()
            while parent is not None:
                if parent.uid() not in seen:
                    seen.add(parent.uid())
                    result.append(parent)
                parent = parent.get_parent()
        return result


def source_uids(sources):
    """uids to save for a list of groups / LayerSet (a LayerSet saves its layers)."""
    uids = []
    for src in sources:
        if isinstance(src, LayerSet):
            uids.extend(layer.uid() for layer in src.layers)
        else:
            uids.append(src.uid())
    return uids


def _is_inside(node, group_uids):
    parent = node.get_parent()
    while parent is not None:
        if parent.uid() in group_uids:
            return True
        parent = parent.get_parent()
    return False


def groups_from_uids(uids):
    """Groups (their children are variations) + a LayerSet for the layers picked one by one.
    Layers already inside a picked group are dropped, so nothing appears twice."""
    groups, layers = [], []
    for uid in uids:
        try:
            node = sp_layers.get_node_by_uid(uid)
        except (ValueError, RuntimeError):
            continue
        if isinstance(node, sp_layers.GroupLayerNode):
            groups.append(node)
        elif isinstance(node, sp_layers.LayerNode):
            layers.append(node)
    group_uids = {g.uid() for g in groups}
    layers = [l for l in layers if not _is_inside(l, group_uids)]
    return groups + ([LayerSet(layers)] if layers else [])


def selected_groups():
    """What "Use selected" picks up: selected groups and selected layers (effects ignored)."""
    stack = sp_textureset.get_active_stack()
    return groups_from_uids([n.uid() for n in sp_layers.get_selected_nodes(stack)
                             if isinstance(n, sp_layers.LayerNode)])


def list_variations(groups, reverse=False):
    result = []
    for group in groups:
        children = group.sub_layers()
        if reverse:
            children = list(reversed(children))
        result.extend(Variation(c.uid(), c.get_name(), group.get_name(),
                                isinstance(c, sp_layers.GroupLayerNode)) for c in children)
    return result


# --------------------------------------------------------------------------- templates

def list_templates():
    """[(label, url, preset_object)] for every export preset known to Painter."""
    templates = []
    for p in sp_export.list_resource_export_presets():
        templates.append((p.resource_id.name, p.resource_id.url(), p))
    for p in sp_export.list_predefined_export_presets():
        templates.append((p.name, p.url, p))
    templates.sort(key=lambda t: t[0].lower())
    return templates


def template_maps(url, stack=None):
    for _, t_url, preset in list_templates():
        if t_url == url:
            if isinstance(preset, sp_export.PredefinedExportPreset):
                return preset.list_output_maps(stack or sp_textureset.get_active_stack())
            return preset.list_output_maps()
    raise ValueError(f"Export template not found: {url}")


def default_suffix(file_name):
    """'$mesh_$textureSet_BaseColor(_$colorSpace)(.$udim)' -> '_BaseColor'."""
    s = re.sub(r"\([^)]*\)", "", file_name)   # optional parts, e.g. (_$colorSpace)
    s = re.sub(r"\$[A-Za-z]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return "_" + s if s else ""


def map_folder_name(key):
    """Default subfolder name the switch fills in: the map name in capitals (ALB, ROUGH...)."""
    return default_suffix(key).strip("_").upper()


def default_map_settings(maps):
    out = []
    for m in maps:
        key = m["fileName"]
        suffix = default_suffix(key)
        low = suffix.lower()
        variant = (any(h in low for h in _VARIANT_HINTS)
                   or any(t in low.strip("_").split("_") for t in _VARIANT_TOKENS))
        out.append(MapSetting(key=key, enabled=True, suffix=suffix, per_variation=variant,
                              folder=""))
    return out


def merge_map_settings(maps, saved):
    """Template maps with the user's saved choices applied where the map still exists."""
    saved_by_key = {s.key: s for s in saved}
    result = []
    for default in default_map_settings(maps):
        s = saved_by_key.get(default.key)
        if s is None:
            result.append(default)
            continue
        # Earlier versions saved suffixes with Painter's optional-part syntax left in.
        if "(" in s.suffix or ")" in s.suffix:
            s = replace(s, suffix=default.suffix)
        if s.folder is None:   # saved before maps had subfolders
            s = replace(s, folder="")
        result.append(s)
    return result


# --------------------------------------------------------------------------- config

def _map_parameters(template_map, settings, output):
    params = dict(template_map.get("parameters") or {})
    if settings.file_format:
        params["fileFormat"] = settings.file_format
    if settings.bit_depth:
        params["bitDepth"] = settings.bit_depth
    params.setdefault("fileFormat", "png")
    params.setdefault("bitDepth", "8")
    params["dithering"] = settings.dithering
    params["paddingAlgorithm"] = settings.padding
    if settings.padding in PADDINGS_WITH_DISTANCE:
        params["dilationDistance"] = settings.dilation
    if output.size:
        params["sizeLog2"] = output.size.bit_length() - 1
    else:
        params.pop("sizeLog2", None)
    return params


def _config(stack, settings, output, template, maps_and_names):
    preset_maps = []
    for map_setting, file_name in maps_and_names:
        m = copy.deepcopy(template[map_setting.key])
        m["fileName"] = file_name
        m["parameters"] = _map_parameters(m, settings, output)
        preset_maps.append(m)
    return {
        "exportShaderParams": False,
        "exportPath": os.path.join(settings.export_dir,
                                   output.folder).replace("\\", "/").rstrip("/"),
        "defaultExportPreset": "em_variant_preset",
        "exportPresets": [{"name": "em_variant_preset", "maps": preset_maps}],
        "exportList": [{"rootPath": str(stack)}],
    }


@dataclass
class Job:
    variation: Optional[Variation]   # None = maps exported once
    output: OutputSetting
    config: dict
    folders: List[str] = field(default_factory=list)   # dirs to create before exporting


def file_name(settings, map_setting, index=""):
    """<prefix>_<file name or this map's override>_<suffix><map suffix>[_<index>]

    The underscores around the prefix / suffix are added automatically (not doubled when the
    user already typed them): "EM" -> "EM_", "v2" -> "_v2".
    """
    prefix = map_setting.prefix or settings.prefix
    suffix = map_setting.name_suffix or settings.name_suffix
    if prefix and not prefix.endswith("_"):
        prefix += "_"
    if suffix and not suffix.startswith("_"):
        suffix = "_" + suffix
    number = f"_{index}" if index else ""
    return f"{prefix}{map_setting.name or settings.base_name}{suffix}{map_setting.suffix}{number}"


def build_jobs(settings, variations, stack, numbers=None):
    """Every export call needed, in execution order (grouped by variation).

    `numbers` maps variation uid -> file number; by default variations are numbered
    consecutively from settings.start_index.
    """
    template = {m["fileName"]: m for m in template_maps(settings.template_url, stack)}
    maps = [m for m in settings.maps if m.enabled and m.key in template]
    static = [m for m in maps if not m.per_variation]
    variant = [m for m in maps if m.per_variation]
    outputs = [o for o in settings.outputs if o.enabled]
    udim = "(.$udim)"   # Painter drops optional parts that resolve empty (no UV tiles)

    def map_folder(m):
        # Nested subfolders are fine ("PBR/Ocean"); accept backslashes and stray slashes.
        return (m.folder or "").replace("\\", "/").strip().strip("/")

    def job(variation, out, named_maps):
        """One Painter export call: every map of one size, map subfolders included in the
        file names. (Painter renders one resolution per call, so sizes stay separate calls.
        Fewer calls = fewer of Painter's export windows and less overhead.)"""
        names = []
        folders = {os.path.join(settings.export_dir, out.folder)}
        for m, name in named_maps:
            folder = map_folder(m)
            names.append((m, f"{folder}/{name}{udim}" if folder else name + udim))
            if folder:
                folders.add(os.path.join(settings.export_dir, out.folder, folder))
        return Job(variation, out, _config(stack, settings, out, template, names),
                   sorted(f.replace("\\", "/") for f in folders))

    jobs = []
    if variant and variations:
        # Maps exported once ride along with the first variation's calls.
        for i, var in enumerate(variations):
            number = numbers[var.uid] if numbers else settings.start_index + i
            index = str(number).zfill(settings.digits)
            for out in outputs:
                named = [(m, file_name(settings, m, index)) for m in variant]
                if i == 0:
                    named += [(m, file_name(settings, m)) for m in static]
                jobs.append(job(var, out, named))
    elif static:
        for out in outputs:
            jobs.append(job(None, out, [(m, file_name(settings, m)) for m in static]))
    return jobs


def preview_files(jobs):
    files = []
    for job in jobs:
        for paths in sp_export.list_project_textures(job.config).values():
            files.extend(paths)
    return files


# --------------------------------------------------------------------------- export

def _solo(variation_uid, all_nodes):
    for node in all_nodes:
        visible = node.uid() == variation_uid
        if node.is_visible() != visible:
            node.set_visible(visible)


def run_export(settings, groups, variations, stack,
               progress: Callable[[int, int, str], bool] = lambda *_: True, numbers=None):
    """Export everything. `progress(done, total, label)` returns False to cancel.

    Returns (written files, warning messages).
    """
    jobs = build_jobs(settings, variations, stack, numbers)
    if not jobs:
        raise ValueError("Nothing to export: enable at least one map and one variation.")
    # Painter doesn't create folders that appear inside file names: create them all first.
    for job in jobs:
        for folder in job.folders:
            os.makedirs(folder, exist_ok=True)

    all_nodes = [c for g in groups for c in g.sub_layers()]
    # Containers that must be visible: picked groups, and the parents of picked layers.
    containers = [c for g in groups
                  for c in (g.parents() if isinstance(g, LayerSet) else [g])]
    restore = [(n, n.is_visible()) for n in all_nodes] + [(c, c.is_visible()) for c in containers]
    written, warnings = [], []
    try:
        for c in containers:
            if not c.is_visible():
                c.set_visible(True)
        current = None
        for done, job in enumerate(jobs):
            label = job.variation.name if job.variation else "shared maps"
            label = f"{label} ({job.output.folder or 'root'})"
            if not progress(done, len(jobs), label):
                raise ExportCancelled()
            # Shared maps are exported with the first variation shown.
            var = job.variation or (variations[0] if variations else None)
            if var and var.uid != current:
                _solo(var.uid, all_nodes)
                current = var.uid
            result = sp_export.export_project_textures(job.config)
            for paths in result.textures.values():
                written.extend(paths)
            if result.status == sp_export.ExportStatus.Cancelled:
                raise ExportCancelled()
            if result.status == sp_export.ExportStatus.Warning:
                warnings.append(f"{label}: {result.message}")
        progress(len(jobs), len(jobs), "done")
    finally:
        for node, visible in restore:
            try:
                if node.is_visible() != visible:
                    node.set_visible(visible)
            except Exception:
                pass
    return written, warnings
