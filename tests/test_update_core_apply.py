from __future__ import annotations

"""Общее ядро: новая версия ставит себя — порядок шагов, проверка запуска, откат."""

from pathlib import Path
from unittest.mock import Mock, patch

from zapret_update import apply, handoff


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for relative, data in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    return root


def _read(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and not p.relative_to(root).as_posix().startswith(".update")
    }


OLD = {"_internal/Zapret.exe": b"old", "user/settings.sqlite3": b"mine"}
NEW = {"_internal/Zapret.exe": b"new!"}


class _Hooks(apply.Hooks):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def close_application(self, plan):
        self.calls.append("close")

    def before_swap(self, plan):
        self.calls.append("before")

    def after_swap(self, plan):
        self.calls.append("after")

    def after_rollback(self, plan):
        self.calls.append("rollback")

    def show_fatal(self, plan, details):
        self.calls.append("fatal")


def _plan(tmp_path: Path) -> apply.ApplyPlan:
    app = _tree(tmp_path / "app", OLD)
    tree = _tree(tmp_path / "tree", NEW)
    return apply.ApplyPlan(
        product="Zapret 2 GUI", version="1.0.0.2", from_version="1.0.0.1", app_dir=str(app),
        tree_dir=str(tree), work_dir=str(app / ".update" / "1.0.0.2"), app_pid=1, app_exe="_internal/Zapret.exe",
        restart_args=["--tray"], preserve=["user"], remove=[],
        failure_file=str(app / ".update" / "failure.txt"), error_log=str(app / ".update" / "error.log"),
    )


def _process(*, exits: bool, mark_ready_for: apply.ApplyPlan | None = None):
    process = Mock()
    process.poll.return_value = 3 if exits else None
    process.returncode = 3 if exits else None
    if mark_ready_for is not None:
        handoff.confirm_ready(mark_ready_for.hold_marker)
    return process


def test_new_version_installs_itself_and_starts(tmp_path) -> None:
    plan = _plan(tmp_path)
    hooks = _Hooks()
    stages = []

    def start(command, cwd, env=None):
        assert command == [str(plan.exe_path), "--tray"]
        assert env == {handoff.HOLD_ENV: str(plan.hold_marker)}
        return _process(exits=False, mark_ready_for=plan)

    with patch.object(apply.processes, "start_detached", side_effect=start):
        assert apply.run_apply(plan, hooks, lambda stage, _f: stages.append(stage)) is True

    assert _read(Path(plan.app_dir)) == {**NEW, "user/settings.sqlite3": b"mine"}
    assert hooks.calls == ["close", "before", "after"]
    assert stages[-1] is apply.Stage.DONE
    assert not (Path(plan.work_dir) / "swap").exists()  # рабочая папка убрана


def test_new_version_that_dies_on_start_is_rolled_back(tmp_path) -> None:
    plan = _plan(tmp_path)
    hooks = _Hooks()
    started = [_process(exits=True), _process(exits=False)]

    with patch.object(apply.processes, "start_detached", side_effect=lambda *a, **k: started.pop(0)), patch.object(
        apply, "OLD_VERSION_READY_WAIT_S", 0.2
    ):
        assert apply.run_apply(plan, hooks) is False

    assert _read(Path(plan.app_dir)) == OLD
    assert Path(plan.failure_file).read_text() == "crash"
    assert "Прежняя версия возвращена" in Path(plan.error_log).read_text(encoding="utf-8")
    assert hooks.calls == ["close", "before", "after", "close", "rollback"]
    assert started == []  # прежняя версия запущена снова


def test_plan_survives_a_round_trip(tmp_path) -> None:
    plan = _plan(tmp_path)
    plan.save(tmp_path / "plan.json")

    assert apply.ApplyPlan.load(tmp_path / "plan.json") == plan


def test_failure_codes_name_the_step() -> None:
    locked = OSError("busy")
    locked.path = Path("x")
    assert apply.failure_code(apply.Stage.START, RuntimeError()) == "crash"
    assert apply.failure_code(apply.Stage.SWAP, locked) == "lock"
    assert apply.failure_code(apply.Stage.PREPARE, RuntimeError()) == "prep"


def test_exit_codes_are_readable() -> None:
    assert apply.exit_code_text(3) == "код 3"
    assert apply.exit_code_text(-1073741510) == "код 0xC000013A, прерван Ctrl+C"
    assert apply.exit_code_text(None) == "код неизвестен"


def test_failed_start_hook_hears_the_mark_and_the_exit_code(tmp_path) -> None:
    plan = _plan(tmp_path)
    heard = []

    class Hooks(_Hooks):
        def before_start(self, plan):
            return "mark"

        def failed_start(self, plan, mark, exit_code):
            heard.append((mark, exit_code))

    started = [_process(exits=True), _process(exits=False)]
    with patch.object(apply.processes, "start_detached", side_effect=lambda *a, **k: started.pop(0)), patch.object(
        apply, "OLD_VERSION_READY_WAIT_S", 0.2
    ):
        apply.run_apply(plan, Hooks())

    assert heard == [("mark", 3)]


