#!/usr/bin/env python3
"""Tests for the failure paths of the screenshot handler.

The happy path is one line. Everything below is what actually happens at 11pm
when the founder sends a photo from a train.

Run: python3 handlers/tests/test_screenshot_to_issue.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import screenshot_to_issue as h  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TOK = "ghp_" + "x" * 36
FAILURES: list[str] = []


def check(name, fn):
    try:
        fn()
        print(f"ok    {name}")
    except AssertionError as e:
        print(f"FAIL  {name}: {e}")
        FAILURES.append(name)
    except Exception as e:  # an unexpected exception is also a failure
        print(f"FAIL  {name}: unexpected {type(e).__name__}: {e}")
        FAILURES.append(name)


def raises(fn, fragment):
    try:
        fn()
    except h.HandlerError as e:
        assert fragment.lower() in str(e).lower(), f"wrong message: {e}"
        return
    raise AssertionError(f"expected HandlerError containing {fragment!r}, got none")


def tmp(data: bytes, suffix=".png") -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(data)
    f.close()
    return f.name


# --- input that is not an image -------------------------------------------
check("missing file is refused", lambda: raises(lambda: h.read_image("/nope/x.png"), "no file"))
check("empty file is refused", lambda: raises(lambda: h.read_image(tmp(b"")), "empty"))
check(
    "a text file named .png is refused",
    lambda: raises(lambda: h.read_image(tmp(b"this is not an image at all")), "not an image"),
)
check(
    "a directory is refused",
    lambda: raises(lambda: h.read_image(tempfile.mkdtemp()), "directory"),
)
check(
    "oversized image is refused before the API call",
    lambda: raises(lambda: h.read_image(tmp(PNG + b"\x00" * h.MAX_BYTES)), "over the"),
)
check("a real png is accepted", lambda: h.read_image(tmp(PNG)))
check("jpeg magic is recognised", lambda: (_ for _ in ()).throw(AssertionError("x"))
      if h.sniff_image(b"\xff\xd8\xff\xe0rest") != "jpeg" else None)
check("heic at offset 4 is recognised",
      lambda: None if h.sniff_image(b"\x00\x00\x00\x20ftypheix" + b"0" * 8) in ("heic", "heif")
      else (_ for _ in ()).throw(AssertionError("heic not recognised")))


# --- captions --------------------------------------------------------------
def caption_cases():
    assert h.clean_caption(None) == "(no caption)"
    assert h.clean_caption("   ") == "(no caption)"
    long = "x" * (h.MAX_CAPTION + 500)
    out = h.clean_caption(long)
    assert "truncated" in out, "a pasted log must be truncated, not filed whole"
    assert len(out) < len(long)
    # A caption cannot break out of the fence we put it in.
    assert "```" not in h.clean_caption("look ```\n rm -rf / \n```")


check("captions are cleaned, truncated and cannot escape the fence", caption_cases)


# --- auth ------------------------------------------------------------------
def no_token():
    import os
    saved = {k: os.environ.pop(k, None) for k in ("GITHUB_TOKEN", "GH_TOKEN")}
    try:
        raises(h.token, "not set")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


check("a missing token fails with a sentence, not a traceback", no_token)


def short_token():
    import os
    saved = os.environ.get("GITHUB_TOKEN")
    os.environ["GITHUB_TOKEN"] = "ghp_short"
    try:
        raises(h.token, "too short")
    finally:
        if saved is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = saved


check("an obviously wrong token is caught before the call", short_token)


def expired_token():
    calls = []

    def post(url, payload, tok):
        calls.append(url)
        return 401, {"message": "Bad credentials"}, {}

    raises(lambda: h.create_issue("t", "b", post=post, sleeper=lambda s: None, tok=TOK),
           "refused the token")
    assert len(calls) == 1, f"a dead token must not be retried, tried {len(calls)} times"


check("an expired token is not retried", expired_token)


# --- retries ---------------------------------------------------------------
def retry_then_succeed():
    seq = [(500, {}, {}), (502, {}, {}), (201, {"number": 7, "html_url": "u"}, {})]
    waits = []

    def post(url, payload, tok):
        return seq.pop(0)

    r = h.create_issue("t", "b", post=post, sleeper=waits.append, tok=TOK)
    assert r.number == 7, r
    assert r.attempts == 3, r.attempts
    assert waits == [1.0, 2.0], f"backoff should double: {waits}"


check("two 500s then a 201 files the issue, with backoff", retry_then_succeed)


def gives_up():
    def post(url, payload, tok):
        return 503, {"message": "unavailable"}, {}

    raises(lambda: h.create_issue("t", "b", post=post, sleeper=lambda s: None, tok=TOK),
           "all 4 attempts")


check("a permanently down API gives up and says nothing was filed", gives_up)


def honours_retry_after():
    seq = [(429, {"message": "rate limited"}, {"Retry-After": "7"}),
           (201, {"number": 9, "html_url": "u"}, {})]
    waits = []
    r = h.create_issue("t", "b", post=lambda *a: seq.pop(0), sleeper=waits.append, tok=TOK)
    assert r.number == 9
    assert waits == [7.0], f"must wait what GitHub asked for, waited {waits}"


check("Retry-After is obeyed instead of our own backoff", honours_retry_after)


def no_retry_on_422():
    calls = []

    def post(url, payload, tok):
        calls.append(1)
        return 422, {"message": "Validation Failed"}, {}

    raises(lambda: h.create_issue("t", "b", post=post, sleeper=lambda s: None, tok=TOK), "422")
    assert len(calls) == 1, f"a bad payload must not be retried, tried {len(calls)}"


check("a 422 is not retried - retrying repeats our own mistake", no_retry_on_422)


def wait_is_capped():
    seq = [(429, {}, {"Retry-After": "99999"}), (201, {"number": 1, "html_url": "u"}, {})]
    waits = []
    h.create_issue("t", "b", post=lambda *a: seq.pop(0), sleeper=waits.append, tok=TOK)
    assert waits == [60.0], f"a silly Retry-After must be capped, got {waits}"


check("an absurd Retry-After is capped at 60s", wait_is_capped)


# --- end to end, no network ------------------------------------------------
def dry_run_files_nothing():
    r = h.handle(tmp(PNG), "login page 500s", dry_run=True)
    assert r.dry_run and r.number is None


check("dry run prints the issue and files nothing", dry_run_files_nothing)


def body_never_labels_agent_go():
    body = h.build_body("/x/a.png", "png", "broken")
    assert "agent-go" in body and "Not labelled `agent-go`" in body, \
        "the body must say why it is not labelled agent-go"


check("the filed issue is never labelled agent-go", body_never_labels_agent_go)

print()
if FAILURES:
    print(f"FAIL {len(FAILURES)} of the failure paths are not handled: {FAILURES}")
    sys.exit(1)
print("PASS every failure path handled")
