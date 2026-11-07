`9router-0.5.112-codex.sse` is synthetic, not a captured model response.
It was generated offline with the installed 9router 0.5.112 webpack modules:

- `2380.js`, export `4845.v`: Codex Responses → Chat Completions mapper.
- `8623.js`, exports `9024.RW` and `9024.v8`: repeat tool IDs and serialize SSE.

Inputs are a synthetic `response.created`, `response.output_item.added` for
`read_file`, two argument deltas, and `response.completed` with usage 10/5.
Translator registration was stubbed to avoid loading executors; the mapper,
ID normalization and serializer were run unchanged. Frame IDs and creation
timestamps were replaced with fixed fixture values before serialization.
No HTTP or model request was made. This covers inline terminal usage,
repeated tool IDs and the serializer's null no-op frame. The normal streaming
tests separately cover the standard empty-choices usage trailer.

`9router-0.5.112-codex-transform.sse` was produced by the full stream transform
`8895.js`, export `71857.i5('openai', 'openai-responses', 'codex', ...)`, using
a synthetic upstream stream that includes `[DONE]`. Its output omits `[DONE]`:
the transform consumes that sentinel, emits the terminal Chat chunk with usage,
and closes. This reproduces the stricter parser's `incomplete_stream` failure.
Registry dispatch was limited to the real `4845.v` mapper for this format pair;
telemetry, logging and usage persistence were stubbed to prevent side effects.
The transform and frame parsing/serialization code ran unchanged. The fixture
retains the terminal usage and clean EOF, with fixed IDs/timestamps and no model
call. Both fixtures are independently replayed through the client in tests.
