"""Tests for the Universal Event Gateway (``otto/ingress``): one door for
every channel and every customer.

No test here opens a socket or reaches a network. The binding store is
SQLite in memory, so the real queries run; the publisher is a recorder,
so the real envelope is inspected; observability binds to the in-memory
exporter through ``OTTO_OBS_MODE=test``.
"""
