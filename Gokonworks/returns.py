"""
Handles the writing logic
"""

from __future__ import annotations

import collections, os, zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .ledger import ENTRY_STRUCT, ZIP_DEFLATE, ZIP_STORED
from .wetworks import (
    GokonworksError,
    ProgressCallback,
    SECTOR,
    align_up,
    backup_available,
    backup_original_size,
    block_extents,
    default_volume_path,
    human_size,
    log,
    normalize_archive_path,
    read_exact,
    read_json,
    restore_from_backup,
    restore_regions_from_backup,
    restore_toc_from_backup,
    utc_now,
    write_json,
)

__all__ = [
    "MODS_FILENAME",
    "ReadyPayload",
    "WriteError",
    "apply_mod",
    "apply_package",
    "compress_payload",
    "disable_all_mods",
    "disable_mod",
    "list_enabled_mods",
    "load_enabled_mods",
    "restore_vanilla",
]

MODS_FILENAME = "Akibas_Trip.MODS.json"
MODS_FORMAT = "akiba-enabled-mods"
MODS_VERSION = 1

U32_MAX = 0xFFFFFFFF
S32_MAX = 0x7FFFFFFF
COMPRESS_LEVEL = 9


class WriteError(GokonworksError):
    pass


@dataclass(frozen=True)
class ReadyPayload:
    """
    A payload that's already in the form the archive wants
    """

    data: bytes
    unpacked_size: int
    compressed: bool


def mods_path(volume_path: Path) -> Path:
    return Path(volume_path).with_name(MODS_FILENAME)


def empty_state(volume_path: Path, taildata: dict) -> dict:
    return {
        "format": MODS_FORMAT,
        "version": MODS_VERSION,
        "volume": Path(volume_path).name,
        "original_size": int(taildata["original_size"]),
        "mods": [],
    }


def load_enabled_mods(volume_path: Path, taildata: dict, *, strict: bool = True) -> dict:
    """
    Read the ledger of what is currently enabled
    """
    path = mods_path(volume_path)
    if not path.is_file():
        return empty_state(volume_path, taildata)
    state = read_json(path, "enabled mods ledger")
    if state.get("format") != MODS_FORMAT:
        raise WriteError(f"{path} isnt a Gokonworks enabled mods ledger")
    recorded = int(state.get("original_size", 0))
    expected = int(taildata["original_size"])
    if recorded != expected:
        message = (
            f"{path} was written for an archive of {human_size(recorded)} but the taildata "
            f"describes {human_size(expected)}. They're not for the same volume.dat."
        )
        if strict:
            raise WriteError(message)
        log.warning("%s Disabling anyway.", message)
    state.setdefault("mods", [])
    return state


def save_enabled_mods(volume_path: Path, state: dict):
    write_json(mods_path(volume_path), state)


def list_enabled_mods(volume_path: Path, taildata: dict) -> list[dict]:
    return load_enabled_mods(volume_path, taildata).get("mods", [])


def compress_payload(data: bytes, level: int = COMPRESS_LEVEL) -> bytes:
    return zlib.compress(data, level)


def read_toc_record(file_obj, toc_offset: int) -> tuple[int, int, int, int, int, int]:
    file_obj.seek(toc_offset)
    return ENTRY_STRUCT.unpack(read_exact(file_obj, ENTRY_STRUCT.size, "toc record"))


def write_toc_record(
    file_obj,
    toc_offset: int,
    hash_value: int,
    stored_offset: int,
    size: int,
    zip_mode: int,
    name_offset: int,
    name_size: int,
):
    file_obj.seek(toc_offset)
    file_obj.write(
        ENTRY_STRUCT.pack(hash_value, stored_offset, size, zip_mode, name_offset, name_size)
    )


def read_data_size(file_obj) -> int:
    file_obj.seek(16)
    return int.from_bytes(read_exact(file_obj, 4, "volume header"), "big")


def write_data_size(file_obj, value: int):
    """
    Keep the header's data area covering everything the TOC points at
    """
    if value > U32_MAX:
        raise WriteError("The data area no longer fits a 32 bit size field")
    file_obj.seek(16)
    file_obj.write(int(value).to_bytes(4, "big"))


