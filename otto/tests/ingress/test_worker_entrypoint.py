"""``python -m otto.worker`` is an entry point, not a second worker.

The estate runs the answering lane in two shapes -- a thread inside the
gateway pod, and its own deployment for the pod that used to be a second
Telegram door -- and the whole value of that is that both shapes run the
same loop against the same durable. A copied implementation would drift,
and a drifted answering lane is two different answers to the same
customer depending on which pod won the pull.
"""

from __future__ import annotations

import otto.ingress.worker as ingress_worker
import otto.worker.runner as worker_runner


def test_the_standalone_worker_builds_the_shared_worker_class() -> None:
    assert worker_runner.Worker is ingress_worker.Worker


def test_both_shapes_pull_the_same_durable_from_the_same_subject() -> None:
    # One durable name means a task is delivered to exactly one replica
    # however many of either shape are running.
    assert ingress_worker.DURABLE == "otto-answer"
    assert ingress_worker.STREAM == "OTTO_TASKS"
