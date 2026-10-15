from __future__ import annotations

"""Установщик не имеет права разрушать рабочую версию до готовности новой.

Прежняя схема удаляла ``{app}\\_internal`` до распаковки, а ``[InstallDelete]``
не участвует в откате Inno: любой сбой распаковки оставлял систему вообще без
приложения. Здесь закреплена схема «распаковать рядом — переключить одним
переименованием — проверить — только потом удалить прежнее».
"""

from pathlib import Path
import re
import unittest


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = PUBLIC_ROOT.parent / "private_zapretgui"
INNO_SCRIPT = PRIVATE_ROOT / "build_zapret" / "zapret_universal.iss"


def _read_script() -> str:
    return INNO_SCRIPT.read_text(encoding="utf-8")


def _section(iss: str, name: str) -> str:
    match = re.search(rf"^\[{name}\]$(.*?)(?=^\[|\Z)", iss, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def _routine(iss: str, name: str) -> str:
    code = iss[iss.index("[Code]"):]
    headers = list(re.finditer(r"^(?:function|procedure)\s+([A-Za-z_]\w*)", code, re.MULTILINE))
    for index, header in enumerate(headers):
        if header.group(1) != name:
            continue
        end = headers[index + 1].start() if index + 1 < len(headers) else len(code)
        return code[header.start():end]
    return ""


@unittest.skipUnless(INNO_SCRIPT.is_file(), "нужен закрытый репозиторий со сборкой установщика")
class InstallerRuntimeLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iss = _read_script()

    def test_runtime_is_unpacked_next_to_the_working_copy(self) -> None:
        files = _section(self.iss, "Files")
        runtime_lines = [
            line
            for line in files.splitlines()
            if line.strip().startswith("Source:") and "_internal" in line
        ]

        self.assertEqual(len(runtime_lines), 1)
        self.assertIn(r'DestDir: "{app}\_internal.new"', runtime_lines[0])

    def test_runtime_is_not_scheduled_for_replacement_after_reboot(self) -> None:
        """restartreplace оставлял пользователя без приложения до перезагрузки."""
        files = _section(self.iss, "Files")

        self.assertNotIn("restartreplace", files)

    def test_working_runtime_is_never_deleted_before_installation(self) -> None:
        install_delete = _section(self.iss, "InstallDelete")
        deleted_names = re.findall(r'Name:\s*"([^"]+)"', install_delete)

        self.assertNotIn(r"{app}\_internal", deleted_names)
        self.assertIn(r"{app}\_internal.new", deleted_names)
        self.assertNotIn(r"{app}\_internal.old", deleted_names)

    def test_uninstall_removes_the_working_runtime_and_both_swap_leftovers(self) -> None:
        uninstall_delete = _section(self.iss, "UninstallDelete")
        deleted_names = re.findall(r'Name:\s*"([^"]+)"', uninstall_delete)

        for expected in (r"{app}\_internal", r"{app}\_internal.new", r"{app}\_internal.old"):
            self.assertIn(expected, deleted_names)


@unittest.skipUnless(INNO_SCRIPT.is_file(), "нужен закрытый репозиторий со сборкой установщика")
class InstallerRuntimeSwapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.iss = _read_script()
        self.swap = _routine(self.iss, "CommitRuntimeSwap")
        self.assertTrue(self.swap, "процедура свопа runtime отсутствует")

    def test_swap_refuses_to_start_without_a_complete_new_version(self) -> None:
        checkpoint = self.swap.index("RuntimeDirectoryContainsExe(StagingDir)")
        rename = self.swap.index("TryRenameRuntimeDirectory(LiveDir, BackupDir)")

        self.assertLess(checkpoint, rename)

    def test_swap_moves_working_copy_aside_before_switching(self) -> None:
        move_aside = self.swap.index("TryRenameRuntimeDirectory(LiveDir, BackupDir)")
        switch = self.swap.index("TryRenameRuntimeDirectory(StagingDir, LiveDir)")

        self.assertLess(move_aside, switch)

    def test_backup_is_removed_only_after_the_new_version_is_verified(self) -> None:
        verify = self.swap.index("RuntimeDirectoryContainsExe(LiveDir)")
        cleanup = self.swap.index("TryRemoveRuntimeDirectory(BackupDir)", verify)

        self.assertLess(verify, cleanup)

    def test_every_failure_restores_the_previous_version(self) -> None:
        rollbacks = self.swap.count("TryRenameRuntimeDirectory(BackupDir, LiveDir)")

        # Откат нужен и при неудачном переключении, и при непрошедшей проверке.
        self.assertGreaterEqual(rollbacks, 2)

    def test_directory_operations_survive_a_temporary_lock(self) -> None:
        rename = _routine(self.iss, "TryRenameRuntimeDirectory")
        remove = _routine(self.iss, "TryRemoveRuntimeDirectory")

        self.assertIn("for Attempt := 1 to", rename)
        self.assertIn("Sleep(", rename)
        self.assertIn("for Attempt := 1 to", remove)

    def test_processes_are_stopped_again_right_before_the_working_copy_is_moved(self) -> None:
        """Жалоба 2026-10-13 «рабочая папка занята другим процессом».

        Процессы останавливаются в PrepareToInstall, потом идёт распаковка. Программу за это
        время могут запустить снова, и одной остановки в начале мало.
        """
        stop = self.swap.index("StopInstallRootProcesses(AppRoot, ExpandConstant('{srcexe}'))")
        move_aside = self.swap.index("TryRenameRuntimeDirectory(LiveDir, BackupDir)")
        rounds = self.swap.index("for Round := 1 to")

        self.assertLess(rounds, stop)
        self.assertLess(stop, move_aside)
        # Без исключения самого установщика он завершил бы себя: его запускают из каталога состояния.
        self.assertNotIn("StopInstallRootProcesses(AppRoot, '')", self.swap)

    def test_busy_working_copy_is_reported_with_the_name_of_its_holder(self) -> None:
        describe = self.swap.index("DescribeRuntimeDirectoryHolders(LiveDir)")
        generic = self.swap.index("'рабочая папка занята другим процессом'")

        self.assertLess(self.swap.index("TryRenameRuntimeDirectory(LiveDir, BackupDir)"), describe)
        self.assertLess(describe, generic)
        self.assertIn("'рабочую папку держит другая программа — ' + Holders", self.swap)
        # Чужую программу установщик только называет: завершать её он не вправе.
        holders = _routine(self.iss, "DescribeRuntimeDirectoryHolders")
        self.assertNotIn("Stop-Process", holders)
        self.assertNotIn("taskkill", holders.lower())

    def test_holder_script_gets_a_working_directory_that_exists_for_64_bit_programs(self) -> None:
        """С пустой рабочей папкой Inno берёт папку PowerShell — C:\\Windows\\Sysnative\\...

        Это имя видит только 32-разрядный установщик. 64-разрядный PowerShell получал
        несуществующую текущую папку, Add-Type не мог запустить компилятор, и сценарий
        возвращал пустой ответ: на стенде 2026-10-13 установщик так и не назвал программу,
        которая держала папку, хотя тот же сценарий вручную её находил.
        """
        holders = _routine(self.iss, "DescribeRuntimeDirectoryHolders")
        launch = holders[holders.index("if not Exec("):]
        launch = launch[: launch.index(") then")]

        self.assertIn("NativePowerShellExe", launch)
        self.assertIn("ExpandConstant('{win}'), SW_HIDE", launch)
        self.assertNotIn("'', SW_HIDE", launch)

    def test_holder_script_is_plain_ascii_and_uses_three_ways_to_find_the_holder(self) -> None:
        routine = _routine(self.iss, "RuntimeHoldersScript")
        lines = re.findall(r"^  Result := Result \+ '(.*)' \+ #13#10;$", routine, re.MULTILINE)
        script = "\n".join(line.replace("''", "'") for line in lines)

        self.assertGreater(len(lines), 60)
        # Файл пишется без BOM: Windows PowerShell 5.1 прочла бы кириллицу в системной кодировке.
        self.assertTrue(script.isascii())
        self.assertTrue(script.startswith("param([string]$Dir, [string]$Out)"))
        for marker in ("RmGetList", ".Modules", "CurrentDirectory"):
            self.assertIn(marker, script)
        # Ответ уходит в файл с BOM: перехват вывода консоли на стенде терял единственную строку
        # без перевода строки, а названия программ бывают русскими.
        self.assertIn("[System.IO.File]::WriteAllText($Out, $text, (New-Object System.Text.UTF8Encoding($true)))", script)
        self.assertIn("LoadStringsFromFile(AnswerPath, Lines)", _routine(self.iss, "DescribeRuntimeDirectoryHolders"))
        self.assertNotIn("Stop-Process", script)
        # Препроцессор Inno разбирает такие вставки даже внутри строк.
        self.assertNotIn("{#", script)

    def test_swap_runs_before_anything_else_after_installation(self) -> None:
        step_changed = _routine(self.iss, "CurStepChanged")
        swap_call = step_changed.index("CommitRuntimeSwap(FailureReason)")

        for later in ("WriteInstallOwnerMarker", "RelocatePersistentBindingsIfNeeded",
                      "CleanupPreviousInstallIfRequested"):
            self.assertLess(swap_call, step_changed.index(later))

    def test_failed_swap_stops_finalization_and_tells_the_user(self) -> None:
        step_changed = _routine(self.iss, "CurStepChanged")
        failure_branch = step_changed[step_changed.index("if not RuntimeSwapSucceeded then"):]

        self.assertIn("WarnRuntimeSwapFailed(FailureReason)", failure_branch)
        self.assertIn("Exit;", failure_branch)
        self.assertIn("MsgBox", _routine(self.iss, "WarnRuntimeSwapFailed"))


class AutoUpdateRelaunchTests(unittest.TestCase):
    """После автообновления программу открывает установщик."""

    def setUp(self) -> None:
        self.iss = _read_script()
        self.step_changed = _routine(self.iss, "CurStepChanged")

    def test_tray_request_matches_the_argument_sent_by_the_app(self) -> None:
        from updater.install.launcher import START_IN_TRAY_ARGUMENT

        self.assertIn(f"'{START_IN_TRAY_ARGUMENT}'", _routine(self.iss, "IsStartInTray"))

    def test_app_updated_in_tray_is_reopened_in_tray(self) -> None:
        self.assertRegex(
            self.step_changed,
            r"if IsStartInTray\(\) then\s+LaunchArguments := '--tray';",
        )
        self.assertRegex(self.step_changed, r"Exec\(\s*LaunchPath, LaunchArguments,")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(INNO_SCRIPT.is_file(), "нужен закрытый репозиторий со сборкой установщика")
class InterruptedInstallRecoveryTests(unittest.TestCase):
    """Обрыв установки не оставляет программу без _internal и без папок поставки."""

    def setUp(self) -> None:
        self.iss = INNO_SCRIPT.read_text(encoding="utf-8-sig")

    def test_interrupted_runtime_swap_is_undone_before_a_new_install(self) -> None:
        recover = _routine(self.iss, "RecoverInterruptedInstall")
        prepare = _routine(self.iss, "PrepareToInstall")

        self.assertIn("(not RuntimeDirectoryContainsExe(LiveDir)) and RuntimeDirectoryContainsExe(BackupDir)", recover)
        self.assertIn("TryRenameRuntimeDirectory(BackupDir, LiveDir)", recover)
        self.assertIn("RestoreStashedDataDirs(AppRoot)", recover)
        # После проверки папки, но до остановки процессов и переноса данных.
        self.assertLess(prepare.index("PrepareDestinationOwnership"), prepare.index("RecoverInterruptedInstall("))
        self.assertLess(prepare.index("RecoverInterruptedInstall("), prepare.index("StopRelocationInstallProcesses"))

    def test_backup_runtime_is_dropped_only_next_to_a_working_one(self) -> None:
        swap = _routine(self.iss, "CommitRuntimeSwap")
        # Раньше хватало, что папка _internal есть, — даже пустая; тогда стиралась единственная копия.
        self.assertIn("if DirExists(BackupDir) and RuntimeDirectoryContainsExe(LiveDir) then", swap)
        self.assertNotIn("if DirExists(BackupDir) and DirExists(LiveDir) then", swap)

    def test_shipped_folders_are_stashed_and_come_back_unless_the_install_succeeds(self) -> None:
        steps = _routine(self.iss, "CurStepChanged")
        stash = steps.index("StashDataDirs(")
        post = steps.index("(CurStep = ssPostInstall)")
        swap = steps.index("CommitRuntimeSwap(FailureReason)")

        self.assertIn("if (CurStep = ssInstall) then", steps[:stash])
        self.assertLess(stash, post)
        after_swap = steps[swap:steps.index("if not RuntimeSwapSucceeded then", swap)]
        self.assertIn("DropStashedDataDirs(", after_swap)
        self.assertIn("RestoreStashedDataDirs(", after_swap)
        self.assertIn("RestoreStashedDataDirs(", _routine(self.iss, "DeinitializeSetup"))
        self.assertIn("if DataDirsStashed then", _routine(self.iss, "DeinitializeSetup"))
