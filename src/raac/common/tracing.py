"""Append-only JSONL events with redaction applied before serialization."""

import json
import logging
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

SECRET_FIELDS = {"apikey", "authorization", "password", "passwd", "accesstoken",
                 "refreshtoken", "clientsecret", "privatekey", "secret"}


class JSONLTrace:
    def __init__(self, path: Path, *, secrets=(), subscriber=None):
        self.path = Path(path)
        self.secrets = tuple(secret for secret in secrets if secret)
        self.sequence = 0
        self.subscriber = subscriber

    def _text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
                       "[REDACTED PRIVATE KEY]", value, flags=re.DOTALL)
        value = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value, flags=re.IGNORECASE)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
        return re.sub(
            r"\b(api[_-]?key|authorization|password|passwd|access[_-]?token|refresh[_-]?token|client[_-]?secret)\b"
            r"(\s*[\"']?\s*[:=]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)",
            r"\1\2[REDACTED]", value, flags=re.IGNORECASE,
        )

    def redact(self, value):
        if isinstance(value, dict):
            return {self._text(str(key)): (
                "[REDACTED]" if re.sub(r"[^a-z]", "", str(key).lower()) in SECRET_FIELDS
                else self.redact(item)
            ) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            # Tool arguments and user messages often contain nested JSON strings.
            if value.lstrip().startswith(("{", "[")):
                try:
                    nested = json.loads(value)
                except (ValueError, RecursionError):
                    pass
                else:
                    return json.dumps(self.redact(nested), ensure_ascii=False)
            return self._text(value)
        return value

    def emit(self, context: dict, **event):
        self.sequence += 1
        record = self.redact({**context, **event,
                              "timestamp": datetime.now(UTC).isoformat(),
                              "sequence": self.sequence, "trace_version": "1"})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.subscriber is not None:
            try:
                self.subscriber(deepcopy(record))
            except Exception:  # noqa: BLE001 -- subscribers cannot affect the runtime
                # A display failure must never change execution or expose payloads.
                logging.getLogger(__name__).warning("Trace subscriber failed")
