# LATEST checkpoint — 2026-09-05 (both Otto bots must answer as themselves)

## RESUME HERE

Founder record: ~/.claude/docs/founder/2026-09-05T1904Z-document-e37607fd.md

Done: idp #1854 (bulk lane to gemini; numun_bot answers) and idp #1860 (Ottototbot binding row,
token, webhook; reconciler logs `bot=alerts registration_ok=1`; seed `INSERT 0 2`) are merged and
rolled (pods otto-gateway-77d7846cd5-*). Both bots' updates reach the gateway.

Open, in hermes-v2 (worktree scratchpad/wt-hermes, branch fix/otto-reply-as-matched-bot):
1. otto/ingress/worker.py answers with `store.find_by_tenant(channel, tenant_id)`; both bots are
   tenant `estate`, so every reply leaves through numun_bot's token and Ottototbot never answers
   in its own chat. Fix: the envelope carries the matched binding's external_id from
   otto/ingress/gateway.py and the worker answers with that row's outbound_secret_ref.
2. otto/router/contract.py refuses `claims[i].confidence` not in ("high","med","low"); gemini
   returns "medium"/casing/numbers, founder saw "provider output refused claims[1].confidence".
   Founder asked: normalise synonyms, case and whitespace before validation.
3. After the hermes-agent image lands in idp (crew#267 image update PR), quote a worker.answered
   line for a message to Ottototbot and the reply arriving from Ottototbot.
Still open: worker naks without backoff; "unverified" marker is by design (P1) until a
Verification Plane verdict exists.

## Founder principle (2026-09-05, verbatim-ish, chat): "that is basically the model we follow for the
enterprise product shaping across platform so founder is both whole estate superadmin and also an
enterprise customer 0." Two hats, one platform: Ottototbot is the superadmin's estate bot, numun_bot is
customer zero's Otto; each must work exactly as a paying tenant's would (LAW 54). Next shaping step,
not started: give customer zero its own tenant row instead of sharing tenant `estate` with the
superadmin bot.

## RESUME HERE (2026-09-06 04:55Z) — door gets hands: steps 1 and 2
Founder reports Otto "unable to do basic things", "no github access". build_registry offers the model only `note`.
PR #88 (step 1 bridge) updated to main, checks re-running, merge when green (no --auto, guard). PR #89 (step 2 loop)
CONFLICTING: worktree scratchpad/wt-otto-step2, rebase onto main after #88 lands, resolve otto/boot/pipeline.py,
run otto/tests, push, merge. idp step 5 on branch feat/otto-door-step5.
