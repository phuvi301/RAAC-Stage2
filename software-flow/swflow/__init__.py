"""Software flow: the steps of a software project that come before the MAS.

clarify -> specify (functional + non-functional requirements) -> architect (technology decisions)
-> plan (a task graph with dependencies). The result is a validated task graph that a MAS level can
later execute task by task. This package does not import the MAS levels; it only reuses the LLM
client of ``raac`` through ``swflow.llm_raac``.
"""

__version__ = "0.1.0"
