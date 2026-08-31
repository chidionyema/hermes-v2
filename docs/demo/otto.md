# Demo: the Otto spine (CP1)

CP1 is the load-bearing plumbing under every future Otto surface: one task
envelope format, one message bus, a way to reconstruct a task from nothing
but the bus's own history, and a signed list of what is actually running.
Nothing here is a chat feature yet — this is what a chat feature will sit
on top of. Two real runs, same day, same machine.

## The capability inventory is generated and signed, never hand-written

```
$ python3 -m otto.spine.cli inventory --verify-signature \
    --output otto-inventory.json

components: 11
key_id: otto-spine-inventory-2026-08
signature_valid: True
diff: +11 -0 ~0
written: otto-inventory.json
```

Run it again against the artifact from the last run and the diff proves
nothing silently drifted:

```
$ python3 -m otto.spine.cli inventory --verify-signature \
    --previous otto-inventory.json

components: 11
key_id: otto-spine-inventory-2026-08
signature_valid: True
diff: +0 -0 ~0
```

`signature_valid: True` is an Ed25519 verification against the key on
disk, not a claim in a log line — a tampered artifact fails that check.

## A task replays end to end from the bus alone, with zero gaps

The BDD suite runs every scenario against a real `nats-server -js`
subprocess and a real Postgres cluster (no fakes, no mocks):

```
$ python3 -m pytest otto/tests/cp1/step_defs/ -v

test_a_completed_task_replays_end_to_end_from_streams_alone PASSED
test_the_eval_runner_records_a_baseline_against_the_real_corpus PASSED
test_the_capability_inventory_is_generated_signed_and_diffed_never_hand_written PASSED
test_edge_case__a_task_with_zero_tool_calls_still_replays_cleanly PASSED
test_network_failure__nats_partitions_mid_publish_the_outbox_relay_recovers_it PASSED
test_bandwidth_degradation__a_slow_jetstream_consumer_does_not_lose_events PASSED

6 passed, 1 warning in 123.55s (0:02:03)
```

The two scenarios worth reading twice: kill the NATS server mid-publish
and the outbox relay proves the row stays unpublished, not lost, and comes
back the moment the partition heals; and throttle a JetStream consumer to
a fraction of normal speed and 500 published events still all arrive —
slower, never fewer.
