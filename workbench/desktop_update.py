"""Detect local source changes and restart the desktop app safely."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".css", ".html", ".json", ".ps1", ".bat"}
SOURCE_DIRECTORIES = ("workbench", "web", "composer_core", "classical_segmentation", "sam2_segmentation")
SOURCE_FILES = ("main.py", "cvat_setup.py", "setup_cvat.ps1", "bootstrap.ps1", "vision-workbench.bat",
                "requirements.txt", "requirements-ai.txt", "requirements-training.txt", "requirements-lock.txt")
VERSION_TAG = re.compile(r"refs/tags/v(\d+)\.(\d+)\.(\d+)(?:\^\{\})?$")


def _git_executable():
    """Find Git in standard Windows locations when the desktop PATH is stale."""
    executable = shutil.which("git")
    if executable:
        return executable
    if os.name != "nt":
        return None
    roots = [os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"),
             os.environ.get("ProgramFiles(x86)")]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(str(Path(local_app_data) / "Programs"))
    for root in dict.fromkeys(value for value in roots if value):
        for relative in (Path("Git") / "cmd" / "git.exe", Path("Git") / "bin" / "git.exe"):
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return None


def github_version_status(root, current_version, timeout=8):
    """Read published version tags from origin without changing the checkout."""
    root = Path(root).resolve()
    current = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current_version)
    if not current:
        return {"state": "unavailable", "message": "無法辨識目前程式版本。"}
    if not (root / ".git").exists():
        return {"state": "unavailable", "message": "此安裝沒有 Git 資料，無法追蹤 GitHub 版本。"}
    git = _git_executable()
    if not git:
        return {"state": "unavailable", "message": "找不到 Git for Windows（git.exe）；請安裝 Git 後重新啟動工作台。"}
    try:
        result = subprocess.run(
            [git, "-C", str(root), "ls-remote", "--tags", "origin", "refs/tags/v*"],
            capture_output=True, text=True, check=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        return {"state": "unavailable", "message": f"GitHub 版本檢查逾時（{timeout} 秒），請重試或檢查網路。"}
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "Git 未能讀取 origin 的版本標籤").strip().splitlines()[-1]
        return {"state": "unavailable", "message": f"GitHub 版本檢查失敗：{detail}"}
    except OSError as error:
        detail = error.strerror or str(error)
        return {"state": "unavailable", "message": f"無法啟動 Git（{detail}）。請確認 Git 安裝狀態。"}
    versions = []
    for line in result.stdout.splitlines():
        match = VERSION_TAG.search(line)
        if match:
            versions.append(tuple(map(int, match.groups())))
    if not versions:
        return {"state": "unavailable", "message": "origin 尚無 vX.Y.Z 版本標籤。"}
    newest = max(versions)
    latest = ".".join(map(str, newest))
    if newest > tuple(map(int, current.groups())):
        return {"state": "available", "current": current_version, "latest": latest,
                "message": f"GitHub 已有 v{latest}；目前安裝為 v{current_version}。"}
    return {"state": "current", "current": current_version, "latest": latest,
            "message": f"目前安裝 v{current_version}；GitHub 最新標籤為 v{latest}。"}


def pull_remote_update(root, timeout=120):
    """Fast-forward the checkout only when the working tree is clean."""
    root = Path(root).resolve()
    if not (root / ".git").exists():
        return {"state": "unavailable", "message": "此安裝沒有 Git 資料，無法由工作台更新。"}
    git = _git_executable()
    if not git:
        return {"state": "unavailable", "message": "找不到 Git for Windows（git.exe）；請安裝 Git 後重新啟動工作台。"}
    try:
        status = subprocess.run([git, "-C", str(root), "status", "--porcelain"],
                                capture_output=True, text=True, check=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if status.stdout.strip():
            files = "、".join(line[3:] for line in status.stdout.splitlines()[:5])
            return {"state": "blocked", "message": f"更新前必須先處理本機未提交修改：{files}"}
        result = subprocess.run([git, "-C", str(root), "pull", "--ff-only"],
                                capture_output=True, text=True, check=True, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "Git 更新失敗").strip().splitlines()[-1]
        return {"state": "error", "message": f"Git 更新失敗：{detail}"}
    except subprocess.TimeoutExpired:
        return {"state": "unavailable", "message": f"GitHub 更新逾時（{timeout} 秒），請重試或檢查網路。"}
    except OSError as error:
        detail = error.strerror or str(error)
        return {"state": "unavailable", "message": f"無法啟動 Git（{detail}）。請確認 Git 安裝狀態。"}
    return {"state": "updated", "message": result.stdout.strip() or "已完成 GitHub 更新，請重新啟動工作台。"}


def source_snapshot(root):
    root = Path(root).resolve()
    paths = [root/name for name in SOURCE_FILES]
    for directory in SOURCE_DIRECTORIES:
        folder = root/directory
        if folder.is_dir():
            paths.extend(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES)
    return {path.relative_to(root).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def changed_sources(before, after):
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


def validate_sources(root):
    root = Path(root).resolve()
    for name in source_snapshot(root):
        if name.endswith(".py"):
            path=root/name
            compile(path.read_bytes(),str(path),"exec")


def missing_runtime_requirements(root):
    """Allow updates after dependencies were already installed into this runtime."""
    from importlib.metadata import version,PackageNotFoundError
    missing=[]
    for line in (Path(root)/'requirements.txt').read_text('utf-8').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        name,separator,required=line.partition('==')
        if not separator:
            missing.append(line);continue
        try:installed=version(name)
        except PackageNotFoundError:installed=None
        if installed!=required:missing.append(line)
    return missing


def schedule_restart(root):
    root=Path(root).resolve();logs=root/"data"/"logs";logs.mkdir(parents=True,exist_ok=True)
    with (logs/"update.log").open("ab") as log:
        return subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"--wait",str(os.getpid()),str(root)],
            cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)


def wait_and_restart(pid, root):
    if os.name=="nt":
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x00100000,False,pid)
        if handle:
            try:
                if kernel.WaitForSingleObject(handle,120000)!=0: raise RuntimeError("更新逾時：原本的工作台仍在執行。")
            finally: kernel.CloseHandle(handle)
    else:
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try: os.kill(pid,0)
            except ProcessLookupError: break
            time.sleep(.2)
        else: raise RuntimeError("更新逾時：原本的工作台仍在執行。")
    subprocess.Popen([sys.executable,str(Path(root)/"main.py")],cwd=root.parent,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)


if __name__=="__main__" and len(sys.argv)==4 and sys.argv[1]=="--wait":
    wait_and_restart(int(sys.argv[2]),Path(sys.argv[3]))
