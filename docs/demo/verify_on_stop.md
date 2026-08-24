# Demo: verify-on-stop on the Telegram surface

Real run, 2026-08-24, against the live gateway's own config and venv:

```
$ HERMES_HOME=~/dev/code/hermes-v2 .venv/bin/python -c \
    "from agent.verification_stop import verify_on_stop_enabled; \
     print('verify_on_stop now:', verify_on_stop_enabled())"

verify_on_stop now: True
```

What just happened: the agent behind Telegram is now nudged, whenever it has
edited files and is about to finish its turn, to actually run the project's
verification command before claiming anything. Before this flip the live
ledger showed the gap plainly:

```
$ sqlite3 verification_evidence.db \
    "SELECT COUNT(*) FROM verification_events"; \
  sqlite3 verification_evidence.db "SELECT COUNT(*) FROM verification_state"

0
4
```

Four workspaces edited, zero proof runs ever. The claim gate stamps a DONE:
that has no green run behind it, so without this flip every honest DONE from
an editing session would have been stamped UNVERIFIED — the nudge is what
lets the agent earn the clean label.
