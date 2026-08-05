"""
Handles the utility code, anything that gets mass reused that isn't GUI code
is coded in here as reusable code that gets imported into other scripts
"""

from __future__ import annotations

import ctypes, hashlib, json, logging, os, struct
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
PNG_DIR = PACKAGE_ROOT / "pngs"

VOLUME_FILENAME = "volume.dat"
UNPACK_FOLDER = "Unpacked_Files"
MODS_FOLDER = "Mods"

LOG_PATH = PROJECT_ROOT / "gokonworks.log"

TOC_BACKUP_SUFFIX = ".toc.bak"
VANILLA_SUFFIX = ".vanilla.json"
VANILLA_FORMAT = "akiba-vanilla-fingerprint"

SECTOR = 2048

ProgressCallback = Callable[[int, int, str], None]


class GokonworksError(RuntimeError):
    """Base class for every error the toolkit raises on purpose"""


def build_logger() -> logging.Logger:
    logger = logging.getLogger("gokonworks")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


log = build_logger()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"

def align_up(value: int, alignment: int = SECTOR) -> int:
    remainder = value % alignment
    return value if remainder == 0 else value + (alignment - remainder)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_volume_path() -> Path:
    return PROJECT_ROOT / VOLUME_FILENAME


def default_output_dir() -> Path:
    return PROJECT_ROOT / UNPACK_FOLDER


def default_mods_dir() -> Path:
    return PROJECT_ROOT / MODS_FOLDER


def decode_name(raw: bytes) -> str:
    """Entry names are ASCII in retail volume.dat, cp932 is the safe fallback"""
    stripped = raw.split(b"\x00", 1)[0]
    try:
        return stripped.decode("ascii")
    except UnicodeDecodeError:
        return stripped.decode("cp932", errors="replace")


def normalize_archive_path(name: str) -> str:
    """Turn a stored lang_us\\ui\\x.bin name into a safe relative posix path"""
    raw = name.replace("\\", "/")
    parts: list[str] = []
    for part in raw.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            parts.append("_")
            continue
        parts.append(part.rstrip(" ."))
    if not parts:
        raise GokonworksError(f"Empty archive path for entry name {name!r}")
    return "/".join(parts)


def to_archive_name(path: str) -> str:
    """Inverse of normalize_archive_path, the on-disk name uses backslashes"""
    return path.replace("/", "\\")


NAME_HASH_MULTIPLIER = 19
NAME_HASH_MAX_CHARS = 199


def hash_name(name: str) -> int:
    """
    The TOC hash the game looks entries up by
    """
    value = 0
    for char in to_archive_name(name).upper()[:NAME_HASH_MAX_CHARS]:
        code = ord(char)
        if code > 0x7F:
            code -= 0x100
        value = (value * NAME_HASH_MULTIPLIER + code) & 0xFFFFFFFF
    return value


def filesystem_path(root: Path, archive_path: str) -> Path:
    return Path(root).joinpath(*archive_path.split("/"))


def read_exact(file_obj, size: int, label: str = "volume") -> bytes:
    data = file_obj.read(size)
    if len(data) != size:
        raise GokonworksError(f"{label}: wanted {size} bytes but the file ended early")
    return data


