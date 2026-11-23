import asyncio
import json
import socket
from collections.abc import Iterator

import httpx
import pytest
from openai import AsyncOpenAI

from candleflow import llm
from candleflow.config import settings
from candleflow.llm import Final, OpenAIProvider, ProviderError, run_round


@pytest.fixture
def anyio_backend():
    return "asyncio"


def completed_response(model: str) -> Iterator[bytes]:
    response = {
        "id": "resp_fake",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": model,
        "output": [],
    }
    for number, name in enumerate(("response.created", "response.completed")):
        event = {"type": name, "sequence_number": number, "response": response}
        yield f"event: {name}\ndata: {json.dumps(event)}\n\n".encode()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "retryable"),
    [
        (429, True),
        (500, True),
        (503, True),
        ("read_timeout", True),
        ("deadline", True),
        (400, False),
        (422, False),
    ],
)
async def test_openai_adapter_fallback_with_in_memory_transport(monkeypatch, failure, retryable):
    models = []
    deadline_cancelled = asyncio.Event()

    async def reply(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "llm.invalid"
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == "backup":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"".join(completed_response(model)),
            )
        if failure == "read_timeout":
            raise httpx.ReadTimeout("simulated provider timeout", request=request)
        if failure == "deadline":
            try:
                await asyncio.Future()
            finally:
                deadline_cancelled.set()
        assert isinstance(failure, int)
        return httpx.Response(failure, json={"error": {"message": "simulated request failure"}})

    def mock_sdk(**options):
        assert options["max_retries"] == 0
        client = httpx.AsyncClient(transport=httpx.MockTransport(reply), trust_env=False)
        return AsyncOpenAI(http_client=client, base_url="https://llm.invalid/v1", **options)

    monkeypatch.setattr(settings, "openai_api_key", "fake-transport-key")
    if failure == "deadline":
        monkeypatch.setattr(settings, "llm_timeout", 0.1)
    monkeypatch.setattr(llm, "AsyncOpenAI", mock_sdk)
    provider = OpenAIProvider()
    selected = []

    async def collect():
        return [
            event
            async for event in run_round(
                provider,
                ("main", "backup"),
                "Instructions",
                [{"role": "user", "content": "hello"}],
                [],
                [],
                selected.append,
            )
        ]

    try:
        if retryable:
            assert await collect() == [Final([], "backup")]
            assert models == selected == ["main", "backup"]
            if failure == "deadline":
                assert deadline_cancelled.is_set()
        else:
            with pytest.raises(ProviderError) as error:
                await collect()
            assert not error.value.retryable
            assert models == selected == ["main"]
    finally:
        await provider.close()


def test_external_python_dns_and_tcp_are_blocked():
    with pytest.raises(AssertionError, match="DNS"):
        socket.getaddrinfo("external.invalid", 443)
    with socket.socket() as client, pytest.raises(AssertionError, match="TCP"):
        client.connect(("192.0.2.1", 443))
