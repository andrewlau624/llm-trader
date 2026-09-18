"""One live process per account.

A lesson that cost a real stop order: two live loops managed the same Alpaca account, both hit the
session-end close in the same minute, and Alpaca's `close_all_positions(cancel_orders=True)` cancels
*every* order on the account. One process's cancel killed the other's cover order, leaving a short
position open and unprotected overnight.

A lock file keyed to the account fingerprint makes the second process refuse to start instead.
flock is released by the kernel when the process dies, so a crash cannot leave a stale lock.
"""

import contextlib
import fcntl
import hashlib
import os
from pathlib import Path


def account_fingerprint(api_key, secret_key=""):
    """Identifies the account without storing the key.

    Same account, same fingerprint, on any host - so two installations cannot silently trade one
    account, which is the failure this module exists to prevent.
    """
    material = f"{api_key}:{secret_key}".encode()
    return hashlib.sha256(material).hexdigest()[:12]


class AccountLock:
    def __init__(self, path):
        self.path = Path(path)
        self.fh = None
        self.held = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("a+")
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.seek(0)
            other = self.fh.read().strip()
            self.fh.close()
            self.fh = None
            return f"held by pid {other or 'unknown'} ({self.path})"
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(str(os.getpid()))
        self.fh.flush()
        self.held = True
        return None

    def release(self):
        if self.fh and self.held:
            with contextlib.suppress(OSError):
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()
            self.held = False
