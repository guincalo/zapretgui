from __future__ import annotations

"""Общее ядро: страховка замены — разовая задача Windows доводит откат без exe программы."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

from zapret_update import apply, handoff, rescue


def test_each_program_folder_has_its_own_rescue_entry() -> None:
    dev = rescue.value_name(r"C:\Zapret\Dev")
    assert dev == rescue.value_name(r"C:\Zapret\Dev")
    assert dev != rescue.value_name(r"C:\Zapret\Stable")
    assert dev.startswith("ZapretUpdateRescue-")


def test_runonce_line_fits_the_windows_limit() -> None:
    line = rescue.command_line(r"C:\ProgramData\Zapret\update\dev\rescue", rescue.value_name(r"C:\Zapret\Dev"))
    # Windows не выполняет строки RunOnce длиннее 260 знаков.
    assert len(line) < 200
    assert "rescue.ps1" in line and "-File" in line


def test_script_and_its_settings_are_written_next_to_each_other(tmp_path) -> None:
    command = rescue.write_files(
        tmp_path, "ZapretUpdateRescue-x", app_dir=r"C:\Zapret\Dev", swap_dir=r"C:\Zapret\Dev\.update\2\swap",
        start=[r"C:\Zapret\Dev\_internal\Zapret.exe", "--tray"],
    )

    assert (tmp_path / "rescue.ps1").read_bytes().startswith(b"\xef\xbb\xbf")  # PowerShell 5.1 читает UTF-8 только с BOM
    settings = json.loads((tmp_path / "ZapretUpdateRescue-x.json").read_text(encoding="utf-8"))
    assert settings["swap_dir"].endswith("swap") and settings["command"] == command
    assert settings["start"][1] == "--tray"


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for relative, data in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    return root


def _plan(tmp_path: Path) -> apply.ApplyPlan:
    app = _tree(tmp_path / "app", {"_internal/Zapret.exe": b"old"})
    tree = _tree(tmp_path / "tree", {"_internal/Zapret.exe": b"new!"})
    return apply.ApplyPlan(
        product="Zapret 2 GUI", version="2", from_version="1", app_dir=str(app), tree_dir=str(tree),
        work_dir=str(app / ".update" / "2"), app_pid=1, app_exe="_internal/Zapret.exe", remove=[],
        failure_file=str(app / ".update" / "failure.txt"), error_log=str(app / ".update" / "error.log"),
        rescue_dir=str(tmp_path / "rescue"),
    )


class _Hooks(apply.Hooks):
    def close_application(self, plan):
        pass

    def show_fatal(self, plan, details):
        pass


def _process(*, exits: bool):
    process = Mock()
    process.poll.return_value = 3 if exits else None
    process.returncode = 3 if exits else None
    return process


def test_rescue_is_armed_before_the_swap_and_removed_after_success(tmp_path) -> None:
    plan = _plan(tmp_path)
    order = []
    exe = Path(plan.app_dir) / "_internal" / "Zapret.exe"

    def arm(*_a, **_k):
        order.append(("arm", exe.read_bytes()))  # ещё прежний exe: замена не началась
        return True

    def start(*_a, **_k):
        handoff.confirm_ready(plan.hold_marker)
        return _process(exits=False)

    with patch.object(apply.rescue, "arm", side_effect=arm), patch.object(
        apply.rescue, "disarm", side_effect=lambda *_a: order.append("disarm")
    ), patch.object(apply.processes, "start_detached", side_effect=start):
        assert apply.run_apply(plan, _Hooks()) is True

    assert order == [("arm", b"old"), "disarm"]


def test_rescue_stays_armed_when_the_rollback_could_not_finish(tmp_path) -> None:
    plan = _plan(tmp_path)
    started = [_process(exits=True), _process(exits=False)]
    disarm = Mock()
    real_move = apply.FileSwap._move
    exe = Path(plan.app_dir) / "_internal" / "Zapret.exe"

    def move(self, source, destination):
        if Path(source) == exe and "new" in Path(destination).parts:
            raise PermissionError(13, "занят антивирусом", str(source))
        real_move(self, source, destination)

    with patch.object(apply.rescue, "arm", return_value=True), patch.object(apply.rescue, "disarm", disarm), patch.object(
        apply.processes, "start_detached", side_effect=lambda *a, **k: started.pop(0)
    ), patch.object(apply, "OLD_VERSION_READY_WAIT_S", 0.2), patch.object(apply, "ROLLBACK_RETRY_PAUSE_S", 0.0), patch.object(
        apply.processes, "stop_processes_under", return_value=[]
    ), patch.object(apply.FileSwap, "_move", move):
        assert apply.run_apply(plan, _Hooks()) is False

    disarm.assert_not_called()  # при следующем входе страховка доведёт откат
    assert Path(plan.failure_file).read_text() == "rback"
