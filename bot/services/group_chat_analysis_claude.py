"""Anthropic Claude client for group-chat ticket analysis (structured tool output)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from bot.services.group_chat_analysis_prompts import (
    CLASSIFICATION_TOOL_NAME,
    CLASSIFICATION_TOOL_SCHEMA,
    SEGMENTATION_TOOL_NAME,
    SEGMENTATION_TOOL_SCHEMA,
    TICKET_CATEGORIES,
    build_classification_system,
    SEGMENTATION_SYSTEM,
    SEGMENTATION_USER_TEMPLATE,
    CLASSIFICATION_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5"
_MAX_TOKENS = 8192


def get_anthropic_model() -> str:
    return (os.getenv("ANTHROPIC_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _require_api_key() -> str:
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return key


def _extract_tool_input(response: Any, tool_name: str) -> dict[str, Any]:
    for block in getattr(response, "content", None) or ():
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
            raw = getattr(block, "input", None)
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str):
                return json.loads(raw)
    raise ValueError(f"Claude response missing tool_use block for {tool_name}")


async def _messages_create(
    *,
    system: str,
    user: str,
    tool_name: str,
    tool_schema: dict,
) -> dict[str, Any]:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=_require_api_key())
    model = get_anthropic_model()
    response = await client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        temperature=0,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[
            {
                "name": tool_name,
                "description": f"Record the {tool_name} result",
                "input_schema": tool_schema,
            }
        ],
        tool_choice={"type": "tool", "name": tool_name},
    )
    return _extract_tool_input(response, tool_name)


def _validate_segmentation(payload: dict[str, Any]) -> dict[str, Any]:
    tickets = payload.get("tickets")
    if not isinstance(tickets, list):
        raise ValueError("segmentation.tickets must be a list")
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(tickets):
        if not isinstance(raw, dict):
            raise ValueError(f"ticket[{i}] must be an object")
        message_ids = raw.get("message_ids")
        if not isinstance(message_ids, list) or not message_ids:
            raise ValueError(f"ticket[{i}].message_ids must be a non-empty list")
        ids = [int(x) for x in message_ids]
        out.append(
            {
                "ticket_index": int(raw.get("ticket_index", i)),
                "start_msg_id": int(raw.get("start_msg_id", ids[0])),
                "end_msg_id": int(raw.get("end_msg_id", ids[-1])),
                "message_ids": ids,
                "brief_summary": str(raw.get("brief_summary") or "").strip(),
            }
        )
    return {"tickets": out}


def _validate_classification(payload: dict[str, Any]) -> dict[str, Any]:
    category = str(payload.get("category") or "").strip()
    if category not in TICKET_CATEGORIES:
        raise ValueError(f"invalid category: {category!r}")
    events_raw = payload.get("events") or {}
    if not isinstance(events_raw, dict):
        raise ValueError("events must be an object")
    events = {
        "customer_first_message": events_raw.get("customer_first_message"),
        "admin_first_response": events_raw.get("admin_first_response"),
        "resolution": events_raw.get("resolution"),
        "escalation": events_raw.get("escalation"),
    }
    for key, val in list(events.items()):
        if val is not None:
            events[key] = str(val)
    summary = str(payload.get("summary") or "").strip()
    return {"category": category, "events": events, "summary": summary}


async def segment_messages(
    *,
    chat_name: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    user = SEGMENTATION_USER_TEMPLATE.format(
        chat_name=chat_name,
        messages_json=json.dumps(messages, ensure_ascii=False),
    )
    raw = await _messages_create(
        system=SEGMENTATION_SYSTEM,
        user=user,
        tool_name=SEGMENTATION_TOOL_NAME,
        tool_schema=SEGMENTATION_TOOL_SCHEMA,
    )
    return _validate_segmentation(raw)


async def classify_ticket(
    *,
    chat_name: str,
    messages: list[dict[str, Any]],
    admin_names: list[str],
    bot_names: list[str],
) -> dict[str, Any]:
    system = build_classification_system(
        admin_names=admin_names,
        bot_names=bot_names,
    )
    user = CLASSIFICATION_USER_TEMPLATE.format(
        chat_name=chat_name,
        messages_json=json.dumps(messages, ensure_ascii=False),
    )
    raw = await _messages_create(
        system=system,
        user=user,
        tool_name=CLASSIFICATION_TOOL_NAME,
        tool_schema=CLASSIFICATION_TOOL_SCHEMA,
    )
    return _validate_classification(raw)
