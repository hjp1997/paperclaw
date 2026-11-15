"""Prevent multiple processes from corrupting the same cache/rate-limit state."""
import os
from pathlib import Path


class InstanceLock:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.handle = None

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.handle = (self.directory / "instance.lock").open("a+b")
        if self.handle.seek(0, 2) == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise OSError("This data folder is already in use. Close the previous PaperClaw window first.") from None
        return self

    def __exit__(self, *args):
        self.handle.close()
