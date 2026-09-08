"""Backstage catalog entity for an onboarded service.

The founder reads the estate from Backstage, so the entity is rendered
straight from the validated manifest — plain-English title and
description are enforced at manifest load, this module only lays them
out. The tier and the declared tools ride along as tags and an
annotation so a buyer's engineer can read a service's authority straight
off the catalog page.
"""

from __future__ import annotations

import yaml

from otto.onboard.manifest import OnboardingManifest

TIER_ANNOTATION = "otto.dev/tier"
TOOLS_ANNOTATION = "otto.dev/declared-tools"


def render_catalog_entity(manifest: OnboardingManifest) -> dict:
    """The entity as a dict; ``catalog_yaml`` turns it into the file."""
    return {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": "Component",
        "metadata": {
            "name": manifest.service,
            "title": manifest.title,
            "description": manifest.description,
            "tags": ["otto-onboarded", f"tier-{manifest.tier.name.lower()}"],
            "annotations": {
                TIER_ANNOTATION: manifest.tier.name,
                TOOLS_ANNOTATION: ", ".join(t.name for t in manifest.tools)
                or "none declared",
            },
        },
        "spec": {
            "type": "service",
            "lifecycle": manifest.lifecycle,
            "owner": manifest.owner,
            "system": "otto",
        },
    }


def catalog_yaml(manifest: OnboardingManifest) -> str:
    return yaml.safe_dump(
        render_catalog_entity(manifest), sort_keys=False, allow_unicode=True
    )
