"""What the flow needs from the outside world. Phases depend only on these interfaces."""

from __future__ import annotations

from typing import Protocol


class JsonModel(Protocol):
    """A language model that answers with one JSON object."""

    async def complete_json(self, *, phase: str, system: str, user: str) -> str:
        """Return the raw text of the model's reply (expected to be a JSON object)."""
        ...
