"""Click the real native GitHub update button against an isolated local origin.

Run explicitly: python tests/desktop_update_workflow.py
No user's checkout, window, project, remote, camera or training is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-background-timer-throttling --disable-renderer-backgrounding")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QApplication

from test_desktop_update_git import DesktopUpdateGitTests
from workbench import desktop
from workbench.server import WorkbenchService


class CapturingPage(desktop.Page):
    def __init__(self, *args):
        super().__init__(*args)
        self.errors = []

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.errors.append(f"{source}:{line}: {message}")
        super().javaScriptConsoleMessage(level, message, line, source)


def main():
    fixture = DesktopUpdateGitTests()
    fixture.setUp()
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = service = None
    try:
        target = fixture.publish_update()
        fixture.write(fixture.checkout, "tests/test_original.py", "staged_local_test = '本機'\n")
        fixture.git(fixture.checkout, "add", "tests/test_original.py")
        fixture.write(fixture.checkout, "tests/test_original.py", "staged_local_test = '本機'\nunstaged_local = True\n")
        fixture.write(fixture.checkout, "workbench/local_extra.py", "local_source = '新檔案'\n")
        before_source = fixture.snapshot()
        data_root = fixture.folder / "custom-project-data"
        bridge = desktop.DialogBridge()
        service = WorkbenchService(data_root, dialog=bridge).start()
        first = service.store.create_project("Update keeps existing projects")["id"]
        second = service.store.create_project("Second original project")["id"]
        service.store.update_project(first, classes=["part", "hand"])
        before_projects = {pid: service.store.get_project(pid) for pid in (first, second)}

        with patch.object(desktop, "APP_ROOT", fixture.checkout), \
                patch.object(desktop, "Page", CapturingPage), \
                patch.object(desktop, "github_version_status", return_value={
                    "state": "current", "current": desktop.__version__, "latest": desktop.__version__,
                    "message": "Local fixture version check"}), \
                patch.object(desktop, "schedule_restart") as restart:
            window = desktop.MainWindow(service, bridge)
            window.show()
            window.page.setVisible(True)

            def js(script):
                values = []
                loop = QEventLoop()
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(loop.quit)
                timer.start(5000)
                window.page.runJavaScript(script, lambda value: (values.append(value), loop.quit()))
                loop.exec()
                timer.stop()
                if not values:
                    raise AssertionError("JavaScript evaluation timed out: " + script)
                if window.page.errors:
                    raise AssertionError("JavaScript console error: " + "\n".join(window.page.errors))
                return values[0]

            def wait(expression, seconds=45):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if js(expression):
                        return
                    if window.remote_update_result and window.remote_update_result.get("state") in {
                            "error", "blocked", "unavailable"}:
                        raise AssertionError("Native update failed: " + repr(window.remote_update_result))
                    QTest.qWait(60)
                raise AssertionError("Timeout: " + expression + "\n" + str(js("document.body.innerText.slice(-3000)")))

            def click(selector):
                encoded = json.dumps(selector)
                wait(f"document.querySelector({encoded})&&!document.querySelector({encoded}).disabled")
                assert js(f"(()=>{{document.querySelector({encoded}).click();return true}})()")

            wait("typeof window.workbenchState==='function' && typeof window.workbenchFlush==='function'")
            wait("document.querySelector('#statusText').textContent.includes('本機服務已連線') && !window.workbenchState().busy && !window.workbenchState().loading")
            click("#settings")
            wait("!document.querySelector('#settingsShell').hidden")
            click("[data-settings-page='updates']")
            wait("!document.querySelector('[data-settings-panel=updates]').hidden")
            assert js("typeof window.workbenchRemoteUpdateStatus==='function'"), "Native update renderer is unavailable"
            js("""window.__observedUpdateStates=[];
                const realUpdateRenderer=window.workbenchRemoteUpdateStatus;
                window.workbenchRemoteUpdateStatus=snapshot=>{
                    window.__observedUpdateStates.push(snapshot.state);
                    return realUpdateRenderer(snapshot);
                };""")
            click("#githubUpdateButton")
            wait("document.querySelector('#githubUpdateButton').disabled && document.querySelector('#githubUpdateButton').textContent.includes('重新啟動')")
            assert window.remote_update_phase == "restarting", window.remote_update_result
            restart.assert_called_once_with(fixture.checkout, data_root=service.data_root)
            observed = json.loads(js("JSON.stringify(window.__observedUpdateStates)"))
            assert "saving" in observed and "restarting" in observed, observed
            assert window.remote_update_result["backup_ref"], window.remote_update_result
            assert fixture.git(fixture.checkout, "rev-parse", "HEAD").decode().strip() == target
            assert (fixture.checkout / "tests/test_original.py").read_text("utf-8") == "original_test = True\n"
            assert not (fixture.checkout / "workbench/local_extra.py").exists()
            fixture.assert_backup_recovers(window.remote_update_result, before_source)
            assert {pid: service.store.get_project(pid) for pid in (first, second)} == before_projects
            assert service.data_root == data_root.resolve()
            assert service._camera is None and service._cvat is None
            assert not service.training.active_runs() and not service.jobs.active()
            deadline = time.monotonic() + 5
            while window.isVisible() and time.monotonic() < deadline:
                QTest.qWait(60)
            assert not window.isVisible(), "Successful single click did not close the old native window"
            restart.assert_called_once()
            assert not window.page.errors, window.page.errors
            print("NATIVE_SINGLE_CLICK_GIT_UPDATE_BACKUP_RESTART_OK", flush=True)
    finally:
        if window is not None:
            window.update_timer.stop()
            window.allow_close = True
            window.close()
            if window.remote_update_thread is not None:
                window.remote_update_thread.join(timeout=10)
                assert not window.remote_update_thread.is_alive(), "Updater still running during fixture cleanup"
        if service is not None:
            service.close()
        fixture.doCleanups()
        app.processEvents()


if __name__ == "__main__":
    main()
