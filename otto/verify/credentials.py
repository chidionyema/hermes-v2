"""Prover credentials: read-only by construction, per system.

Spec section 7: the prover holds its own read-only ServiceAccounts,
distinct from the orchestrator's, and "every write attempt is denied by
the credential itself, not by application logic". This module pins that
contract in-process: a :class:`ReadOnlyServiceAccount` is minted with a
reader and *no write capability at all* — there is no write token to
misuse and no application-level ``if`` guarding a write path. On the
staging cluster the same property is enforced at OCI IAM / RBAC level;
wiring `otto test prover-write-deny` to the live systems is the
integration wave's job, against this exact interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

#: The systems the prover can read (spec section 7 acceptance test).
PROVER_SYSTEMS: tuple[str, ...] = ("k8s", "postgres", "object_storage")


class WriteDenied(PermissionError):
    """Raised by the credential layer itself: the account has no write scope."""

    def __init__(self, system: str, operation: str) -> None:
        super().__init__(
            f"credential for {system!r} holds no write scope; "
            f"operation {operation!r} denied by the credential, "
            "not by application logic"
        )
        self.system = system
        self.operation = operation


@dataclass(frozen=True)
class ReadOnlyServiceAccount:
    """A per-system credential minted with read scope only.

    ``owner`` is the identity the account belongs to; the Background of
    the CP3 feature asserts the prover's accounts are distinct from the
    orchestrator's.
    """

    system: str
    owner: str
    reader: Callable[[str], Any]

    def read(self, target: str) -> Any:
        return self.reader(target)

    def write(self, operation: str, *_args: Any, **_kwargs: Any) -> None:
        raise WriteDenied(self.system, operation)


def mint_prover_accounts(
    owner: str,
    readers: Mapping[str, Callable[[str], Any]],
) -> dict[str, ReadOnlyServiceAccount]:
    """Mint one read-only account per prover system for ``owner``."""
    return {
        system: ReadOnlyServiceAccount(
            system=system,
            owner=owner,
            reader=readers.get(system, lambda target: None),
        )
        for system in PROVER_SYSTEMS
    }


def prover_write_deny_report(
    accounts: Mapping[str, ReadOnlyServiceAccount],
) -> dict[str, bool]:
    """Attempt one write per system; report ``system -> denied``.

    This is the in-process body of ``otto test prover-write-deny``: it
    tries a representative write through each credential and records
    whether the credential itself refused. Any write that *succeeds*
    reports ``False`` and the caller must treat that as a build defect.
    """
    report: dict[str, bool] = {}
    for system, account in accounts.items():
        try:
            account.write("test-write")
        except WriteDenied:
            report[system] = True
        else:
            report[system] = False
    return report
