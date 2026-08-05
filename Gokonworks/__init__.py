from .ledger import (
    TAILDATA_FILENAME,
    Volume,
    VolumeEntry,
    VolumeError,
    find_taildata,
    hash_name,
    load_taildata,
    open_volume,
    unpack_status,
    unpack_volume,
)
from .recipe import PACKAGE_EXTENSION, RecipeError, create_package, read_package_manifest
from .returns import (
    MODS_FILENAME,
    WriteError,
    apply_mod,
    apply_package,
    disable_all_mods,
    disable_mod,
    list_enabled_mods,
    restore_vanilla,
)
from .wetworks import LOG_PATH, GokonworksError, ensure_backups, log

__all__ = [
    "CoreTools",
    "LOG_PATH",
    "MODS_FILENAME",
    "GokonworksError",
    "PACKAGE_EXTENSION",
    "RecipeError",
    "TAILDATA_FILENAME",
    "Volume",
    "VolumeEntry",
    "VolumeError",
    "WriteError",
    "apply_mod",
    "apply_package",
    "create_package",
    "disable_all_mods",
    "disable_mod",
    "ensure_backups",
    "find_taildata",
    "hash_name",
    "list_enabled_mods",
    "load_taildata",
    "log",
    "open_volume",
    "read_package_manifest",
    "restore_vanilla",
    "unpack_status",
    "unpack_volume",
]

def __getattr__(name):
    if name == "CoreTools":
        from .gokon_gui import CoreTools

        return CoreTools
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
