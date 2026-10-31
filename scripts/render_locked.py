"""Run one render command at a time on macOS/Linux, without PID/stale-slot races.

Usage: python scripts/render_locked.py --lock /project/output/render.lock -- COMMAND ...
The lock file must never be removed: flock ownership follows an open inode, not a PID.
The command inherits its descriptor so killing this wrapper cannot unlock a live render.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide a command after --")
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()) + "\n")
        lock.flush()
        child = subprocess.Popen(command, pass_fds=(lock.fileno(),))
        while True:
            try:
                code = child.wait()
                return code if code >= 0 else 128 - code
            except KeyboardInterrupt:
                # The terminal also signals the child. Keep ownership until it exits.
                continue


if __name__ == "__main__":
    raise SystemExit(main())
