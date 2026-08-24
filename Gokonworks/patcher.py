"""
Handles the exe patches
"""

from __future__ import annotations
import hashlib, re, shutil, struct
from pathlib import Path
from .wetworks import GokonworksError, MODS_FOLDER, exe_backup_path, log

__all__ = [
    "EXE_FILENAME",
    "INTRO_MODES",
    "LOOSE_ROOT",
    "PatchError",
    "apply_patches",
    "read_state",
]

EXE_FILENAME = "AkibaUU.exe"

KNOWN_BUILDS = {
    "c3904599957575c957b30e89a4c0fc3fd531ac0f73b3ce29072504f772c03bf0": "Steam",
    "045b03750143d03be24379b43c0936c532a520f561d6ae325ef48be0d42f2254": "GOG",
}

LOOSE_ROOT = f"{MODS_FOLDER}/"

INTRO_VANILLA = 0x19
INTRO_MODES = {
    "vanilla": INTRO_VANILLA,
    "keep_op": 0x21,
    "skip_all": 0x22,
}
INTRO_NAMES = {value: name for name, value in INTRO_MODES.items()}


class PatchError(GokonworksError):
    pass

def compile_signature(text: str) -> re.Pattern:
    parts = [b"." if token == "??" else re.escape(bytes([int(token, 16)]))
             for token in text.split()]
    return re.compile(b"".join(parts), re.S)

SIGNATURES = {
    "loose_gate": ("C7 85 F0 FE FF FF ?? ?? ?? ?? C7 85 F4 FE FF FF A0 2C A1 00", 6, 4),
    "loose_resolver": ("C7 85 E4 FD FF FF ?? ?? ?? ?? C7 85 E8 FD FF FF A0 2C A1 00", 6, 4),
    "loose_aux": ("C7 85 E8 FE FF FF ?? ?? ?? ?? C7 85 EC FE FF FF A0 2C A1 00", 6, 4),
    "intro": ("0F 84 E4 0C 00 00 6A ?? 8B CB E8 0F 0E 00 00", 7, 1),
    "clothing_a": ("83 C4 08 46 ?? ?? ?? ?? ?? BE 01 00 00 00 56", 4, 5),
    "clothing_b": ("83 C4 0C 46 ?? ?? ?? ?? ?? 8B 4D F4 64 89 0D", 4, 5),
}

LOOSE_SITES = ("loose_gate", "loose_resolver", "loose_aux")
LOOSE_REQUIRED = ("loose_gate", "loose_resolver")

VANILLA_ROOT_VA = 0xA12DA8
LONGEST_VIRTUAL_PATH = 93
PATH_BUFFER_SIZE = 0x108

CLOTHING = {
    "clothing_a": {"cap": 0x46, "global_va": 0xAB32D0, "field": 0x18},
    "clothing_b": {"cap": 0x56, "global_va": 0xAB329C, "field": 0x4C},
}
STUB_SIZE = 19
PADDING = 0xCC

class ExeImage:

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = bytearray(self.path.read_bytes())
        self.sections = []
        try:
            pe = struct.unpack_from("<I", self.data, 0x3C)[0]
            if self.data[pe:pe + 4] != b"PE\0\0":
                raise ValueError("no PE signature")
            count = struct.unpack_from("<H", self.data, pe + 6)[0]
            opt_size = struct.unpack_from("<H", self.data, pe + 20)[0]
            self.image_base = struct.unpack_from("<I", self.data, pe + 24 + 28)[0]
            table = pe + 24 + opt_size
            for index in range(count):
                base = table + index * 40
                name = self.data[base:base + 8].rstrip(b"\0").decode("ascii", "ignore")
                vsize, vaddr, rawsize, rawoff = struct.unpack_from("<IIII", self.data, base + 8)
                self.sections.append(
                    {"name": name, "vsize": vsize, "vaddr": vaddr,
                     "rawsize": rawsize, "rawoff": rawoff}
                )
        except (struct.error, ValueError, IndexError) as exc:
            raise PatchError(f"{self.path.name} isnt a readable 32 bit exe ({exc})") from None
        if not self.sections:
            raise PatchError(f"{self.path.name} has no section table")

    def section(self, name: str) -> dict:
        for entry in self.sections:
            if entry["name"] == name:
                return entry
        raise PatchError(f"{self.path.name} has no {name} section")

    def va(self, offset: int) -> int:
        for entry in self.sections:
            if entry["rawoff"] <= offset < entry["rawoff"] + entry["rawsize"]:
                return self.image_base + entry["vaddr"] + (offset - entry["rawoff"])
        raise PatchError(f"Offset 0x{offset:X} is outside every section")

    def offset(self, va: int) -> int:
        rva = va - self.image_base
        for entry in self.sections:
            if entry["vaddr"] <= rva < entry["vaddr"] + max(entry["vsize"], entry["rawsize"]):
                return entry["rawoff"] + (rva - entry["vaddr"])
        raise PatchError(f"Address 0x{va:X} is outside every section")

    def find(self, key: str) -> tuple[int, int]:
        text, delta, length = SIGNATURES[key]
        hits = [m.start() for m in compile_signature(text).finditer(self.data)]
        if not hits:
            raise PatchError(
                f"{self.path.name} doesnt contain the {key} code, so it's not a "
                "build these patches understand."
            )
        if len(hits) > 1:
            raise PatchError(
                f"{self.path.name} matches the {key} signature {len(hits)} times. "
                "Refusing patching."
            )
        return hits[0] + delta, length

    def rdata_padding(self) -> tuple[int, int, int]:
        section = self.section(".rdata")
        offset = section["rawoff"] + section["vsize"]
        size = section["rawsize"] - section["vsize"]
        if size <= 0:
            raise PatchError(f"{self.path.name} has no spare room at the end of .rdata")
        return offset, self.image_base + section["vaddr"] + section["vsize"], size

    def take_stub(self) -> int:
        section = self.section(".text")
        lo = section["rawoff"]
        hi = lo + section["rawsize"]
        window = bytes(self.data[lo:hi])
        for match in re.finditer(bytes([PADDING]) + b"{%d,}" % STUB_SIZE, window):
            return lo + match.start()
        raise PatchError(f"{self.path.name} has no free padding left for a stub")

    def build_name(self) -> str:
        return KNOWN_BUILDS.get(hashlib.sha256(bytes(self.data)).hexdigest(), "")

    def save(self, backup: bool) -> Path | None:
        made = None
        if backup:
            target = exe_backup_path(self.path)
            if not target.exists():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self.path, target)
                    made = target
                except OSError as exc:
                    log.warning("Couldn't keep a copy of %s: %s", self.path.name, exc)
        self.path.write_bytes(bytes(self.data))
        return made