def append_block(file_obj, base: int, payload: bytes, name_bytes: bytes) -> dict:
    """
    Append one [padding][payload][name] block to the end of the archive

    Returns the relative offsets the TOC needs plus the absolute block bounds the
    mod ledger uses when it wants to reclaim the space again
    """
    file_obj.seek(0, os.SEEK_END)
    end = file_obj.tell()
    if end < base:
        raise WriteError("Archive is smaller than its own data base offset")

    payload_start = base + align_up(end - base, SECTOR)
    padding = payload_start - end
    stored_offset = payload_start - base
    name_offset = stored_offset + len(payload)

    if base + name_offset + len(name_bytes) > S32_MAX:
        spare = max(0, S32_MAX - base - (end - base))
        raise WriteError(
            "Appending this mod would push the archive past 2 GB.\n\n"
            "TOC offsets are unsigned on paper but past that point they read as "
            "negative to anything treating them as a signed 32 bit number, and the game "
            "stops being able to find those files.\n\n"
            f"Room left for appended data: {human_size(spare)}.\n"
            "Disable some mods or build this one in Overwrite mode so nothing is "
            "appended at all."
        )

    if padding:
        file_obj.write(b"\x00" * padding)
    file_obj.write(payload)
    file_obj.write(name_bytes)

    return {
        "stored_offset": stored_offset,
        "name_offset": name_offset,
        "payload_offset": payload_start,
        "block_start": end,
        "block_end": payload_start + len(payload) + len(name_bytes),
        "appended_bytes": padding + len(payload) + len(name_bytes),
    }


def resolve_record(taildata: dict, path: str) -> dict:
    key = normalize_archive_path(path)
    record = taildata["files"].get(key)
    if record is None:
        raise WriteError(f"{key} is not in the taildata manifest, it can't be modded")
    return record


def payload_room(record: dict, extent: int) -> int:
    """
    How many bytes a replacement payload may use
    """
    return max(0, extent - int(record["name_size"]))


def region_owner(mods: list[dict], region_start: int) -> dict | None:
    """The newest still enabled overwrite mod holding these bytes, if any"""
    owner = None
    for mod in mods:
        if mod.get("mode") != "overwrite":
            continue
        for entry in mod.get("entries", []):
            if int(entry.get("region_start", -1)) == region_start:
                owner = entry
    return owner


def current_owner(mods: list[dict], toc_offset: int) -> dict | None:
    """
    The newest still enabled mod that overrides this entry, if there's one
    """
    owner = None
    for mod in mods:
        for entry in mod.get("entries", []):
            if int(entry["toc_offset"]) == toc_offset:
                owner = entry
    return owner


def truncate_to_vanilla(volume_path: Path, state: dict) -> int:
    """
    Cut every appended byte off the archive
    """
    if state.get("mods"):
        raise WriteError("Refusing to truncate while mods are still enabled")
    target = int(state["original_size"])
    volume_path = Path(volume_path)
    current = volume_path.stat().st_size
    if current <= target:
        return 0
    with volume_path.open("r+b") as file_obj:
        file_obj.truncate(target)
        file_obj.flush()
        os.fsync(file_obj.fileno())
    log.info("Reclaimed %s from %s", human_size(current - target), volume_path.name)
    return current - target


