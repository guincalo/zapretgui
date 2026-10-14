from __future__ import annotations

"""Общее ядро: замена файлов программы с журналом на диске и откатом."""

import os
from pathlib import Path

import pytest

from zapret_update import swap


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for relative, data in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    return root


def _read(root: Path, skip: str = ".update") -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.relative_to(root).as_posix().startswith(skip)
    }


OLD = {"_internal/Zapret.exe": b"old exe", "lua/a.lua": b"a", "lua/gone.lua": b"gone", "user/settings.sqlite3": b"mine"}
NEW = {"_internal/Zapret.exe": b"new exe!", "lua/a.lua": b"a", "lua/added.lua": b"added"}


def _swap(tmp_path, *, remove=("lua/gone.lua",)):
    app = _tree(tmp_path / "app", OLD)
    stage = _tree(tmp_path / "stage", NEW)
    return app, swap.FileSwap(stage, app, app / ".update" / "2" / "swap", preserve=("user",), remove=remove)


def test_commit_replaces_changed_adds_new_removes_owned_and_keeps_user_data(tmp_path) -> None:
    app, files = _swap(tmp_path)
    files.prepare()
    files.commit()

    assert _read(app) == {**NEW, "user/settings.sqlite3": b"mine"}
    assert (files.unchanged, files.replaced, files.added, files.removed) == (1, 1, 1, 1)


def test_unknown_leftovers_stay_when_the_owner_list_says_nothing(tmp_path) -> None:
    app, files = _swap(tmp_path, remove=())
    files.prepare()
    files.commit()

    assert _read(app)["lua/gone.lua"] == b"gone"


def test_rollback_restores_everything(tmp_path) -> None:
    app, files = _swap(tmp_path)
    files.prepare()
    files.commit()

    assert files.rollback() == []
    assert _read(app) == OLD


def test_swap_interrupted_by_power_loss_is_undone_on_next_start(tmp_path) -> None:
    app, files = _swap(tmp_path)
    files.prepare()
    moves = []
    real_move = files._move

    def move_then_die(source, destination):
        if len(moves) == 2:
            raise SystemExit("питание пропало")
        moves.append(source)
        real_move(source, destination)

    files._move = move_then_die
    with pytest.raises(SystemExit):
        files.commit()
    assert _read(app) != OLD  # программа наполовину заменена

    assert swap.recover(app / ".update" / "2" / "swap") == []
    assert _read(app) == OLD
    assert swap.recover(app / ".update" / "2" / "swap") is None  # журнал убран


def test_new_version_never_overwrites_user_data(tmp_path) -> None:
    app = _tree(tmp_path / "app", OLD)
    stage = _tree(tmp_path / "stage", {**NEW, "user/settings.sqlite3": b"packaged default"})
    files = swap.FileSwap(stage, app, app / ".update" / "x", preserve=("user",))

    files.prepare()
    files.commit()

    assert (app / "user" / "settings.sqlite3").read_bytes() == b"mine"


def test_hardlinked_stage_files_count_as_unchanged(tmp_path) -> None:
    app = _tree(tmp_path / "app", {"big.bin": b"x" * 1000})
    stage = tmp_path / "stage"
    stage.mkdir()
    os.link(app / "big.bin", stage / "big.bin")
    files = swap.FileSwap(stage, app, app / ".update" / "x")

    files.prepare()

    assert (files.unchanged, files.replaced) == (1, 0)
