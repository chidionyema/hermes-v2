"""The channel-plane adapter contract (crew#768 CP2b, founder 2026-08-31:
"day 0 ability for all surfaces, not just telegram").

Spec: docs/specs/otto-platform-v1/SURFACE-CONTRACT-DAY0.md. Every surface
(Telegram today; web, Slack, email, a voice session, a glasses card
later) normalizes its native event into the SAME neutral
``SurfaceEnvelope`` (``otto/surface/envelope.py``) through a
``SurfaceAdapter`` (``otto/surface/adapter.py``) and renders a response
back through the same capability-negotiating path
(``otto/surface/renderer.py``). The gateway, tiers, taint rules and the
Verification Plane (peer lanes, not imported here) never see which
surface they are serving; nothing in this package can build a path
around them, because nothing in this package calls them at all — it only
produces and consumes the envelope and rendered-message shapes those
lanes wire against.

No prompt strings in this package; DSPy not applicable. R64.
"""

from __future__ import annotations
