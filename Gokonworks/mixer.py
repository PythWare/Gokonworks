"""
Handles the Mod Creator, the station where a folder of edited files becomes a .at
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from .recipe import GENRES, MAX_PREVIEW_IMAGES, PACKAGE_EXTENSION, create_package
from .refresh import Button, Panel, ProgressBar, StatusLog, Theme, Worker
from .wetworks import GokonworksError, human_size, log

WINDOW_WIDTH = 760
WINDOW_HEIGHT = 780

PAD = 18
ROW_HEIGHT = 30
LABEL_WIDTH = 104
BUTTON_HEIGHT = 30


class ModCreatorWindow(tk.Toplevel):
    """Fill in the card, pick the previews, bottle it"""

    def __init__(self, master, theme: Theme, taildata: dict, mods_dir: Path, on_built=None):
        super().__init__(master)
        self.theme = theme
        self.taildata = taildata
        self.mods_dir = Path(mods_dir)
        self.on_built = on_built

        self.source_folder: Path | None = None
        self.image_paths: list[Path] = []
        self.audio_path: Path | None = None
        self.embedded: list[int] = []

        self.title("Gokonworks, Mod Creator")
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.resizable(False, False)
        self.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.maxsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.configure(bg=theme.bg)

        self.canvas = tk.Canvas(self, bg=theme.bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.worker = Worker(self.canvas)

        self.build()
        self.protocol("WM_DELETE_WINDOW", self.close)

    def entry(self, x, y, width, value="") -> tk.Entry:
        widget = tk.Entry(
            self.canvas, bg=self.theme.field, fg=self.theme.text,
            insertbackground=self.theme.accent, relief="flat", highlightthickness=1,
            highlightbackground=self.theme.panel_soft, highlightcolor=self.theme.accent,
            font=("Segoe UI", 10),
        )
        if value:
            widget.insert(0, value)
        self.embedded.append(
            self.canvas.create_window(x, y, window=widget, anchor="nw", width=width, height=26)
        )
        return widget

    def label(self, x, y, text, width=LABEL_WIDTH):
        self.canvas.create_text(
            x, y + 5, text=text, anchor="nw", fill=self.theme.text_muted,
            font=("Segoe UI", 9), width=width,
        )

    def build(self):
        theme = self.theme
        width = WINDOW_WIDTH
        inner = width - PAD * 2

        self.canvas.create_text(
            PAD, 18, text="Mod Creator", anchor="nw", fill=theme.text,
            font=("Segoe UI", 17, "bold"),
        )
        self.canvas.create_text(
            PAD + 2, 46, text="Bottle a folder of edited files into one .at package",
            anchor="nw", fill=theme.accent, font=("Segoe UI", 9), width=inner,
        )

        top = 78
        self.form_panel = Panel(self.canvas, theme, PAD, top, inner, 440, title="Recipe Card")

        x = PAD + 16
        field_x = x + LABEL_WIDTH
        field_w = inner - LABEL_WIDTH - 150
        y = top + 44

        self.label(x, y, "Source folder")
        self.source_entry = self.entry(field_x, y, field_w)
        self.source_entry.configure(state="readonly", readonlybackground=theme.field)
        Button(self.canvas, theme, field_x + field_w + 10, y, 118, BUTTON_HEIGHT - 4,
               "Browse", self.choose_source)
        y += ROW_HEIGHT + 8

        self.label(x, y, "Mod name")
        self.name_entry = self.entry(field_x, y, field_w)
        y += ROW_HEIGHT + 8

        self.label(x, y, "Author")
        self.author_entry = self.entry(field_x, y, int(field_w * 0.55))
        self.canvas.create_text(
            field_x + int(field_w * 0.55) + 14, y + 5, text="Version", anchor="nw",
            fill=theme.text_muted, font=("Segoe UI", 9),
        )
        self.version_entry = self.entry(
            field_x + int(field_w * 0.55) + 72, y, field_w - int(field_w * 0.55) - 72, "1"
        )
        y += ROW_HEIGHT + 8

        self.label(x, y, "Genre")
        self.genre_var = tk.StringVar(value=GENRES[0])
        genre_menu = tk.OptionMenu(self.canvas, self.genre_var, *GENRES)
        genre_menu.configure(
            bg=theme.panel_soft, fg=theme.text, activebackground=theme.accent,
            activeforeground=theme.bg, relief="flat", highlightthickness=0,
            font=("Segoe UI", 9), anchor="w",
        )
        genre_menu["menu"].configure(bg=theme.panel, fg=theme.text, relief="flat")
        self.embedded.append(
            self.canvas.create_window(field_x, y, window=genre_menu, anchor="nw",
                                      width=200, height=26)
        )
        y += ROW_HEIGHT + 8

        self.label(x, y, "Description")
        self.description_text = tk.Text(
            self.canvas, bg=theme.field, fg=theme.text, insertbackground=theme.accent,
            relief="flat", highlightthickness=1, highlightbackground=theme.panel_soft,
            highlightcolor=theme.accent, font=("Segoe UI", 9), wrap="word",
        )
        self.embedded.append(
            self.canvas.create_window(field_x, y, window=self.description_text, anchor="nw",
                                      width=field_w, height=104)
        )
        y += 118

        self.label(x, y, "Preview images")
        self.images_item = self.canvas.create_text(
            field_x, y + 5, text="None chosen", anchor="nw", fill=theme.text_muted,
            font=("Segoe UI", 9), width=field_w,
        )
        Button(self.canvas, theme, field_x + field_w + 10, y, 118, BUTTON_HEIGHT - 4,
               "Add Images", self.choose_images)
        y += ROW_HEIGHT + 8

        self.label(x, y, "Theme WAV")
        self.audio_item = self.canvas.create_text(
            field_x, y + 5, text="None chosen", anchor="nw", fill=theme.text_muted,
            font=("Segoe UI", 9), width=field_w,
        )
        Button(self.canvas, theme, field_x + field_w + 10, y, 118, BUTTON_HEIGHT - 4,
               "Choose WAV", self.choose_audio)
        y += ROW_HEIGHT + 10

        self.canvas.create_text(
            x, y, text=f"Packages are written into {self.mods_dir}", anchor="nw",
            fill=theme.text_muted, font=("Segoe UI", 8), width=inner - 32,
        )

        actions_y = top + 456
        self.build_button = Button(
            self.canvas, theme, PAD, actions_y, 176, BUTTON_HEIGHT + 6,
            "Bottle It", self.build_package, tone="accent",
        )
        Button(self.canvas, theme, PAD + 186, actions_y, 150, BUTTON_HEIGHT + 6,
               "Clear Extras", self.clear_extras)
        self.progress = ProgressBar(self.canvas, theme, PAD + 348, actions_y + 14, inner - 348)

        self.status_log = StatusLog(
            self.canvas, theme, PAD, actions_y + BUTTON_HEIGHT + 20, inner,
            WINDOW_HEIGHT - (actions_y + BUTTON_HEIGHT + 20) - PAD,
        )
        self.say("Pick the folder holding your edited files to begin.", "accent")
        self.say(
            "The folder has to keep the layout the unpack made, for example "
            "lang_us/ui/texture/whatever.phyre because that is how each file is "
            "matched back to its slot in the archive."
        )

    def say(self, message: str, tone: str = "muted"):
        self.status_log.write(message, tone)

    def choose_source(self):
        chosen = filedialog.askdirectory(title="Select the folder holding your edited files")
        if not chosen:
            return
        self.source_folder = Path(chosen)
        self.source_entry.configure(state="normal")
        self.source_entry.delete(0, "end")
        self.source_entry.insert(0, str(self.source_folder))
        self.source_entry.configure(state="readonly")
        if not self.name_entry.get().strip():
            self.name_entry.insert(0, self.source_folder.name)

        from .recipe import collect_source_files

        try:
            matched = collect_source_files(self.source_folder, self.taildata)
        except GokonworksError as exc:
            self.say(str(exc), "danger")
            return
        if matched:
            self.say(f"{len(matched)} file(s) in this folder match the archive.", "ok")
            for path, _ in matched[:6]:
                self.say(f"   {path}")
            if len(matched) > 6:
                self.say(f"   and {len(matched) - 6} more")
        else:
            self.say("Nothing in this folder matches the unpacked layout.", "danger")

    def choose_images(self):
        chosen = filedialog.askopenfilenames(
            title="Select preview images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp"), ("All files", "*.*")],
        )
        if not chosen:
            return
        for path in chosen:
            if len(self.image_paths) >= MAX_PREVIEW_IMAGES:
                self.say(f"Only the first {MAX_PREVIEW_IMAGES} previews are kept.", "danger")
                break
            self.image_paths.append(Path(path))
        self.refresh_extras()

    def choose_audio(self):
        chosen = filedialog.askopenfilename(
            title="Select a WAV to bundle", filetypes=[("WAV audio", "*.wav")]
        )
        if not chosen:
            return
        self.audio_path = Path(chosen)
        self.refresh_extras()

    def clear_extras(self):
        self.image_paths = []
        self.audio_path = None
        self.refresh_extras()
        self.say("Cleared the previews and the theme tune.")

    def refresh_extras(self):
        if self.image_paths:
            text = ", ".join(path.name for path in self.image_paths)
            self.canvas.itemconfigure(self.images_item, text=text, fill=self.theme.text)
        else:
            self.canvas.itemconfigure(
                self.images_item, text="None chosen", fill=self.theme.text_muted
            )
        if self.audio_path:
            size = human_size(self.audio_path.stat().st_size)
            self.canvas.itemconfigure(
                self.audio_item, text=f"{self.audio_path.name}  ({size})", fill=self.theme.text
            )
        else:
            self.canvas.itemconfigure(
                self.audio_item, text="None chosen", fill=self.theme.text_muted
            )

    def build_package(self):
        if self.worker.busy:
            return
        if self.source_folder is None:
            messagebox.showinfo("Mod Creator", "Pick the source folder first.")
            return
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showinfo("Mod Creator", "Give the mod a name.")
            return

        safe = "".join(char for char in name if char not in '<>:"/\\|?*').strip() or "mod"
        output_path = self.mods_dir / f"{safe}{PACKAGE_EXTENSION}"
        if output_path.exists() and not messagebox.askyesno(
            "Mod Creator", f"{output_path.name} already exists.\n\nOverwrite it?"
        ):
            return

        self.mods_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "taildata": self.taildata,
            "source_folder": self.source_folder,
            "output_path": output_path,
            "name": name,
            "description": self.description_text.get("1.0", "end").strip(),
            "author": self.author_entry.get().strip(),
            "version": self.version_entry.get().strip(),
            "genre": self.genre_var.get(),
            "image_paths": list(self.image_paths),
            "audio_path": self.audio_path,
        }
        self.build_button.set_enabled(False)
        self.say(f"Bottling {name}...", "accent")

        def job(report):
            return create_package(
                progress=lambda done, total, message: report("progress", (done, total, message)),
                **payload,
            )

        self.worker.start(
            job,
            {"progress": self.on_progress, "done": self.on_done, "error": self.on_error},
            name="gokonworks-package",
        )

    def on_progress(self, payload):
        done, total, message = payload
        self.progress.set_fraction(done / total if total else 0.0)
        self.say(message)

    def on_done(self, result):
        self.build_button.set_enabled(True)
        self.progress.set_fraction(1.0)
        self.say(
            f"Wrote {Path(result['package_path']).name}, {result['entries']} file(s), "
            f"{result['images']} preview(s), "
            f"{'with' if result['has_audio'] else 'no'} audio, {human_size(result['size'])}.",
            "ok",
        )
        log.info("Package built: %s", result["package_path"])
        if callable(self.on_built):
            self.on_built(result)

    def on_error(self, exc):
        self.build_button.set_enabled(True)
        self.progress.set_fraction(0.0)
        self.say(f"{type(exc).__name__}: {exc}", "danger")
        messagebox.showerror("Mod Creator", f"{type(exc).__name__}\n\n{exc}")

    def close(self):
        self.destroy()
