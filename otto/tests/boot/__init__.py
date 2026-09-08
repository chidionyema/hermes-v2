"""Tests for the boot lane (``otto/boot``): the webhook server that
crosses the platform lanes for a real Telegram update.

Every test in this package uses a fake ``TelegramTransport`` and
``OTTO_OBS_MODE=test`` (the in-memory exporter). No test opens a real
socket and no test dials out to Telegram.
"""
