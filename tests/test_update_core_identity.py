from __future__ import annotations

"""Общее ядро обновления: сумма в CORE_SHA256 совпадает с файлами.

Ядро одно на Zapret 2 GUI и Zapret KVN. Если этот тест упал, ядро правили без
пересчёта суммы: запустите ``python scripts/update_core_hash.py`` и перенесите
ядро в Zapret KVN (``scripts/sync_update_core.py`` там).
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_core_hash_matches_files() -> None:
    spec = importlib.util.spec_from_file_location("update_core_hash", ROOT / "scripts" / "update_core_hash.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    recorded = (module.CORE / module.HASH_FILE).read_text(encoding="ascii").strip()

    assert recorded == module.core_hash()


def test_core_has_no_dependencies_beyond_the_standard_library() -> None:
    import ast
    import sys

    for path in (ROOT / "src" / "zapret_update").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module or ""]
            # compression (zstd) — стандартная библиотека с Python 3.14.
            # zstandard — только запасной распаковщик в store.py для программ
            # на старом Python (Zapret KVN на 3.12); ядро работает и без него.
            allowed = {"__future__", "compression"} | ({"zstandard"} if path.name == "store.py" else set())
            for name in names:
                top = name.split(".")[0]
                assert top in sys.stdlib_module_names or top in allowed, f"{path.name}: {name}"
