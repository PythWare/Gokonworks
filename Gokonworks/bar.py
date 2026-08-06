"""
Handles Mod Manager code as well as the GUI for it
"""

from __future__ import annotations

import io, subprocess
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageTk

from .recipe import (
    PACKAGE_EXTENSION,
    RecipeError,
    read_package_audio,
    read_package_images,
    read_package_manifest,
)
from .refresh import (
    Button,
    Panel,
    ProgressBar,
    StatusLog,
    Theme,
    Worker,
    fit_line,
    load_png,
    load_settings,
    save_settings,
    scale_to_height,
    wrap_lines,
)
from .returns import apply_package, disable_all_mods, disable_mod, list_enabled_mods
from .wetworks import (
    PROJECT_ROOT,
    GokonworksError,
    WinMemoryAudioPlayer,
    default_mods_dir,
    human_size,
    log,
)

WINDOW_WIDTH = 1240
WINDOW_HEIGHT = 800
MIN_WIDTH = 960
MIN_HEIGHT = 760
DETAILS_HEIGHT = 560

PAD = 16
TOP_HEIGHT = 62
SIDE_WIDTH = 380
BUTTON_HEIGHT = 34
BUTTON_GAP = 8

PREVIEW_WIDTH = SIDE_WIDTH - PAD * 3
PREVIEW_HEIGHT = 196
PREVIEW_Y = PAD + 86
NAV_Y = PREVIEW_Y + PREVIEW_HEIGHT + 8
META_Y = NAV_Y + 40
DESC_Y = META_Y + 108
DESC_MIN = 96
LOG_MIN = 110

BOTTLE_HEIGHT = 230
BOTTLE_GAP = 26
SHELF_MARGIN = 34
SHELF_PLANK = 18
SHELF_CAPTION = 26
SHELF_OVERHANG = 16

LABEL_BOX = (0.085, 0.4333, 0.915, 0.7564)
SHOULDER = 0.3431
GLASS_LUMA_MAX = 150

@dataclass
class Bottle:
    """One sealed .at package on the shelf"""

    mod_id: str
    name: str
    source: Path
    entries: int
    enabled: bool
    author: str = ""
    genre: str = ""
    version: str = ""
    description: str = ""
    image_count: int = 0
    has_audio: bool = False
    manifest: dict = field(default_factory=dict)
    problem: str = ""
    x: float = 0.0
    y: float = 0.0
    image_item: int = 0
    label_item: int = 0
    halo_item: int = 0


def render_bottle(base: Image.Image, theme: Theme, filled: bool) -> Image.Image:
    """Recolour the bottle art once per state at full resolution"""
    width, height = base.size
    alpha = base.getchannel("A")
    luma = base.convert("RGB").convert("L")

    dark = luma.point(lambda value: 255 if value <= GLASS_LUMA_MAX else 0)
    solid = alpha.point(lambda value: 255 if value >= 128 else 0)
    mask = ImageChops.multiply(dark, solid)
    cut = ImageDraw.Draw(mask)
    cut.rectangle([0, 0, width, int(height * SHOULDER)], fill=0)
    cut.rectangle(
        [int(LABEL_BOX[0] * width), int(LABEL_BOX[1] * height),
         int(LABEL_BOX[2] * width), int(LABEL_BOX[3] * height)],
        fill=0,
    )

    if filled:
        tinted = ImageOps.colorize(
            luma, black=shade(theme.liquid_bottom, 0.3), white=theme.liquid_top,
            mid=theme.liquid_bottom,
        )
    else:
        tinted = ImageOps.colorize(luma, black="#08080a", white="#4c4c55", mid="#2a2a30")

    out = base.copy()
    out.paste(tinted.convert("RGBA"), (0, 0), mask)
    return out

def shade(colour: str, factor: float) -> tuple[int, int, int]:
    value = colour.lstrip("#")
    parts = [int(value[i : i + 2], 16) for i in (0, 2, 4)]
    return tuple(max(0, min(255, round(part * factor))) for part in parts)


