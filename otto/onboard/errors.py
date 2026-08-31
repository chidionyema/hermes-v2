"""The one refusal shape for onboarding — structured, loud, never a bare string.

A refused onboarding is DATA the caller can render and a machine can act
on: which service, which step said no, why in plain English, and what to
do about it. The CLI prints ``as_dict()`` as one JSON line and exits
nonzero; nothing downstream has to parse prose out of a stack trace.
"""

from __future__ import annotations

import json


class OnboardingRefused(RuntimeError):
    """Onboarding stopped at a named step and admitted nothing."""

    def __init__(
        self,
        service: str,
        step: str,
        reason: str,
        remedy: str = "",
        detail: dict | None = None,
    ) -> None:
        self.service = service
        self.step = step
        self.reason = reason
        self.remedy = remedy
        self.detail = detail
        super().__init__(json.dumps(self.as_dict()))

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "error": "otto.onboard.refused",
            "result": "red",
            "service": self.service,
            "step": self.step,
            "reason": self.reason,
            "remedy": self.remedy,
        }
        if self.detail is not None:
            out["detail"] = self.detail
        return out
