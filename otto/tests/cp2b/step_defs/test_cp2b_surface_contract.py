"""Step definitions for ``features/cp2b_surface_contract.feature``.

Verifies the CP2b channel-plane adapter contract (crew#768): the same
envelope from two surfaces, the UNVERIFIED marker surviving rendering,
loud (never silent) capability degradation, the ambient-input taint
backstop, and the no-voiceprint rule.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pytest_bdd import given, parsers, scenarios, then, when
from ulid import ULID

from otto.surface.bindings.http import HttpBinding
from otto.surface.bindings.telegram import TelegramBinding
from otto.surface.envelope import Capability, SurfaceEnvelope, TrustClass
from otto.surface.identity import validate_principal_source
from otto.surface.renderer import render_parts

scenarios("../features/cp2b_surface_contract.feature")


def _capabilities(raw: str) -> frozenset[Capability]:
    return frozenset(Capability(part.strip()) for part in raw.split(","))


def _render_for(surface: str, response: dict, capabilities: frozenset[Capability]):
    if surface == "telegram":
        return TelegramBinding(chat_id_allowlist={}).render(response, capabilities)
    if surface == "http":
        return HttpBinding(principal_allowlist={}).render(response, capabilities)
    # A surface with no concrete binding yet (e.g. a future voice adapter)
    # still goes through the same shared renderer helper every binding
    # calls — proving the contract, not just the two shipped bindings.
    from otto.surface.renderer import parts_from_response

    return render_parts(parts_from_response(response), capabilities, surface=surface)


# -- bindings ----------------------------------------------------------------


@given(
    parsers.parse(
        'a Telegram binding with chat id {chat_id:d} bound to principal "{principal}"'
    )
)
def telegram_binding(ctx: dict, chat_id: int, principal: str) -> None:
    ctx["telegram_binding"] = TelegramBinding(chat_id_allowlist={chat_id: principal})


@given(
    parsers.parse(
        'an HTTP binding with caller id "{caller_id}" bound to principal "{principal}"'
    )
)
def http_binding(ctx: dict, caller_id: str, principal: str) -> None:
    ctx["http_binding"] = HttpBinding(principal_allowlist={caller_id: principal})


@when(
    parsers.parse(
        'the Telegram binding normalizes a message "{text}" from chat {chat_id:d}'
    )
)
def telegram_normalize(ctx: dict, text: str, chat_id: int) -> None:
    native_event = {
        "message": {
            "chat": {"id": chat_id},
            "text": text,
            "date": 1_700_000_000,
        }
    }
    ctx["telegram_envelope"] = ctx["telegram_binding"].normalize(native_event)


@when(
    parsers.parse(
        'the HTTP binding normalizes a POST with content "{content}" from caller "{caller_id}"'
    )
)
def http_normalize(ctx: dict, content: str, caller_id: str) -> None:
    native_event = {
        "caller_id": caller_id,
        "content": content,
        "received_at": "2026-08-31T12:00:00+00:00",
    }
    ctx["http_envelope"] = ctx["http_binding"].normalize(native_event)


@then(
    parsers.parse(
        'both envelopes carry principal "{principal}" and trust class "{trust_class}"'
    )
)
def both_carry_principal(ctx: dict, principal: str, trust_class: str) -> None:
    for env in (ctx["telegram_envelope"], ctx["http_envelope"]):
        assert env.principal == principal
        assert env.trust_class is TrustClass(trust_class)


@then(parsers.parse('both envelopes carry the content "{content}"'))
def both_carry_content(ctx: dict, content: str) -> None:
    for env in (ctx["telegram_envelope"], ctx["http_envelope"]):
        assert env.content == content


@then("both envelopes carry a valid ULID correlation id")
def both_carry_ulid(ctx: dict) -> None:
    for env in (ctx["telegram_envelope"], ctx["http_envelope"]):
        ULID.from_str(env.correlation_id)  # raises ValueError if invalid


# -- rendering -----------------------------------------------------------


@given(parsers.parse('a response with an UNVERIFIED claim "{text}"'))
def response_with_unverified_claim(ctx: dict, text: str) -> None:
    ctx["response"] = {
        "parts": [{"kind": "text", "text": text, "claim_status": "UNVERIFIED"}]
    }


@given(parsers.parse('a response with a voice_out part "{text}"'))
def response_with_voice_out_part(ctx: dict, text: str) -> None:
    ctx["response"] = {"parts": [{"kind": "voice_out", "text": text}]}


@when(
    parsers.parse(
        'the response renders for the "{surface}" surface with capabilities "{capabilities}"'
    )
)
def render_response(ctx: dict, surface: str, capabilities: str) -> None:
    ctx["rendered"] = _render_for(surface, ctx["response"], _capabilities(capabilities))


@then(parsers.parse('the rendered text contains "{needle}"'))
def rendered_text_contains(ctx: dict, needle: str) -> None:
    assert needle in ctx["rendered"].text


@then("the rendered message is marked degraded")
def rendered_is_degraded(ctx: dict) -> None:
    assert ctx["rendered"].degraded is True
    assert len(ctx["rendered"].degradation_notes) >= 1


@then("the rendered message is not marked degraded")
def rendered_is_not_degraded(ctx: dict) -> None:
    assert ctx["rendered"].degraded is False
    assert ctx["rendered"].degradation_notes == ()


# -- ambient taint backstop ------------------------------------------------


@given(
    parsers.parse(
        'an envelope with trust class "{trust_class}" and content "{content}"'
    )
)
def make_envelope(ctx: dict, trust_class: str, content: str) -> None:
    ctx["envelope"] = SurfaceEnvelope(
        surface="test",
        principal=None,
        trust_class=TrustClass(trust_class),
        capabilities=frozenset({Capability.TEXT}),
        content=content,
        received_at=datetime.now(timezone.utc),
    )


@then("the envelope is not instruction bearing")
def envelope_not_instruction_bearing(ctx: dict) -> None:
    assert ctx["envelope"].is_instruction_bearing is False


@then("the envelope is instruction bearing")
def envelope_instruction_bearing(ctx: dict) -> None:
    assert ctx["envelope"].is_instruction_bearing is True


# -- no-voiceprint rule -----------------------------------------------------


@when(parsers.parse('a principal source "{source}" is validated'))
def validate_source(ctx: dict, source: str) -> None:
    try:
        validate_principal_source(source)
        ctx["validation_error"] = None
    except ValueError as exc:
        ctx["validation_error"] = exc


@then(parsers.parse('validation raises a ValueError mentioning "{needle}"'))
def validation_raises(ctx: dict, needle: str) -> None:
    assert ctx["validation_error"] is not None
    assert needle in str(ctx["validation_error"])


@then("validation raises no error")
def validation_raises_no_error(ctx: dict) -> None:
    assert ctx["validation_error"] is None
