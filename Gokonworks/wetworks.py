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

BACKUP_SUFFIX = ".bak"
LEGACY_TOC_SUFFIX = ".toc.bak"
LEGACY_VANILLA_SUFFIX = ".vanilla.json"
VANILLA_FORMAT = "akiba-vanilla-fingerprint"

SECTOR = 2048
COPY_CHUNK = 8 * 1024 * 1024

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


def block_extents(taildata: dict) -> dict[str, int]:
    """
    How much room each file's block actually has
    """
    files = taildata["files"]
    ordered = sorted(
        (int(record["stored_offset"]), path, record) for path, record in files.items()
    )
    data_end = max(
        (int(record["name_offset"]) + int(record["name_size"]) for record in files.values()),
        default=0,
    )
    extents: dict[str, int] = {}
    for index, (stored, path, record) in enumerate(ordered):
        boundary = ordered[index + 1][0] if index + 1 < len(ordered) else data_end
        extents[path] = max(0, boundary - stored)
    return extents


def backup_path(volume_path: Path) -> Path:
    """The one file the toolkit needs to undo anything"""
    volume_path = Path(volume_path)
    return volume_path.with_name(volume_path.name + BACKUP_SUFFIX)


def legacy_backup_paths(volume_path: Path) -> tuple[Path, Path]:
    volume_path = Path(volume_path)
    return (
        volume_path.with_name(volume_path.name + LEGACY_TOC_SUFFIX),
        volume_path.with_name(volume_path.name + LEGACY_VANILLA_SUFFIX),
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


def copy_stream(source, target, total: int, label: str, progress: ProgressCallback | None):
    done = 0
    while True:
        chunk = source.read(COPY_CHUNK)
        if not chunk:
            break
        target.write(chunk)
        done += len(chunk)
        if progress:
            progress(done, total, f"{label} {human_size(done)} of {human_size(total)}")
    target.flush()
    os.fsync(target.fileno())
    return done


def make_backup(volume_path: Path, progress: ProgressCallback | None = None) -> dict:
    """
    Copy the whole clean archive once
    """
    volume_path = Path(volume_path)
    target = backup_path(volume_path)
    total = volume_path.stat().st_size
    partial = target.with_name(target.name + ".part")

    try:
        with volume_path.open("rb") as source, partial.open("wb") as sink:
            copy_stream(source, sink, total, "Backing up", progress)
        partial.replace(target)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise GokonworksError(f"Couldn't write the archive backup: {exc}") from exc

    magic, files, names, base, fsize = peek_header(target)
    log.info("Backed up %s to %s (%s)", volume_path.name, target.name, human_size(total))
    return {"backup": str(target), "original_size": total, "entry_count": files}


def backup_available(volume_path: Path) -> bool:
    return backup_path(volume_path).is_file()


def backup_original_size(volume_path: Path) -> int:
    """Vanilla size, straight off the backup rather than out of a side file"""
    target = backup_path(volume_path)
    if not target.is_file():
        raise GokonworksError("No archive backup exists, can't tell the vanilla size")
    return target.stat().st_size


def backup_toc_blob(volume_path: Path) -> bytes:
    """Header plus the whole table of contents, walked out of the backup"""
    target = backup_path(volume_path)
    if not target.is_file():
        raise GokonworksError("No archive backup exists, can't read the vanilla index")
    with target.open("rb") as file_obj:
        header = read_exact(file_obj, 20, "backup header")
        magic, files, names, base, fsize = struct.unpack(">5I", header)
        return header + read_exact(file_obj, 24 * files, "backup toc")


def restore_toc_from_backup(volume_path: Path) -> dict:
    """
    Put the vanilla index back and cut off every appended byte
    """
    volume_path = Path(volume_path)
    blob = backup_toc_blob(volume_path)
    original_size = backup_original_size(volume_path)
    previous_size = volume_path.stat().st_size

    with volume_path.open("r+b") as file_obj:
        file_obj.seek(0)
        file_obj.write(blob)
        if previous_size > original_size:
            file_obj.truncate(original_size)
        file_obj.flush()
        os.fsync(file_obj.fileno())

    log.info(
        "Restored the index of %s from %s, %s to %s",
        volume_path.name, backup_path(volume_path).name,
        human_size(previous_size), human_size(original_size),
    )
    return {
        "volume": str(volume_path),
        "previous_size": previous_size,
        "restored_size": original_size,
        "reclaimed_bytes": max(0, previous_size - original_size),
    }


def restore_regions_from_backup(
    volume_path: Path,
    regions: list[tuple[int, int]],
    progress: ProgressCallback | None = None,
) -> int:
    """
    Copy specific byte ranges back out of the backup
    """
    volume_path = Path(volume_path)
    source_path = backup_path(volume_path)
    if not source_path.is_file():
        raise GokonworksError("No archive backup exists, can't undo an overwrite mod")

    ordered = sorted((int(start), int(length)) for start, length in regions if int(length) > 0)
    if not ordered:
        return 0

    limit = source_path.stat().st_size
    written = 0
    total = len(ordered)
    with source_path.open("rb") as source, volume_path.open("r+b") as target:
        for index, (start, length) in enumerate(ordered, start=1):
            if start >= limit:
                log.warning("Skipping a region at %d, past the end of the backup", start)
                continue
            if start + length > limit:
                log.info("Trimming a restore region at %d from %d to %d bytes",
                         start, length, limit - start)
                length = limit - start
            source.seek(start)
            target.seek(start)
            remaining = length
            while remaining:
                chunk = source.read(min(COPY_CHUNK, remaining))
                if not chunk:
                    raise GokonworksError("Backup ended early while restoring a region")
                target.write(chunk)
                remaining -= len(chunk)
                written += len(chunk)
            if progress:
                progress(index, total, f"Restored {human_size(length)} of original data")
        target.flush()
        os.fsync(target.fileno())

    log.info("Restored %d region(s), %s, from the backup", len(ordered), human_size(written))
    return written


def restore_from_backup(volume_path: Path, progress: ProgressCallback | None = None) -> dict:
    """Put the whole archive back, byte for byte"""
    volume_path = Path(volume_path)
    source_path = backup_path(volume_path)
    if not source_path.is_file():
        raise GokonworksError("No archive backup exists, can't restore the vanilla archive")

    previous_size = volume_path.stat().st_size if volume_path.is_file() else 0
    total = source_path.stat().st_size
    with source_path.open("rb") as source, volume_path.open("r+b") as target:
        target.seek(0)
        copy_stream(source, target, total, "Restoring", progress)
        target.truncate(total)
        target.flush()
        os.fsync(target.fileno())

    log.info("Restored %s from %s, %s", volume_path.name, source_path.name, human_size(total))
    return {
        "volume": str(volume_path),
        "previous_size": previous_size,
        "restored_size": total,
        "reclaimed_bytes": max(0, previous_size - total),
    }


def migrate_legacy_backup(volume_path: Path) -> bool:
    """
    Fold an old index only backup into the new shape
    """
    volume_path = Path(volume_path)
    toc_file, fingerprint_file = legacy_backup_paths(volume_path)
    if not (toc_file.is_file() and fingerprint_file.is_file()):
        return False

    try:
        fingerprint = read_json(fingerprint_file, "vanilla fingerprint")
        if fingerprint.get("format") != VANILLA_FORMAT:
            return False
        blob = toc_file.read_bytes()
        if sha256_bytes(blob) != fingerprint.get("toc_sha256"):
            log.warning("Legacy TOC backup is corrupt, ignoring it")
            return False
        original_size = int(fingerprint["original_size"])
        previous = volume_path.stat().st_size
        with volume_path.open("r+b") as file_obj:
            file_obj.seek(0)
            file_obj.write(blob)
            if previous > original_size:
                file_obj.truncate(original_size)
            file_obj.flush()
            os.fsync(file_obj.fileno())
    except (GokonworksError, OSError, KeyError, ValueError) as exc:
        log.warning("Couldnt use the legacy backup: %s", exc)
        return False

    toc_file.unlink(missing_ok=True)
    fingerprint_file.unlink(missing_ok=True)
    log.info("Migrated the legacy index backup, archive is back to %s", human_size(original_size))
    return True


def needs_full_backup(volume_path: Path | None = None) -> bool:
    """Whether the one time archive copy still has to be taken"""
    volume_path = Path(volume_path) if volume_path else default_volume_path()
    return volume_path.is_file() and not backup_path(volume_path).is_file()


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

    target = backup_path(volume_path)

    if target.is_file():
        original_size = target.stat().st_size
        actual = volume_path.stat().st_size
        if actual < original_size:
            message = (
                f"{volume_path.name} is smaller than the backup "
                f"({human_size(actual)} vs {human_size(original_size)}).\n\n"
                "The archive was replaced or truncated. Restore it from "
                f"{target.name}, or delete the backup and start again from a clean archive."
            )
            log.warning("Volume shrank below the backup size")
            return False, "warning", message
        return True, "info", ""

    migrated = migrate_legacy_backup(volume_path)
    actual = volume_path.stat().st_size

    if not migrated:
        expected = pristine_size(volume_path)
        if actual != expected:
            message = (
                f"{volume_path.name} is {human_size(actual)} but a vanilla archive of this "
                f"index should be {human_size(expected)}.\n\n"
                "It looks like this archive was already modified and there is no backup to "
                "compare against. Restore a clean copy before enabling any mods."
            )
            log.warning("Volume size %d != pristine %d and no backup exists", actual, expected)
            return False, "warning", message

    lead = (
        "Your old index backup has been folded in and the archive is back to vanilla.\n\n"
        if migrated else "First run.\n\n"
    )
    message = (
        f"{lead}Gokonworks is about to copy {volume_path.name} to {target.name} "
        f"({human_size(actual)}).\n\n"
        "That one copy is everything the toolkit needs to undo any mod, including the "
        "kind that overwrites files in place. It runs in the background, the log will "
        "say when it is done."
    )
    return True, "info", message
