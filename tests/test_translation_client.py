import asyncio
import json

import httpx
import pytest

from backend.ai import AIRequestError, TranslationClient


@pytest.mark.parametrize("status,retryable", [(429,True), (503,True), (401,False), (403,False), (404,False)])
def test_provider_errors_are_classified_without_echoing_credentials(status, retryable):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, headers={"Retry-After":"7"}, text="private-key"))) as transport:
            client = TranslationClient("https://api.example.test", "private-key", "model", client=transport)
            with pytest.raises(AIRequestError) as failure:
                await client.translate("hello")
            assert failure.value.retryable is retryable
            assert failure.value.retry_after == 7
            assert "private-key" not in str(failure.value)
    asyncio.run(run())


def test_truncated_stream_never_publishes_a_partial_translation():
    payload = 'data: ' + json.dumps({"type":"response.output_text.delta", "delta":'{"translated":"你好","topic":"问候"}'}) + '\n\n'

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"Content-Type":"text/event-stream"}, text=payload))) as transport:
            client = TranslationClient("https://api.example.test", "private-key", "model", client=transport)
            with pytest.raises(AIRequestError, match="提前中断"):
                await client.translate("hello")
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_both_compatible_json_endpoints_are_supported(mode):
    translated = json.dumps({"translated":"你好", "topic":"问候"})
    response = {"output":[{"content":[{"type":"output_text", "text":translated}]}]} if mode == "responses" else {"choices":[{"message":{"content":translated}}]}

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))) as transport:
            client = TranslationClient("https://api.example.test/v1", "private-key", "model", api_mode=mode, client=transport)
            assert (await client.translate("hello"))["translated"] == "你好"
    asyncio.run(run())
