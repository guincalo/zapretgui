from __future__ import annotations

"""Хранилище выпуска: файлы по контрольной сумме и заплатки zstd (Python 3.14)."""

import random
from pathlib import Path

import pytest

pytest.importorskip("compression.zstd", reason="сжатие zstd есть только в Python 3.14")

from zapret_update import builder, manifest, store  # noqa: E402


def _blob(size: int, seed: int) -> bytes:
    # Случайные байты почти не сжимаются — как код в настоящем exe.
    return random.Random(seed).randbytes(size)


def test_blob_round_trip_and_wrong_content_is_refused() -> None:
    data = _blob(50_000, 1)
    sha = manifest.hashlib.sha256(data).hexdigest()
    packed = store.compress(data)

    assert store.unpack_blob(packed, sha256=sha, size=len(data)) == data
    with pytest.raises(store.StoreError):
        store.unpack_blob(packed, sha256="0" * 64, size=len(data))
    with pytest.raises(store.StoreError):
        store.unpack_blob(b"not zstd", sha256=sha, size=len(data))


def test_patch_rebuilds_the_new_file_from_the_old_one_and_is_small() -> None:
    old = _blob(2_000_000, 2)
    new = old[:700_000] + b"new code" * 300 + old[700_000:]
    sha = manifest.hashlib.sha256(new).hexdigest()

    patch = store.make_patch(old, new)

    assert store.apply_patch(old, patch, sha256=sha, size=len(new)) == new
    assert len(patch) < len(store.compress(new)) / 10
    with pytest.raises(store.StoreError):
        store.apply_patch(old[:-1] + b"x", patch, sha256=sha, size=len(new))


def _layout(root: Path, files: dict[str, bytes]) -> dict[str, Path]:
    layout = {}
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        layout[relative] = path
    return layout


def _as_manifest(version: str, built: builder.StoreBuild) -> manifest.Manifest:
    return manifest.parse(
        {
            "schema": 1, "product": "zapret-gui", "channel": "dev", "version": version, "created": 1,
            "key": "k", "files": built.files, "patches": built.patches,
        }
    )


def test_store_compresses_new_files_once_and_patches_big_changed_ones(tmp_path) -> None:
    cache = tmp_path / "cache"
    big_old = _blob(1_000_000, 3)
    first = builder.build_store(
        layout=_layout(tmp_path / "v1", {"_internal/Zapret.exe": big_old, "lua/a.lua": b"print(1)"}),
        cache_dir=cache,
    )
    big_new = big_old[:500_000] + b"fix" * 100 + big_old[500_000:]
    second = builder.build_store(
        layout=_layout(tmp_path / "v2", {"_internal/Zapret.exe": big_new, "lua/a.lua": b"print(1)"}),
        cache_dir=cache,
        previous=[_as_manifest("1.0.0.1", first)],
    )

    assert first.compressed == 2
    # Не изменившийся lua/a.lua второй раз не сжимается.
    assert second.compressed == 1 and second.patched == 1
    exe = next(item for item in second.files if item["path"] == "_internal/Zapret.exe")
    assert second.patches == [
        {"from": first.files[0]["sha256"], "to": exe["sha256"], "size": second.patches[0]["size"]}
    ]
    assert second.patches[0]["size"] < exe["zsize"] / 10
    # Всё, на что ссылается выпуск, лежит в кэше и собирается обратно.
    for name in second.names:
        assert (cache / name).is_file()
    rebuilt = store.apply_patch(
        big_old, (cache / store.patch_name(first.files[0]["sha256"], exe["sha256"])).read_bytes(),
        sha256=exe["sha256"], size=exe["size"],
    )
    assert rebuilt == big_new


def test_old_file_for_a_patch_is_fetched_when_not_cached(tmp_path) -> None:
    old = _blob(800_000, 4)
    new = old + b"tail" * 1000
    old_sha = manifest.hashlib.sha256(old).hexdigest()
    previous = manifest.parse({
        "schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.1", "created": 1, "key": "k",
        "files": [{"path": "big.bin", "size": len(old), "sha256": old_sha, "zsize": 10}],
    })
    asked = []

    def read_old(sha, size):
        asked.append((sha, size))
        return old

    built = builder.build_store(
        layout=_layout(tmp_path / "v2", {"big.bin": new}), cache_dir=tmp_path / "cache",
        previous=[previous], read_old=read_old,
    )

    assert asked == [(old_sha, len(old))]
    assert len(built.patches) == 1


def test_manifest_with_store_fields_round_trips(tmp_path) -> None:
    built = builder.build_store(layout=_layout(tmp_path, {"a.txt": b"hello"}), cache_dir=tmp_path / "c")
    loaded = _as_manifest("1.0.0.2", built)

    assert loaded.files[0].zsize == built.files[0]["zsize"] > 0
    with pytest.raises(manifest.ManifestError):
        manifest.parse({
            "schema": 1, "product": "zapret-gui", "channel": "dev", "version": "1.0.0.2", "created": 1, "key": "k",
            "files": built.files, "patches": [{"from": "a" * 64, "to": "b" * 64, "size": 1}],
        })


def test_store_names_are_strict() -> None:
    assert store.is_store_name("a" * 64 + ".zst")
    assert store.is_store_name("a" * 64 + "-" + "b" * 64 + ".zst")
    assert not store.is_store_name("../" + "a" * 61 + ".zst")
