"""Tells you when a newer Omni is on GitHub, and updates when you ask.

Omni never updates itself on its own: new code only runs after you click "Update".
Works for installs made with `git clone`; ZIP installs just never see the notice.
"""
import os
import subprocess
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_EVERY = 24 * 3600
RETRY_AFTER = 3600


def _git(*args, timeout=60):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


class Updater:
    def __init__(self, log=print):
        self.log = log
        self.is_git = os.path.isdir(os.path.join(ROOT, ".git"))
        self.behind = 0           # how many changes GitHub has that you don't
        self.checked = 0
        self.tried = 0
        self.checking = False
        self.notify = lambda n: None   # set by the tray: shows a one-time message

    def maybe_check(self, force=False):
        if not self.is_git or self.checking:
            return
        now = time.time()
        if not force and (now - self.checked < CHECK_EVERY or now - self.tried < RETRY_AFTER):
            return
        self.tried = now
        self.checking = True
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        try:
            r = _git("fetch", "-q")
            if r.returncode:
                raise RuntimeError(r.stderr.strip() or "git fetch failed")
            r = _git("rev-list", "--count", "HEAD..@{u}")
            if r.returncode:
                raise RuntimeError(r.stderr.strip() or "no upstream branch")
            before, self.behind = self.behind, int(r.stdout.strip() or 0)
            self.checked = time.time()
            if self.behind and not before:
                self.log(f"update: {self.behind} new change(s) on GitHub")
                self.notify(self.behind)
        except Exception as e:
            self.log("update: couldn't check for updates:", e)
        finally:
            self.checking = False

    def update(self):
        """Downloads the new version. Returns (ok, message). The caller restarts Omni."""
        if not self.is_git:
            return False, "This copy of Omni wasn't installed with git. Download it again from GitHub."
        before = _git("rev-parse", "HEAD").stdout.strip()
        try:
            r = _git("pull", "--ff-only", "-q", timeout=120)
        except subprocess.TimeoutExpired:
            return False, "Downloading the update took too long. Try again later."
        if r.returncode:
            return False, ("Couldn't update, probably because files in the Omni folder were changed:\n"
                           + (r.stderr.strip() or r.stdout.strip()))
        self.behind = 0
        if _git("rev-parse", "HEAD").stdout.strip() == before:
            return False, "Omni is already up to date."
        return True, "Updated."
