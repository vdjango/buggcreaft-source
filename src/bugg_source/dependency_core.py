"""Service adapter for the exact C++ kernel linked into the native launcher.

No approximate Python range evaluator or solver fallback. Optional installation
is explicit; failure stays unavailable rather than claiming compatibility.
"""

import ctypes
import json
from pathlib import Path

from .contracts import CollectionError


class DependencyCore:
    def __init__(self, library: Path | None):
        self.library = None
        if library:
            loaded = ctypes.CDLL(str(library.resolve(strict=True)))
            loaded.bugg_dependency_version.restype = ctypes.c_char_p
            if loaded.bugg_dependency_version() != b"bugg-dependency-core-v1":
                raise CollectionError("dependency_core_version_mismatch")
            loaded.bugg_dependency_evaluate.argtypes = [ctypes.c_char_p, ctypes.c_int]
            loaded.bugg_dependency_evaluate.restype = ctypes.c_void_p
            loaded.bugg_dependency_free.argtypes = [ctypes.c_void_p]
            self.library = loaded

    def evaluate(self, value: dict, *, solve: bool = False) -> dict:
        if self.library is None:
            raise CollectionError("dependency_core_unavailable")
        raw = json.dumps(value, ensure_ascii=False).encode()
        if len(raw) > 8 * 1024 * 1024:
            raise CollectionError("dependency_check_input_budget")
        result = self.library.bugg_dependency_evaluate(raw, int(solve))
        if not result:
            raise CollectionError("dependency_check_invalid_input")
        try:
            return json.loads(ctypes.string_at(result))
        finally:
            self.library.bugg_dependency_free(result)
