"""Restart handoff preserves the data location and installs only after exit."""
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from workbench import desktop_update as updater


@contextmanager
def exited_parent(events=None, *, wait=False):
    """Model process exit through the host's own process-wait API."""
    events = events if events is not None else []
    if os.name == "nt":
        def process_handle(*args):
            events.append("open-parent")
            return 1 if wait else 0

        def waited(*args):
            events.append("parent-exited")
            return 0

        kernel = SimpleNamespace(OpenProcess=Mock(side_effect=process_handle),
                                 WaitForSingleObject=Mock(side_effect=waited),
                                 CloseHandle=Mock())
        with patch("ctypes.WinDLL", return_value=kernel):
            yield kernel
    else:
        def already_exited(*args):
            events.append("parent-exited")
            raise ProcessLookupError

        with patch("workbench.desktop_update.os.kill", side_effect=already_exited) as probe:
            yield probe


class DesktopUpdateRestartTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # Windows runners may expose TEMP through an 8.3 alias. Production
        # canonicalizes these paths, so compare against existing resolved paths.
        self.folder = Path(self.temporary.name).resolve()
        self.root = self.folder / "應用 程式"
        self.root.mkdir()
        self.root = self.root.resolve()
        self.data = self.folder / "自訂 資料"
        self.data.mkdir()
        self.data = self.data.resolve()

    def requirements(self):
        (self.root / "requirements.txt").write_text("fixture-package==2.0\n", encoding="utf-8")

    def test_schedule_preserves_unicode_custom_data_and_utf8_helper_output(self):
        helper = Mock()
        with patch.object(updater.subprocess, "Popen", return_value=helper) as launch:
            self.assertIs(updater.schedule_restart(self.root, self.data), helper)
        command = launch.call_args.args[0]
        self.assertEqual(command, [sys.executable, str(Path(updater.__file__).resolve()),
                                  "--wait", str(os.getpid()), str(self.root.resolve()),
                                  "--data-root", str(self.data.resolve()), "--ensure-runtime"])
        options = launch.call_args.kwargs
        self.assertEqual(options["cwd"], self.root.resolve())
        self.assertEqual(options["env"]["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertIs(options["stdout"], options["stderr"])
        self.assertTrue(options["stdout"].closed)
        self.assertTrue((self.data / "logs" / "update.log").is_file())
        self.assertFalse((self.root / "data").exists())
        if os.name == "nt":
            self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_schedule_default_data_directory_is_passed_explicitly(self):
        with patch.object(updater.subprocess, "Popen") as launch:
            updater.schedule_restart(self.root)
        command = launch.call_args.args[0]
        offset = command.index("--data-root")
        self.assertEqual(command[offset + 1], str((self.root / "data").resolve()))
        self.assertTrue((self.root / "data" / "logs" / "update.log").exists())

    def test_restart_without_runtime_upgrade_preserves_custom_data(self):
        with exited_parent(), patch.object(updater.subprocess, "Popen") as launch, \
                patch.object(updater.subprocess, "run") as install, \
                patch.object(updater, "missing_runtime_requirements") as missing:
            updater.wait_and_restart(123, self.root, self.data)
        install.assert_not_called()
        missing.assert_not_called()
        self.assertEqual(launch.call_args.args[0],
                         [sys.executable, str(self.root / "main.py"),
                          "--data-root", str(self.data.resolve())])
        self.assertEqual(launch.call_args.kwargs["cwd"], self.root.parent)

    def test_legacy_restart_arguments_remain_supported(self):
        with exited_parent(), patch.object(updater.subprocess, "Popen") as launch:
            updater.wait_and_restart(123, self.root)
        self.assertEqual(launch.call_args.args[0], [sys.executable, str(self.root / "main.py")])

    def test_runtime_install_occurs_after_parent_exit_and_before_restart(self):
        self.requirements()
        events = []

        def check_runtime(root):
            events.append("check-runtime")
            return ["fixture-package==2.0"] if events.count("check-runtime") == 1 else []

        with exited_parent(events, wait=True), \
                patch.object(updater, "missing_runtime_requirements", side_effect=check_runtime) as missing, \
                patch.object(updater.subprocess, "run", side_effect=lambda *a, **k: events.append("install")) as install, \
                patch.object(updater.subprocess, "Popen", side_effect=lambda *a, **k: events.append("restart")) as launch, \
                patch("builtins.print"):
            updater.wait_and_restart(123, self.root, self.data, ensure_runtime=True)
        self.assertLess(events.index("parent-exited"), events.index("check-runtime"))
        self.assertLess(events.index("install"), events.index("restart"))
        self.assertEqual(missing.call_count, 2)
        self.assertEqual(install.call_args.args[0],
                         [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                          "-r", str(self.root / "requirements.txt")])
        self.assertEqual(install.call_args.kwargs["cwd"], self.root)
        self.assertTrue(install.call_args.kwargs["check"])
        self.assertEqual(install.call_args.kwargs["timeout"], 1800)
        self.assertEqual(launch.call_args.args[0][-2:], ["--data-root", str(self.data.resolve())])

    def test_already_installed_requirements_do_not_run_pip(self):
        self.requirements()
        with exited_parent(), patch.object(updater, "missing_runtime_requirements", return_value=[]) as missing, \
                patch.object(updater.subprocess, "run") as install, \
                patch.object(updater.subprocess, "Popen") as launch:
            updater.wait_and_restart(123, self.root, self.data, ensure_runtime=True)
        missing.assert_called_once_with(self.root)
        install.assert_not_called()
        launch.assert_called_once()

    def test_failed_install_never_launches_updated_application(self):
        self.requirements()
        error = subprocess.CalledProcessError(1, ["fixture-pip"])
        with exited_parent(), \
                patch.object(updater, "missing_runtime_requirements", return_value=["fixture-package==2.0"]), \
                patch.object(updater.subprocess, "run", side_effect=error), \
                patch.object(updater.subprocess, "Popen") as launch, patch("builtins.print"):
            with self.assertRaises(subprocess.CalledProcessError):
                updater.wait_and_restart(123, self.root, self.data, ensure_runtime=True)
        launch.assert_not_called()

    def test_incomplete_runtime_after_install_never_launches_application(self):
        self.requirements()
        with exited_parent(), \
                patch.object(updater, "missing_runtime_requirements", return_value=["fixture-package==2.0"]) as missing, \
                patch.object(updater.subprocess, "run") as install, \
                patch.object(updater.subprocess, "Popen") as launch, patch("builtins.print"):
            with self.assertRaisesRegex(RuntimeError, "桌面執行環境尚未完成更新"):
                updater.wait_and_restart(123, self.root, self.data, ensure_runtime=True)
        self.assertEqual(missing.call_count, 2)
        install.assert_called_once()
        launch.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows parent-process handle wait")
    def test_parent_wait_timeout_closes_handle_without_installing_or_restarting(self):
        self.requirements()
        with exited_parent(wait=True) as kernel, \
                patch.object(updater, "missing_runtime_requirements") as missing, \
                patch.object(updater.subprocess, "run") as install, \
                patch.object(updater.subprocess, "Popen") as launch:
            kernel.WaitForSingleObject.side_effect = None
            kernel.WaitForSingleObject.return_value = 258
            with self.assertRaisesRegex(RuntimeError, "原本的工作台仍在執行"):
                updater.wait_and_restart(123, self.root, self.data, ensure_runtime=True)
        kernel.CloseHandle.assert_called_once_with(1)
        missing.assert_not_called()
        install.assert_not_called()
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
