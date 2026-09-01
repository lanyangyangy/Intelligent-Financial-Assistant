# DeepSeek Replaces Doubao

## Goal

Replace Doubao with DeepSeek V4 Flash in the chat-model routing layer while
keeping Qwen as the sole embedding provider for the knowledge base.

## Architecture

`DeepSeekProvider` will use the existing OpenAI-compatible client pattern and
read `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_CHAT_MODEL` from
`Settings`. `ModelRouter` will own only Qwen and DeepSeek chat providers:
customer-service FAQ and small-talk prefer DeepSeek; investment advice, risk,
SQL, compliance, and long requests prefer Qwen. The non-preferred available
provider is the fallback for a failed request.

Qwen embeddings remain unchanged. Startup creates Qwen and DeepSeek, injects
the router into all Agents and the customer SSE route, and closes every
provider independently. An SSE that has already sent a delta ends cleanly on a
stream failure instead of appending a second model response.

## Scope

- Add DeepSeek settings and a provider.
- Remove Doubao settings, provider wiring, and router references.
- Retain deterministic routing, fallback, streaming, and Qwen embeddings.
- Add unit tests for provider configuration, routing, stream behavior, and
  independent provider shutdown.

## Non-Goals

- Change knowledge-base embedding models or existing pgvector data.
- Change agent business prompts or suitability rules.
- Make an external paid API call during unit tests.