def write_json(path: Path, data: dict):
    """Write JSON through a temp file so a crash can't shred an existing ledger"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def read_json(path: Path, label: str = "file") -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GokonworksError(f"Could not read {label}: {path}") from exc


SOUND_ASYNC = 0x0001
SOUND_NODEFAULT = 0x0002
SOUND_MEMORY = 0x0004
SOUND_LOOP = 0x0008
SOUND_PURGE = 0x0040


class WinMemoryAudioPlayer:
    """
    Loops a WAV straight out of a bytes object
    """

    def __init__(self):
        self.buffer = None
        self.available = True
        try:
            self.winmm = ctypes.WinDLL("winmm", use_last_error=True)
            self.play_sound = self.winmm.PlaySoundW
            self.play_sound.argtypes = [ctypes.c_void_p, wintypes.HMODULE, wintypes.DWORD]
            self.play_sound.restype = wintypes.BOOL
        except Exception:
            self.available = False
            self.winmm = None
            self.play_sound = None

    @staticmethod
    def is_wav(data: bytes) -> bool:
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"

    def play_loop_bytes(self, wav_bytes: bytes) -> bool:
        if not self.available or not wav_bytes or not self.is_wav(wav_bytes):
            return False
        self.stop()
        self.buffer = ctypes.create_string_buffer(wav_bytes)
        pointer = ctypes.cast(self.buffer, ctypes.c_void_p)
        result = self.play_sound(
            pointer, None, SOUND_MEMORY | SOUND_ASYNC | SOUND_LOOP | SOUND_NODEFAULT
        )
        return bool(result)

    def stop(self):
        if self.available and self.play_sound:
            self.play_sound(None, None, SOUND_PURGE)
        self.buffer = None


def toc_backup_paths(volume_path: Path) -> tuple[Path, Path]:
    volume_path = Path(volume_path)
    return (
        volume_path.with_name(volume_path.name + TOC_BACKUP_SUFFIX),
        volume_path.with_name(volume_path.name + VANILLA_SUFFIX),
    )


def peek_header(volume_path: Path) -> tuple[int, int, int, int, int]:
    """Read the five big endian header longs without pulling in the ledger module"""
    with Path(volume_path).open("rb") as file_obj:
        return struct.unpack(">5I", read_exact(file_obj, 20, "volume header"))


def pristine_size(volume_path: Path) -> int:
    """
    Size volume.dat should be when untouched
    """
    with Path(volume_path).open("rb") as file_obj:
        magic, files, names, base, fsize = struct.unpack(
            ">5I", read_exact(file_obj, 20, "volume header")
        )
        toc = read_exact(file_obj, 24 * files, "volume toc")
    end = 0
    for index in range(files):
        hash, offset, size, zip, name_offset, name_size = struct.unpack_from(
            ">6I", toc, index * 24
        )
        end = max(end, name_offset + name_size)
    return base + end


def make_toc_backup(volume_path: Path) -> dict:
    """
    Snapshot the header and the whole table of contents
    """
    volume_path = Path(volume_path)
    backup_path, fingerprint_path = toc_backup_paths(volume_path)
    magic, files, names, base, fsize = peek_header(volume_path)
    toc_bytes = 20 + 24 * files

    with volume_path.open("rb") as file_obj:
        blob = read_exact(file_obj, toc_bytes, "volume toc")

    backup_path.write_bytes(blob)
    fingerprint = {
        "format": VANILLA_FORMAT,
        "version": 1,
        "created_utc": utc_now(),
        "volume": volume_path.name,
        "original_size": volume_path.stat().st_size,
        "entry_count": files,
        "toc_bytes": toc_bytes,
        "toc_sha256": sha256_bytes(blob),
    }
    write_json(fingerprint_path, fingerprint)
    log.info("Created TOC backup for %s (%d entries)", volume_path.name, files)
    return fingerprint


def load_fingerprint(volume_path: Path) -> dict | None:
    backup_path, fingerprint_path = toc_backup_paths(volume_path)
    if not fingerprint_path.is_file():
        return None
    data = read_json(fingerprint_path, "vanilla fingerprint")
    if data.get("format") != VANILLA_FORMAT:
        raise GokonworksError(f"{fingerprint_path} isn't a Gokonworks vanilla fingerprint")
    return data


def restore_toc_from_backup(volume_path: Path) -> dict:
    """Reset, put the vanilla TOC back and cut off every appended byte"""
    volume_path = Path(volume_path)
    backup_path, fingerprint_path = toc_backup_paths(volume_path)
    fingerprint = load_fingerprint(volume_path)
    if fingerprint is None or not backup_path.is_file():
        raise GokonworksError("No TOC backup exists, can't restore the vanilla archive")

    blob = backup_path.read_bytes()
    if sha256_bytes(blob) != fingerprint.get("toc_sha256"):
        raise GokonworksError(f"TOC backup is corrupt: {backup_path}")

    original_size = int(fingerprint["original_size"])
    previous_size = volume_path.stat().st_size
    with volume_path.open("r+b") as file_obj:
        file_obj.seek(0)
        file_obj.write(blob)
        if previous_size > original_size:
            file_obj.truncate(original_size)
        file_obj.flush()
        os.fsync(file_obj.fileno())

    log.info(
        "Restored %s from TOC backup, %s to %s",
        volume_path.name, human_size(previous_size), human_size(original_size),
    )
    return {
        "volume": str(volume_path),
        "previous_size": previous_size,
        "restored_size": original_size,
        "reclaimed_bytes": max(0, previous_size - original_size),
    }


def ensure_backups(volume_path: Path | None = None) -> tuple[bool, str, str]:
    """
    Called once at startup
    """
    volume_path = Path(volume_path) if volume_path else default_volume_path()

    if not volume_path.is_file():
        message = (
            f"{volume_path.name} wasn't found next to the toolkit.\n"
            f"Expected: {volume_path}\n\n"
            "Copy the game archive here or point the toolkit at it before unpacking."
        )
        log.warning("Volume not found at %s", volume_path)
        return False, "warning", message

    try:
        magic, files, names, base, fsize = peek_header(volume_path)
    except (GokonworksError, struct.error, OSError) as exc:
        log.error("Couldn't read volume header: %s", exc)
        return False, "error", f"{volume_path.name} isn't readable as a volume archive.\n\n{exc}"

    from .ledger import MAGIC

    if magic != MAGIC:
        message = (
            f"{volume_path.name} doesn't start with the expected 0x{MAGIC:08X} signature.\n"
            "This doesn't look like an Akiba's Trip volume archive."
        )
        log.error("Bad magic 0x%08X in %s", magic, volume_path)
        return False, "error", message

    try:
        fingerprint = load_fingerprint(volume_path)
    except GokonworksError as exc:
        return False, "error", str(exc)

    backup_path, fingerprint_path = toc_backup_paths(volume_path)

    if fingerprint is None:
        expected = pristine_size(volume_path)
        actual = volume_path.stat().st_size
        if actual != expected:
            message = (
                f"{volume_path.name} is {human_size(actual)} but a vanilla archive of this "
                f"TOC should be {human_size(expected)}.\n\n"
                "It looks like this archive was already modified and there is no backup to "
                "compare against. Restore a clean copy before enabling any mods."
            )
            log.warning("Volume size %d != pristine %d and no backup exists", actual, expected)
            return False, "warning", message
        try:
            make_toc_backup(volume_path)
        except (GokonworksError, OSError) as exc:
            log.error("Couldn't create TOC backup: %s", exc)
            return False, "error", f"Couldn't create the TOC backup.\n\n{exc}"
        message = (
            f"First run, backed up the archive index for {volume_path.name}.\n\n"
            f"Saved to: {backup_path.name}\n"
            "Keep this file. It's all the toolkit needs to undo any mod."
        )
        return True, "info", message

    if not backup_path.is_file():
        message = (
            f"The TOC backup {backup_path.name} is missing but its fingerprint is still here.\n\n"
            "Mods cannot be safely undone until a clean archive is restored."
        )
        log.warning("TOC backup missing: %s", backup_path)
        return False, "warning", message

    original_size = int(fingerprint.get("original_size", 0))
    actual = volume_path.stat().st_size
    if actual < original_size:
        message = (
            f"{volume_path.name} is smaller than the backed up vanilla size "
            f"({human_size(actual)} vs {human_size(original_size)}).\n\n"
            "The archive was replaced or truncated. Delete the stale backup files and "
            "restart the toolkit against a clean archive."
        )
        log.warning("Volume shrank below recorded original size")
        return False, "warning", message

    return True, "info", ""