def letterbox(data: bytes, width: int, height: int, background: str) -> Image.Image:
    """Fit a preview inside the box without cropping or stretching it"""
    image = Image.open(io.BytesIO(data)).convert("RGB")
    scale = min(width / image.width, height / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    canvas = Image.new("RGB", (width, height), background)
    canvas.paste(image.resize(size, Image.Resampling.LANCZOS),
                 ((width - size[0]) // 2, (height - size[1]) // 2))
    return canvas


class ModManagerWindow(tk.Toplevel):
    """The shelf"""

    def __init__(self, master, theme: Theme, volume_path: Path, taildata: dict):
        super().__init__(master)
        self.theme = theme
        self.volume_path = Path(volume_path)
        self.taildata = taildata
        self.mods_dir = default_mods_dir()
        self.bottles: list[Bottle] = []
        self.selected: Bottle | None = None
        self.columns = 0
        self.shelf_size = (0, 0)
        self.resize_job = None
        self.item_lookup: dict[int, Bottle] = {}
        self.creator_window = None

        self.settings = load_settings(PROJECT_ROOT)
        self.audio_enabled = bool(self.settings.get("mod_audio", True))
        self.player = WinMemoryAudioPlayer()
        self.preview_images: list[bytes] = []
        self.preview_index = 0
        self.preview_photo = None
        self.current_audio: bytes | None = None

        self.title("Gokonworks, Mod Shelf")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.configure(bg=theme.bg)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self.label_font = tkfont.Font(family="Segoe UI", size=7, weight="bold")
        self.button_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")

        native = load_png("bottle.png")
        self.art = {
            filled: ImageTk.PhotoImage(
                scale_to_height(render_bottle(native, theme, filled), BOTTLE_HEIGHT)
            )
            for filled in (True, False)
        }
        sample = self.art[True]
        self.bottle_width = sample.width()
        self.bottle_height = sample.height()
        self.label_width = int((LABEL_BOX[2] - LABEL_BOX[0]) * self.bottle_width) - 4
        self.label_offset = ((LABEL_BOX[1] + LABEL_BOX[3]) / 2 - 0.5) * self.bottle_height

        self.build()
        self.worker = Worker(self.shelf)
        self.rescan()

    def build(self):
        theme = self.theme

        self.top = tk.Canvas(self, bg=theme.bg, height=TOP_HEIGHT, highlightthickness=0)
        self.top.pack(side="top", fill="x")
        self.top.create_text(
            PAD, TOP_HEIGHT / 2, text="Mod Shelf", anchor="w", fill=theme.text,
            font=("Segoe UI", 15, "bold"),
        )

        self.top_buttons = []
        specs = [
            ("Mod Creator", self.open_creator, "accent"),
            ("Rescan", self.rescan, "normal"),
            ("Pour", self.enable_selected, "accent"),
            ("Empty", self.disable_selected, "normal"),
            ("Empty Every Bottle", self.disable_all, "danger"),
            ("Open Mods Folder", self.open_mods_folder, "normal"),
        ]
        cursor = 132
        for text, command, tone in specs:
            width = self.button_font.measure(text) + 30
            self.top_buttons.append(
                Button(self.top, theme, cursor, (TOP_HEIGHT - BUTTON_HEIGHT) / 2,
                       width, BUTTON_HEIGHT, text, command, tone=tone)
            )
            cursor += width + BUTTON_GAP

        body = tk.Frame(self, bg=theme.bg)
        body.pack(side="top", fill="both", expand=True)

        self.side = tk.Canvas(body, bg=theme.bg, width=SIDE_WIDTH, highlightthickness=0)
        self.side.pack(side="right", fill="y")
        self.side.pack_propagate(False)

        self.shelf = tk.Canvas(body, bg=theme.bg, highlightthickness=0)
        self.shelf.pack(side="left", fill="both", expand=True)
        self.shelf.bind("<Configure>", self.on_shelf_configure)
        self.shelf.bind("<MouseWheel>", self.on_wheel)
        self.shelf.bind("<Button-1>", self.on_shelf_click)

        self.build_side()

    def build_side(self):
        theme = self.theme
        panel_h = DETAILS_HEIGHT
        self.side_panel = Panel(self.side, theme, PAD // 2, PAD, SIDE_WIDTH - PAD, panel_h,
                                title="Bottle Details")

        self.detail_title = self.side.create_text(
            PAD, PAD + 40, text="Nothing selected", anchor="nw", fill=theme.text,
            font=("Segoe UI", 12, "bold"), width=SIDE_WIDTH - PAD * 2,
        )

        preview_y = PREVIEW_Y
        self.side.create_rectangle(
            PAD, preview_y, PAD + PREVIEW_WIDTH, preview_y + PREVIEW_HEIGHT,
            fill=theme.field, outline=theme.panel_soft,
        )
        self.preview_item = self.side.create_image(
            PAD + PREVIEW_WIDTH / 2, preview_y + PREVIEW_HEIGHT / 2, anchor="center"
        )
        self.preview_empty = self.side.create_text(
            PAD + PREVIEW_WIDTH / 2, preview_y + PREVIEW_HEIGHT / 2, text="No preview",
            fill=theme.text_muted, font=("Segoe UI", 9),
        )

        nav_y = NAV_Y
        Button(self.side, theme, PAD, nav_y, 54, 26, "<", lambda: self.cycle_preview(-1))
        self.preview_count = self.side.create_text(
            PAD + 78, nav_y + 13, text="0/0", anchor="w", fill=theme.text_muted,
            font=("Segoe UI", 9),
        )
        Button(self.side, theme, PAD + 122, nav_y, 54, 26, ">", lambda: self.cycle_preview(1))
        self.audio_button = Button(
            self.side, theme, PAD + PREVIEW_WIDTH - 128, nav_y, 128, 26,
            self.audio_label(), self.toggle_audio,
        )

        meta_y = META_Y
        self.detail_meta = self.side.create_text(
            PAD, meta_y, text="", anchor="nw", fill=theme.text_muted,
            font=("Segoe UI", 9), width=SIDE_WIDTH - PAD * 2,
        )
        self.detail_description = StatusLog(
            self.side, theme, PAD, DESC_Y, SIDE_WIDTH - PAD * 2, DESC_MIN,
            font=("Segoe UI", 9), follow_tail=False,
        )

        self.progress = ProgressBar(self.side, theme, PAD, PAD + panel_h + 12, SIDE_WIDTH - PAD * 2)
        self.log = StatusLog(self.side, theme, PAD // 2, PAD + panel_h + 34, SIDE_WIDTH - PAD, 160)
        self.side.bind("<Configure>", self.on_side_configure)

    def on_side_configure(self, event):
        """
        Give the description whatever vertical room the window can spare
        """
        spare = event.height - PAD - LOG_MIN - 34 - PAD
        panel_h = max(DETAILS_HEIGHT, spare)
        self.side_panel.place(PAD // 2, PAD, SIDE_WIDTH - PAD, panel_h)

        desc_h = max(DESC_MIN, (PAD + panel_h) - DESC_Y - 12)
        self.detail_description.place(PAD, DESC_Y, SIDE_WIDTH - PAD * 2, desc_h)

        self.progress.place(PAD, PAD + panel_h + 12, SIDE_WIDTH - PAD * 2)
        log_y = PAD + panel_h + 34
        self.log.place(PAD // 2, log_y, SIDE_WIDTH - PAD, max(48, event.height - log_y - PAD))

    def rescan(self):
        if getattr(self, "worker", None) and self.worker.busy:
            return
        keep = self.selected.mod_id if self.selected else None
        self.mods_dir.mkdir(parents=True, exist_ok=True)

        try:
            enabled = {mod["id"] for mod in list_enabled_mods(self.volume_path, self.taildata)}
        except GokonworksError as exc:
            enabled = set()
            self.say(str(exc), "danger")

        found: list[Bottle] = []
        for path in sorted(self.mods_dir.iterdir(), key=lambda item: item.name.lower()):
            if path.is_file() and path.suffix.lower() == PACKAGE_EXTENSION:
                found.append(self.read_package_bottle(path, enabled))

        self.stop_audio()
        self.clear_shelf()
        self.bottles = found
        self.draw_shelf()
        self.select(next((b for b in self.bottles if b.mod_id == keep), None))

        poured = sum(1 for bottle in self.bottles if bottle.enabled)
        self.say(f"{len(self.bottles)} bottle(s) on the shelf, {poured} poured.", "accent")
        dead = self.dead_bytes()
        if dead and not poured:
            self.say(
                f"{human_size(dead)} of appended data is still on the archive from earlier "
                "mods. Empty Every Bottle slices it off."
            )
        if not self.bottles:
            self.say(f"Build a mod with the Mod Creator or drop .at files into {self.mods_dir}.")

    def read_package_bottle(self, path: Path, enabled: set[str]) -> Bottle:
        try:
            manifest = read_package_manifest(path)
        except GokonworksError as exc:
            return Bottle(
                mod_id=path.name, name=path.stem, source=path,
                entries=0, enabled=path.name in enabled, problem=str(exc),
            )
        problem = ""
        if int(manifest.get("original_size", 0)) != int(self.taildata["original_size"]):
            problem = "Built for a different volume.dat"
        return Bottle(
            mod_id=path.name,
            name=manifest.get("name") or path.stem,
            source=path,
            entries=len(manifest.get("entries", [])),
            enabled=path.name in enabled,
            author=manifest.get("author", ""),
            genre=manifest.get("genre", ""),
            version=manifest.get("mod_version", ""),
            description=manifest.get("description", ""),
            image_count=len(manifest.get("images", [])),
            has_audio=bool(manifest.get("audio")),
            manifest=manifest,
            problem=problem,
        )

    def clear_shelf(self):
        self.shelf.delete("shelf")
        self.item_lookup.clear()
        self.selected = None

    def cell_size(self) -> tuple[int, int]:
        return (
            self.bottle_width + BOTTLE_GAP,
            self.bottle_height + SHELF_PLANK + SHELF_CAPTION,
        )

    def draw_shelf(self):
        """Build every canvas item once, later scrolling is pure yview"""
        self.clear_shelf()
        width = max(1, self.shelf.winfo_width())
        cell_w, cell_h = self.cell_size()
        self.columns = max(1, (width - SHELF_MARGIN * 2) // cell_w)
        rows = max(1, (len(self.bottles) + self.columns - 1) // self.columns)

        plank_left = SHELF_MARGIN - SHELF_OVERHANG
        plank_right = SHELF_MARGIN + self.columns * cell_w - BOTTLE_GAP + SHELF_OVERHANG
        for row in range(rows):
            plank_y = SHELF_MARGIN + row * cell_h + self.bottle_height
            self.shelf.create_rectangle(
                plank_left, plank_y, plank_right, plank_y + SHELF_PLANK,
                fill=self.theme.shelf, outline="", tags="shelf",
            )
            self.shelf.create_rectangle(
                plank_left, plank_y, plank_right, plank_y + 4,
                fill=self.theme.shelf_edge, outline="", tags="shelf",
            )
            self.shelf.create_line(
                plank_left, plank_y + SHELF_PLANK, plank_right, plank_y + SHELF_PLANK,
                fill=self.theme.panel_soft, tags="shelf",
            )

        for index, bottle in enumerate(self.bottles):
            row, column = divmod(index, self.columns)
            bottle.x = SHELF_MARGIN + column * cell_w
            bottle.y = SHELF_MARGIN + row * cell_h
            self.draw_bottle(bottle)

        height = SHELF_MARGIN * 2 + rows * cell_h
        self.shelf.configure(scrollregion=(0, 0, width, max(height, self.shelf.winfo_height())))

    def bottle_art(self, bottle: Bottle):
        return self.art[bottle.enabled]

    def draw_bottle(self, bottle: Bottle):
        centre_x = bottle.x + self.bottle_width / 2
        centre_y = bottle.y + self.bottle_height / 2

        bottle.halo_item = self.shelf.create_rectangle(
            bottle.x - 6, bottle.y - 6,
            bottle.x + self.bottle_width + 6, bottle.y + self.bottle_height + 6,
            outline="", width=2, tags="shelf",
        )
        bottle.image_item = self.shelf.create_image(
            centre_x, centre_y, image=self.bottle_art(bottle), anchor="center", tags="shelf"
        )
        lines = wrap_lines(self.label_font, bottle.name, self.label_width, max_lines=3)
        bottle.label_item = self.shelf.create_text(
            centre_x, centre_y + self.label_offset, text="\n".join(lines), anchor="center",
            fill="#3b3226", font=self.label_font, justify="center",
            width=self.label_width, tags="shelf",
        )
        caption = fit_line(self.label_font, bottle.problem or f"{bottle.entries} files",
                           self.bottle_width + BOTTLE_GAP - 4)
        self.shelf.create_text(
            centre_x, bottle.y + self.bottle_height + SHELF_PLANK + 6, text=caption,
            anchor="n", fill=self.theme.danger if bottle.problem else self.theme.text_muted,
            font=("Segoe UI", 7), width=self.bottle_width + BOTTLE_GAP, justify="center",
            tags="shelf",
        )

        for item in (bottle.image_item, bottle.label_item, bottle.halo_item):
            self.item_lookup[item] = bottle

    def refresh_bottle(self, bottle: Bottle):
        """State change is two itemconfigure calls"""
        self.shelf.itemconfigure(bottle.image_item, image=self.bottle_art(bottle))
        self.shelf.itemconfigure(
            bottle.halo_item,
            outline=self.theme.accent if bottle is self.selected else "",
        )

    def on_shelf_click(self, event):
        """
        One handler for the whole shelf, so clicking past the bottles clears
        """
        item = self.shelf.find_withtag("current")
        self.select(self.item_lookup.get(item[0]) if item else None)

    def on_wheel(self, event):
        self.shelf.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def on_shelf_configure(self, event):
        if (event.width, event.height) == self.shelf_size:
            return
        columns = max(1, (event.width - SHELF_MARGIN * 2) // self.cell_size()[0])
        self.shelf_size = (event.width, event.height)
        if columns == self.columns:
            region = self.shelf.cget("scrollregion")
            if region:
                _, _, _, bottom = (float(value) for value in region.split())
                self.shelf.configure(scrollregion=(0, 0, event.width, max(bottom, event.height)))
            return
        if self.resize_job is not None:
            self.after_cancel(self.resize_job)
        self.resize_job = self.after(50, self.redraw_after_resize)

    def redraw_after_resize(self):
        self.resize_job = None
        keep = self.selected
        self.draw_shelf()
        if keep is not None:
            self.selected = next((b for b in self.bottles if b.mod_id == keep.mod_id), None)
            if self.selected is not None:
                self.refresh_bottle(self.selected)

    def select(self, bottle: Bottle | None):
        previous = self.selected
        self.selected = bottle
        if previous is not None and any(item is previous for item in self.bottles):
            self.refresh_bottle(previous)

        self.preview_images = []
        self.preview_index = 0
        self.current_audio = None
        self.stop_audio()

        if bottle is None:
            self.side.itemconfigure(self.detail_title, text="Nothing selected")
            self.side.itemconfigure(self.detail_meta, text="")
            self.detail_description.clear()
            self.show_preview()
            return

        self.refresh_bottle(bottle)
        self.side.itemconfigure(self.detail_title, text=bottle.name)

        meta = [
            f"Status: {'Poured' if bottle.enabled else 'Empty'}",
            f"Files: {bottle.entries}",
        ]
        if bottle.author:
            meta.append(f"Author: {bottle.author}   Version: {bottle.version or '1'}")
        if bottle.genre:
            meta.append(f"Genre: {bottle.genre}")
        if bottle.problem:
            meta.append(f"Note: {bottle.problem}")
        self.side.itemconfigure(self.detail_meta, text="\n".join(meta))
        self.detail_description.set_text(bottle.description or "No description.")

        if bottle.manifest and not bottle.problem:
            try:
                self.preview_images = read_package_images(bottle.source, bottle.manifest)
                self.current_audio = read_package_audio(bottle.source, bottle.manifest)
            except (RecipeError, OSError) as exc:
                self.say(f"Couldn't read the extras in {bottle.name}: {exc}", "danger")

        self.show_preview()
        self.refresh_audio()

    def show_preview(self):
        if not self.preview_images:
            self.side.itemconfigure(self.preview_item, image="")
            self.side.itemconfigure(self.preview_empty, text="No preview")
            self.side.itemconfigure(self.preview_count, text="0/0")
            self.preview_photo = None
            return
        self.preview_index %= len(self.preview_images)
        try:
            rendered = letterbox(
                self.preview_images[self.preview_index],
                PREVIEW_WIDTH, PREVIEW_HEIGHT, self.theme.field,
            )
        except Exception as exc:
            self.side.itemconfigure(self.preview_item, image="")
            self.side.itemconfigure(self.preview_empty, text=f"Preview error\n{exc}")
            return
        self.preview_photo = ImageTk.PhotoImage(rendered)
        self.side.itemconfigure(self.preview_item, image=self.preview_photo)
        self.side.itemconfigure(self.preview_empty, text="")
        self.side.itemconfigure(
            self.preview_count, text=f"{self.preview_index + 1}/{len(self.preview_images)}"
        )

    def cycle_preview(self, delta: int):
        if not self.preview_images:
            return
        self.preview_index = (self.preview_index + delta) % len(self.preview_images)
        self.show_preview()

    def audio_label(self) -> str:
        return "Music: On" if self.audio_enabled else "Music: Off"

    def toggle_audio(self):
        self.audio_enabled = not self.audio_enabled
        self.audio_button.set_text(self.audio_label())
        self.settings["mod_audio"] = self.audio_enabled
        save_settings(PROJECT_ROOT, self.settings)
        self.refresh_audio()
        self.say(f"Theme tunes {'on' if self.audio_enabled else 'off'}.")

    def refresh_audio(self):
        if not self.audio_enabled or not self.current_audio:
            self.stop_audio()
            return
        if not self.player.available:
            self.say("Audio playback is unavailable on this system.", "danger")
            return
        if not self.player.play_loop_bytes(self.current_audio):
            self.say("Bundled audio is not a playable WAV.", "danger")

    def stop_audio(self):
        self.player.stop()

    def dead_bytes(self) -> int:
        """Appended data still sitting on the archive"""
        if not self.volume_path.is_file():
            return 0
        return max(0, self.volume_path.stat().st_size - int(self.taildata["original_size"]))

    def say(self, message: str, tone: str = "muted"):
        self.log.write(message, tone)

    def set_busy(self, busy: bool):
        for button in self.top_buttons:
            button.set_enabled(not busy)

    def open_creator(self):
        if self.creator_window is not None and self.creator_window.winfo_exists():
            self.creator_window.lift()
            self.creator_window.focus_force()
            return

        from .mixer import ModCreatorWindow

        self.creator_window = ModCreatorWindow(
            self, self.theme, self.taildata, self.mods_dir, on_built=lambda result: self.rescan()
        )

    def enable_selected(self):
        bottle = self.selected
        if bottle is None:
            messagebox.showinfo("Pour", "Pick a bottle off the shelf first.")
            return
        if bottle.enabled:
            self.say(f"{bottle.name} is already poured.", "danger")
            return
        if bottle.problem:
            messagebox.showerror("Pour", bottle.problem)
            return
        if not bottle.entries:
            messagebox.showerror("Pour", "This package has no files in it.")
            return

        volume_path, taildata, source = self.volume_path, self.taildata, bottle.source
        self.set_busy(True)
        self.say(f"Pouring {bottle.name}", "accent")

        def job(report):
            return apply_package(
                volume_path, taildata, source,
                progress=lambda done, total, message: report("progress", (done, total, message)),
            )

        self.worker.start(
            job,
            {"progress": self.on_progress, "done": self.on_enabled, "error": self.on_error},
            name="gokonworks-apply",
        )

    def disable_selected(self):
        bottle = self.selected
        if bottle is None:
            messagebox.showinfo("Empty", "Pick a bottle off the shelf first.")
            return
        if not bottle.enabled:
            self.say(f"{bottle.name} is already empty.", "danger")
            return

        volume_path, taildata, mod_id = self.volume_path, self.taildata, bottle.mod_id
        self.set_busy(True)
        self.say(f"Emptying {bottle.name}", "accent")

        def job(report):
            return disable_mod(
                volume_path, taildata, mod_id,
                progress=lambda done, total, message: report("progress", (done, total, message)),
            )

        self.worker.start(
            job,
            {"progress": self.on_progress, "done": self.on_disabled, "error": self.on_error},
            name="gokonworks-disable",
        )

    def disable_all(self):
        poured = any(bottle.enabled for bottle in self.bottles)
        dead = self.dead_bytes()
        if not poured and not dead:
            self.say("Every bottle is already empty and the archive is its vanilla size.")
            return

        lines = ["Put the whole archive index back to vanilla and cut every appended byte off the end?"]
        if poured:
            lines.append(f"This empties {sum(1 for b in self.bottles if b.enabled)} poured bottle(s).")
        if dead:
            lines.append(f"It also reclaims {human_size(dead)} of appended data.")
        if not messagebox.askyesno("Empty Every Bottle", "\n\n".join(lines)):
            return

        volume_path, taildata = self.volume_path, self.taildata
        self.set_busy(True)
        self.say("Emptying the whole shelf", "accent")

        def job(report):
            return disable_all_mods(
                volume_path, taildata,
                progress=lambda done, total, message: report("progress", (done, total, message)),
            )

        self.worker.start(
            job,
            {"progress": self.on_progress, "done": self.on_disabled_all, "error": self.on_error},
            name="gokonworks-disable-all",
        )

    def on_progress(self, payload):
        done, total, message = payload
        self.progress.set_fraction(done / total if total else 0.0)

    def on_enabled(self, result):
        self.set_busy(False)
        self.progress.set_fraction(1.0)
        bottle = next((b for b in self.bottles if b.mod_id == result["mod_id"]), None)
        if bottle:
            bottle.enabled = True
            self.refresh_bottle(bottle)
            self.select(bottle)
        self.say(
            f"Poured {result['mod_id']}, {result['entries']} files, "
            f"{human_size(result['appended_bytes'])} appended.",
            "ok",
        )
        log.info("Mod %s enabled from the shelf", result["mod_id"])

    def on_disabled(self, result):
        self.set_busy(False)
        self.progress.set_fraction(0.0)
        bottle = next((b for b in self.bottles if b.mod_id == result["mod_id"]), None)
        if bottle:
            bottle.enabled = False
            self.refresh_bottle(bottle)
            self.select(bottle)
        parts = [f"Emptied {result['mod_id']}, {result['restored_entries']} entries back to vanilla"]
        if result["handed_over_entries"]:
            parts.append(f"{result['handed_over_entries']} left to a mod that still owns them")
        self.say(", ".join(parts) + ".", "ok")
        if result["dead_bytes"]:
            self.say(
                f"{human_size(result['dead_bytes'])} of appended data is still on the archive. "
                "Emptying every bottle is what slices it back off.",
            )

    def on_disabled_all(self, result):
        self.set_busy(False)
        self.progress.set_fraction(0.0)
        for bottle in self.bottles:
            if bottle.enabled:
                bottle.enabled = False
                self.refresh_bottle(bottle)
        source = "index backup" if result["method"] == "backup" else "mod ledger"
        self.say(
            f"Emptied {result['disabled_mods']} mod(s). Restored {result['restored_entries']} "
            f"entries from the {source} and reclaimed {human_size(result['reclaimed_bytes'])}.",
            "ok",
        )

    def on_error(self, exc):
        self.set_busy(False)
        self.progress.set_fraction(0.0)
        self.say(f"{type(exc).__name__}: {exc}", "danger")
        messagebox.showerror("Mod Shelf", f"{type(exc).__name__}\n\n{exc}")

    def open_mods_folder(self):
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["explorer", str(self.mods_dir)])
        except OSError as exc:
            self.say(f"Could not open {self.mods_dir}: {exc}", "danger")

    def close(self):
        self.stop_audio()
        self.destroy()
