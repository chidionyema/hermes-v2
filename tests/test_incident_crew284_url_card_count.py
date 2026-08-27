"""crew#284, 2026-08-27: the pinned URL card said "5 links" above four bullets.

Rung 2 (property). `card()` prints each (title, url) once but counted duplicates, so a catalogue
component with the same https link twice made the founder's card lie by one. The number on the
card must equal the number of bullets, for any `found` mapping.
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("estate_urls", os.path.join(ROOT, "scripts", "estate-urls.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _count(found):
    body = mod.card(found)
    bullets = [l for l in body.splitlines() if l.startswith("• ")]
    said = int(body.splitlines()[-1].split(" ")[0])
    return len(bullets), said


def test_count_matches_bullets_with_duplicates():
    found = {"llm.mumchimp.com": [("Open", "https://llm.mumchimp.com"), ("Open", "https://llm.mumchimp.com")],
             "auth.mumchimp.com": [("Open", "https://auth.mumchimp.com")]}
    assert _count(found) == (2, 2)


def test_count_matches_bullets_without_duplicates():
    found = {"a": [("Open", "https://a"), ("UI", "https://a/ui")], "b": [("Open", "https://b")]}
    assert _count(found) == (3, 3)
