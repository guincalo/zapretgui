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


def _lock_once(files, path: Path):
    """Первый перенос ``path`` падает «файл занят» (антивирус), дальше — как обычно."""
    real_move = files._move
    state = {"locked": True}

    def move(source, destination):
        if state["locked"] and Path(source) == path:
            state["locked"] = False
            raise PermissionError(13, "занят антивирусом", str(source))
        real_move(source, destination)

    files._move = move


def test_rollback_that_hit_a_locked_file_keeps_it_in_the_journal(tmp_path) -> None:
    app, files = _swap(tmp_path)
    files._lock_wait_s = 0.0
    files.prepare()
    files.commit()
    _lock_once(files, app / "_internal" / "Zapret.exe")

    errors = files.rollback()

    assert errors and "Zapret.exe" in errors[0]
    work = app / ".update" / "2" / "swap"
    # Прежний exe ещё в old: журнал его помнит, уборка папку не тронет.
    assert (work / "old" / "_internal" / "Zapret.exe").read_bytes() == b"old exe"
    assert swap.pending(work)
    # Повтор доделывает только оставшееся — и программа целиком прежняя.
    assert files.rollback() == []
    assert _read(app) == OLD
    assert not swap.pending(work)


def test_recover_repeats_only_what_failed(tmp_path, monkeypatch) -> None:
    app, files = _swap(tmp_path)
    files._lock_wait_s = 0.0
    files.prepare()
    files.commit()
    _lock_once(files, app / "_internal" / "Zapret.exe")
    assert files.rollback()

    work = app / ".update" / "2" / "swap"
    # Раньше журнал после такого отката был пуст: recover() ничего не делал,
    # а уборка через сутки стирала old с последней копией программы.
    assert swap.recover(work) == []
    assert _read(app) == OLD
    assert swap.recover(work) is None


def test_next_attempt_finishes_the_old_rollback_before_wiping_old(tmp_path) -> None:
    app, files = _swap(tmp_path)
    files._lock_wait_s = 0.0
    files.prepare()
    files.commit()
    _lock_once(files, app / "_internal" / "Zapret.exe")
    assert files.rollback()

    again = swap.FileSwap(tmp_path / "stage", app, app / ".update" / "2" / "swap", preserve=("user",), remove=["lua/gone.lua"])
    again.prepare()

    # Прежний exe не стёрт вместе с old, а возвращён на место до новой попытки.
    assert (app / "_internal" / "Zapret.exe").read_bytes() == b"old exe"
    again.commit()
    assert _read(app) == {**NEW, "user/settings.sqlite3": b"mine"}


def test_next_attempt_stops_when_the_old_rollback_still_cannot_finish(tmp_path, monkeypatch) -> None:
    app, files = _swap(tmp_path)
    files._lock_wait_s = 0.0
    files.prepare()
    files.commit()
    _lock_once(files, app / "_internal" / "Zapret.exe")
    assert files.rollback()
    monkeypatch.setattr(swap.FileSwap, "_move", lambda self, s, d: (_ for _ in ()).throw(PermissionError(13, "занят")))

    again = swap.FileSwap(tmp_path / "stage", app, app / ".update" / "2" / "swap", preserve=("user",))
    with pytest.raises(swap.InstallError, match="не откачено"):
        again.prepare()

    assert (app / ".update" / "2" / "swap" / "old" / "_internal" / "Zapret.exe").read_bytes() == b"old exe"


def test_old_journal_does_not_undo_a_reinstall(tmp_path) -> None:
    app, files = _swap(tmp_path)
    files.prepare()
    files.commit()  # журнал остался: «питание пропало» до уборки
    # Человек переустановил программу установщиком поверх.
    (app / "_internal" / "Zapret.exe").write_bytes(b"reinstalled exe")
    os.utime(app / "_internal" / "Zapret.exe", ns=(1, 1))

    swap.recover(app / ".update" / "2" / "swap")

    assert (app / "_internal" / "Zapret.exe").read_bytes() == b"reinstalled exe"


def test_program_is_without_its_exe_for_one_rename_only(tmp_path) -> None:
    app, files = _swap(tmp_path)
    files.prepare()
    exe = app / "_internal" / "Zapret.exe"
    missing_after = []
    real_move = files._move

    def move(source, destination):
        real_move(source, destination)
        missing_after.append(not exe.exists())

    files._move = move
    files.commit()

    # Раньше exe отсутствовал, пока выносились все заменяемые и лишние файлы.
    assert sum(missing_after) == 1


def test_own_files_in_a_shipped_folder_survive(tmp_path) -> None:
    app = _tree(tmp_path / "app", {**OLD, "presets/Default.txt": b"builtin", "presets/My own.txt": b"mine"})
    stage = _tree(tmp_path / "stage", {**NEW, "presets/Default.txt": b"builtin v2"})
    files = swap.FileSwap(stage, app, app / ".update" / "x", preserve=("user",), keep=("presets",))

    files.prepare()
    files.commit()

    assert (app / "presets" / "My own.txt").read_bytes() == b"mine"
    assert (app / "presets" / "Default.txt").read_bytes() == b"builtin v2"
    assert not (app / "lua" / "gone.lua").exists()  # остальная поставка убирается как прежде
