"""Handles the GUI code"""

from __future__ import annotations
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from .ledger import (
    TAILDATA_FILENAME,
    open_volume,
    unpack_status,
    unpack_volume,
)
from .patcher import EXE_FILENAME, PatchError, apply_patches, read_state
from .refresh import (
    PIL_AVAILABLE,
    PIL_MESSAGE,
    Button,
    GlassGauge,
    Panel,
    ProgressBar,
    StatusLog,
    Worker,
    bring_forward,
    load_settings,
    own_window,
    pick_theme,
    save_settings,
)
from .wetworks import (
    MODS_FOLDER,
    PROJECT_ROOT,
    backup_path,
    default_backups_dir,
    default_mods_dir,
    default_output_dir,
    default_volume_path,
    ensure_folders,
    human_size,
    log,
    make_backup,
    needs_full_backup,
    relocate_stray_backup,
    restore_from_backup,
)

WINDOW_WIDTH = 1120
WINDOW_HEIGHT = 720
MIN_WIDTH = 880
MIN_HEIGHT = 880

TOP_HEIGHT = 74
SIDE_WIDTH = 250
PAD = 16
BUTTON_HEIGHT = 34
BUTTON_GAP = 8
GAUGE_HEIGHT = 104

PATCH_WIDTH = 620
PATCH_HEIGHT = 470

INTRO_CYCLE = ("vanilla", "keep_op", "skip_all")
INTRO_LABELS = {
    "vanilla": "Intro: Everything",
    "keep_op": "Intro: Skip logos, keep OP",
    "skip_all": "Intro: Skip logos and intro",
    "custom": "Intro: Unrecognised value",
}


