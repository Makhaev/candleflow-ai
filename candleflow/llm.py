import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

from candleflow.config import settings


@dataclass(frozen=True)
class Final:
    output: list[dict]


@dataclass(frozen=True)
class Restart:
    pass


class ProviderError(Exception):
    def __init__(self, retryable: bool):
        self.retryable = retryable


class Provider(Protocol):
    def stream(
        self, model: str, instructions: str, items: list[dict], tools: list[dict], files: list[str]
    ) -> AsyncIterator[str | Final]: ...

    async def upload(self, filename: str, content: bytes) -> str: ...

    async def delete(self, file_id: str) -> None: ...

    async def download(self, container_id: str, file_id: str) -> bytes: ...

    async def close(self) -> None: ...


class OpenAIProvider:
    def __init__(self):
        if not settings.openai_api_key:
            raise ProviderError(False)
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key, timeout=settings.llm_timeout, max_retries=0
        )

    async def stream(self, model, instructions, items, tools, files):
        try:
            active_tools = list(tools)
            if files:
                active_tools.append(
                    {"type": "code_interpreter", "container": {"type": "auto", "file_ids": files}}
                )
            async with self.client.responses.stream(
                model=model,
                instructions=instructions,
                input=items,
                tools=active_tools,
                store=False,
                include=["reasoning.encrypted_content", "code_interpreter_call.outputs"],
                max_output_tokens=3000,
                parallel_tool_calls=False,
            ) as events:
                async for event in events:
                    if event.type == "response.output_text.delta":
                        yield event.delta
                response = await events.get_final_response()
                if response.status != "completed":
                    raise ProviderError(False)
                yield Final([item.model_dump(exclude_none=True) for item in response.output])
        except (RateLimitError, APIConnectionError) as exc:
            raise ProviderError(True) from exc
        except APIStatusError as exc:
            raise ProviderError(exc.status_code >= 500) from exc

    async def upload(self, filename, content):
        async with asyncio.timeout(settings.llm_timeout):
            file = await self.client.files.create(file=(filename, content), purpose="user_data")
        return file.id

    async def delete(self, file_id):
        async with asyncio.timeout(5):
            await self.client.files.delete(file_id)

    async def download(self, container_id, file_id):
        async with asyncio.timeout(settings.llm_timeout):
            async with self.client.containers.files.content.with_streaming_response.retrieve(
                file_id, container_id=container_id
            ) as response:
                result = bytearray()
                async for chunk in response.iter_bytes():
                    result.extend(chunk)
                    if len(result) > 5_000_000:
                        raise ValueError("Artifact too large")
        return bytes(result)

    async def close(self):
        await self.client.close()


