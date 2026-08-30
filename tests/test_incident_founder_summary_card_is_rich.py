"""Founder, 2026-08-30 01:2xZ: "in the old telegram I have my /summary game which had a rich
interface, now reduced to commands, not nice."

The card (hermes-agent gateway/summary_card.py) is <details> blocks and tables. Telegram renders
those only through the rich message endpoint, which the adapter guards with
platforms.telegram.extra.rich_messages, default False since upstream 6183e8ce1b (2026-06-21).
This repo's config.yaml never opted in, so the cluster gateway sent the card flat on every pin.
The pin is here so the flag cannot fall out again; the second test reads the adapter source that the
image actually clones, so a future pin that renames or drops the flag reads red, not silently flat.
"""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _config():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def test_telegram_rich_messages_are_opted_in():
    extra = _config()["platforms"]["telegram"]["extra"]
    assert extra.get("rich_messages") is True, (
        "platforms.telegram.extra.rich_messages must be true: the /summary card is <details> "
        "blocks and tables, which only Telegram's rich message endpoint renders"
    )


def test_pinned_adapter_still_reads_the_same_flag():
    adapter = ROOT / "hermes-agent" / "plugins" / "platforms" / "telegram" / "adapter.py"
    if not adapter.exists():
        import pytest

        pytest.skip("hermes-agent checkout not beside this repo; CI clones it at PINNED_VERSION")
    src = adapter.read_text(encoding="utf-8")
    assert re.search(r'_coerce_bool_extra\(\s*"rich_messages"', src), (
        "the pinned adapter no longer reads platforms.telegram.extra.rich_messages; "
        "find the new switch before bumping PINNED_VERSION"
    )
    card = ROOT / "hermes-agent" / "gateway" / "summary_card.py"
    assert card.exists(), "the pinned hermes-agent lost gateway/summary_card.py"
    assert "<details>" in card.read_text(encoding="utf-8")
