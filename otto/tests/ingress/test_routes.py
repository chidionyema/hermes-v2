"""Path routing: one door, and no route that names a channel.

No socket is opened here. ``channel_from_path`` is the whole of the
routing decision, so testing it directly tests the router.
"""

from __future__ import annotations

import pytest

from otto.ingress.server import channel_from_path


@pytest.mark.parametrize(
    ("path", "channel"),
    [
        ("/webhook/telegram", "telegram"),
        ("/webhook/http", "http"),
        ("/webhook/slack", "slack"),  # a channel with no plugin still parses
        ("/webhook/telegram/", "telegram"),
        ("/webhook/telegram?update=7", "telegram"),
    ],
)
def test_the_channel_is_the_one_segment_after_the_webhook_prefix(
    path: str, channel: str
) -> None:
    assert channel_from_path(path) == channel


@pytest.mark.parametrize(
    "path",
    [
        "/webhook/",
        "/webhook",
        "/",
        "/healthz",
        "/telegram-webhook",  # the per-channel route this design removes
        "/webhook/telegram/tenant-acme",  # a per-customer path leaks the list
    ],
)
def test_anything_that_is_not_one_channel_segment_is_not_a_channel(
    path: str,
) -> None:
    assert channel_from_path(path) is None


def test_the_gateway_serves_every_registered_channel_from_the_same_door(
    gateway,
) -> None:
    """If this list ever needs a matching route, the design has been lost."""
    assert gateway.channels == ("http", "telegram")
