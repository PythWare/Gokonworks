"""Handles the GUI code"""

from __future__ import annotations

import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from .ledger import (
    TAILDATA_FILENAME,
    load_taildata,
    open_volume,
    unpack_status,
    unpack_volume,
)
from .refresh import (
    PIL_AVAILABLE,
    PIL_MESSAGE,
    Button,
    GlassGauge,
    Panel,
    ProgressBar,
    StatusLog,
    Worker,
    load_settings,
    pick_theme,
    save_settings,
)
from .returns import MODS_FILENAME, restore_vanilla
from .wetworks import (
    PROJECT_ROOT,
    GokonworksError,
    default_mods_dir,
    default_output_dir,
    default_volume_path,
    human_size,
    log,
)

WINDOW_WIDTH = 1120
WINDOW_HEIGHT = 720
MIN_WIDTH = 880
MIN_HEIGHT = 560

TOP_HEIGHT = 74
SIDE_WIDTH = 250
PAD = 16
BUTTON_HEIGHT = 38
BUTTON_GAP = 10
GAUGE_HEIGHT = 104


class CoreTools:
    """The Gokonworks hub"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.project_root = PROJECT_ROOT
        self.settings = load_settings(self.project_root)
        self.theme = pick_theme(self.settings.get("last_theme", ""))
        self.settings["last_theme"] = self.theme.key
        save_settings(self.project_root, self.settings)

        self.volume_path = Path(self.settings.get("volume_path") or default_volume_path())
        self.output_dir = Path(self.settings.get("output_dir") or default_output_dir())
        self.mod_window = None
        self.size = (0, 0)
        self.resize_job = None

        root.title("Gokonworks, Akiba's Trip Undead & Undressed Toolkit")
        root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        root.minsize(MIN_WIDTH, MIN_HEIGHT)
        root.configure(bg=self.theme.bg)

        if not PIL_AVAILABLE:
            messagebox.showerror("Gokonworks", PIL_MESSAGE)
            raise SystemExit(1)

        self.canvas = tk.Canvas(root, bg=self.theme.bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.worker = Worker(self.canvas)

        self.build()
        self.canvas.bind("<Configure>", self.on_configure)
        self.report_startup()

    def build(self):
        theme = self.theme

        self.title_item = self.canvas.create_text(
            PAD + 4, 18, text="Gokonworks", anchor="nw", fill=theme.text,
            font=("Segoe UI", 20, "bold"),
        )
        self.subtitle_item = self.canvas.create_text(
            PAD + 6, 48, text=f"Tonight's mocktail: {theme.name}", anchor="nw",
            fill=theme.accent, font=("Segoe UI", 9),
        )
        self.rule_item = self.canvas.create_line(0, TOP_HEIGHT, 10, TOP_HEIGHT, fill=theme.panel_soft)

        self.side_panel = Panel(self.canvas, theme, 0, 0, 10, 10, title="Mixing Station")
        self.main_panel = Panel(self.canvas, theme, 0, 0, 10, 10, title="Service Log")

        self.buttons = {
            "unpack": Button(self.canvas, theme, 0, 0, 10, BUTTON_HEIGHT,
                             "Unpack Archive", self.start_unpack, tone="accent"),
            "mods": Button(self.canvas, theme, 0, 0, 10, BUTTON_HEIGHT,
                           "Mod Manager", self.open_mod_manager),
            "verify": Button(self.canvas, theme, 0, 0, 10, BUTTON_HEIGHT,
                             "Verify Archive", self.start_verify),
            "reveal": Button(self.canvas, theme, 0, 0, 10, BUTTON_HEIGHT,
                             "Open Unpack Folder", self.open_output_folder),
            "archive": Button(self.canvas, theme, 0, 0, 10, BUTTON_HEIGHT,
                              "Choose volume.dat", self.choose_volume),
            "output": Button(self.canvas, theme, 0, 0, 10, BUTTON_HEIGHT,
                             "Choose Unpack Folder", self.choose_output),
            "restore": Button(self.canvas, theme, 0, 0, 10, BUTTON_HEIGHT,
                              "Restore Vanilla", self.restore_vanilla, tone="danger"),
        }
        self.button_order = ["unpack", "mods", "verify", "reveal", "archive", "output", "restore"]

        self.paths_item = self.canvas.create_text(
            0, 0, text="", anchor="nw", fill=theme.text_muted, font=("Segoe UI", 8), width=10,
        )
        self.progress = ProgressBar(self.canvas, theme, 0, 0, 10)
        self.progress_label = self.canvas.create_text(
            0, 0, text="Idle", anchor="nw", fill=theme.text_muted, font=("Segoe UI", 9), width=10,
        )
        self.status_log = StatusLog(self.canvas, theme, 0, 0, 10, 10)
        self.gauge = GlassGauge(self.canvas, theme, 0, 0, height=GAUGE_HEIGHT)
        self.gauge.set_caption("Checking")

        self.refresh_paths()

    def layout(self, width: int, height: int):
        """Position everything, only ever called when the canvas size changed"""
        self.canvas.coords(self.rule_item, 0, TOP_HEIGHT, width, TOP_HEIGHT)

        side_x = PAD
        side_y = TOP_HEIGHT + PAD
        side_h = height - side_y - PAD
        self.side_panel.place(side_x, side_y, SIDE_WIDTH, side_h)

        inner = SIDE_WIDTH - PAD * 2
        cursor = side_y + 44
        for key in self.button_order:
            self.buttons[key].place(side_x + PAD, cursor, inner, BUTTON_HEIGHT)
            cursor += BUTTON_HEIGHT + BUTTON_GAP

        self.canvas.coords(self.paths_item, side_x + PAD, cursor + 6)
        self.canvas.itemconfigure(self.paths_item, width=inner)

        main_x = side_x + SIDE_WIDTH + PAD
        main_w = width - main_x - PAD
        self.main_panel.place(main_x, side_y, main_w, side_h)

        gauge_x = main_x + main_w - self.gauge.width - PAD * 2
        gauge_y = side_y + side_h - self.gauge.height - 26
        self.gauge.place(gauge_x, gauge_y)

        log_x = main_x + PAD
        log_y = side_y + 44
        log_w = main_w - PAD * 2
        bar_w = max(60, main_w - self.gauge.width - PAD * 4)
        bar_y = side_y + side_h - 46
        self.status_log.place(log_x, log_y, log_w, max(80, gauge_y - 16 - log_y))

        self.progress.place(log_x, bar_y, bar_w)
        self.canvas.coords(self.progress_label, log_x, bar_y + 16)
        self.canvas.itemconfigure(self.progress_label, width=bar_w)

    def on_configure(self, event):
        if (event.width, event.height) == self.size:
            return
        self.size = (event.width, event.height)
        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(40, lambda: self.layout(*self.size))

    def refresh_paths(self):
        text = (
            f"Archive:\n{self.volume_path}\n\n"
            f"Unpack to:\n{self.output_dir}\n\n"
            f"Mods:\n{default_mods_dir()}"
        )
        self.canvas.itemconfigure(self.paths_item, text=text)

    def say(self, message: str, tone: str = "muted"):
        self.status_log.write(message, tone)

    def set_progress(self, fraction: float, message: str = ""):
        self.progress.set_fraction(fraction)
        if message:
            self.canvas.itemconfigure(self.progress_label, text=message)

    def set_busy(self, busy: bool):
        for key in self.button_order:
            self.buttons[key].set_enabled(not busy)

    def taildata_path(self) -> Path:
        return self.output_dir / TAILDATA_FILENAME

    def report_startup(self):
        self.say(f"Gokonworks ready. Fuji is serving {self.theme.name}.", "accent")
        if self.volume_path.is_file():
            size = self.volume_path.stat().st_size
            self.say(f"Archive: {self.volume_path.name}, {human_size(size)}")
        else:
            self.say(f"No archive at {self.volume_path}", "danger")
            self.say("Use Choose volume.dat to point at the game archive.", "muted")
        self.check_unpack()

    def check_unpack(self, announce: bool = True):
        if self.worker.busy:
            return
        output_dir = self.output_dir

        def job(report):
            return unpack_status(output_dir)

        self.worker.start(
            job,
            {"done": lambda status: self.on_unpack_status(status, announce),
             "error": self.on_job_error},
            name="gokonworks-unpack-check",
        )

    def on_unpack_status(self, status: dict, announce: bool = True):
        if status["complete"]:
            self.gauge.set_fraction(1.0)
            self.gauge.set_caption("Poured", tone="ok")
            if announce:
                self.say(f"Unpack found: {status['files']} files in {status['output_dir']}", "ok")
        elif status["taildata"]:
            self.gauge.set_fraction(status["fraction"])
            self.gauge.set_caption("Short pour")
            if announce:
                self.say(
                    f"The unpack in {status['output_dir']} is incomplete, only "
                    f"{status['present']} of {status['checked']} sampled files are still "
                    "there. Unpack again before building mods.",
                    "danger",
                )
        else:
            self.gauge.set_fraction(0.0)
            self.gauge.set_caption("Empty")
            if announce:
                self.say("No unpack on disk yet. Unpack the archive before building mods.")

    def start_unpack(self):
        if self.worker.busy:
            return
        if not self.volume_path.is_file():
            messagebox.showwarning("Unpack", f"No archive at:\n{self.volume_path}")
            return
        if self.taildata_path().is_file() and not messagebox.askyesno(
            "Unpack",
            f"{self.output_dir} already holds an unpack.\n\nUnpack again and overwrite it?",
        ):
            return

        volume_path = self.volume_path
        output_dir = self.output_dir
        self.set_busy(True)
        self.gauge.set_fraction(0.0)
        self.gauge.set_caption("Pouring")
        self.say(f"Unpacking {volume_path.name} to {output_dir}", "accent")
        self.set_progress(0.0, "Starting unpack")

        def job(report):
            def progress(done, total, message):
                report("progress", (done, total, message))

            return unpack_volume(volume_path, output_dir, progress=progress)

        self.worker.start(
            job,
            {
                "progress": self.on_unpack_progress,
                "done": self.on_unpack_done,
                "error": self.on_job_error,
            },
            name="gokonworks-unpack",
        )

    def on_unpack_progress(self, payload):
        done, total, message = payload
        fraction = done / total if total else 0.0
        self.gauge.set_fraction(fraction)
        self.set_progress(fraction, f"{done}/{total}  {message}")

    def on_unpack_done(self, result):
        self.set_busy(False)
        self.gauge.set_fraction(1.0)
        self.gauge.set_caption("Poured", tone="ok")
        self.set_progress(1.0, f"Unpacked {result['files']} files")
        self.say(
            f"Unpacked {result['files']} of {result['available_files']} files "
            f"({result['bytes']}) to {result['output_dir']}",
            "ok",
        )
        self.say(f"Taildata written to {result['taildata_path']}", "ok")
        self.settings["output_dir"] = str(self.output_dir)
        save_settings(self.project_root, self.settings)

    def on_job_error(self, exc):
        self.set_busy(False)
        self.gauge.set_caption("Spilled")
        self.set_progress(0.0, "Failed")
        self.say(f"{type(exc).__name__}: {exc}", "danger")
        messagebox.showerror("Gokonworks", f"{type(exc).__name__}\n\n{exc}")

    def start_verify(self):
        if self.worker.busy:
            return
        if not self.volume_path.is_file():
            messagebox.showwarning("Verify", f"No archive at:\n{self.volume_path}")
            return
        volume_path = self.volume_path
        self.set_busy(True)
        self.say("Verifying archive structure", "accent")
        self.set_progress(0.0, "Verifying")

        def job(report):
            with open_volume(volume_path) as volume:
                return volume.check()

        self.worker.start(
            job,
            {"done": self.on_verify_done, "error": self.on_job_error},
            name="gokonworks-verify",
        )

    def on_verify_done(self, summary):
        self.set_busy(False)
        self.set_progress(1.0, "Verify complete")
        self.say(
            f"{summary['entries']} entries, {summary['compressed_entries']} zlib, "
            f"{summary['stored_entries']} stored",
            "text",
        )
        self.say(
            f"Archive is {human_size(summary['file_size'])}, "
            f"{human_size(summary['appended_bytes'])} of that is appended mod data"
        )
        self.say(f"Free TOC slots for new files: {summary['free_toc_slots']}")
        problems = (
            len(summary["bad_entries"])
            + len(summary["unaligned_entries"])
            + len(summary["bad_hashes"])
        )
        if problems:
            self.say(
                f"{len(summary['bad_entries'])} bad entries, "
                f"{len(summary['unaligned_entries'])} unaligned, "
                f"{len(summary['bad_hashes'])} hash mismatches",
                "danger",
            )
        else:
            self.say("Every entry checks out, hashes included.", "ok")
        if not summary["hash_sorted"]:
            self.say("TOC is not sorted by hash, the game can't look files up.", "danger")

    def open_mod_manager(self):
        if self.mod_window is not None and self.mod_window.winfo_exists():
            self.mod_window.lift()
            self.mod_window.focus_force()
            return
        taildata_path = self.taildata_path()
        if not taildata_path.is_file():
            messagebox.showinfo(
                "Mod Manager",
                "No taildata yet.\n\nUnpack the archive first so the mod manager knows "
                "where every file lives.",
            )
            return
        try:
            taildata = load_taildata(taildata_path)
        except GokonworksError as exc:
            messagebox.showerror("Mod Manager", str(exc))
            return

        from .bar import ModManagerWindow

        self.mod_window = ModManagerWindow(self.root, self.theme, self.volume_path, taildata)
        self.say("Opened the mod shelf.", "accent")

    def open_output_folder(self):
        target = self.output_dir if self.output_dir.is_dir() else self.project_root
        try:
            subprocess.Popen(["explorer", str(target)])
        except OSError as exc:
            self.say(f"Could not open {target}: {exc}", "danger")

    def choose_volume(self):
        chosen = filedialog.askopenfilename(
            title="Select the Akiba's Trip volume archive",
            initialdir=str(self.volume_path.parent),
            filetypes=[("Volume archive", "*.dat"), ("All files", "*.*")],
        )
        if not chosen:
            return
        self.volume_path = Path(chosen)
        self.settings["volume_path"] = str(self.volume_path)
        save_settings(self.project_root, self.settings)
        self.refresh_paths()
        self.say(f"Archive set to {self.volume_path}", "ok")

    def choose_output(self):
        chosen = filedialog.askdirectory(
            title="Select the unpack folder", initialdir=str(self.output_dir.parent)
        )
        if not chosen:
            return
        self.output_dir = Path(chosen)
        self.settings["output_dir"] = str(self.output_dir)
        save_settings(self.project_root, self.settings)
        self.refresh_paths()
        self.say(f"Unpack folder set to {self.output_dir}", "ok")
        self.check_unpack()

    def restore_vanilla(self):
        if self.worker.busy:
            return
        if not messagebox.askyesno(
            "Restore Vanilla",
            "Rewrite the archive index from the backup and cut off every appended byte?\n\n"
            "This undoes all mods, including any the ledger has lost track of.",
        ):
            return
        try:
            result = restore_vanilla(self.volume_path)
        except GokonworksError as exc:
            self.say(str(exc), "danger")
            messagebox.showerror("Restore Vanilla", str(exc))
            return
        self.say(f"Restored {self.volume_path.name} to {human_size(result['restored_size'])}", "ok")
        self.say(f"Mod ledger cleared ({MODS_FILENAME})")
        log.info("Vanilla restore run from the hub")
