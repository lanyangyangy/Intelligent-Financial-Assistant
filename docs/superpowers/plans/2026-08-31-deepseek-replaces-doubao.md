# DeepSeek Replaces Doubao Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Doubao with DeepSeek V4 Flash in deterministic chat routing.

**Architecture:** Qwen remains the embedding provider and handles complex
financial reasoning. DeepSeek handles customer FAQ and small-talk. The router
falls back to the other available provider before the first response token.

**Tech Stack:** FastAPI, Pydantic Settings, OpenAI-compatible AsyncOpenAI,
pytest, Ruff.

---

### Task 1: Specify DeepSeek Routing Behavior

**Files:**
- Modify: `tests/test_model_router.py`
- Create: `tests/test_deepseek_provider.py`

- [ ] **Step 1: Write failing route tests**

```python
router = ModelRouter(qwen=qwen, deepseek=deepseek)
result = await router.chat_with_routing(messages, agent_name="customer_service")
assert result == "deepseek:ok"
```

- [ ] **Step 2: Run the focused tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests\\test_model_router.py tests\\test_deepseek_provider.py -q`

Expected: FAIL because `DeepSeekProvider` and the `deepseek` router argument do
not exist.

- [ ] **Step 3: Add unavailable/configured provider tests**

```python
provider = DeepSeekProvider(Settings(deepseek_api_key=""))
assert provider.available is False
assert asyncio.run(provider.check_config())["status"] == "skipped"
```

### Task 2: Implement the DeepSeek Provider and Settings

**Files:**
- Create: `app/infrastructure/deepseek.py`
- Modify: `app/core/settings.py`
- Delete: `app/infrastructure/doubao.py`
- Delete: `tests/test_doubao_provider.py`

- [ ] **Step 1: Add explicit DeepSeek settings**

```python
deepseek_api_key: str = Field(default="", repr=False)
deepseek_base_url: str = "https://api.deepseek.com"
deepseek_chat_model: str = "deepseek-v4-flash"
```

- [ ] **Step 2: Implement the minimal provider**

```python
class DeepSeekProvider:
    @property
    def available(self) -> bool: ...

    async def chat(self, messages, temperature=0.3, max_tokens=1024) -> str: ...

    async def chat_stream(self, messages, temperature=0.3, max_tokens=1024): ...
```

- [ ] **Step 3: Run provider tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests\\test_deepseek_provider.py -q`

Expected: PASS.

### Task 3: Replace Router and Startup Wiring

**Files:**
- Modify: `app/infrastructure/model_router.py`
- Modify: `app/main.py`
- Modify: `app/core/settings.py`
- Modify: `tests/test_model_router.py`

- [ ] **Step 1: Replace the second provider interface**

```python
router = ModelRouter(
    qwen=qwen,
    deepseek=deepseek,
    default_provider=settings.model_router_default,
)
```

- [ ] **Step 2: Make provider order deterministic**

```python
fallback = "deepseek" if preferred == "qwen" else "qwen"
```

- [ ] **Step 3: Preserve the embedding contract**

```python
async def embed(self, texts: list[str]) -> list[list[float]]:
    return await self.qwen.embed(texts)
```

- [ ] **Step 4: Run routing tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests\\test_model_router.py tests\\test_agent_model_router.py -q`

Expected: PASS.

### Task 4: Harden Streaming and Shutdown Edges

**Files:**
- Modify: `app/infrastructure/model_router.py`
- Modify: `app/api/chat_stream.py`
- Modify: `app/main.py`
- Modify: `tests/test_model_router.py`
- Modify: `tests/test_chat_stream.py`

- [ ] **Step 1: Add failing stream and shutdown tests**

```python
with pytest.raises(RuntimeError):
    async for _ in router.chat_stream_with_routing(messages):
        pass
assert qwen.close_called is True
assert deepseek.close_called is True
```

- [ ] **Step 2: Stop a partially sent SSE response cleanly**

```python
if chunks:
    yield "event: error\\ndata: {\\\"code\\\": \\\"STREAM_INTERRUPTED\\\"}\\n\\n"
    yield "data: [DONE]\\n\\n"
    return
```

- [ ] **Step 3: Isolate provider close failures**

```python
for provider in self._providers():
    try:
        await provider.close()
    except Exception:
        logger.exception("model_provider_close_failed")
```

- [ ] **Step 4: Run focused safety tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests\\test_model_router.py tests\\test_chat_stream.py -q`

Expected: PASS.

### Task 5: Verify the Complete Feature

**Files:**
- Verify: `app/infrastructure/deepseek.py`
- Verify: `app/infrastructure/model_router.py`
- Verify: `app/main.py`

- [ ] **Step 1: Run static checks**

Run: `.venv\\Scripts\\python.exe -m ruff check app tests`

Expected: `All checks passed!`

- [ ] **Step 2: Run the full suite**

Run: `.venv\\Scripts\\python.exe -m pytest -q`

Expected: all available unit tests pass; infrastructure tests may skip when
PostgreSQL or Redis is not running.
