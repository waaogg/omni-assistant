#!/usr/bin/env python3
"""
Test Git Isolation and Boundary Defense
Ensures that sensitive files, credentials, and runtime data are strictly ignored by Git.
"""

import subprocess
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SENSITIVE_TEST_FILES = [
    ".env",
    ".env.local",
    "data/synced_todos.json",
    "data/group_history.json",
    "data/wechat/auth.json",
    "data/wechat/sync_buf.txt",
    "data/wechat/conversations.json",
    "media/test_image.png",
    "napcat.token",
    "server.key",
    "assistant.log",
    "intrusion_audit.log"
]

def test_git_ignore_defense():
    # Initialize a dummy git repo if not already
    is_temp_git = False
    git_dir = PROJECT_ROOT / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init"], cwd=str(PROJECT_ROOT), check=True, capture_output=True)
        is_temp_git = True

    created_files = []
    try:
        # Create dummy sensitive files
        for rel_path in SENSITIVE_TEST_FILES:
            f = PROJECT_ROOT / rel_path
            f.parent.mkdir(parents=True, exist_ok=True)
            if not f.exists():
                f.write_text("SENSITIVE_DUMMY_CONTENT")
                created_files.append(f)

        # Check git status
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(PROJECT_ROOT),
            check=True,
            capture_output=True,
            text=True
        )
        status_output = res.stdout

        leaked_files = []
        for line in status_output.splitlines():
            line = line.strip()
            if not line:
                continue
            # status line: "?? path" or "M path"
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                tracked_path = parts[1].replace("\\", "/")
                for sensitive in SENSITIVE_TEST_FILES:
                    if tracked_path == sensitive or tracked_path.endswith("/" + sensitive):
                        leaked_files.append(tracked_path)

        if leaked_files:
            assert False, f"CRITICAL: The following sensitive files were NOT ignored by .gitignore: {leaked_files}"
        
        print("✅ test_git_ignore_defense passed: 100% of sensitive files are blocked by .gitignore.")

    finally:
        # Cleanup dummy sensitive test files
        for f in created_files:
            try:
                if f.exists():
                    f.unlink()
            except Exception:
                pass
        # Clean up dummy media dir if empty
        media_dir = PROJECT_ROOT / "media"
        if media_dir.exists() and not any(media_dir.iterdir()):
            media_dir.rmdir()
        if is_temp_git and git_dir.exists():
            shutil.rmtree(git_dir)

if __name__ == "__main__":
    test_git_ignore_defense()
    print("🎉 GIT ISOLATION DEFENSE TEST PASSED 100%!")
