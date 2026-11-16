# Сборка Recipe из фактов STT и OCR через OpenAI-совместимый LLM.
# Ответ LLM проверяется моделью Recipe 1.0.0; при ошибке схемы модель получает
# конкретное нарушение и повторяет ответ ограниченное число раз. Источник
# (source) ставит сервер, LLM его не выбирает; неизвестные числа запрещены.
# Транзиторные сетевые сбои повторяются отдельно (LLM_NETWORK_RETRIES).
from __future__ import annotations

import json
import re
from time import sleep

import httpx
from fropti_core_contracts import Recipe
from pydantic import ValidationError

from src.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_RETRIES,
    LLM_MODEL,
    LLM_NETWORK_RETRIES,
    LLM_RETRY_DELAY_SECONDS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
    OUTPUT_LANGUAGE,
)
from src.errors import LLMInvalidResponseError, LLMUnavailableError, NoUsableContentError
from src.models import Source


def _language_rule() -> str:
    # Читается при каждом вызове: значение можно менять в тестах без перезапуска.
    language = (OUTPUT_LANGUAGE or "").strip()
    if language.lower() in ("", "auto"):
        return "Язык ответа — язык исходного видео (речи и надписей с экрана)"
    return f"Язык ответа — {language}, даже если входные данные на другом языке"


def _system_prompt() -> str:
    return f"""Ты извлекаешь рецепт из расшифровки речи и текста с экрана кулинарного видео.
Ответь только JSON-объектом по схеме:
{{
  "schema_version": "1.0.0",
  "title": "название блюда",
  "servings": 1,
  "ingredients": [
    {{"name": "Яйца", "quantity": {{"value": 2, "unit": "pcs"}}, "note": null}}
  ],
  "steps": [
    {{"instruction": "Взбейте яйца.", "duration_seconds": null}}
  ],
  "total_time_minutes": null
}}
Правила:
- Поле source не включай: его добавит сервер.
- Единицы только из списка: g, kg, ml, l, pcs, tsp, tbsp, pinch ("г" -> "g", "шт." -> "pcs", "ч. л." -> "tsp", "ст. л." -> "tbsp", "щепотка" -> "pinch").
- Счётные уточнения («зубчик», «ломтик», «пачка», «банка»; в англоязычных видео — clove, slice, can, stick) единицей не считаются: количество — в value с "pcs", само слово — в note ("3 зубчика чеснока" -> quantity {{"value": 3, "unit": "pcs"}}, note "зубчика"; "1 stick of butter" -> quantity {{"value": 1, "unit": "pcs"}}, note "stick").
- Единиц "cup", "oz" и "lb" нет в контракте — пересчитай количество по фиксированным коэффициентам: 1 cup = 240 ml, 1 oz = 28.35 g, 1 lb = 453.6 g; результат округли до целых, а исходную формулировку сохрани в note ("1 cup flour" -> quantity {{"value": 240, "unit": "ml"}}, note "1 cup flour").
- Остальные единицы вне списка («стакан», «лист», «пучок», "fl oz") не выдумывай и не переводи: quantity: null, а формулировка количества целиком — в note ("1 стакан муки").
- Неизвестное количество -> quantity: null; уточнения вроде "по вкусу" -> note.
- Неизвестные порции -> servings: null; неизвестное время -> total_time_minutes: null.
- Не придумывай числа, ингредиенты и шаги, которых нет во входных данных.
- Включи ВСЕ ингредиенты из обоих текстов, включая мелкие (соль, специи, разрыхлитель): перед ответом сверь, что каждый ингредиент из входных данных попал в список.
- Обязательные поля не опускай, даже когда значение null.
- {_language_rule()}. Порядок шагов — как в видео."""


# Готовый промпт при дефолтных настройках — для чтения и отладки извне.
SYSTEM_PROMPT = _system_prompt()


