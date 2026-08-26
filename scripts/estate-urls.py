#!/usr/bin/env python3
"""The estate's URLs, generated from the catalogue and pinned in the founder's chat.

Founder, 2026-08-26 (crew#282): "all urls need to be pinned on telegram, all
urls across estate, dagster also, every interface/ui needs exposure."

Source of truth is the Backstage catalogue in the platform repo (crew#269 row 2
put every published hostname there as an Open link; bin/catalog-links-check in
idp keeps it there). Nothing here is typed by hand: a UI that gains a host
appears on the next tick, one that loses it disappears.

One tick:
  1. pull the catalogue the cluster runs (the OCI artifact idp pushes; flux + gh login)
  2. collect every Component's https links whose host matches `urls.include`
  3. render one card, hash it, compare with state/urls-pin.json
  4. unchanged -> print nothing (a job that says nothing is the normal state)
     changed   -> edit the pinned message if there is one, else send + pin it,
                  and print `URLS pinned msg=<id> n=<count>`

Config, in estate.yaml:
  urls:
    catalog_oci: oci://ghcr.io/OWNER/idp/estate-catalog:latest   # what the cluster runs
    catalog_repo: OWNER/idp                 # fallback: the file in the platform repo
    catalog_path: catalog/catalog-info.yaml
    include: mumchimp.com                   # substring a URL's host must contain
    chat_env: TELEGRAM_HOME_CHANNEL         # env var naming the chat to pin in

Telegram is the Bot API over HTTPS (sendMessage, editMessageText, pinChatMessage);
the token is TELEGRAM_BOT_TOKEN in the gateway's environment, never in a file here.

  --dry-run          print the card and exit, no network to Telegram
  --catalog FILE     read the catalogue from a file instead of GitHub (drills, tests)
  --transport FILE   append each Telegram call as JSON to FILE instead of sending (tests)
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("HERMES_URLS_STATE") or os.path.join(HOME, "state", "urls-pin.json")
DEFAULTS = {"catalog_path": "catalog/catalog-info.yaml", "include": "", "chat_env": "TELEGRAM_HOME_CHANNEL"}


def load_config():
    import yaml
    with open(os.environ.get("HERMES_ESTATE_YAML") or os.path.join(HOME, "estate.yaml")) as f:
        estate = yaml.safe_load(f) or {}
    cfg = dict(DEFAULTS)
    cfg.update(estate.get("urls") or {})
    if "catalog_repo" not in cfg:
        sys.exit("FAIL estate.yaml urls.catalog_repo is not set")
    return cfg


def fetch_catalog(cfg):
    """The catalogue the cluster runs: the OCI artifact idp pushes (bin/idp-catalog-push),
    pulled with flux and the GitHub login already on this machine. Falls back to the
    file in the platform repo when urls.catalog_oci is not set."""
    import tempfile
    oci = cfg.get("catalog_oci")
    if oci:
        for t in ("flux", "gh"):
            if not shutil.which(t):
                sys.exit(f"FAIL {t} is not on PATH")
        # The machine's own GitHub login, never a GITHUB_TOKEN from .env: that one is a
        # repo token without read:packages and gh prefers it over the keyring (crew#282:
        # the first live tick was DENIED on the private package).
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
        user = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True, env=env).stdout.strip()
        token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, env=env).stdout.strip()
        if not (user and token):
            sys.exit("FAIL gh has no login on this machine (gh auth login)")
        registry = oci.removeprefix("oci://").split("/")[0]
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as dc:
            # flux reads docker's config.json; a private DOCKER_CONFIG keeps the token out of ps argv.
            import base64
            auth = base64.b64encode(f"{user}:{token}".encode()).decode()
            with open(os.path.join(dc, "config.json"), "w") as f:
                json.dump({"auths": {registry: {"auth": auth}}}, f)
            r = subprocess.run(["flux", "pull", "artifact", oci, "-o", d],
                               capture_output=True, text=True, env=dict(env, DOCKER_CONFIG=dc))
            if r.returncode:
                sys.exit(f"FAIL flux pull {oci}: {r.stderr.strip()[-200:]}")
            import yaml
            texts = []
            for name in sorted(os.listdir(d)):
                with open(os.path.join(d, name)) as f:
                    raw = f.read()
                for doc in yaml.safe_load_all(raw):
                    if isinstance(doc, dict) and doc.get("kind") == "ConfigMap":
                        texts.extend((doc.get("data") or {}).values())
                    elif isinstance(doc, dict):
                        texts.append(raw)
                        break
            return "\n---\n".join(texts)
    r = subprocess.run(["gh", "api", f"repos/{cfg['catalog_repo']}/contents/{cfg['catalog_path']}",
                        "-H", "Accept: application/vnd.github.raw"], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"FAIL gh api {cfg['catalog_repo']}: {r.stderr.strip()}")
    return r.stdout


def urls(catalog_text, include):
    import yaml
    out = {}
    for doc in yaml.safe_load_all(catalog_text):
        if not isinstance(doc, dict) or doc.get("kind") != "Component":
            continue
        md = doc.get("metadata") or {}
        title = md.get("title") or md.get("name") or "?"
        for link in md.get("links") or []:
            u = str(link.get("url", ""))
            host = urllib.parse.urlparse(u).hostname or ""
            if u.startswith("https://") and include in host and "{" not in u:
                out.setdefault(title, []).append((link.get("title") or "open", u))
    return out


def card(found):
    lines = ["[Architect] Estate URLs, from the catalogue. Pinned; this message edits itself."]
    for title in sorted(found):
        for name, u in sorted(set(found[title])):
            lines.append(f"• {title} ({name}): {u}")
    lines.append(f"{sum(len(v) for v in found.values())} links. Missing one? Its component has no https link in the catalogue.")
    return "\n".join(lines)


class TelegramError(RuntimeError):
    pass


def telegram(method, payload, transport):
    if transport and method == "editMessageText" and os.environ.get("HERMES_URLS_FAKE_DELETED"):
        raise TelegramError("Bad Request: message to edit not found")
    if transport:
        with open(transport, "a") as f:
            f.write(json.dumps({"method": method, "payload": payload}) + "\n")
        return {"message_id": 1}
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("FAIL TELEGRAM_BOT_TOKEN is not in the environment")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                 data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.load(resp)
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('description', body)}")
    return body["result"] if isinstance(body["result"], dict) else {}


def tick(cfg, text, transport):
    chat = os.environ.get(cfg["chat_env"])
    if not chat:
        sys.exit(f"FAIL {cfg['chat_env']} is not in the environment")
    digest = hashlib.sha256(text.encode()).hexdigest()
    state = {}
    if os.path.exists(STATE):
        with open(STATE) as f:
            state = json.load(f)
    if state.get("hash") == digest and state.get("message_id"):
        return None
    payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    mid = state.get("message_id")
    if mid:
        try:
            telegram("editMessageText", dict(payload, message_id=mid), transport)
        except TelegramError as e:
            # The founder deleted the pinned card: send and pin a fresh one instead of
            # failing every tick until someone removes the state file.
            print(f"edit of {mid} failed ({e}); sending a new card", file=sys.stderr)
            mid = None
    if not mid:
        mid = telegram("sendMessage", payload, transport)["message_id"]
        telegram("pinChatMessage", {"chat_id": chat, "message_id": mid, "disable_notification": True}, transport)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump({"message_id": mid, "hash": digest, "n": text.count("\n• ")}, f)
    return mid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--catalog")
    ap.add_argument("--transport")
    a = ap.parse_args()
    cfg = load_config()
    text = fetch_catalog(cfg) if not a.catalog else open(a.catalog).read()
    found = urls(text, cfg["include"])
    if not found:
        sys.exit("FAIL no https link in the catalogue matches urls.include; nothing to pin")
    body = card(found)
    if a.dry_run:
        print(body)
        return 0
    mid = tick(cfg, body, a.transport)
    if mid:
        print(f"URLS pinned msg={mid} n={sum(len(v) for v in found.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
