"""``python -m otto.worker`` — the answering lane as its own process.

The answering loop itself lives in ``otto.ingress.worker`` and is not
duplicated here: this package is an entry point, not a second
implementation. It exists because the estate runs two shapes of the same
lane and they must be the same code.

* Inside the gateway pod, ``otto.ingress.__main__`` starts the loop in a
  thread beside the socket, because it already holds the binding table,
  the secret resolver and the bus.
* As its own deployment, this module, for the pod that used to be a
  second Telegram door. Collapsing that door left a pod with a model
  lane, an instrumented process and nothing to listen to; pointing it at
  the bus makes it the second half of the one door rather than a rival
  to it, and lets the answering side scale separately from the socket.

Both paths build the same ``Worker`` against the same durable, so a task
goes to exactly one of them however many are running.
"""

from otto.worker.runner import main

__all__ = ["main"]