def _user_message(transcript: str, screen_text: str) -> str:
    return (
        f"ТЕКСТ РЕЧИ:\n{transcript.strip() or '(речь не распознана)'}\n\n"
        f"ТЕКСТ С ЭКРАНА:\n{screen_text.strip() or '(текст с экрана не распознан)'}"
    )


def _extract_json(text: str):
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("в ответе нет JSON-объекта")
    return json.loads(candidate[start : end + 1])


# Статусы, которые обычно означают временный сбой провайдера или шлюза:
# 408 — timeout шлюза, 429 — rate limit, 502/503/504 — прокси/перегрузка.
# Остальные не-200 (400/401/403/404…) постоянные: повтор бесполезен.
_TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 502, 503, 504})


def _call_llm(messages: list[dict]) -> str:
    if not (LLM_API_KEY and LLM_BASE_URL and LLM_MODEL):
        raise LLMUnavailableError(
            "LLM не настроена: задайте LLM_API_KEY, LLM_BASE_URL и LLM_MODEL в .env"
        )
    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    # Повторы LLM_MAX_RETRIES покрывают только невалидный JSON/нарушение схемы;
    # сетевые сбои и статусы шлюза повторяются здесь с паузой между попытками.
    for attempt in range(LLM_NETWORK_RETRIES + 1):
        if attempt:
            sleep(LLM_RETRY_DELAY_SECONDS)
        try:
            with httpx.Client(timeout=LLM_TIMEOUT_SECONDS) as client:
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                    json={"model": LLM_MODEL, "messages": messages, "temperature": LLM_TEMPERATURE},
                )
        except httpx.TransportError as exc:  # обрыв соединения, DNS, таймауты httpx
            if attempt == LLM_NETWORK_RETRIES:
                raise LLMUnavailableError(f"LLM-сервис недоступен: {exc}") from exc
            continue
        if response.status_code == 200:
            break
        if response.status_code in _TRANSIENT_HTTP_STATUSES and attempt < LLM_NETWORK_RETRIES:
            continue  # повтор после паузы в начале следующей итерации
        raise LLMUnavailableError(f"LLM-сервис вернул HTTP {response.status_code}")
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMUnavailableError("LLM-сервис вернул некорректный ответ") from exc
    if not isinstance(content, str):
        # content: null (refusal части провайдеров) — невалидный ответ:
        # пустая строка не пройдёт проверку JSON/схемы — повтор и
        # 502 llm_invalid_response после исчерпания попыток.
        return ""
    return content


def fuse_recipe(transcript: str, screen_text: str, source: Source) -> Recipe:
    if not transcript.strip() and not screen_text.strip():
        raise NoUsableContentError(
            "В видео нет ни распознанной речи, ни читаемого текста с экрана; "
            "достоверный рецепт извлечь нельзя"
        )
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _user_message(transcript, screen_text)},
    ]
    last_error = "неизвестная ошибка"
    for _attempt in range(LLM_MAX_RETRIES + 1):
        raw = _call_llm(messages)
        data = None
        try:
            data = _extract_json(raw)
        except ValueError as exc:
            last_error = str(exc)
        if data is not None:
            data.pop("source", None)  # источник ставит сервер, а не LLM
            try:
                return Recipe.model_validate({**data, "source": source})
            except ValidationError as exc:
                last_error = f"нарушение схемы: {exc.errors(include_url=False)[:3]}"
        # Без своего предыдущего ответа в сообщениях повтор при температуре 0
        # повторит ту же ошибку. Пустой ответ assistant-сообщение не получает:
        # часть провайдеров отклоняет пустой content ассистента с 400 —
        # повтор ушёл бы как llm_unavailable вместо llm_invalid_response.
        if raw.strip():
            messages.append({"role": "assistant", "content": raw})
        messages.append({
            "role": "user",
            "content": f"Предыдущий ответ не прошёл проверку: {last_error}. "
                      "Исправь и верни только исправленный JSON.",
        })
    raise LLMInvalidResponseError(
        f"LLM не вернула валидный Recipe после {LLM_MAX_RETRIES + 1} попыток: {last_error}"
    )
