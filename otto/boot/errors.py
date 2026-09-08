"""The one refusal shape for the boot lane — structured, loud, never a bare string.

Mirrors ``otto.obs.core.ObsBootError``: a component that cannot start
safely raises this instead of running dark or crashing opaquely. Every
field is safe to print and to log — a message never carries the secret
value of an environment variable, only its name.
"""

from __future__ import annotations

import json


class BootRefused(RuntimeError):
    """The boot lane refuses to start (or to act) for a named reason."""

    def __init__(self, reason: str, remedy: str) -> None:
        self.reason = reason
        self.remedy = remedy
        super().__init__(json.dumps(self.as_dict()))

    def as_dict(self) -> dict[str, str]:
        return {
            "error": "otto.boot.refused",
            "reason": self.reason,
            "remedy": self.remedy,
        }
