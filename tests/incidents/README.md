# Incident regression tests

One file per incident, named after its row in `estate-evals/incidents.jsonl`.
The test is the failing case from the incident. It runs on every pull request,
so the same break cannot land twice.

A post-mortem that does not add a file here has not closed its class.