def normalize_root(root: str | None) -> tuple[str, bytes]:
    root = (root or LOOSE_ROOT).strip().replace("\\", "/")
    if not root:
        raise PatchError("The loose file folder name cant be empty.")
    if not root.endswith("/"):
        root += "/"
    try:
        encoded = root.encode("ascii")
    except UnicodeEncodeError:
        raise PatchError(
            f"{root} is not ASCII and the game reads the folder name as a C string."
        ) from None
    if len(encoded) + LONGEST_VIRTUAL_PATH + 1 > PATH_BUFFER_SIZE:
        raise PatchError(
            f"{root} is too long. With a {LONGEST_VIRTUAL_PATH} character asset path "
            f"it overruns the game's {PATH_BUFFER_SIZE} byte path buffer."
        )
    return root, encoded

def read_loose(image: ExeImage) -> dict:
    cave_off, cave_va, cave_size = image.rdata_padding()
    states = {}
    for key in LOOSE_SITES:
        offset, _length = image.find(key)
        value = struct.unpack_from("<I", image.data, offset)[0]
        if value == VANILLA_ROOT_VA:
            states[key] = "vanilla"
        elif value == cave_va:
            states[key] = "patched"
        else:
            states[key] = "unknown"

    required = [states[key] for key in LOOSE_REQUIRED]
    if all(state == "patched" for state in required):
        status = "on"
    elif all(state == "vanilla" for state in required):
        status = "off"
    else:
        status = "mixed"

    end = image.data.find(b"\0", cave_off, cave_off + cave_size)
    root = bytes(image.data[cave_off:end]).decode("ascii", "replace") if end > cave_off else ""
    return {"status": status, "root": root, "sites": states}

def write_loose(image: ExeImage, enabled: bool, root: str | None):
    cave_off, cave_va, cave_size = image.rdata_padding()
    if not enabled:
        for key in LOOSE_SITES:
            offset, _length = image.find(key)
            struct.pack_into("<I", image.data, offset, VANILLA_ROOT_VA)
        image.data[cave_off:cave_off + cave_size] = bytes(cave_size)
        return

    text, encoded = normalize_root(root)
    if len(encoded) + 1 > cave_size:
        raise PatchError(
            f"{text} needs {len(encoded) + 1} bytes, the exe only has {cave_size} spare."
        )
    for key in LOOSE_REQUIRED:
        offset, _length = image.find(key)
        current = struct.unpack_from("<I", image.data, offset)[0]
        if current not in (VANILLA_ROOT_VA, cave_va):
            raise PatchError(
                f"The {key} site holds 0x{current:X}. Something else has edited this "
                "exe, so loose file loading wasnt enabled."
            )
    image.data[cave_off:cave_off + cave_size] = bytes(cave_size)
    image.data[cave_off:cave_off + len(encoded)] = encoded
    for key in LOOSE_REQUIRED:
        offset, length = image.find(key)
        struct.pack_into("<I", image.data, offset, cave_va)

def read_intro(image: ExeImage) -> dict:
    offset, length = image.find("intro")
    if image.data[offset - 1] != 0x6A:
        raise PatchError("The intro site isnt the push instruction it should be.")
    value = image.data[offset]
    return {"status": INTRO_NAMES.get(value, "custom"), "value": value}


