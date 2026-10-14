"""Пересчитать контрольную сумму общего ядра обновления (src/zapret_update/CORE_SHA256).

Ядро одно на Zapret 2 GUI и Zapret KVN. После правки ядра здесь запустите
этот сценарий, закоммитьте, затем в Zapret KVN — ``scripts/sync_update_core.py``:
он кладёт байт-в-байт копию вместе с этой суммой. Тесты обеих программ
сверяют сумму с файлами, поэтому правка «только в одной копии» не пройдёт.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "src" / "zapret_update"
HASH_FILE = "CORE_SHA256"


def core_hash(directory: Path = CORE) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.py")):
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def main() -> int:
    value = core_hash()
    (CORE / HASH_FILE).write_text(value + "\n", encoding="ascii")
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
