"""Update safety against real, entirely local Git repositories (no GUI/network)."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from workbench.desktop_update import pull_remote_update


_REAL_RUN = subprocess.run


@unittest.skipUnless(shutil.which("git"), "Git required for local repository tests")
class DesktopUpdateGitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.origin = self.folder / "origin.git"
        self.publisher = self.folder / "publisher"
        self.checkout = self.folder / "checkout"
        # User/system configuration must not introduce hooks or remote services.
        environment = patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1",
                                              "GIT_CONFIG_GLOBAL": os.devnull,
                                              "GIT_TERMINAL_PROMPT": "0"})
        environment.start()
        self.addCleanup(environment.stop)
        self.git(self.folder, "init", "--bare", "--initial-branch=main", str(self.origin))
        self.git(self.folder, "clone", str(self.origin), str(self.publisher))
        self.configure(self.publisher)
        for name, content in {
            ".gitignore": "data/\n__pycache__/\n",
            "main.py": "version = 'initial'\n",
            "workbench/component.py": "value = 'initial'\n",
            "workbench/舊模組.py": "renamed_value = 1\n",
            "web/app.mjs": "export const version = 'initial';\n",
            "tests/test_original.py": "original_test = True\n",
        }.items():
            self.write(self.publisher, name, content)
        self.git(self.publisher, "add", "--all")
        self.git(self.publisher, "commit", "-m", "initial fixture")
        self.git(self.publisher, "push", "-u", "origin", "main")
        self.git(self.folder, "clone", str(self.origin), str(self.checkout))
        self.configure(self.checkout)
        self.original_head = self.git(self.checkout, "rev-parse", "HEAD").decode().strip()

    @staticmethod
    def git(root, *arguments):
        return _REAL_RUN(["git", "-C", str(root), *map(str, arguments)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                         timeout=15).stdout

    def configure(self, root):
        for key, value in (("user.name", "Update Test"), ("user.email", "update@example.invalid"),
                           ("core.autocrlf", "false"), ("core.quotePath", "false")):
            self.git(root, "config", key, value)

    @staticmethod
    def write(root, name, content):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")

    def publish_update(self, **extra):
        self.write(self.publisher, "main.py", "version = 'remote'\n")
        self.write(self.publisher, "workbench/component.py", "value = 'remote'\n")
        for name, content in extra.items():
            self.write(self.publisher, name, content)
        self.git(self.publisher, "add", "--all")
        self.git(self.publisher, "commit", "-m", "remote fixture update")
        self.git(self.publisher, "push", "origin", "main")
        return self.git(self.publisher, "rev-parse", "HEAD").decode().strip()

    def dirty_source(self):
        self.write(self.checkout, "workbench/component.py", "value = '本機暫存'\n")
        self.git(self.checkout, "add", "workbench/component.py")
        self.write(self.checkout, "workbench/component.py", "value = '本機暫存'\nunstaged_value = 2\n")
        self.git(self.checkout, "mv", "workbench/舊模組.py", "workbench/新的 模組.py")
        self.write(self.checkout, "workbench/新的 模組.py", "renamed_value = 1\nlocal_value = '中文'\n")
        self.git(self.checkout, "rm", "tests/test_original.py")
        self.write(self.checkout, "tests/test_local_new.py", "local_test = '尚未加入 Git'\n")
        self.write(self.checkout, "web/新的 工具.mjs", "export const local = '本機';\n")

    def source_files(self, root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*") if path.is_file()
                and (path.relative_to(root).parts[0] in {"workbench", "tests", "web"}
                     or path.relative_to(root).as_posix() == "main.py")}

    def snapshot(self):
        return {"head": self.git(self.checkout, "rev-parse", "HEAD"),
                "status": self.git(self.checkout, "status", "--porcelain=v1", "-z"),
                "staged": self.git(self.checkout, "diff", "--cached", "--binary"),
                "unstaged": self.git(self.checkout, "diff", "--binary"),
                "sources": self.source_files(self.checkout)}

    def stash_ids(self):
        return self.git(self.checkout, "stash", "list", "--format=%H").decode().splitlines()

    def assert_backup_recovers(self, result, before):
        backup = result.get("backup_ref")
        self.assertTrue(backup, result)
        self.assertEqual(self.git(self.checkout, "cat-file", "-t", backup).strip(), b"commit")
        self.assertIn(backup, self.stash_ids(), "update backup must remain in stash history")
        if result.get("backup_path"):
            self.assertTrue(Path(result["backup_path"]).exists())
        restore = self.folder / "restored-backup"
        self.git(self.checkout, "worktree", "add", "--detach", str(restore),
                 before["head"].decode().strip())
        self.git(restore, "stash", "apply", "--index", backup)
        self.assertEqual(self.source_files(restore), before["sources"])
        self.assertEqual(self.git(restore, "diff", "--cached", "--binary"), before["staged"])
        self.assertEqual(self.git(restore, "diff", "--binary"), before["unstaged"])

    def test_clean_checkout_fast_forwards_to_local_origin(self):
        remote_head = self.publish_update()
        messages = []
        result = pull_remote_update(self.checkout, progress=messages.append)
        self.assertEqual(result["state"], "updated", result)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").decode().strip(), remote_head)
        self.assertFalse(self.git(self.checkout, "status", "--porcelain"))
        self.assertFalse(result.get("backup_ref"))
        self.assertEqual(self.stash_ids(), [])
        self.assertTrue(messages, "the long operation should report progress")

    def test_dirty_update_backs_up_index_renames_deletes_and_untracked_sources(self):
        remote_head = self.publish_update()
        self.dirty_source()
        self.write(self.checkout, "data/project.sqlite3", "ignored project data")
        self.write(self.checkout, "captures/user.bin", "untracked user artifact")
        before = self.snapshot()
        result = pull_remote_update(self.checkout)
        self.assertEqual(result["state"], "updated", result)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").decode().strip(), remote_head)
        self.assertEqual((self.checkout / "workbench/component.py").read_text("utf-8"),
                         "value = 'remote'\n")
        self.assertTrue((self.checkout / "workbench/舊模組.py").exists())
        self.assertTrue((self.checkout / "tests/test_original.py").exists())
        self.assertFalse((self.checkout / "workbench/新的 模組.py").exists())
        self.assertFalse((self.checkout / "tests/test_local_new.py").exists())
        self.assertFalse((self.checkout / "web/新的 工具.mjs").exists())
        self.assertEqual((self.checkout / "data/project.sqlite3").read_bytes(), b"ignored project data")
        self.assertEqual((self.checkout / "captures/user.bin").read_bytes(), b"untracked user artifact")
        self.assertFalse(self.git(self.checkout, "diff", "--cached"))
        self.assertFalse(self.git(self.checkout, "diff"))
        self.assert_backup_recovers(result, before)

    def test_existing_stash_is_preserved_alongside_update_backup(self):
        self.write(self.checkout, "main.py", "prior_unsaved = True\n")
        self.git(self.checkout, "stash", "push", "-m", "earlier user stash")
        previous_stash = self.stash_ids()[0]
        self.publish_update()
        self.dirty_source()
        before = self.snapshot()
        result = pull_remote_update(self.checkout)
        self.assertEqual(result["state"], "updated", result)
        self.assertIn(previous_stash, self.stash_ids())
        self.assertNotEqual(result.get("backup_ref"), previous_stash)
        self.assertEqual(self.git(self.checkout, "show", f"{previous_stash}:main.py"),
                         b"prior_unsaved = True\n")
        self.assert_backup_recovers(result, before)

    def test_already_current_dirty_sources_are_backed_up_to_activate_published_code(self):
        published = self.source_files(self.checkout)
        self.dirty_source()
        self.write(self.checkout, "data/project.sqlite3", "ignored project data")
        self.write(self.checkout, "captures/user.bin", "untracked user artifact")
        before = self.snapshot()
        result = pull_remote_update(self.checkout)
        self.assertEqual(result["state"], "updated", result)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD"), before["head"])
        self.assertEqual(self.source_files(self.checkout), published)
        self.assertFalse(self.git(self.checkout, "diff", "--cached"))
        self.assertFalse(self.git(self.checkout, "diff"))
        self.assertEqual((self.checkout / "data/project.sqlite3").read_bytes(), b"ignored project data")
        self.assertEqual((self.checkout / "captures/user.bin").read_bytes(), b"untracked user artifact")
        self.assert_backup_recovers(result, before)

    def test_already_current_clean_checkout_does_not_create_backup(self):
        before = self.snapshot()
        result = pull_remote_update(self.checkout)
        self.assertEqual(result["state"], "current", result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.stash_ids(), [])
        self.assertFalse(result.get("backup_ref"))

    def test_untracked_path_that_remote_adds_is_preserved_in_backup(self):
        remote_head = self.publish_update(**{"tests/test_added.py": "upstream_value = True\n"})
        self.write(self.checkout, "tests/test_added.py", "local_value = '本機新檔'\n")
        before = self.snapshot()
        result = pull_remote_update(self.checkout)
        self.assertEqual(result["state"], "updated", result)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").decode().strip(), remote_head)
        self.assertEqual((self.checkout / "tests/test_added.py").read_text("utf-8"),
                         "upstream_value = True\n")
        self.assert_backup_recovers(result, before)

    def test_fetch_failure_does_not_touch_files_index_head_or_existing_stashes(self):
        self.write(self.checkout, "main.py", "existing_stash = True\n")
        self.git(self.checkout, "stash", "push", "-m", "preserve this stash")
        self.dirty_source()
        self.git(self.checkout, "remote", "set-url", "origin", str(self.folder / "does-not-exist.git"))
        before, stashes = self.snapshot(), self.stash_ids()
        result = pull_remote_update(self.checkout, timeout=10)
        self.assertIn(result["state"], {"error", "unavailable"}, result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.stash_ids(), stashes)

    def test_merge_failure_restores_worktree_and_index_and_keeps_backup(self):
        self.publish_update()
        self.dirty_source()
        before = self.snapshot()
        failed = []

        def fail_fast_forward(command, *arguments, **kwargs):
            if "--ff-only" in command and ("merge" in command or "pull" in command):
                failed.append(command)
                raise subprocess.CalledProcessError(1, command, stderr="injected merge failure")
            return _REAL_RUN(command, *arguments, **kwargs)

        with patch("workbench.desktop_update.subprocess.run", side_effect=fail_fast_forward):
            result = pull_remote_update(self.checkout)
        self.assertTrue(failed, "the fixture must fail after backup creation")
        self.assertIn(result["state"], {"error", "unavailable"}, result)
        self.assertEqual(self.snapshot(), before)
        self.assert_backup_recovers(result, before)

    def test_backup_reference_failure_restores_index_and_retains_created_stash(self):
        self.publish_update()
        self.dirty_source()
        before = self.snapshot()
        failed = []

        def fail_backup_ref(command, *arguments, **kwargs):
            if "update-ref" in command and any(
                    str(value).startswith("refs/workbench-update-backups/") for value in command):
                failed.append(command)
                raise subprocess.CalledProcessError(1, command, stderr="cannot write durable backup ref")
            return _REAL_RUN(command, *arguments, **kwargs)

        with patch("workbench.desktop_update.subprocess.run", side_effect=fail_backup_ref):
            result = pull_remote_update(self.checkout)
        self.assertTrue(failed, "the fixture must fail after creating its stash")
        self.assertEqual(result["state"], "error", result)
        self.assertEqual(self.snapshot(), before)
        self.assert_backup_recovers(result, before)

    def test_backup_manifest_failure_restores_index_and_retains_created_stash(self):
        self.publish_update()
        self.dirty_source()
        before = self.snapshot()
        real_write_text = Path.write_text
        failed = []

        def fail_manifest(path, *arguments, **kwargs):
            if path.name == "manifest.json":
                failed.append(path)
                raise OSError("cannot write backup manifest")
            return real_write_text(path, *arguments, **kwargs)

        with patch("workbench.desktop_update.Path.write_text", new=fail_manifest):
            result = pull_remote_update(self.checkout)
        self.assertTrue(failed, "the fixture must fail after creating its stash")
        self.assertEqual(result["state"], "error", result)
        self.assertEqual(self.snapshot(), before)
        self.assert_backup_recovers(result, before)

    def test_timeout_reported_after_fast_forward_keeps_updated_code_and_backup(self):
        remote_head = self.publish_update()
        self.dirty_source()
        before = self.snapshot()
        timed_out = []

        def finish_then_timeout(command, *arguments, **kwargs):
            result = _REAL_RUN(command, *arguments, **kwargs)
            if "--ff-only" in command and ("merge" in command or "pull" in command):
                timed_out.append(command)
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 120),
                                                output=result.stdout, stderr=result.stderr)
            return result

        with patch("workbench.desktop_update.subprocess.run", side_effect=finish_then_timeout):
            result = pull_remote_update(self.checkout)
        self.assertTrue(timed_out, "the fixture must finish the merge before reporting timeout")
        self.assertEqual(result["state"], "updated", result)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD").decode().strip(), remote_head)
        self.assertEqual(self.source_files(self.checkout), self.source_files(self.publisher))
        self.assertFalse(self.git(self.checkout, "diff", "--cached"))
        self.assertFalse(self.git(self.checkout, "diff"))
        self.assert_backup_recovers(result, before)

    def test_diverged_history_is_blocked_before_stashing_local_work(self):
        self.publish_update()
        self.write(self.checkout, "main.py", "version = 'local commit'\n")
        self.git(self.checkout, "add", "main.py")
        self.git(self.checkout, "commit", "-m", "local committed change")
        self.dirty_source()
        before = self.snapshot()
        result = pull_remote_update(self.checkout)
        self.assertEqual(result["state"], "blocked", result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.stash_ids(), [])

    def test_in_progress_merge_is_blocked_without_mutating_local_work(self):
        self.publish_update()
        self.dirty_source()
        marker = self.checkout / ".git" / "MERGE_HEAD"
        marker.write_text(self.original_head + "\n", encoding="ascii")
        before = self.snapshot()
        result = pull_remote_update(self.checkout)
        self.assertEqual(result["state"], "blocked", result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(marker.read_text("ascii"), self.original_head + "\n")
        self.assertEqual(self.stash_ids(), [])

    def test_missing_git_returns_an_error_without_touching_local_work(self):
        self.dirty_source()
        before = self.snapshot()
        with patch("workbench.desktop_update.subprocess.run", side_effect=FileNotFoundError("git missing")):
            result = pull_remote_update(self.checkout)
        self.assertIn(result["state"], {"error", "unavailable"}, result)
        self.assertEqual(self.snapshot(), before)

    def test_installation_without_git_metadata_reports_unavailable(self):
        root = self.folder / "installation"
        root.mkdir()
        self.write(root, "main.py", "installed_program = True\n")
        result = pull_remote_update(root)
        self.assertEqual(result["state"], "unavailable", result)
        self.assertEqual((root / "main.py").read_text("utf-8"), "installed_program = True\n")


if __name__ == "__main__":
    unittest.main()
