"""The onboarding manifest — the service's own declaration, validated hard.

The manifest is the single input to ``otto onboard <service>``: which
tools the service declares, at which authority tier, what it may spend,
and how it presents on Backstage. Validation is fail closed and every
refusal is plain English:

- the tier MUST be named explicitly (``tier: T0..T3``). A manifest with
  no tier is refused outright — there is no default tier, because the
  only wrong default is a privileged one and a safe default would still
  teach services that omitting the field is fine;
- a tool may name its own explicit tier; a tool that names none takes
  the service's explicitly named tier (still explicit — it is written
  in the same manifest, never invented here);
- the Backstage title and description must read as plain English, not
  identifiers (founder rule: no cryptic text on Backstage surfaces);
- budgets are per-lane daily allocations in USD, never negative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from otto.gateway import Tier
from otto.onboard.errors import OnboardingRefused

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

_STEP = "load_manifest"


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    """One tool the service declares, with its explicit tier."""

    name: str
    tier: Tier
    input_schema: dict


@dataclass(frozen=True, slots=True)
class OnboardingManifest:
    """The validated manifest. Every field was checked at load time."""

    service: str
    tier: Tier
    title: str
    description: str
    owner: str
    tools: tuple[ToolDeclaration, ...]
    budgets: dict[str, float]
    lifecycle: str = "experimental"


def _refuse(service: str, reason: str, remedy: str = "") -> OnboardingRefused:
    return OnboardingRefused(service or "(unnamed)", _STEP, reason, remedy)


def _require_plain_english(service: str, field_name: str, value: object) -> str:
    """Backstage surfaces read like English, never like identifiers."""
    if not isinstance(value, str) or not value.strip():
        raise _refuse(
            service,
            f"the manifest's {field_name} is missing or empty; Backstage "
            "surfaces need a plain-English one",
            f"add a {field_name} a person can read, for example "
            '"Keeps the billing ledger in step with the store."',
        )
    text = value.strip()
    if "_" in text or text == service:
        raise _refuse(
            service,
            f"the manifest's {field_name} ({text!r}) reads like an "
            "identifier, not plain English",
            f"write the {field_name} as words a person would say, not a code name",
        )
    return text


def _parse_tier(service: str, raw: object, where: str) -> Tier:
    try:
        return Tier.parse(raw)  # type: ignore[arg-type]
    except (ValueError, TypeError) as exc:
        raise _refuse(
            service,
            f"{where} names an unknown tier {raw!r}",
            "use one of T0, T1, T2 or T3",
        ) from exc


def _parse_tools(
    service: str, raw_tools: object, service_tier: Tier
) -> tuple[ToolDeclaration, ...]:
    if raw_tools is None:
        raw_tools = []
    if not isinstance(raw_tools, list):
        raise _refuse(service, "the manifest's tools entry must be a list of tools")
    tools: list[ToolDeclaration] = []
    for row in raw_tools:
        if not isinstance(row, dict) or not str(row.get("name", "")).strip():
            raise _refuse(
                service,
                f"a declared tool has no name (entry: {row!r})",
                "give every tool a name, for example billing.read",
            )
        name = str(row["name"]).strip()
        tier = (
            _parse_tier(service, row["tier"], f"tool {name!r}")
            if "tier" in row
            else service_tier
        )
        schema = row.get("input_schema", {"type": "object"})
        if not isinstance(schema, dict):
            raise _refuse(
                service,
                f"tool {name!r} has an input_schema that is not a mapping",
                "declare the tool's input as a JSON Schema object",
            )
        tools.append(ToolDeclaration(name=name, tier=tier, input_schema=schema))
    return tuple(tools)


def _parse_budgets(service: str, raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise _refuse(
            service,
            "the manifest names no budgets mapping; every onboarded service "
            "declares what it may spend per lane (an empty mapping means "
            "zero spend, but it must be written down)",
            'add a budgets mapping, for example budgets: {"bulk": 1.50}',
        )
    budgets: dict[str, float] = {}
    for lane, amount in raw.items():
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise _refuse(
                service,
                f"the budget for lane {lane!r} is not a number ({amount!r})",
                "budgets are daily USD amounts, for example 1.50",
            )
        if amount < 0:
            raise _refuse(
                service, f"the budget for lane {lane!r} is negative ({amount})"
            )
        budgets[str(lane)] = float(amount)
    return budgets


def parse_manifest(raw: object, *, source: str = "") -> OnboardingManifest:
    """Validate a parsed manifest document; refuse loudly on any defect."""
    label = f" ({source})" if source else ""
    if not isinstance(raw, dict):
        raise _refuse(
            "",
            f"the manifest{label} is not a mapping of fields",
            "write the manifest as YAML keys: service, tier, title, "
            "description, owner, tools, budgets",
        )
    service = str(raw.get("service", "")).strip()
    if not service or not _SERVICE_NAME_RE.match(service):
        raise _refuse(
            service,
            f"the manifest{label} needs a service name in lowercase letters, "
            f"digits, dots and dashes (got {raw.get('service')!r})",
        )

    # The admission rule: the tier must be written down. No tier, no
    # onboarding — and NEVER a default, because a defaulted tier is one
    # config drift away from default-to-privileged.
    if "tier" not in raw:
        raise _refuse(
            service,
            "the manifest names no tier; onboarding refuses rather than "
            "assume one (there is no default tier, and never a "
            "privileged one)",
            "add an explicit tier: T0, T1, T2 or T3",
        )
    tier = _parse_tier(service, raw["tier"], "the manifest's tier field")

    title = _require_plain_english(service, "title", raw.get("title"))
    description = _require_plain_english(service, "description", raw.get("description"))
    if len(description.split()) < 3:
        raise _refuse(
            service,
            f"the manifest's description ({description!r}) is too short to "
            "tell a person what the service does",
            "write at least one full sentence",
        )

    owner = str(raw.get("owner", "")).strip()
    if not owner:
        raise _refuse(
            service,
            "the manifest names no owner; every catalog entity has one",
            "add an owner, for example group:default/platform",
        )

    lifecycle = str(raw.get("lifecycle", "experimental")).strip() or "experimental"

    return OnboardingManifest(
        service=service,
        tier=tier,
        title=title,
        description=description,
        owner=owner,
        tools=_parse_tools(service, raw.get("tools"), tier),
        budgets=_parse_budgets(service, raw.get("budgets")),
        lifecycle=lifecycle,
    )


def load_manifest(path) -> OnboardingManifest:
    """Read and validate a manifest YAML file; every failure is a refusal."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OnboardingRefused(
            "(unnamed)",
            _STEP,
            f"the manifest file could not be read: {exc}",
            "pass --manifest with the path to the service's onboarding "
            "manifest, or set OTTO_ONBOARD_MANIFEST_DIR",
        ) from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OnboardingRefused(
            "(unnamed)", _STEP, f"the manifest file is not valid YAML: {exc}"
        ) from exc
    return parse_manifest(raw, source=str(path))
