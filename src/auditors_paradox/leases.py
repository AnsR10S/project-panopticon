"""Use an OS-owned lease to distinguish an orphaned run from a live worker."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any


class RunLease:
    """A live writer holds this file lock; process death releases it automatically."""

    def __init__(self, directory: Path, *, existing: bool = False) -> None:
        path = directory / "control/writer.lease"
        if existing and not path.is_file():
            raise ValueError(
                "This run has no worker lease; orphan recovery cannot establish ownership"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise ValueError(
                "The run still has an active writer; orphan recovery is blocked"
            ) from None

    def close(self) -> None:
        """Closing the owned handle releases the lease on either supported OS."""
        self.stream.close()

    def __enter__(self) -> RunLease:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