class FakeProvider:
    """Deterministic offline demo with the same tool dispatch and permission checks."""

    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def upload(self, filename, content):
        key = f"demo-input-{len(self.files) + 1}"
        self.files[key] = content
        return key

    async def delete(self, file_id):
        self.files.pop(file_id, None)

    async def download(self, container_id, file_id):
        return b"status,message\noffline,Code Interpreter simulation\n"

    async def close(self):
        pass

    async def stream(self, model, instructions, items, tools, files):
        last = items[-1]
        question = next(item["content"] for item in reversed(items) if item.get("role") == "user")
        normalized = question.casefold()
        output = []
        if last.get("type") == "function_call_output":
            result = json.loads(last["output"])
            if "error" in result:
                answer = result["error"]
            elif "proposal" in result:
                answer = "Подготовлено предложение. Проверьте параметры и подтвердите планирование."
            elif "preference" in result:
                answer = "Предпочтение сохранено."
            elif "dataset" in result and files:
                answer, output = self.interpreter()
            elif "total" in result:
                answer = (
                    f"{result['name']}: общий остаток {result['total']} {result['unit']}, "
                    f"свободно {result['free']} {result['unit']}."
                )
            elif "total_rub" in result:
                answer = f"Себестоимость задания: {result['total_rub']} RUB."
                if "converted" in result:
                    answer += f" В выбранной валюте: {result['converted']} {result['currency']}."
            elif "history" in result:
                batch = result["batch"]
                actors = ", ".join(dict.fromkeys(row["actor"] for row in result["history"]))
                answer = f"Поставщик партии — {batch['supplier']}. Изменения внесли: {actors}."
            elif result.get("jobs"):
                answer = "\n".join(
                    f"Задание {job['id']} · {component['name']} — {component['missing']} {component['unit']}"
                    for job in result["jobs"]
                    for component in job["components"]
                    if component["signal"] != "green"
                )
            else:
                answer = "За указанный период дефицит отсутствует."
        elif match := self.route(normalized, instructions):
            name, args = match
            yield Final(
                [
                    {
                        "type": "function_call",
                        "call_id": "demo-call",
                        "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    }
                ]
            )
            return
        elif files:
            answer, output = self.interpreter()
        else:
            answer = (
                "В доступных данных нет ответа на этот вопрос."
                if "забудь" not in normalized
                else "Я могу читать учётные данные и предлагать планирование."
            )
        for word in re.findall(r"\S+\s*", answer):
            await asyncio.sleep(0)
            yield word
        yield Final(output)

    @staticmethod
    def interpreter():
        return (
            "В деморежиме подготовлен пример файла. Для расчётов подключите OpenAI.",
            [
                {"type": "code_interpreter_call", "code": "# Демонстрация вызова", "status": "completed"},
                {
                    "type": "message",
                    "content": [
                        {
                            "annotations": [
                                {
                                    "type": "container_file_citation",
                                    "container_id": "demo",
                                    "file_id": "demo-artifact",
                                    "filename": "demo.csv",
                                }
                            ]
                        }
                    ],
                },
            ],
        )

    @staticmethod
    def route(text, instructions):
        if "забудь" in text or "отмени" in text:
            return None
        day = date.fromisoformat(re.search(r"DATE=(\d{4}-\d{2}-\d{2})", instructions)[1])
        week = day - timedelta(days=day.weekday())
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
        nums = re.findall(r"\b\d+\b", text.split("20" + str(day.year)[2:] + "-")[0])
        period = {
            "start": dates[0] if dates else str(week),
            "end": dates[1] if len(dates) > 1 else str(week + timedelta(days=6)),
        }
        codes = {
            "соев": "SOY",
            "кокос": "COCONUT",
            "пчел": "BEES",
            "фитил": "WICK",
            "этикет": "LABEL",
            "лавандов": "LAV",
            "крыш": "LID",
            "короб": "BOX",
        }
        code = next((code for word, code in codes.items() if word in text or code.lower() in text), None)
        if text.startswith("запланируй") and nums:
            recipes = json.loads(instructions.rsplit("\nRECIPES=", 1)[1])
            recipe = next((row for row in recipes if row["name"].casefold() in text), None)
            if recipe:
                target = (
                    dates[-1]
                    if dates
                    else str(day + timedelta(days=(4 - day.weekday()) % 7) if "пятниц" in text else day)
                )
                return "propose_job", {"recipe_id": recipe["id"], "quantity": nums[0], "planned_on": target}
        if text.startswith("запомни"):
            currency = next(
                (
                    value
                    for key, value in (("евро", "EUR"), ("рубл", "RUB"), ("доллар", "USD"))
                    if key in text
                ),
                None,
            )
            if currency:
                return "remember_preference", {"key": "currency", "value": currency}
        if ("график" in text or "по неделям" in text) and ("воск" in text or code):
            if not dates:
                period = {"start": str(day - timedelta(days=90)), "end": str(day)}
            return "consumption_dataset", {"material_code": code or "SOY", **period}
        if "себестоимость" in text and nums:
            return ("job_cost_dataset" if "подорож" in text else "job_cost"), {"job_id": int(nums[0])}
        if ("партии" in text or "партия" in text) and nums:
            return "batch_history", {"batch_id": int(nums[0])}
        if "дефицит" in text or "не хват" in text:
            return "shortages", period
        if ("остат" in text or "сколько" in text) and code:
            return "stock", {"material_code": code}
        return None


async def run_round(
    provider: Provider,
    models: tuple[str, ...],
    instructions: str,
    items: list[dict],
    tools: list[dict],
    files: list[str],
    on_model: Callable[[str], None],
) -> AsyncIterator[str | Final | Restart]:
    for index, model in enumerate(models):
        on_model(model)
        started = False
        stream = provider.stream(model, instructions, items, tools, files)
        try:
            async with asyncio.timeout(settings.llm_timeout):
                async for event in stream:
                    started = started or isinstance(event, str)
                    yield event
            return
        except (ProviderError, TimeoutError) as exc:
            retryable = isinstance(exc, TimeoutError) or exc.retryable
            if not retryable or index == len(models) - 1:
                raise ProviderError(False) from exc
        finally:
            await stream.aclose()
        if started:
            yield Restart()
