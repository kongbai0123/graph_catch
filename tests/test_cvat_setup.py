import io
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cvat_setup import CvatSetup, CVAT_VERSION, SetupPause


class CvatReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.setup = CvatSetup(self.tmp.name)
        self.setup._set = Mock()
        self.setup.stopping.wait = Mock()

    def ready_probe(self, **overrides):
        result = {
            "supported": True, "boot_id": "boot", "reboot_pending": False,
            "docker_installed": True, "wsl_ready": True, "features_ready": True,
        }
        result.update(overrides)
        return result

    def test_moved_workspace_never_reuses_ready_state(self):
        self.setup.compose_file.parent.mkdir(parents=True)
        self.setup.compose_file.write_text("{}", encoding="utf-8")
        self.setup._state = {
            "phase": "ready", "text": "CVAT 執行中。", "step": "account",
            "installed_version": CVAT_VERSION, "project": "annotation_cvat_000000000000",
        }
        self.setup._probe = Mock(return_value=self.ready_probe())

        status = self.setup.status()

        self.assertEqual(status["phase"], "workspace_moved")
        self.assertFalse(status["ready"])
        self.assertFalse(status["installed"])
        self.assertTrue(status["can_setup"])
        self.assertTrue(status["resume"])
        self.assertIn("位置已變更", status["text"])
        self.assertEqual(next(step for step in status["steps"] if step["state"] == "attention")["id"], "download")

    def test_current_workspace_ready_state_remains_ready(self):
        self.setup.compose_file.parent.mkdir(parents=True)
        self.setup.compose_file.write_text("{}", encoding="utf-8")
        self.setup._state = {
            "phase": "ready", "step": "account", "installed_version": CVAT_VERSION,
            "project": self.setup.project,
        }
        self.setup._probe = Mock(return_value=self.ready_probe())

        status = self.setup.status()

        self.assertEqual(status["phase"], "ready")
        self.assertTrue(status["ready"])
        self.assertTrue(status["installed"])

    def test_unsupported_system_takes_precedence_after_workspace_move(self):
        self.setup.compose_file.parent.mkdir(parents=True)
        self.setup.compose_file.write_text("{}", encoding="utf-8")
        self.setup._state = {
            "phase": "ready", "installed_version": CVAT_VERSION,
            "project": "annotation_cvat_000000000000",
        }
        self.setup._probe = Mock(return_value=self.ready_probe(
            supported=False, reason="硬體虛擬化尚未啟用。"))

        status = self.setup.status()

        self.assertEqual(status["phase"], "blocked")
        self.assertFalse(status["can_setup"])
        self.assertEqual(status["text"], "硬體虛擬化尚未啟用。")

    def test_workspace_move_does_not_hide_setup_error(self):
        self.setup.compose_file.parent.mkdir(parents=True)
        self.setup.compose_file.write_text("{}", encoding="utf-8")
        self.setup._state = {
            "phase": "error", "text": "Docker 啟動失敗。",
            "installed_version": CVAT_VERSION, "project": "annotation_cvat_000000000000",
        }
        self.setup._probe = Mock(return_value=self.ready_probe())

        status = self.setup.status()

        self.assertEqual(status["phase"], "error")
        self.assertEqual(status["text"], "Docker 啟動失敗。")
        self.assertTrue(status["resume"])

    def test_about_success_still_waits_for_policy_activation(self):
        setup = self.setup
        setup._project_running = Mock(return_value=True)
        setup._compose = Mock(side_effect=[
            SimpleNamespace(returncode=0),  # compose up
            SimpleNamespace(returncode=1),  # bundle has not activated
            SimpleNamespace(returncode=0),
        ])
        client = Mock()
        client.open.return_value = io.BytesIO(
            ('{"version":"' + CVAT_VERSION.lstrip('v') + '"}').encode())
        with patch('cvat_setup.build_opener', return_value=client):
            setup._start_services()
        self.assertEqual(setup._compose.call_count, 3)
        self.assertIn('health?bundles=true', setup._compose.call_args.args[-1])
        setup.stopping.wait.assert_called_once_with(1)

    def test_authorization_timeout_does_not_mark_services_ready(self):
        self.setup._compose = Mock(return_value=SimpleNamespace(returncode=1))
        with patch('cvat_setup.time.monotonic', side_effect=[0, 1, 121]):
            with self.assertRaisesRegex(RuntimeError, '權限規則尚未就緒'):
                self.setup._wait_authorization()
        self.assertEqual(self.setup._compose.call_count, 1)

    def test_authorization_wait_honors_shutdown(self):
        self.setup.stopping.set()
        self.setup._compose = Mock()
        with self.assertRaises(SetupPause):
            self.setup._wait_authorization()
        self.setup._compose.assert_not_called()

    def test_temporary_probe_failure_is_retried(self):
        self.setup._compose = Mock(side_effect=[
            RuntimeError('probe timeout'), SimpleNamespace(returncode=0)])
        self.setup._wait_authorization()
        self.assertEqual(self.setup._compose.call_count, 2)


if __name__ == '__main__':
    unittest.main()
