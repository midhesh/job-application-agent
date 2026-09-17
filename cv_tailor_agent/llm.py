from __future__ import annotations

import os
from typing import TypeVar

import anthropic
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

MODEL = os.environ.get("CV_TAILOR_MODEL", "claude-sonnet-5")

T = TypeVar("T", bound=BaseModel)


def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def load_style_rules(path: str = "rules/style_rules.yaml") -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def style_rules_block(rules: list[dict]) -> str:
    return "\n".join(f"- {r['rule']}" for r in rules)


def research_with_web_search(client: anthropic.Anthropic, prompt: str, max_uses: int = 4) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def call_structured(
    client: anthropic.Anthropic,
    system: str,
    user_content: str,
    tool_name: str,
    tool_description: str,
    result_model: type[T],
    retries: int = 2,
) -> T:
    schema = result_model.model_json_schema()
    tool = {"name": tool_name, "description": tool_description, "input_schema": schema}
    messages = [{"role": "user", "content": user_content}]

    last_error: Exception | None = None
    for _ in range(retries + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=messages,
        )
        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_use is None:
            last_error = RuntimeError("Model did not return a tool_use block")
            continue
        try:
            return result_model.model_validate(tool_use.input)
        except ValidationError as e:
            last_error = e
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "content": f"Validation failed: {e}. Call {tool_name} again with corrected input.",
                            "is_error": True,
                        }
                    ],
                }
            )
    raise RuntimeError(f"Failed to get valid {tool_name} after {retries + 1} attempts: {last_error}")
