# Layer 1 Lifecycle — approved design
Approved by user in chat on 2026-10-23. Extract the existing single-agent loop into Layer 1, keeping DemoAgentRunner callable compatibility. LangGraph controls Reason, Act, Observe and finalization; collaboration owns topology and inter-agent handoff.
Six lifecycle states, validated transitions, guard/cancellation and manual approval with benchmark auto-approval. SQLite JSON checkpoints preserve invocation, messages, usage, runtime and operation receipts. Resume only the same verified workspace/container; never replay uncertain in-flight operations. Restore runtime, memory binding and evidence, not messages alone. No credential persistence; changed redacted execution payloads block resume.
No L3 changes, Docker reconstruction, context-policy changes, real-model calls or automatic commits/pushes.

## Follow-up approved 2026-10-23

The user explicitly requested removal of the DemoAgentRunner compatibility facade. This supersedes the earlier facade requirement: delete harness/demo_agent.py and migrate CLI, benchmark and tests directly to raac.harness.layer1_lifecycle.AgentRunner. Keep collaboration Protocol naming and runtime behavior unchanged. Historical entries above describe the initial implementation.
