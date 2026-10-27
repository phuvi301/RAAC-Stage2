"""Concise, model-authored working notes; never execution or acceptance evidence."""

import json
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator

ShortText = Annotated[str, Field(min_length=1, max_length=240)]
EvidenceRef = Annotated[str, Field(min_length=1, max_length=120)]


class Conclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    statement: ShortText
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=2)


class RuledOutHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    hypothesis: ShortText
    reason: ShortText
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=2)


class WorkingMemoryNotes(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    conclusions: list[Conclusion] = Field(max_length=6)
    ruled_out: list[RuledOutHypothesis] = Field(max_length=6)
    next_steps: list[ShortText] = Field(max_length=5)

    @model_validator(mode="after")
    def concise(self):
        if len(json.dumps(self.model_dump(), ensure_ascii=False, separators=(",", ":"))) > 2000:
            raise ValueError("Working notes must fit 2000 JSON characters; retain only current conclusions, rejected hypotheses and next actions")
        return self
