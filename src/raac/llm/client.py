"""OpenAI-compatible, non-streaming Chat Completions client for 9router."""

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from raac.common.errors import RunGuardError
from raac.common.tracing import JSONLTrace

from .config import LLMConfig
from .protocol import normalize_message
from .usage import UsageTracker, logged_usage


class LLMError(RuntimeError):
    """Safe error without response bodies, auth headers or provider credentials."""


def has_dsml_tool_call(content):
    text = (content or "").replace("\uff5c", "|").lower()
    return "invoke" in text and any(marker in text for marker in ("<|dsml|", "<||dsml||", "<dsml"))


class CompletionFormatError(LLMError):
    """A received completion needs correction; only public content crosses to lifecycle."""

    def __init__(self, code, content, call_id):
        super().__init__(code)
        self.code, self.content, self.call_id = code, content, call_id


@dataclass
class Completion:
    message: dict
    model: str
    usage: dict | None
    call_id: str | None = None


class LLMClient:
    def __init__(self, config: LLMConfig, *, log_path: Path,
                 transport=None, usage: UsageTracker | None = None, trace_content=False):
        self._seen_context = {}
        self.upstream_errors = {}
        self._activation = {}
        self.config = config
        self.log_path = Path(log_path)
        self.usage = usage or UsageTracker()
        key = config.api_key.get_secret_value()
        self.trace = JSONLTrace(self.log_path, secrets=(key,))
        self.trace_content = trace_content
        self._http = httpx.AsyncClient(
            base_url=config.base_url + "/", timeout=config.timeout_seconds,
            headers={"Authorization": f"Bearer {key}"} if key else {},
            transport=transport, trust_env=False,
        )

    async def aclose(self):
        await self._http.aclose()

    def _log(self, context, **record):
        if record.get("event") == "llm_call" and record.get("error"):
            self.upstream_errors[context["run_id"]] = {
                key: value for key, value in {**context, **record}.items()
                if key in {"agent_id", "role", "turn", "attempt", "status", "error",
                           "llm_call_id", "retry_scheduled", "retry_blocked_reason", "response_error", "finish_reason"}
            }
        self.trace.emit(context, **record)

    def trace_event(self, context, event: str, **record):
        if self.trace_content:
            self._log(context, event=event, **record)

    def _require_complete_usage(self, context):
        # A later successful attempt cannot account for a timed-out request.
        # Apply the same run-wide rule as the adapter before spending again.
        if not self.usage.snapshot(run_id=context["run_id"])["usage_complete"]:
            raise RunGuardError("usage_unavailable")

    @staticmethod
    def _visible_message(message):
        # Only protocol messages, never provider-specific hidden reasoning fields.
        if not isinstance(message, dict):
            return None
        return {key: message[key] for key in ("role", "content", "tool_calls") if key in message}

    async def list_models(self) -> list[str]:
        try:
            response = await self._http.get("models")
            response.raise_for_status()
            return sorted(item["id"] for item in response.json()["data"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise LLMError("Cannot list 9router models; check endpoint/authentication") from None

    async def probe_tools(self):
        """Two harmless protocol turns; no repository tools or hidden reasoning are logged."""
        context = {"run_id": "tool-probe-" + uuid.uuid4().hex, "task_id": "protocol-probe", "level": "probe",
                   "agent_id": "probe", "registry_version": "probe-v1", "prompt_version": "probe-v1", "provenance": {}}
        declaration = {"type": "function", "function": {"name": "echo_probe", "description": "Echo a fixed probe value.",
            "parameters": {"type": "object", "properties": {"value": {"type": "string", "enum": ["raac-probe"]}},
                           "required": ["value"], "additionalProperties": False}}}
        messages = [{"role": "user", "content": 'Call echo_probe with value "raac-probe" exactly once. After its result, reply exactly RAAC_PROBE_OK.'}]
        stage = "tool_call"
        result = {"status": "failed", "reason": "invalid_tool_call"}
        try:
            first = await self.complete(messages, context=context, tools=[declaration])
            if has_dsml_tool_call(first.message.get("content")):
                result["reason"] = "invalid_tool_call_format"
            else:
                calls = first.message.get("tool_calls")
                if (isinstance(calls, list) and len(calls) == 1 and isinstance(calls[0], dict)
                        and calls[0].get("type") == "function" and isinstance(calls[0].get("id"), str)
                        and calls[0]["id"] and isinstance(calls[0].get("function"), dict)
                        and calls[0]["function"].get("name") == "echo_probe"
                        and json.loads(calls[0]["function"].get("arguments", "null")) == {"value": "raac-probe"}):
                    stage = "tool_result"
                    messages.extend([self._visible_message(first.message),
                                     {"role": "tool", "tool_call_id": calls[0]["id"], "content": "raac-probe"}])
                    second = await self.complete(messages, context=context)
                    if has_dsml_tool_call(second.message.get("content")):
                        result["reason"] = "invalid_tool_call_format"
                    elif not second.message.get("tool_calls") and (second.message.get("content") or "").strip() == "RAAC_PROBE_OK":
                        result = {"status": "passed", "reason": None}
                    else:
                        result["reason"] = "invalid_tool_result_response"
        except CompletionFormatError as exc:
            result["reason"] = exc.code
        except RunGuardError as exc:
            result["reason"] = exc.reason
        except LLMError:
            result["reason"] = "api_error"
        except (ValueError, TypeError):
            result["reason"] = "invalid_tool_arguments"
        report = {**result, "stage": stage, "model_settings": self.config.resolve_model().model_dump(),
                  "usage": self.usage.snapshot(run_id=context["run_id"])}
        self._log(context, event="tool_protocol_probe", **report)
        return report

    async def complete(self, messages: list[dict], *, context: dict,
                       tools: list[dict] | None = None, allow_empty: bool = False,
                       model_ref: str = "default") -> Completion:
        required = {"run_id", "task_id", "level", "agent_id", "registry_version",
                    "prompt_version", "provenance"}
        if not required.issubset(context):
            raise ValueError("LLM call context is missing run/agent/version provenance")
        try:
            settings = self.config.resolve_model(model_ref)
        except ValueError as exc:
            raise LLMError(str(exc)) from None
        activation = context.get("agent_call_id", context.get("agent_id"))
        if self._activation.get(context["run_id"]) != activation:
            self.upstream_errors.pop(context["run_id"], None)
            self._activation[context["run_id"]] = activation
        self._require_complete_usage(context)
        context = {**context, "llm_call_id": uuid.uuid4().hex,
                   "model_ref": model_ref, "model_settings": settings.model_dump()}
        metrics = context.pop("context_metrics", None)
        if metrics is not None:
            # Track exact repeated message/schema representations within this run only.
            seen = self._seen_context.setdefault(context["run_id"], set())
            repeated_bytes = 0
            current = set()
            from raac.harness.layer2_core.context_metrics import encoded
            for item in [*messages, *(tools or [])]:
                raw = encoded(item).encode("utf-8")
                fingerprint = hashlib.sha256(raw).digest()
                if fingerprint in seen:
                    repeated_bytes += len(raw)
                current.add(fingerprint)
            seen.update(current)
            self._log(context, event="context_metrics", **metrics,
                      repeated_representation_bytes=repeated_bytes,
                      repetition_scope="prior requests in this client session and run; exact messages/schemas")
        payload = {
            **settings.model_dump(), "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        self.trace_event(context, "llm_request", request=payload)
        for attempt in range(self.config.max_retries + 1):
            self._require_complete_usage(context)
            self.usage.record_attempt(context)
            started = time.perf_counter()
            response = None
            self.trace_event(context, "llm_attempt_started", attempt=attempt + 1)
            data = None
            message = None
            try:
                response = await self._http.post("chat/completions", json=payload,
                                                 **({"timeout": None} if context.get("unlimited_timeout") else {}))
                status = response.status_code
                if response.is_error:
                    raise httpx.HTTPStatusError("API request failed", request=response.request,
                                               response=response)
            except asyncio.CancelledError:
                self.usage.add(context, None, count_call=False)
                self._log(context, event="llm_call", attempt=attempt + 1,
                          requested_model=settings.model, status=None, usage=None,
                          latency_ms=round((time.perf_counter() - started) * 1000, 3),
                          error="Cancelled")
                raise
            except httpx.HTTPError as exc:
                status = response.status_code if response is not None else None
                if status is None or status in (408, 504, 524):
                    self.usage.add(context, None, count_call=False)
                usage_complete = self.usage.snapshot(run_id=context["run_id"])["usage_complete"]
                retryable = status is None or status in (408, 429) or status >= 500
                retry_scheduled = usage_complete and retryable and attempt < self.config.max_retries
                self._log(context, event="llm_call", attempt=attempt + 1,
                          requested_model=settings.model, status=status,
                          latency_ms=round((time.perf_counter() - started) * 1000, 3),
                          usage=None, error=type(exc).__name__, retry_scheduled=retry_scheduled,
                          retry_blocked_reason=None if usage_complete else "usage_unavailable")
                self._require_complete_usage(context)
                if retry_scheduled:
                    await asyncio.sleep(self.config.backoff_seconds * 2 ** attempt)
                    continue
                raise LLMError(f"9router call failed (HTTP {status or 'connection'})") from None
            finish_reason, response_error = None, None
            try:
                data = response.json()
                message = data["choices"][0]["message"]
                finish_reason = data["choices"][0].get("finish_reason")
                if not isinstance(message, dict):
                    raise TypeError("Missing assistant message")
                if message.get("content") is not None and not isinstance(message["content"], str):
                    raise ValueError("Unsupported assistant content")
                explicit_noop = allow_empty and message.get("content") == "" and finish_reason == "stop"
                if finish_reason == "length":
                    response_error = "truncated_completion"
                elif not message.get("content") and not message.get("tool_calls") and not explicit_noop:
                    response_error = "empty_completion"
            except (ValueError, KeyError, IndexError, TypeError):
                response_error = "invalid_response"
            usage = data.get("usage") if isinstance(data, dict) else None
            self.usage.add(context, usage, count_call=False)
            if response_error:
                self._log(context, event="llm_call", attempt=attempt + 1,
                          requested_model=settings.model, status=response.status_code,
                          latency_ms=round((time.perf_counter() - started) * 1000, 3),
                          usage=logged_usage(usage), error="InvalidResponse",
                          response_error=response_error, finish_reason=finish_reason)
                self.trace_event(context, "llm_response", attempt=attempt + 1,
                                 message=self._visible_message(message), error="InvalidResponse",
                                 response_error=response_error, finish_reason=finish_reason)
                if response_error in {"empty_completion", "truncated_completion"}:
                    raise CompletionFormatError(response_error, message.get("content") or "", context["llm_call_id"])
                raise LLMError("9router returned an invalid completion") from None
            try:
                message, protocol = normalize_message(message, tools, context["llm_call_id"])
            except ValueError:
                self._log(context, event="llm_call", attempt=attempt + 1,
                          status=response.status_code, usage=logged_usage(usage),
                          error="InvalidResponse", response_error="invalid_tool_call_format",
                          finish_reason=finish_reason)
                self.trace_event(context, "llm_response", attempt=attempt + 1,
                                 message=self._visible_message(message), error="InvalidResponse",
                                 response_error="invalid_tool_call_format", finish_reason=finish_reason)
                raise CompletionFormatError("invalid_tool_call_format", message.get("content") or "", context["llm_call_id"]) from None
            if protocol == "dsml":
                self._log(context, event="tool_protocol_normalized", protocol=protocol)
            self._log(context, event="llm_call", attempt=attempt + 1,
                      requested_model=settings.model, served_model=data.get("model"),
                      status=response.status_code,
                      latency_ms=round((time.perf_counter() - started) * 1000, 3),
                      usage=logged_usage(usage))
            self.trace_event(context, "llm_response", attempt=attempt + 1,
                             message=self._visible_message(message),
                             finish_reason=data["choices"][0].get("finish_reason"),
                             served_model=data.get("model"))
            return Completion(message, data.get("model", settings.model), usage, context["llm_call_id"])
        raise LLMError("Retry limit reached")
