"""Итоговая проверка в настоящем браузере без окна (сам браузер подменён)."""

from __future__ import annotations

import unittest

from blockcheck import browser_check


class _Process:
    def __init__(self, output, text: str, *, finishes: bool = True) -> None:
        output.write(text.encode())
        self.finishes = finishes
        self.killed = False

    def poll(self):
        return 0 if self.finishes or self.killed else None

    def kill(self):
        self.killed = True

    def wait(self):
        return 0


class BrowserCheckTests(unittest.TestCase):
    def _visit(self, text: str, **kwargs):
        commands: list[list[str]] = []
        made: list[_Process] = []
        finishes = kwargs.pop("finishes", True)

        def popen(command, output):
            commands.append(command)
            made.append(_Process(output, text, finishes=finishes))
            return made[-1]

        visit = browser_check.open_in_browser("discord.com", popen=popen, browser=r"C:\Edge\msedge.exe", **kwargs)
        return visit, commands[0], made[0]

    def test_page_means_the_site_opened(self) -> None:
        visit, command, _process = self._visit("<html><head><title>Discord</title></head></html>")
        self.assertTrue(visit.ok)
        self.assertEqual(visit.browser, "msedge.exe")
        # Без окна, с пустым профилем, без QUIC: проверяется обычное соединение.
        self.assertIn("--headless=new", command)
        self.assertIn("--disable-quic", command)
        self.assertIn("--disable-background-networking", command)
        self.assertEqual(command[-1], "https://discord.com/")

    def test_browser_error_is_named(self) -> None:
        visit, _command, _process = self._visit("[1:2:ERROR:headless_command_handler.cc:403] Page load failed: net::ERR_CONNECTION_RESET\n")
        self.assertEqual((visit.state, visit.error, visit.text), (browser_check.BROWSER_FAILED, "ERR_CONNECTION_RESET", "соединение сброшено"))

    def test_page_that_never_loads_is_a_timeout(self) -> None:
        visit, _command, _process = self._visit("[1:2:ERROR:x.cc:409] Page load timed out.\n<html><body></body></html>")
        self.assertEqual(visit.state, browser_check.BROWSER_TIMEOUT)

    def test_stop_kills_the_browser(self) -> None:
        visit, _command, process = self._visit("", finishes=False, cancelled=lambda: True)
        self.assertEqual(visit.state, browser_check.BROWSER_TIMEOUT)
        self.assertTrue(process.killed)

    def test_no_browser_is_said_plainly(self) -> None:
        visit = browser_check.open_in_browser("discord.com", browser="")
        self.assertEqual(visit.state, browser_check.BROWSER_MISSING)
        self.assertIn("Edge", visit.text)


if __name__ == "__main__":
    unittest.main()
