"""``otto onboard <service>`` — the estate-onboarding lane (W4, crew#768).

One command onboards an estate service onto Otto, and onboarding IS the
admission ticket: a service that has not been onboarded is not admitted.
The command, in order:

1. registers the service's declared tools with the tool gateway
   (``otto.gateway.ToolRegistry``) at the tier the manifest names
   EXPLICITLY — a manifest that names no tier is refused, never defaulted
   to a privileged tier;
2. signs the service's capability inventory with CP1's Ed25519 machinery
   (``otto.spine.inventory`` — the one signing scheme, never a second);
3. allocates the service's budgets through the router's own config
   (``otto.router.config.RouterConfig`` + ``otto.router.budget``);
4. stamps trace attributes through ``otto.obs.instrument`` so the
   service's spans carry the service name and tier;
5. writes a Backstage catalog entity file with a plain-English title and
   description (founder rule: no cryptic text on Backstage surfaces);
6. REFUSES to finish unless the observability coverage gate
   (``otto.obs.coverage``) can see the service in the trace backend —
   fail closed, a loud structured red, never a silent green. Artifacts
   are staged and only promoted after the gate is green, so a refusal
   leaves nothing half-onboarded.

R64 note: this package contains no prompts and performs no model calls;
DSPy does not apply here.
"""

from otto.onboard.catalog import render_catalog_entity
from otto.onboard.core import OnboardingOutcome, onboard_service
from otto.onboard.errors import OnboardingRefused
from otto.onboard.manifest import OnboardingManifest, ToolDeclaration, load_manifest

__all__ = [
    "OnboardingManifest",
    "OnboardingOutcome",
    "OnboardingRefused",
    "ToolDeclaration",
    "load_manifest",
    "onboard_service",
    "render_catalog_entity",
]