def apply_mod(
    volume_path: Path,
    taildata: dict,
    payloads: Iterable[tuple[str, bytes | Path]],
    mod_id: str,
    name: str = "",
    description: str = "",
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Append every replacement file, then repoint the TOC at the appended copies
    """
    volume_path = Path(volume_path)
    if not volume_path.is_file():
        raise WriteError(f"Archive not found: {volume_path}")

    state = load_enabled_mods(volume_path, taildata)
    if any(mod.get("id") == mod_id for mod in state["mods"]):
        raise WriteError(f"{mod_id} is already enabled")

    base = int(taildata["base"])
    items = list(payloads)
    total = len(items)
    if not total:
        raise WriteError("This mod has no files that match the taildata manifest")

    applied: list[dict] = []

    with volume_path.open("r+b") as file_obj:
        for index, (path, source) in enumerate(items, start=1):
            record = resolve_record(taildata, path)
            if isinstance(source, ReadyPayload):
                payload = source.data
                unpacked_size = source.unpacked_size
                compressed = source.compressed
            else:
                data = (
                    Path(source).read_bytes()
                    if isinstance(source, (str, Path))
                    else bytes(source)
                )
                unpacked_size = len(data)
                compressed = bool(record["compressed"])
                payload = compress_payload(data) if compressed else data
            if unpacked_size > U32_MAX:
                raise WriteError(f"{path} is too large for a 32 bit TOC size field")

            hash_value, previous_offset, previous_size, previous_zip, previous_name_offset, name_size = (
                read_toc_record(file_obj, int(record["toc_offset"]))
            )
            file_obj.seek(base + previous_name_offset)
            name_bytes = read_exact(file_obj, name_size, f"{path} name")

            block = append_block(file_obj, base, payload, name_bytes)
            applied.append(
                {
                    "path": normalize_archive_path(path),
                    "toc_offset": int(record["toc_offset"]),
                    "hash": hash_value,
                    "name_size": name_size,
                    "previous": {
                        "stored_offset": previous_offset,
                        "size": previous_size,
                        "zip": previous_zip,
                        "name_offset": previous_name_offset,
                    },
                    "default": {
                        "stored_offset": int(record["stored_offset"]),
                        "size": int(record["size"]),
                        "zip": int(record["zip"]),
                        "name_offset": int(record["name_offset"]),
                    },
                    "new": {
                        "stored_offset": block["stored_offset"],
                        "size": unpacked_size,
                        "zip": ZIP_DEFLATE if compressed else ZIP_STORED,
                        "name_offset": block["name_offset"],
                    },
                    "payload_offset": block["payload_offset"],
                    "payload_size": len(payload),
                    "block_end": block["block_end"],
                    "appended_bytes": block["appended_bytes"],
                }
            )
            if progress:
                progress(index, total, f"Appended {path}")

        file_obj.flush()
        os.fsync(file_obj.fileno())

        for entry in applied:
            new = entry["new"]
            write_toc_record(
                file_obj,
                entry["toc_offset"],
                entry["hash"],
                new["stored_offset"],
                new["size"],
                new["zip"],
                new["name_offset"],
                entry["name_size"],
            )
        previous_data_size = read_data_size(file_obj)
        file_obj.seek(0, os.SEEK_END)
        grown = align_up(file_obj.tell() - base, SECTOR)
        if grown > previous_data_size:
            write_data_size(file_obj, grown)
        file_obj.flush()
        os.fsync(file_obj.fileno())

    state["mods"].append(
        {
            "id": mod_id,
            "name": name or mod_id,
            "description": description,
            "applied_utc": utc_now(),
            "entries": applied,
        }
    )
    save_enabled_mods(volume_path, state)

    appended = sum(entry["appended_bytes"] for entry in applied)
    log.info("Enabled %s, %d files, %s appended", mod_id, len(applied), human_size(appended))
    if progress:
        progress(total, total, f"Enabled {mod_id}")

    return {
        "mod_id": mod_id,
        "entries": len(applied),
        "appended_bytes": appended,
        "volume_size": volume_path.stat().st_size,
    }


PLACE_INPLACE = "inplace"
PLACE_SLIDE = "slide"
PLACE_APPEND = "append"


def choose_placement(record: dict, extent: int, payload: bytes, unpacked_size: int) -> str:
    """
    Decide where a replacement can go, cheapest first

    inplace  the payload fits the run the original occupied, so it's written
             there, the leftovers are wiped, and the index is left alone
    slide    it's a little bigger so the name moves down into the block's own
             trailing slack and only its offset changes
    append   it doesn't fit the block at all or it unpacks to a different size,
             so it goes on the end like an append mod would do
    """
    if unpacked_size != int(record["size"]):
        return PLACE_APPEND
    if len(payload) <= int(record["name_offset"]) - int(record["stored_offset"]):
        return PLACE_INPLACE
    if len(payload) <= payload_room(record, extent):
        return PLACE_SLIDE
    return PLACE_APPEND


def apply_overwrite(
    volume_path: Path,
    taildata: dict,
    payloads: Iterable[tuple[str, bytes | Path]],
    mod_id: str,
    name: str = "",
    description: str = "",
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Put every replacement back inside the block it came from where it can
    """
    volume_path = Path(volume_path)
    if not volume_path.is_file():
        raise WriteError(f"Archive not found: {volume_path}")
    if not backup_available(volume_path):
        raise WriteError(
            "Overwrite mods rewrite bytes that are already in the archive, so they can "
            "only be enabled once the backup copy exists. Wait for it to finish."
        )

    state = load_enabled_mods(volume_path, taildata)
    if any(mod.get("id") == mod_id for mod in state["mods"]):
        raise WriteError(f"{mod_id} is already enabled")

    base = int(taildata["base"])
    items = list(payloads)
    total = len(items)
    if not total:
        raise WriteError("This mod has no files that match the taildata manifest")

    extents = block_extents(taildata)
    prepared: list[tuple[dict, bytes, bytes, int]] = []
    for path, source in items:
        record = resolve_record(taildata, path)
        key = normalize_archive_path(path)
        extent = extents.get(key, int(record["name_offset"]) + int(record["name_size"])
                             - int(record["stored_offset"]))
        if isinstance(source, ReadyPayload):
            payload, unpacked_size = source.data, source.unpacked_size
        else:
            data = (
                Path(source).read_bytes()
                if isinstance(source, (str, Path))
                else bytes(source)
            )
            unpacked_size = len(data)
            payload = compress_payload(data) if bool(record["compressed"]) else data

        stored = int(record["stored_offset"])
        placement = choose_placement(record, extent, payload, unpacked_size)
        prepared.append(
            (
                {
                    "path": key,
                    "toc_offset": int(record["toc_offset"]),
                    "mode": "overwrite",
                    "placement": placement,
                    "region_start": base + stored,
                    "region_size": extent,
                    "payload_size": len(payload),
                    "unpacked_size": unpacked_size,
                    "compressed": bool(record["compressed"]),
                    "hash": int(record["hash"]),
                    "name_size": int(record["name_size"]),
                    "default": {
                        "stored_offset": stored,
                        "size": int(record["size"]),
                        "zip": int(record["zip"]),
                        "name_offset": int(record["name_offset"]),
                    },
                    "new": {
                        "stored_offset": stored,
                        "size": int(record["size"]),
                        "zip": int(record["zip"]),
                        "name_offset": int(record["name_offset"]),
                    },
                },
                payload,
                b"",
                extent,
            )
        )

    applied: list[dict] = []
    rewritten: list[dict] = []
    appended_bytes = 0
    with volume_path.open("r+b") as file_obj:
        for index, (entry, payload, _unused, extent) in enumerate(prepared, start=1):
            default = entry["default"]
            original_room = int(default["name_offset"]) - int(default["stored_offset"])
            file_obj.seek(base + int(default["name_offset"]))
            name_bytes = read_exact(file_obj, entry["name_size"], f"{entry['path']} name")
            placement = entry["placement"]

            if placement == PLACE_INPLACE:
                file_obj.seek(entry["region_start"])
                file_obj.write(payload.ljust(original_room, b"\x00"))
                label = "Rewrote"
            elif placement == PLACE_SLIDE:
                file_obj.seek(entry["region_start"])
                file_obj.write((payload + name_bytes).ljust(extent, b"\x00"))
                entry["new"]["name_offset"] = int(default["stored_offset"]) + len(payload)
                rewritten.append(entry)
                label = "Rewrote"
            else:
                block = append_block(file_obj, base, payload, name_bytes)
                entry["new"] = {
                    "stored_offset": block["stored_offset"],
                    "size": entry["unpacked_size"],
                    "zip": ZIP_DEFLATE if entry["compressed"] else ZIP_STORED,
                    "name_offset": block["name_offset"],
                }
                entry["appended_bytes"] = block["appended_bytes"]
                appended_bytes += block["appended_bytes"]
                rewritten.append(entry)
                label = "Appended"
            applied.append(entry)
            if progress:
                progress(index, total, f"{label} {entry['path']}")
        file_obj.flush()
        os.fsync(file_obj.fileno())
        for entry in rewritten:
            new = entry["new"]
            write_toc_record(
                file_obj, entry["toc_offset"], entry["hash"],
                new["stored_offset"], new["size"], new["zip"],
                new["name_offset"], entry["name_size"],
            )
        if appended_bytes:
            previous_data_size = read_data_size(file_obj)
            file_obj.seek(0, os.SEEK_END)
            grown = align_up(file_obj.tell() - base, SECTOR)
            if grown > previous_data_size:
                write_data_size(file_obj, grown)
        if rewritten:
            file_obj.flush()
            os.fsync(file_obj.fileno())

    state["mods"].append(
        {
            "id": mod_id,
            "name": name or mod_id,
            "description": description,
            "mode": "overwrite",
            "applied_utc": utc_now(),
            "entries": applied,
        }
    )
    save_enabled_mods(volume_path, state)

    counts = collections.Counter(entry["placement"] for entry in applied)
    written = sum(
        entry["region_size"] for entry in applied if entry["placement"] != PLACE_APPEND
    )
    log.info(
        "Enabled %s in overwrite mode, %d in place, %d slid, %d appended (%s added)",
        mod_id, counts[PLACE_INPLACE], counts[PLACE_SLIDE], counts[PLACE_APPEND],
        human_size(appended_bytes),
    )
    if progress:
        progress(total, total, f"Enabled {mod_id}")

    return {
        "mod_id": mod_id,
        "entries": len(applied),
        "in_place": counts[PLACE_INPLACE] + counts[PLACE_SLIDE],
        "appended_entries": counts[PLACE_APPEND],
        "appended_bytes": appended_bytes,
        "overwritten_bytes": written,
        "volume_size": volume_path.stat().st_size,
    }


def apply_package(
    volume_path: Path,
    taildata: dict,
    package_path: Path,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Enable a .at package, in whichever mode it was bottled for
    """
    from .recipe import (
        APPLY_OVERWRITE,
        check_against_taildata,
        package_apply_mode,
        package_ready_payloads,
        read_package_manifest,
    )

    package_path = Path(package_path)
    manifest = read_package_manifest(package_path)
    check_against_taildata(manifest, taildata)
    writer = apply_overwrite if package_apply_mode(manifest) == APPLY_OVERWRITE else apply_mod
    return writer(
        volume_path,
        taildata,
        package_ready_payloads(package_path, manifest),
        mod_id=package_path.name,
        name=manifest.get("name") or package_path.stem,
        description=manifest.get("description", ""),
        progress=progress,
    )


def disable_overwrite_mod(
    volume_path: Path,
    taildata: dict,
    state: dict,
    mod: dict,
    remaining: list[dict],
    mod_id: str,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Put the original bytes back from the backup
    """
    regions: list[tuple[int, int]] = []
    rewind: list[dict] = []
    handed_over = 0
    for entry in mod.get("entries", []):
        start = int(entry["region_start"])
        if region_owner(remaining, start) is not None:
            handed_over += 1
            continue
        if entry.get("placement", PLACE_INPLACE) != PLACE_APPEND:
            regions.append((start, int(entry["region_size"])))
        rewind.append(entry)

    written = restore_regions_from_backup(volume_path, regions, progress)
    if rewind:
        with volume_path.open("r+b") as file_obj:
            for entry in rewind:
                default = entry["default"]
                write_toc_record(
                    file_obj, int(entry["toc_offset"]), int(entry["hash"]),
                    int(default["stored_offset"]), int(default["size"]),
                    int(default["zip"]), int(default["name_offset"]),
                    int(entry["name_size"]),
                )
            file_obj.flush()
            os.fsync(file_obj.fileno())

    state["mods"] = remaining
    save_enabled_mods(volume_path, state)

    dead_bytes = max(0, volume_path.stat().st_size - int(state["original_size"]))
    log.info(
        "Disabled overwrite mod %s, %d region(s) back to vanilla (%s), %d left to another mod",
        mod_id, len(regions), human_size(written), handed_over,
    )
    if progress:
        progress(1, 1, f"Disabled {mod_id}")

    return {
        "mod_id": mod_id,
        "restored_entries": len(regions),
        "handed_over_entries": handed_over,
        "reclaimed_bytes": 0,
        "dead_bytes": dead_bytes,
        "remaining_mods": len(remaining),
    }


def disable_mod(
    volume_path: Path,
    taildata: dict,
    mod_id: str,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Undo one mod by pointing its entries somewhere that is still alive
    """
    volume_path = Path(volume_path)
    state = load_enabled_mods(volume_path, taildata, strict=False)
    mods = state["mods"]
    mod_index = next((index for index, mod in enumerate(mods) if mod.get("id") == mod_id), -1)
    if mod_index < 0:
        raise WriteError(f"{mod_id} is not currently enabled")

    mod = mods[mod_index]
    entries = list(reversed(mod.get("entries", [])))
    total = len(entries)
    remaining = mods[:mod_index] + mods[mod_index + 1 :]

    if mod.get("mode") == "overwrite":
        return disable_overwrite_mod(
            volume_path, taildata, state, mod, remaining, mod_id, progress
        )

    restored = 0
    handed_over = 0

    with volume_path.open("r+b") as file_obj:
        for index, entry in enumerate(entries, start=1):
            toc_offset = int(entry["toc_offset"])
            owner = current_owner(remaining, toc_offset)
            if owner is not None:
                target = owner["new"]
                handed_over += 1
                label = f"Left {entry['path']} to another mod"
            else:
                target = entry["default"]
                restored += 1
                label = f"Restored {entry['path']}"
            write_toc_record(
                file_obj,
                toc_offset,
                int(entry["hash"]),
                int(target["stored_offset"]),
                int(target["size"]),
                int(target["zip"]),
                int(target["name_offset"]),
                int(entry["name_size"]),
            )
            if progress:
                progress(index, total, label)
        file_obj.flush()
        os.fsync(file_obj.fileno())

    state["mods"] = remaining
    save_enabled_mods(volume_path, state)

    dead_bytes = max(0, volume_path.stat().st_size - int(state["original_size"]))
    log.info(
        "Disabled %s, %d entries back to vanilla, %d left to other mods, %s still appended",
        mod_id, restored, handed_over, human_size(dead_bytes),
    )
    if progress:
        progress(total, total, f"Disabled {mod_id}")

    return {
        "mod_id": mod_id,
        "restored_entries": restored,
        "handed_over_entries": handed_over,
        "reclaimed_bytes": 0,
        "dead_bytes": dead_bytes,
        "remaining_mods": len(remaining),
    }


def disable_all_mods(
    volume_path: Path,
    taildata: dict,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Every TOC record goes back to vanilla, then the tail comes off
    """
    volume_path = Path(volume_path)
    state = load_enabled_mods(volume_path, taildata, strict=False)
    mods = state["mods"]
    disabled = len(mods)
    original_size = int(state["original_size"])

    have_backup = backup_available(volume_path)
    backup_usable = have_backup and backup_original_size(volume_path) == original_size
    if have_backup and not backup_usable:
        log.warning(
            "The backup is %s but the taildata says the vanilla archive is %s, "
            "falling back to the ledger",
            human_size(backup_original_size(volume_path)), human_size(original_size),
        )

    overwritten = [
        (int(entry["region_start"]), int(entry["region_size"]))
        for mod in mods if mod.get("mode") == "overwrite"
        for entry in mod.get("entries", [])
    ]

    if backup_usable:
        if overwritten:
            if progress:
                progress(0, 1, "Putting overwritten files back")
            restore_regions_from_backup(volume_path, overwritten)
        if progress:
            progress(0, 1, "Restoring the vanilla index")
        result = restore_toc_from_backup(volume_path)
        restored = sum(len(mod.get("entries", [])) for mod in mods)
        reclaimed = max(0, result["previous_size"] - result["restored_size"])
        method = "backup"
        if progress:
            progress(1, 1, "Restored the vanilla index")
    else:
        if overwritten:
            log.warning(
                "%d file(s) were overwritten in place and the backup is missing, so "
                "their original bytes can't be put back. Restoring everything else.",
                len(overwritten),
            )
        touched: dict[int, dict] = {}
        for mod in mods:
            for entry in mod.get("entries", []):
                touched[int(entry["toc_offset"])] = entry
        entries = list(touched.values())
        restored = len(entries)
        method = "ledger"

        with volume_path.open("r+b") as file_obj:
            for index, entry in enumerate(entries, start=1):
                default = entry["default"]
                write_toc_record(
                    file_obj,
                    int(entry["toc_offset"]),
                    int(entry["hash"]),
                    int(default["stored_offset"]),
                    int(default["size"]),
                    int(default["zip"]),
                    int(default["name_offset"]),
                    int(entry["name_size"]),
                )
                if progress:
                    progress(index, restored, f"Restored {entry['path']}")
            file_obj.flush()
            os.fsync(file_obj.fileno())
        state["mods"] = []
        reclaimed = truncate_to_vanilla(volume_path, state)

    state["mods"] = []
    save_enabled_mods(volume_path, state)

    log.info(
        "Disabled all %d mods via the %s, %d entries restored, reclaimed %s",
        disabled, method, restored, human_size(reclaimed),
    )
    if progress:
        progress(1, 1, "Disabled all mods")

    return {
        "disabled_mods": disabled,
        "restored_entries": restored,
        "reclaimed_bytes": reclaimed,
        "method": method,
        "volume_size": volume_path.stat().st_size,
    }


def restore_vanilla(
    volume_path: Path | None = None,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Ignore the mod ledger and put the whole archive back byte for byte
    """
    volume_path = Path(volume_path) if volume_path else default_volume_path()
    result = restore_from_backup(volume_path, progress)
    path = mods_path(volume_path)
    if path.is_file():
        state = read_json(path, "enabled mods ledger")
        state["mods"] = []
        write_json(path, state)
    return result
