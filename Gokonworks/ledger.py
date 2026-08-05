"""
Handles the reading logic
"""

from __future__ import annotations

import struct, zlib
from dataclasses import dataclass, asdict
from pathlib import Path

from .wetworks import (
    LOG_PATH,
    GokonworksError,
    ProgressCallback,
    SECTOR,
    decode_name,
    default_output_dir,
    default_volume_path,
    ensure_backups,
    filesystem_path,
    hash_name,
    human_size,
    log,
    normalize_archive_path,
    read_exact,
    read_json,
    utc_now,
    write_json,
)

__all__ = [
    "LOG_PATH",
    "MAGIC",
    "TAILDATA_FILENAME",
    "TAILDATA_FORMAT",
    "VolumeEntry",
    "VolumeError",
    "VolumeHeader",
    "Volume",
    "ZIP_DEFLATE",
    "ZIP_STORED",
    "decompress_payload",
    "ensure_backups",
    "hash_name",
    "load_taildata",
    "log",
    "open_volume",
    "read_entries",
    "read_header",
    "unpack_status",
    "unpack_volume",
]

MAGIC = 0xFADEBABE
HEADER_STRUCT = struct.Struct(">5I")
ENTRY_STRUCT = struct.Struct(">6I")
TOC_START = HEADER_STRUCT.size

ZIP_STORED = 0
ZIP_DEFLATE = 8

TAILDATA_FILENAME = "akiba_taildata.json"
TAILDATA_FORMAT = "akiba-taildata"
TAILDATA_VERSION = 1

class VolumeError(GokonworksError):
    pass

@dataclass(frozen=True)
class VolumeHeader:
    magic: int
    file_count: int
    name_count: int
    base: int
    data_size: int

    @property
    def toc_size(self) -> int:
        return ENTRY_STRUCT.size * self.file_count

    @property
    def toc_end(self) -> int:
        return TOC_START + self.toc_size

@dataclass(frozen=True)
class VolumeEntry:
    index: int
    hash: int
    stored_offset: int
    size: int
    zip_mode: int
    name_offset: int
    name_size: int
    name: str
    path: str
    toc_offset: int
    base: int

    @property
    def stored_size(self) -> int:
        """Bytes on disk, derived from where this entry's name string begins"""
        return self.name_offset - self.stored_offset

    @property
    def absolute_offset(self) -> int:
        return self.base + self.stored_offset

    @property
    def absolute_name_offset(self) -> int:
        return self.base + self.name_offset

    @property
    def compressed(self) -> bool:
        return self.zip_mode != ZIP_STORED

    @property
    def hash_matches(self) -> bool:
        """Whether the stored hash agrees with the game's hash of this name"""
        return self.hash == hash_name(self.name)

    @property
    def end_offset(self) -> int:
        """End of this entry's block, payload plus the trailing name string"""
        return self.name_offset + self.name_size


def read_header(file_obj) -> VolumeHeader:
    header = VolumeHeader(*HEADER_STRUCT.unpack(read_exact(file_obj, HEADER_STRUCT.size, "header")))
    if header.magic != MAGIC:
        raise VolumeError(
            f"Bad archive signature 0x{header.magic:08X}, expected 0x{MAGIC:08X}"
        )
    if header.file_count == 0:
        raise VolumeError("Archive declares zero files")
    return header


def read_entries(file_obj, header: VolumeHeader) -> list[VolumeEntry]:
    file_obj.seek(TOC_START)
    toc = read_exact(file_obj, header.toc_size, "toc")
    entries: list[VolumeEntry] = []
    names_end = 0

    for index in range(header.file_count):
        record_offset = index * ENTRY_STRUCT.size
        hash_value, stored_offset, size, zip_mode, name_offset, name_size = ENTRY_STRUCT.unpack_from(
            toc, record_offset
        )
        if name_offset < stored_offset:
            raise VolumeError(
                f"Entry {index}: name offset 0x{name_offset:x} sits before its payload"
            )
        if name_size == 0:
            raise VolumeError(f"Entry {index}: zero length name")
        names_end = max(names_end, name_offset + name_size)
        entries.append(
            VolumeEntry(
                index=index,
                hash=hash_value,
                stored_offset=stored_offset,
                size=size,
                zip_mode=zip_mode,
                name_offset=name_offset,
                name_size=name_size,
                name="",
                path="",
                toc_offset=TOC_START + record_offset,
                base=header.base,
            )
        )

    named: list[VolumeEntry] = []
    for entry in sorted(entries, key=lambda item: item.name_offset):
        file_obj.seek(entry.absolute_name_offset)
        raw = read_exact(file_obj, entry.name_size, f"entry {entry.index} name")
        name = decode_name(raw)
        named.append(
            VolumeEntry(**{**asdict(entry), "name": name, "path": normalize_archive_path(name)})
        )

    named.sort(key=lambda item: item.index)
    return named


