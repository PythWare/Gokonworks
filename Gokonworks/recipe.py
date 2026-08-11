"""
Handles the .at mod package, the recipe card for one drink
"""

from __future__ import annotations

import hashlib, json, mimetypes, struct
from pathlib import Path
from typing import Iterable, Iterator

from .ledger import TAILDATA_FILENAME
from .returns import ReadyPayload, compress_payload
from .wetworks import (
    block_extents,
    GokonworksError,
    ProgressCallback,
    WinMemoryAudioPlayer,
    human_size,
    log,
    normalize_archive_path,
    read_exact,
    utc_now,
)

__all__ = [
    "GENRES",
    "PACKAGE_EXTENSION",
    "RecipeError",
    "create_package",
    "iter_package_payloads",
    "package_ready_payloads",
    "read_package_audio",
    "read_package_images",
    "read_package_manifest",
]

PACKAGE_MAGIC = b"AKIBA_TRIP_AT_MIX"
PACKAGE_FORMAT = "akiba-trip-at"
PACKAGE_VERSION = 1
PACKAGE_EXTENSION = ".at"

APPLY_APPEND = "append"
APPLY_OVERWRITE = "overwrite"
APPLY_MODES = (APPLY_APPEND, APPLY_OVERWRITE)
HEADER_SIZE_STRUCT = struct.Struct("<I")

MAX_PREVIEW_IMAGES = 5
DEFAULT_GENRE = "Miscellaneous"
GENRES = [
    "Gameplay",
    "Visual",
    "Audio",
    "User Interface",
    "Restoration",
    "Balance",
    "Text",
    "Experimental",
    "Miscellaneous",
]

class RecipeError(GokonworksError):
    pass


def normalize_genre(genre: str) -> str:
    clean = (genre or "").strip()
    return clean if clean in GENRES else DEFAULT_GENRE


def package_data_start(header_size: int) -> int:
    return len(PACKAGE_MAGIC) + HEADER_SIZE_STRUCT.size + header_size


def entries_size(manifest: dict) -> int:
    return sum(int(entry["payload_size"]) for entry in manifest.get("entries", []))


def images_size(manifest: dict) -> int:
    return sum(int(image["size"]) for image in manifest.get("images", []))