def write_intro(image: ExeImage, mode: str):
    if mode not in INTRO_MODES:
        raise PatchError(f"{mode} isn't one of {', '.join(INTRO_MODES)}.")
    offset, _length = image.find("intro")
    if image.data[offset - 1] != 0x6A:
        raise PatchError("The intro site isnt the push instruction it should be.")
    image.data[offset] = INTRO_MODES[mode]

def stub_bytes(global_va: int, field: int, loop_top_va: int, fall_va: int, stub_va: int) -> bytes:
    body = struct.pack("<BI", 0xA1, global_va)
    body += bytes([0x3B, 0x70, field])
    body += b"\x0F\x8C" + struct.pack("<i", loop_top_va - (stub_va + len(body) + 6))
    body += b"\xE9" + struct.pack("<i", fall_va - (stub_va + len(body) + 5))
    if len(body) != STUB_SIZE:
        raise PatchError(f"Built a {len(body)} byte stub, expected {STUB_SIZE}.")
    return body

def read_clothing(image: ExeImage) -> dict:
    sites = {}
    for key, spec in CLOTHING.items():
        offset, _length = image.find(key)
        head = image.data[offset]
        if head == 0x83 and image.data[offset + 1] == 0xFE:
            sites[key] = "vanilla" if image.data[offset + 2] == spec["cap"] else "unknown"
        elif head == 0xE9:
            sites[key] = "patched"
        else:
            sites[key] = "unknown"
    values = list(sites.values())
    if all(value == "patched" for value in values):
        status = "on"
    elif all(value == "vanilla" for value in values):
        status = "off"
    else:
        status = "mixed"
    return {"status": status, "sites": sites}

def write_clothing(image: ExeImage, enabled: bool):
    for key, spec in CLOTHING.items():
        offset, length = image.find(key)
        site_va = image.va(offset)
        head = image.data[offset]

        if enabled:
            if head == 0xE9:
                continue
            if head != 0x83 or image.data[offset + 1] != 0xFE:
                raise PatchError(f"The {key} site isnt the compare it should be.")
            if image.data[offset + 2] != spec["cap"]:
                raise PatchError(
                    f"The {key} site caps at {image.data[offset + 2]}, expected "
                    f"{spec['cap']}. Skipping patching."
                )
            loop_top = site_va + length + struct.unpack_from("<b", image.data, offset + 4)[0]
            stub_off = image.take_stub()
            stub_va = image.va(stub_off)
            body = stub_bytes(spec["global_va"], spec["field"], loop_top, site_va + length, stub_va)
            image.data[stub_off:stub_off + STUB_SIZE] = body
            image.data[offset:offset + length] = (
                b"\xE9" + struct.pack("<i", stub_va - (site_va + 5))
            )
            log.info("Clothing stub for %s at 0x%X, cap was %d", key, stub_va, spec["cap"])
            continue

        if head != 0xE9:
            continue
        stub_va = site_va + 5 + struct.unpack_from("<i", image.data, offset + 1)[0]
        stub_off = image.offset(stub_va)
        if image.data[stub_off] != 0xA1:
            raise PatchError(
                f"The {key} jump doesnt land on one of the stubs, so nothing was "
                "reverted. This exe was patched by something else."
            )
        jl_target = image.va(stub_off) + 14 + struct.unpack_from("<i", image.data, stub_off + 10)[0]
        image.data[stub_off:stub_off + STUB_SIZE] = bytes([PADDING]) * STUB_SIZE
        image.data[offset:offset + length] = (
            bytes([0x83, 0xFE, spec["cap"], 0x7C])
            + struct.pack("<b", jl_target - (site_va + length))
        )

def read_state(exe_path: Path) -> dict:
    image = ExeImage(exe_path)
    loose = read_loose(image)
    root = loose["root"]
    folder = image.path.resolve().parent / root.rstrip("/") if root else None
    return {
        "exe": str(image.path),
        "build": image.build_name(),
        "loose": loose,
        "intro": read_intro(image),
        "clothing": read_clothing(image),
        "loose_dir": str(folder) if folder else "",
        "loose_dir_exists": bool(folder and folder.is_dir()),
    }


def apply_patches(
    exe_path: Path,
    loose: bool | None = None,
    root: str | None = None,
    intro: str | None = None,
    clothing: bool | None = None,
    backup: bool = True,
) -> dict:
    
    image = ExeImage(exe_path)
    before = bytes(image.data)

    if loose is not None:
        write_loose(image, loose, root)
    if intro is not None:
        write_intro(image, intro)
    if clothing is not None:
        write_clothing(image, clothing)

    changed = bytes(image.data) != before
    made = image.save(backup) if changed else None
    if changed:
        log.info(
            "Patched %s (loose=%s intro=%s clothing=%s)", image.path, loose, intro, clothing
        )
    result = read_state(image.path)
    result["changed"] = changed
    result["backup"] = str(made) if made else None
    return result