def test_holder_code_is_a_short_name_without_path_or_extension() -> None:
    from zapret_update import processes

    assert processes.holder_code("C:\\Program Files\\Windows Defender\\MsMpEng.exe", "Antimalware Service") == "msmpeng"
    assert processes.holder_code("C:\\Windows\\System32\\svchost.exe", "Windows Search", "WSearch") == "wsearch"
    assert processes.holder_code("", "7-Zip File Manager") == "p7zipfilemanager"
    assert processes.holder_code("D:\\Очень длинное имя программы для проверки.exe") == ""


def test_locked_file_names_its_holder_for_the_server(tmp_path) -> None:
    from zapret_update import swap

    plan = _plan(tmp_path)
    plan.blocker_file = str(Path(plan.app_dir) / ".update" / "blocker.txt")
    locked = Path(plan.app_dir) / "_internal" / "Zapret.exe"

    def busy(self):
        raise swap.InstallError("Файл занят другой программой", path=locked)

    with patch.object(swap.FileSwap, "commit", busy), patch.object(
        apply.processes, "lock_holders", return_value=["Antimalware Service (C:\\av\\MsMpEng.exe)"]
    ), patch.object(apply.processes, "lock_holder_codes", return_value=["msmpeng"]) as codes, patch.object(
        apply.processes, "start_detached", return_value=_process(exits=False)
    ), patch.object(apply, "OLD_VERSION_READY_WAIT_S", 0.2):
        assert apply.run_apply(plan, _Hooks()) is False

    codes.assert_called_once_with(locked)
    assert Path(plan.failure_file).read_text() == "lock"
    assert Path(plan.blocker_file).read_text() == "msmpeng"
    assert "MsMpEng.exe" in Path(plan.error_log).read_text(encoding="utf-8")


def test_programs_that_survive_the_update_are_not_stopped(tmp_path) -> None:
    from zapret_update import processes

    root = tmp_path / "Zapret"
    engine = root / "exe" / "winws2.exe"
    gui = root / "_internal" / "Zapret.exe"
    terminated = []
    alive = {11: str(engine), 22: str(gui)}

    def terminate(pid):
        terminated.append(pid)
        alive.pop(pid, None)

    with patch.object(processes, "processes_under", side_effect=lambda _root: list(alive.items())), patch.object(
        processes, "terminate", side_effect=terminate
    ):
        survivors = processes.stop_processes_under(root, 1.0, spare=[engine])

    assert terminated == [22]
    assert survivors == []


def test_plan_names_the_programs_that_survive(tmp_path) -> None:
    plan = _plan(tmp_path)
    plan.keep_running = ["exe/winws2.exe"]
    plan.save(tmp_path / "plan.json")

    loaded = apply.ApplyPlan.load(tmp_path / "plan.json")
    assert loaded.kept_paths == [Path(plan.app_dir) / "exe" / "winws2.exe"]
    with patch.object(apply.processes, "wait_for_exit", return_value=True), patch.object(
        apply.processes, "stop_processes_under", return_value=[]
    ) as stop:
        apply.Hooks().close_application(loaded)
    assert stop.call_args.kwargs["spare"] == loaded.kept_paths


def test_rollback_that_hit_a_locked_file_is_repeated(tmp_path) -> None:
    plan = _plan(tmp_path)
    hooks = _Hooks()
    started = [_process(exits=True), _process(exits=False)]
    real_move = apply.FileSwap._move
    exe = Path(plan.app_dir) / "_internal" / "Zapret.exe"
    locks = {"left": 1}

    def move(self, source, destination):
        # Откат: новый exe держит антивирус — первый раз вынести его нельзя.
        if Path(source) == exe and "new" in Path(destination).parts and locks["left"]:
            locks["left"] -= 1
            raise PermissionError(13, "занят антивирусом", str(source))
        real_move(self, source, destination)

    with patch.object(apply.processes, "start_detached", side_effect=lambda *a, **k: started.pop(0)), patch.object(
        apply, "OLD_VERSION_READY_WAIT_S", 0.2
    ), patch.object(apply, "ROLLBACK_RETRY_PAUSE_S", 0.0), patch.object(
        apply.processes, "stop_processes_under", return_value=[]
    ), patch.object(apply.FileSwap, "_move", move):
        assert apply.run_apply(plan, hooks) is False

    assert locks["left"] == 0
    assert _read(Path(plan.app_dir)) == OLD
    assert "Прежняя версия возвращена" in Path(plan.error_log).read_text(encoding="utf-8")