class CoreTools:
    """Gokonworks hub"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.project_root = PROJECT_ROOT
        self.settings = load_settings(self.project_root)
        self.theme = pick_theme(self.settings.get("last_theme", ""))
        self.settings["last_theme"] = self.theme.key
        save_settings(self.project_root, self.settings)

        self.volume_path = Path(self.settings.get("volume_path") or default_volume_path())
        self.output_dir = Path(self.settings.get("output_dir") or default_output_dir())
        self.exe_path = Path(self.settings.get("exe_path") or "")
        self.patch_window = None
        self.size = (0, 0)
        self.resize_job = None

        root.title("Gokonworks, Akiba's Trip Undead & Undressed Toolkit")
        root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        root.minsize(MIN_WIDTH, MIN_HEIGHT)
        root.configure(bg=self.theme.bg)

        if not PIL_AVAILABLE:
            messagebox.showerror("Gokonworks", PIL_MESSAGE, parent=self.root)
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
            "patch": Button(self.canvas, theme, 0, 0, 10, BUTTON_HEIGHT,
                            "Patch EXE", self.open_patch_window),
            "unpatch": Button(self.canvas, theme, 0, 0, 10, BUTTON_HEIGHT,
                              "Revert EXE", self.revert_game_exe, tone="danger"),
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
        self.button_order = [
            "unpack", "patch", "unpatch", "verify", "reveal",
            "archive", "output", "restore",
        ]

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
        chosen = bool(self.exe_path.name)
        exe = self.exe_path if chosen else "not chosen yet"
        loose = self.exe_path.parent / MODS_FOLDER if chosen else default_mods_dir()
        text = (
            f"Archive:\n{self.volume_path}\n\n"
            f"Unpack to:\n{self.output_dir}\n\n"
            f"Game exe:\n{exe}\n\n"
            f"Loose files:\n{loose}\n\n"
            f"Backups:\n{default_backups_dir()}"
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

    def prepare_folders(self):
        for key, folder in ensure_folders(self.exe_path).items():
            self.say(f"Made the {key} folder: {folder}")
        moved = relocate_stray_backup(self.volume_path)
        if moved:
            self.say(f"Moved the existing archive backup into {moved.parent}", "ok")

    def report_startup(self):
        self.say(f"Gokonworks ready. Fuji is serving {self.theme.name}.", "accent")
        self.prepare_folders()
        if self.volume_path.is_file():
            size = self.volume_path.stat().st_size
            self.say(f"Archive: {self.volume_path.name}, {human_size(size)}")
        else:
            self.say(f"No archive at {self.volume_path}", "danger")
            self.say("Use Choose volume.dat to point at the game archive.", "muted")
        if needs_full_backup(self.volume_path):
            self.start_backup()
        else:
            self.check_unpack()

    def start_backup(self):
        if self.worker.busy:
            return
        volume_path = self.volume_path
        target = backup_path(volume_path)
        self.set_busy(True)
        self.say(f"Backing up {volume_path.name} to {target.name}, this runs once.", "accent")
        self.set_progress(0.0, "Backing up the archive")

        def job(report):
            return make_backup(
                volume_path,
                progress=lambda done, total, message: report("progress", (done, total, message)),
            )

        self.worker.start(
            job,
            {
                "progress": self.on_backup_progress,
                "done": self.on_backup_done,
                "error": self.on_backup_error,
            },
            name="gokonworks-backup",
        )

    def on_backup_progress(self, payload):
        done, total, message = payload
        self.set_progress(done / total if total else 0.0, message)

    def on_backup_done(self, result):
        self.set_busy(False)
        self.set_progress(1.0, "Backup ready")
        self.say(
            f"Backup ready: {Path(result['backup']).name}, {human_size(result['original_size'])}. "
            "Keep it, it's the only way any mod gets undone.",
            "ok",
        )
        self.check_unpack()

    def on_backup_error(self, exc):
        self.set_busy(False)
        self.set_progress(0.0, "Backup failed")
        self.say(f"Couldn't back the archive up: {exc}", "danger")
        messagebox.showerror(
            "Gokonworks",
            f"The archive backup couldnt be written.\n\n{exc}\n\n"
            "Mods can't be safely enabled until it exists.",
            parent=self.root,
        )
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
            messagebox.showwarning("Unpack", f"No archive at:\n{self.volume_path}", parent=self.root)
            return
        if self.taildata_path().is_file() and not messagebox.askyesno(
            "Unpack",
            f"{self.output_dir} already holds an unpack.\n\nUnpack again and overwrite it?", parent=self.root
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
        messagebox.showerror("Gokonworks", f"{type(exc).__name__}\n\n{exc}", parent=self.root)

    def start_verify(self):
        if self.worker.busy:
            return
        if not self.volume_path.is_file():
            messagebox.showwarning("Verify", f"No archive at:\n{self.volume_path}", parent=self.root)
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
            self.say("TOC isnt sorted by hash, the game can't look files up.", "danger")

    def choose_exe(self) -> Path | None:
        start = self.exe_path.parent if self.exe_path.name else self.volume_path.parent
        chosen = filedialog.askopenfilename(
            title=f"Select the game's {EXE_FILENAME}",
            initialdir=str(start),
            filetypes=[("Akiba's Trip executable", EXE_FILENAME), ("Executables", "*.exe")],
            parent=self.root,
        )
        if not chosen:
            return None
        self.exe_path = Path(chosen)
        self.settings["exe_path"] = str(self.exe_path)
        save_settings(self.project_root, self.settings)
        for key, folder in ensure_folders(self.exe_path).items():
            self.say(f"Made the {key} folder: {folder}")
        self.refresh_paths()
        return self.exe_path

    def open_patch_window(self):
        if self.patch_window is not None and self.patch_window.winfo_exists():
            bring_forward(self.patch_window)
            return
        exe_path = self.choose_exe()
        if exe_path is None:
            return
        try:
            state = read_state(exe_path)
        except (PatchError, OSError) as exc:
            self.say(f"Can't read that exe: {exc}", "danger")
            messagebox.showerror("Patch EXE", str(exc), parent=self.root)
            return

        build = state["build"] or "an unrecognised build"
        self.say(f"Opened the patch bench on {exe_path.name} ({build}).", "accent")
        self.patch_window = PatchWindow(self.root, self.theme, exe_path, state, self.say)

    def revert_game_exe(self):
        exe_path = self.choose_exe()
        if exe_path is None:
            return
        try:
            state = read_state(exe_path)
        except (PatchError, OSError) as exc:
            self.say(f"Can't read that exe: {exc}", "danger")
            messagebox.showerror("Revert EXE", str(exc), parent=self.root)
            return

        active = describe_active(state)
        if not active:
            self.say(f"{exe_path.name} has no patches to undo.", "ok")
            messagebox.showinfo(
                "Revert EXE", f"{exe_path.name} is already vanilla.", parent=self.root
            )
            return
        if not messagebox.askyesno(
            "Revert EXE",
            f"{exe_path.name} currently has:\n\n  " + "\n  ".join(active) +
            "\n\nPut all of it back to vanilla?",
            parent=self.root,
        ):
            return

        try:
            result = apply_patches(
                exe_path, loose=False, intro="vanilla", clothing=False, backup=False
            )
        except (PatchError, OSError) as exc:
            self.say(f"Revert failed: {exc}", "danger")
            messagebox.showerror("Revert EXE", str(exc), parent=self.root)
            return

        if self.patch_window is not None and self.patch_window.winfo_exists():
            self.patch_window.adopt(result)
        self.say(f"Reverted every patch in {exe_path.name}.", "ok")
        if result["build"]:
            self.say(f"It's vanilla {result['build']} exe again.")
        else:
            self.say("Patched bytes are back, though the exe still differs from vanilla.")

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
            filetypes=[("Volume archive", "*.dat"), ("All files", "*.*")], parent=self.root
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
            title="Select the unpack folder", initialdir=str(self.output_dir.parent), parent=self.root
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
        target = backup_path(self.volume_path)
        if not target.is_file():
            messagebox.showerror(
                "Restore Vanilla",
                f"Theres no backup at {target.name}, so there's nothing to restore from.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Restore Vanilla",
            f"Copy {target.name} back over {self.volume_path.name}?\n\n"
            "This turns the archive back to vanilla.", parent=self.root
        ):
            return

        volume_path = self.volume_path
        self.set_busy(True)
        self.say(f"Restoring {volume_path.name} from {target.name}", "accent")
        self.set_progress(0.0, "Restoring")

        def job(report):
            return restore_from_backup(
                volume_path,
                progress=lambda done, total, message: report("progress", (done, total, message)),
            )

        self.worker.start(
            job,
            {
                "progress": self.on_backup_progress,
                "done": self.on_restore_done,
                "error": self.on_job_error,
            },
            name="gokonworks-restore",
        )

    def on_restore_done(self, result):
        self.set_busy(False)
        self.set_progress(1.0, "Restored")
        self.say(
            f"Restored {self.volume_path.name} to {human_size(result['restored_size'])}", "ok"
        )
        log.info("Vanilla restore run from the hub")


def describe_active(state: dict) -> list[str]:
    active = []
    loose = state["loose"]
    if loose["status"] != "off":
        root = loose["root"] or "?"
        active.append(f"loose file loading from {root}")
    if state["intro"]["status"] != "vanilla":
        active.append(INTRO_LABELS.get(state["intro"]["status"], "an intro change").lower())
    if state["clothing"]["status"] != "off":
        active.append("the clothing limit raise")
    return active


class PatchWindow(tk.Toplevel):

    def __init__(self, master, theme, exe_path: Path, state: dict, echo):
        super().__init__(master)
        self.theme = theme
        self.exe_path = Path(exe_path)
        self.state = state
        self.echo = echo

        self.title(f"EXE Patches, {self.exe_path.name}")
        self.geometry(f"{PATCH_WIDTH}x{PATCH_HEIGHT}")
        self.resizable(False, False)
        self.configure(bg=theme.bg)
        own_window(self, master)

        self.canvas = tk.Canvas(self, bg=theme.bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        inner = PATCH_WIDTH - PAD * 4
        self.panel = Panel(self.canvas, theme, PAD, PAD, PATCH_WIDTH - PAD * 2, 232,
                           title="Executable Patches")
        self.header = self.canvas.create_text(
            PAD * 2, PAD + 40, text="", anchor="nw", fill=theme.text_muted,
            font=("Segoe UI", 8), width=inner,
        )

        row = PAD + 84
        self.buttons = {
            "loose": Button(self.canvas, theme, PAD * 2, row, inner, BUTTON_HEIGHT,
                            "", self.toggle_loose, tone="accent"),
            "intro": Button(self.canvas, theme, PAD * 2, row + 44, inner, BUTTON_HEIGHT,
                            "", self.cycle_intro),
            "clothing": Button(self.canvas, theme, PAD * 2, row + 88, inner, BUTTON_HEIGHT,
                               "", self.toggle_clothing),
        }
        self.hint = self.canvas.create_text(
            PAD * 2, row + 130, text="Click a row to change it. Every click writes to the exe.",
            anchor="nw", fill=theme.text_muted, font=("Segoe UI", 8), width=inner,
        )

        self.log = StatusLog(self.canvas, theme, PAD, 264, PATCH_WIDTH - PAD * 2,
                             PATCH_HEIGHT - 264 - PAD - 44)
        Button(self.canvas, theme, PATCH_WIDTH - PAD - 120, PATCH_HEIGHT - PAD - 34,
               120, BUTTON_HEIGHT, "Close", self.destroy)

        self.refresh()
        self.say(f"Reading {self.exe_path}")
        self.report_loose()

    def say(self, message: str, tone: str = "muted"):
        self.log.write(message, tone)

    def adopt(self, state: dict):
        self.state = state
        self.refresh()
        self.say("The hub reverted the exe, rows refreshed.", "ok")

    def refresh(self):
        state = self.state
        build = state["build"] or "unrecognised build, patched by signature"
        self.canvas.itemconfigure(
            self.header, text=f"{self.exe_path}\n{build}"
        )

        loose = state["loose"]
        if loose["status"] == "on":
            label = f"Loose Files: On, reading {loose['root']}"
        elif loose["status"] == "off":
            label = "Loose Files: Off, everything from volume.dat"
        else:
            label = "Loose Files: Half patched, click to fix"
        self.buttons["loose"].set_text(label)
        self.buttons["intro"].set_text(INTRO_LABELS.get(state["intro"]["status"], "Intro: ?"))

        clothing = state["clothing"]["status"]
        self.buttons["clothing"].set_text({
            "on": "Clothing Limit: Raised",
            "off": "Clothing Limit: Vanilla caps",
        }.get(clothing, "Clothing Limit: Half patched, click to fix"))

    def apply(self, **changes):
        try:
            result = apply_patches(self.exe_path, **changes)
        except (PatchError, OSError) as exc:
            self.say(f"{exc}", "danger")
            messagebox.showerror("EXE Patches", str(exc), parent=self)
            return None
        self.state = result
        self.refresh()
        if result["backup"]:
            self.say(f"Kept the original as {Path(result['backup']).name}")
        elif not result["changed"]:
            self.say("Nothing needed changing.")
        self.echo(f"{self.exe_path.name}: " + (", ".join(describe_active(result)) or "vanilla"), "ok")
        return result

    def toggle_loose(self):
        turning_on = self.state["loose"]["status"] != "on"
        result = self.apply(loose=turning_on)
        if result is None:
            return
        if turning_on:
            self.say(f"Loose file loading on, root {result['loose']['root']}", "ok")
            self.report_loose()
        else:
            self.say("Loose file loading off, everything comes from volume.dat again.", "ok")

    def cycle_intro(self):
        current = self.state["intro"]["status"]
        nxt = INTRO_CYCLE[(INTRO_CYCLE.index(current) + 1) % len(INTRO_CYCLE)] \
            if current in INTRO_CYCLE else "vanilla"
        result = self.apply(intro=nxt)
        if result is not None:
            self.say(INTRO_LABELS[result["intro"]["status"]], "ok")

    def toggle_clothing(self):
        turning_on = self.state["clothing"]["status"] != "on"
        result = self.apply(clothing=turning_on)
        if result is None:
            return
        if turning_on:
            self.say("Clothing loops now read their count from the game's own struct.", "ok")
        else:
            self.say("Clothing caps back to vanilla.", "ok")

    def report_loose(self):
        if self.state["loose"]["status"] != "on":
            return
        folder = self.state["loose_dir"]
        self.say(f"Loose files go in {folder}", "accent")
        if not self.state["loose_dir_exists"]:
            self.say("That folder doesnt exist yet, make it beside the exe.", "danger")
        self.say(f"Files keep their archive paths, an example is "
                 f"{MODS_FOLDER}/lang_us/ui/texture/<name>.")
        self.say("Anything not there is read from volume.dat, so leave the archive alone.")
