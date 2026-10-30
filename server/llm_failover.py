"""LLM provider failover for the live brain.

Why this exists (Oct 30, 2026): the intelligence-pass retest call
(attempt 743d1d80) produced five transcribed user turns and ZERO
assistant replies. The journal shows the resolved fast model,
gemini-3.1-flash-lite, answering every inference request with a
provider 503 ("This model is currently experiencing high demand").
The pipeline built exactly ONE GoogleLLMService from pool[0]
(model_select.resolve_model), so the second fast-pool model —
gemini-3.5-flash-lite, healthy the whole evening — was never
instantiated, and pipecat's error path (ErrorFrame, fatal=False)
ended each turn in silence. Reproduced offline the same evening
against the REAL providers: 3 turns, 3 real 503s, 0 replies; a
direct probe confirmed 3.1-flash-lite 503 / 3.5-flash-lite OK.

The fix keeps failover INSIDE the LLM service boundary. The
aggregators see a single response envelope (the base class's
_process_context still owns the start/end frames and the error if
every model fails), while _stream_response walks the model chain.
A model is abandoned only before its first streamed chunk — text
already flowing toward TTS is never taken back. Switching is
sticky: the next turn starts from the last model that answered,
so a dead primary costs one failover, not one per turn.

The chain is the fast pool in order, then the frontier pool —
dynamic from the environment at build time, never hard-pinned
(model_select's "dynamic always" rule). Availability failover to
the frontier model does not change the BrainLadder's content
escalation semantics; it only means the frontier model may also
serve ordinary turns when every fast model is down.
"""
from __future__ import annotations

import os

from loguru import logger

from model_select import parse_pool


def is_failover_worthy(exc: BaseException) -> bool:
    """True when another model plausibly answers where this one
    could not: provider 5xx (incl. 503 high-demand), rate limiting,
    connection failure, timeouts, and a pool entry the provider does
    not know (404 — pool hygiene must not kill a call). Client
    errors that indict the REQUEST (400/401/403...) are not
    failover-worthy: the next model would reject the same request,
    and an auth failure must surface, not be papered over."""
    try:
        from google.genai import errors as genai_errors
    except Exception:
        genai_errors = None
    if genai_errors is not None:
        server_error = getattr(genai_errors, "ServerError", None)
        if server_error is not None and isinstance(exc, server_error):
            return True
        connection_error = getattr(genai_errors, "APIConnectionError", None)
        if connection_error is not None and isinstance(exc, connection_error):
            return True
        client_error = getattr(genai_errors, "ClientError", None)
        if client_error is not None and isinstance(exc, client_error):
            return getattr(exc, "code", None) in (404, 408, 409, 429)
    # Transport-level failures (this SDK generation surfaces them as
    # httpx errors rather than a genai connection-error type): the
    # request may never have reached the model that was asked, so
    # another model is a fair next try.
    try:
        import httpx

        if isinstance(exc, httpx.TransportError):
            return True
    except Exception:
        pass
    if isinstance(exc, TimeoutError):
        return True
    try:
        from google.api_core.exceptions import DeadlineExceeded

        if isinstance(exc, DeadlineExceeded):
            return True
    except Exception:
        pass
    return False


def model_chain(resolved_fast: str, resolved_frontier: str | None,
                environ: dict | None = None) -> list[str]:
    """Ordered failover chain: the resolved fast model first, then
    the rest of the fast pool, then the frontier pool (each pool in
    environment order, deduplicated)."""
    environ = os.environ if environ is None else environ
    chain: list[str] = []

    def add(model: str | None) -> None:
        if model and model not in chain:
            chain.append(model)

    add(resolved_fast)
    for model in parse_pool(environ.get("GEMINI_FAST_MODELS")):
        add(model)
    for model in parse_pool(environ.get("GEMINI_FRONTIER_MODELS")):
        add(model)
    add(resolved_frontier)
    return chain


class LLMFailoverMixin:
    """Failover behavior mixed in ahead of GoogleLLMService.

    Construction is via a dynamic subclass in build_live_pipeline
    so the test-suite's provider patching (which swaps the
    GoogleLLMService name) keeps working unchanged: the mixin only
    relies on the base service's _stream_response/_settings seam,
    and a fake base that never calls _stream_response is untouched
    by it.
    """

    def __init__(self, *args, model_pool=None, **kwargs):
        super().__init__(*args, **kwargs)
        pool: list[str] = []
        for model in model_pool or []:
            if model and model not in pool:
                pool.append(model)
        current = getattr(getattr(self, "_settings", None), "model", None)
        if current:
            if current in pool:
                pool.remove(current)
            pool.insert(0, current)
        self._model_pool = pool
        self._pool_index = 0
        self.active_model = pool[0] if pool else current
        self.failover_events = 0
        self.failover_log: list[dict] = []

    def _set_model(self, model: str) -> None:
        settings = self._settings
        try:
            settings.model = model
        except Exception:
            self._settings = settings.model_copy(update={"model": model})

    async def _stream_response(self, context):
        pool = getattr(self, "_model_pool", None) or []
        if len(pool) <= 1:
            async for chunk in super()._stream_response(context):
                yield chunk
            return
        order = pool[self._pool_index:] + pool[:self._pool_index]
        for offset, candidate in enumerate(order):
            self._set_model(candidate)
            yielded = False
            try:
                async for chunk in super()._stream_response(context):
                    yielded = True
                    yield chunk
            except Exception as exc:
                if yielded or not is_failover_worthy(exc) \
                        or offset == len(order) - 1:
                    raise
                nxt = order[offset + 1]
                self.failover_events += 1
                self.failover_log.append({
                    "from": candidate, "to": nxt,
                    "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
                logger.warning(
                    "llm-failover model={} -> {} after {}: {}",
                    candidate, nxt, type(exc).__name__, str(exc)[:160])
                continue
            self._pool_index = pool.index(candidate)
            self.active_model = candidate
            return
