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
from .patcher import (
    EXE_FILENAME,
    INTRO_MODES,
    LOOSE_ROOT,
    PatchError,
    apply_patches,
    read_state,
)
from .wetworks import (
    LOG_PATH,
    GokonworksError,
    ensure_backups,
    log,
    restore_from_backup,
)

__all__ = [
    "CoreTools",
    "EXE_FILENAME",
    "INTRO_MODES",
    "LOG_PATH",
    "LOOSE_ROOT",
    "GokonworksError",
    "PatchError",
    "TAILDATA_FILENAME",
    "Volume",
    "VolumeEntry",
    "VolumeError",
    "ensure_backups",
    "find_taildata",
    "hash_name",
    "load_taildata",
    "log",
    "open_volume",
    "apply_patches",
    "read_state",
    "restore_from_backup",
    "unpack_status",
    "unpack_volume",
]

def __getattr__(name):
    if name == "CoreTools":
        from .gokon_gui import CoreTools

        return CoreTools
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