def read_package_manifest(package_path: Path) -> dict:
    package_path = Path(package_path)
    try:
        with package_path.open("rb") as file_obj:
            magic = read_exact(file_obj, len(PACKAGE_MAGIC), package_path.name)
            if magic != PACKAGE_MAGIC:
                raise RecipeError(f"{package_path.name} is not an Akiba's Trip .at package")
            header_size = HEADER_SIZE_STRUCT.unpack(
                read_exact(file_obj, HEADER_SIZE_STRUCT.size, package_path.name)
            )[0]
            header = read_exact(file_obj, header_size, package_path.name)
    except OSError as exc:
        raise RecipeError(f"Couldn't read mod package: {package_path}") from exc

    try:
        manifest = json.loads(header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecipeError(f"Package manifest isn't valid JSON: {package_path}") from exc

    if manifest.get("format") != PACKAGE_FORMAT:
        raise RecipeError(f"{package_path.name} isn't an Akiba's Trip .at package")
    if int(manifest.get("version", 0)) != PACKAGE_VERSION:
        raise RecipeError(f"Unsupported package version {manifest.get('version')!r}")

    manifest["header_size"] = header_size
    manifest["package_path"] = str(package_path)
    manifest.setdefault("entries", [])
    manifest.setdefault("images", [])
    manifest.setdefault("audio", None)
    manifest.setdefault("apply_mode", APPLY_APPEND)
    return manifest


def package_apply_mode(manifest: dict) -> str:
    mode = str(manifest.get("apply_mode") or APPLY_APPEND).strip().lower()
    return mode if mode in APPLY_MODES else APPLY_APPEND


def check_against_taildata(manifest: dict, taildata: dict):
    """Refuse a package that was cut against a different volume.dat"""
    built_for = int(manifest.get("original_size", 0))
    have = int(taildata["original_size"])
    if built_for and built_for != have:
        raise RecipeError(
            f"{Path(manifest['package_path']).name} was built for an archive of "
            f"{built_for:,} bytes but this one is {have:,}. They're not the same game files."
        )
    missing = [
        entry["path"] for entry in manifest["entries"] if entry["path"] not in taildata["files"]
    ]
    if missing:
        raise RecipeError(
            f"{len(missing)} file(s) in this package arent in the archive, "
            f"starting with {missing[0]}"
        )


def iter_package_payloads(package_path: Path, manifest: dict) -> Iterator[tuple[dict, bytes]]:
    with Path(package_path).open("rb") as file_obj:
        file_obj.seek(package_data_start(int(manifest["header_size"])))
        for entry in manifest["entries"]:
            payload = read_exact(file_obj, int(entry["payload_size"]), entry["path"])
            digest = hashlib.sha256(payload).hexdigest()
            if entry.get("payload_sha256") and digest != entry["payload_sha256"]:
                raise RecipeError(f"{entry['path']} is corrupt inside the package")
            yield entry, payload


def package_ready_payloads(
    package_path: Path, manifest: dict
) -> Iterator[tuple[str, ReadyPayload]]:
    """Feed straight into returns.apply_mod"""
    for entry, payload in iter_package_payloads(package_path, manifest):
        yield entry["path"], ReadyPayload(
            data=payload,
            unpacked_size=int(entry["unpacked_size"]),
            compressed=bool(entry["compressed"]),
        )


def read_package_images(package_path: Path, manifest: dict) -> list[bytes]:
    images: list[bytes] = []
    with Path(package_path).open("rb") as file_obj:
        file_obj.seek(package_data_start(int(manifest["header_size"])) + entries_size(manifest))
        for image in manifest["images"]:
            images.append(read_exact(file_obj, int(image["size"]), image["name"]))
    return images


def read_package_audio(package_path: Path, manifest: dict) -> bytes | None:
    audio = manifest.get("audio")
    if not audio:
        return None
    start = (
        package_data_start(int(manifest["header_size"]))
        + entries_size(manifest)
        + images_size(manifest)
    )
    with Path(package_path).open("rb") as file_obj:
        file_obj.seek(start)
        return read_exact(file_obj, int(audio["size"]), audio["name"])

def read_asset(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise RecipeError(f"Could not read asset: {path}") from exc


def build_image_records(image_paths: Iterable[Path]) -> tuple[list[dict], list[bytes]]:
    records: list[dict] = []
    blobs: list[bytes] = []
    for image_path in list(image_paths)[:MAX_PREVIEW_IMAGES]:
        data = read_asset(image_path)
        records.append(
            {
                "name": Path(image_path).name,
                "size": len(data),
                "mime": mimetypes.guess_type(str(image_path))[0] or "application/octet-stream",
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        blobs.append(data)
    return records, blobs


def build_audio_record(audio_path: Path | None) -> tuple[dict | None, bytes | None]:
    if audio_path is None:
        return None, None
    data = read_asset(audio_path)
    if not WinMemoryAudioPlayer.is_wav(data):
        raise RecipeError(
            f"{Path(audio_path).name} is not a RIFF/WAVE file. In-memory playback only "
            "handles WAV, so convert the track first."
        )
    record = {
        "name": Path(audio_path).name,
        "size": len(data),
        "mime": "audio/wav",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return record, data


def collect_source_files(source_folder: Path, taildata: dict) -> list[tuple[str, Path]]:
    """
    Match a folder of edited files against the unpacked layout
    """
    source_folder = Path(source_folder).resolve()
    taildata_root = Path(taildata.get("taildata_root", source_folder))
    known = taildata["files"]
    matched: list[tuple[str, Path]] = []
    seen: set[str] = set()

    for file_path in sorted(source_folder.rglob("*")):
        if not file_path.is_file() or file_path.name == TAILDATA_FILENAME:
            continue
        if file_path.suffix.lower() == PACKAGE_EXTENSION:
            continue
        for base_dir in (source_folder, taildata_root):
            try:
                candidate = file_path.relative_to(base_dir).as_posix()
            except ValueError:
                continue
            if candidate in known and candidate not in seen:
                seen.add(candidate)
                matched.append((candidate, file_path))
                break
    return matched


def create_package(
    taildata: dict,
    source_folder: Path,
    output_path: Path,
    name: str,
    description: str = "",
    author: str = "Unknown",
    version: str = "1",
    genre: str = DEFAULT_GENRE,
    image_paths: Iterable[Path] | None = None,
    audio_path: Path | None = None,
    apply_mode: str = APPLY_APPEND,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Bottle a folder of edited files into one .at package
    """
    source_folder = Path(source_folder).resolve()
    output_path = Path(output_path)
    name = (name or "").strip() or output_path.stem
    author = (author or "").strip() or "Unknown"
    version = (version or "").strip() or "1"
    description = (description or "").strip()
    genre = normalize_genre(genre)

    if not source_folder.is_dir():
        raise RecipeError(f"Source folder doesn't exist: {source_folder}")

    matched = collect_source_files(source_folder, taildata)
    if not matched:
        raise RecipeError(
            f"Nothing in {source_folder} matched the unpacked layout.\n\n"
            "Keep the folders the unpack made, for example "
            "lang_us/ui/texture/whatever.phyre"
        )

    apply_mode = str(apply_mode or APPLY_APPEND).strip().lower()
    if apply_mode not in APPLY_MODES:
        raise RecipeError(f"Unknown apply mode {apply_mode!r}")

    records: list[dict] = []
    payloads: list[bytes] = []
    slid = will_append = append_bytes = 0
    total = len(matched)
    extents = block_extents(taildata) if apply_mode == APPLY_OVERWRITE else {}

    for index, (archive_path, file_path) in enumerate(matched, start=1):
        record = taildata["files"][archive_path]
        raw = read_asset(file_path)
        compressed = bool(record["compressed"])

        payload = compress_payload(raw) if compressed else raw

        if apply_mode == APPLY_OVERWRITE:
            tight = int(record["name_offset"]) - int(record["stored_offset"])
            roomy = max(0, extents.get(archive_path, 0) - int(record["name_size"]))
            if len(raw) != int(record["size"]) or len(payload) > roomy:
                will_append += 1
                append_bytes += len(payload) + int(record["name_size"])
            elif len(payload) > tight:
                slid += 1

        payloads.append(payload)
        records.append(
            {
                "path": normalize_archive_path(archive_path),
                "toc_offset": int(record["toc_offset"]),
                "hash": int(record["hash"]),
                "default_stored_offset": int(record["stored_offset"]),
                "default_size": int(record["size"]),
                "default_zip": int(record["zip"]),
                "default_name_offset": int(record["name_offset"]),
                "compressed": compressed,
                "unpacked_size": len(raw),
                "payload_size": len(payload),
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        if progress:
            progress(index, total, f"Bottled {archive_path}")

    if apply_mode == APPLY_OVERWRITE and progress:
        in_place = total - will_append
        note = (
            f"{in_place} of {total} file(s) go back in place"
            + (f", {slid} using their block's spare room" if slid else "")
            + (f". {will_append} won't fit and will be appended, about "
               f"{human_size(append_bytes)}" if will_append else ". Nothing is appended")
        )
        progress(total, total, note)

    image_records, image_blobs = build_image_records(image_paths or [])
    audio_record, audio_blob = build_audio_record(audio_path)

    if output_path.suffix.lower() != PACKAGE_EXTENSION:
        output_path = output_path.with_suffix(PACKAGE_EXTENSION)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "name": name,
        "author": author,
        "mod_version": version,
        "genre": genre,
        "description": description,
        "apply_mode": apply_mode,
        "created_utc": utc_now(),
        "volume": taildata.get("volume", "volume.dat"),
        "original_size": int(taildata["original_size"]),
        "source_folder": str(source_folder),
        "entries": records,
        "images": image_records,
        "audio": audio_record,
    }

    header = json.dumps(manifest, indent=2).encode("utf-8")
    with output_path.open("wb") as file_obj:
        file_obj.write(PACKAGE_MAGIC)
        file_obj.write(HEADER_SIZE_STRUCT.pack(len(header)))
        file_obj.write(header)
        for payload in payloads:
            file_obj.write(payload)
        for blob in image_blobs:
            file_obj.write(blob)
        if audio_blob:
            file_obj.write(audio_blob)

    if progress:
        progress(total, total, f"Wrote {output_path.name}")

    log.info("Created %s package %s with %d entries", apply_mode, output_path.name, len(records))
    return {
        "package_path": str(output_path),
        "apply_mode": apply_mode,
        "entries": len(records),
        "images": len(image_records),
        "has_audio": audio_record is not None,
        "size": output_path.stat().st_size,
    }
