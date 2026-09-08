"""Exceptions raised at registration time (not call time).

A call-time refusal is never an exception — it is a structured
``GatewayResponse`` (see ``otto.gateway.core``) so a refusal is always data
the caller can render, never a stack trace the caller has to catch. Only
registration-time programmer errors raise.
"""

from __future__ import annotations


class ToolCapacityExceeded(RuntimeError):
    """Raised when registering a tool would exceed the constitution cap."""

    def __init__(self, max_tools: int) -> None:
        super().__init__(
            f"tool registry is at its cap of {max_tools} tools "
            "(constitution section 6: 'Hard cap: 12 core tools in v1')"
        )
        self.max_tools = max_tools


class DuplicateTool(RuntimeError):
    """Raised when registering a tool name that is already registered."""

    def __init__(self, name: str) -> None:
        super().__init__(f"tool {name!r} is already registered")
        self.name = name


class SchemaViolation(RuntimeError):
    """Raised by ``ToolSpec`` construction when its own schema is not a
    valid JSON Schema document — a build defect, not a call-time refusal."""