def decompress_payload(payload: bytes, entry: VolumeEntry) -> bytes:
    if not entry.compressed:
        return payload
    try:
        return zlib.decompress(payload)
    except zlib.error:
        try:
            return zlib.decompress(payload, -zlib.MAX_WBITS)
        except zlib.error as exc:
            raise VolumeError(f"{entry.path} failed zlib decompression") from exc


class Volume:
    """Read only view over volume.dat"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.file = self.path.open("rb")
        try:
            self.header = read_header(self.file)
            self.entries = read_entries(self.file, self.header)
        except Exception:
            self.file.close()
            raise
        self.by_path = {entry.path: entry for entry in self.entries}
        self.file_size = self.path.stat().st_size

    def __enter__(self) -> "Volume":
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        if not self.file.closed:
            self.file.close()

    @property
    def pristine_size(self) -> int:
        """Where the vanilla archive ends, anything past this was appended"""
        return self.header.base + max(entry.end_offset for entry in self.entries)

    @property
    def appended_bytes(self) -> int:
        return max(0, self.file_size - self.pristine_size)

    @property
    def free_toc_slots(self) -> int:
        """How many entries could be inserted before the TOC would collide with base"""
        return max(0, (self.header.base - self.header.toc_end) // ENTRY_STRUCT.size)

    def entry(self, path: str) -> VolumeEntry:
        try:
            return self.by_path[normalize_archive_path(path)]
        except KeyError:
            raise VolumeError(f"{path} is not in {self.path.name}") from None

    def read_payload(self, entry: VolumeEntry) -> bytes:
        """The bytes exactly as stored, still compressed when zip is set"""
        end = entry.absolute_offset + entry.stored_size
        if entry.absolute_offset < 0 or end > self.file_size:
            raise VolumeError(f"{entry.path} points outside {self.path.name}")
        self.file.seek(entry.absolute_offset)
        return read_exact(self.file, entry.stored_size, entry.path)

    def read_file(self, entry_or_path) -> bytes:
        entry = entry_or_path if isinstance(entry_or_path, VolumeEntry) else self.entry(entry_or_path)
        data = decompress_payload(self.read_payload(entry), entry)
        if len(data) != entry.size:
            raise VolumeError(
                f"{entry.path} unpacked to {len(data)} bytes, the TOC claims {entry.size}"
            )
        return data

    def taildata_record(self, entry: VolumeEntry) -> dict:
        return {
            "index": entry.index,
            "path": entry.path,
            "name": entry.name,
            "hash": entry.hash,
            "toc_offset": entry.toc_offset,
            "stored_offset": entry.stored_offset,
            "stored_size": entry.stored_size,
            "size": entry.size,
            "zip": entry.zip_mode,
            "compressed": entry.compressed,
            "name_offset": entry.name_offset,
            "name_size": entry.name_size,
            "absolute_offset": entry.absolute_offset,
        }

    def check(self) -> dict:
        """Integrity sweep"""
        summary = {
            "volume": str(self.path),
            "file_size": self.file_size,
            "pristine_size": self.pristine_size,
            "appended_bytes": self.appended_bytes,
            "entries": len(self.entries),
            "free_toc_slots": self.free_toc_slots,
            "compressed_entries": 0,
            "stored_entries": 0,
            "bad_entries": [],
            "unaligned_entries": [],
            "bad_hashes": [],
            "hash_sorted": True,
        }
        previous_hash = -1
        for entry in self.entries:
            if not entry.hash_matches:
                summary["bad_hashes"].append(entry.path)
            if entry.hash < previous_hash:
                summary["hash_sorted"] = False
            previous_hash = entry.hash
            if entry.compressed:
                summary["compressed_entries"] += 1
            else:
                summary["stored_entries"] += 1
                if entry.stored_size != entry.size:
                    summary["bad_entries"].append(entry.path)
                    continue
            if entry.stored_size < 0 or entry.absolute_offset + entry.stored_size > self.file_size:
                summary["bad_entries"].append(entry.path)
            if entry.stored_offset % SECTOR:
                summary["unaligned_entries"].append(entry.path)
        return summary


def open_volume(path: Path | None = None) -> Volume:
    return Volume(Path(path) if path else default_volume_path())


def build_taildata(volume: Volume) -> dict:
    """
    The single JSON every unpacked file gets recorded in
    """
    return {
        "format": TAILDATA_FORMAT,
        "version": TAILDATA_VERSION,
        "created_utc": utc_now(),
        "volume": volume.path.name,
        "volume_path": str(volume.path.resolve()),
        "original_size": volume.pristine_size,
        "base": volume.header.base,
        "data_size": volume.header.data_size,
        "entry_count": volume.header.file_count,
        "toc_start": TOC_START,
        "sector": SECTOR,
        "taildata_note": (
            "External metadata for taildata, used for mod applying/disabling. "
            "Keys are unpacked relative file paths."
        ),
        "files": {},
    }


def unpack_volume(
    volume_path: Path | None = None,
    output_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    limit: int | None = None,
) -> dict:
    """
    Extract every entry to output_dir and drop the taildata JSON beside them

    Entries are walked in payload order instead of TOC order so the read head
    sweeps the archive once instead of seeking 15,000 times
    """
    volume_path = Path(volume_path) if volume_path else default_volume_path()
    output_dir = Path(output_dir) if output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    with open_volume(volume_path) as volume:
        taildata = build_taildata(volume)
        ordered = sorted(volume.entries, key=lambda entry: entry.stored_offset)
        available = len(ordered)
        total = min(available, limit) if limit is not None else available
        done = 0

        for entry in ordered:
            if limit is not None and done >= limit:
                break
            data = volume.read_file(entry)
            target = filesystem_path(output_dir, entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

            record = volume.taildata_record(entry)
            record["unpacked_size"] = len(data)
            taildata["files"][entry.path] = record

            done += 1
            if progress and (done == total or done % 100 == 0):
                progress(done, total, f"Unpacked {entry.path}")

        taildata["files"] = dict(sorted(taildata["files"].items()))
        taildata_path = output_dir / TAILDATA_FILENAME
        write_json(taildata_path, taildata)

        if progress:
            progress(done, total, f"Wrote {taildata_path.name}")

        log.info("Unpacked %d/%d entries from %s", done, available, volume_path.name)
        return {
            "volume": str(volume_path),
            "output_dir": str(output_dir),
            "taildata_path": str(taildata_path),
            "files": done,
            "available_files": available,
            "bytes": human_size(sum(entry.size for entry in ordered[:done])),
        }


def load_taildata(path: Path) -> dict:
    data = read_json(path, "taildata manifest")
    if data.get("format") != TAILDATA_FORMAT:
        raise VolumeError(f"{path} is not an Akiba's Trip taildata manifest")
    if int(data.get("version", 0)) != TAILDATA_VERSION:
        raise VolumeError(f"Unsupported taildata version {data.get('version')!r}")
    data.setdefault("files", {})
    data["taildata_root"] = str(Path(path).resolve().parent)
    return data


def unpack_status(output_dir: Path, sample: int = 240) -> dict:
    """
    Ask the disk whether an unpack is really sitting in this folder
    """
    output_dir = Path(output_dir)
    status = {
        "output_dir": str(output_dir),
        "taildata": False,
        "files": 0,
        "checked": 0,
        "present": 0,
        "fraction": 0.0,
        "complete": False,
    }

    taildata_path = output_dir / TAILDATA_FILENAME
    if not taildata_path.is_file():
        return status
    try:
        taildata = load_taildata(taildata_path)
    except (VolumeError, GokonworksError):
        return status

    status["taildata"] = True
    paths = list(taildata.get("files", {}))
    status["files"] = len(paths)
    if not paths:
        return status

    if sample and sample < len(paths):
        stride = len(paths) / sample
        checked = [paths[int(index * stride)] for index in range(sample)]
    else:
        checked = paths

    present = sum(1 for path in checked if filesystem_path(output_dir, path).is_file())
    status["checked"] = len(checked)
    status["present"] = present
    status["fraction"] = present / len(checked)
    status["complete"] = present == len(checked)
    return status


def find_taildata(start: Path) -> Path | None:
    """Walk up from an unpacked folder looking for the manifest that owns it"""
    start = Path(start).resolve()
    for candidate in (start, *start.parents):
        taildata_path = candidate / TAILDATA_FILENAME
        if taildata_path.is_file():
            return taildata_path
    return None
