from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI


@dataclass(frozen=True, slots=True)
class ChatResult:
    status: str
    answer: str | None = None
    error: str | None = None


def run_chat(
    message: str,
    *,
    model: str,
    conversation_history: list[dict[str, str]] | None = None,
    client: Any | None = None,
) -> ChatResult:
    if client is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            return ChatResult(
                status="configuration_error",
                error="没有找到 DEEPSEEK_API_KEY",
            )
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=90,
        )

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是 Mini Pi 终端中的对话助手。"
                f"宿主程序当前配置的模型标识是 {model}。"
                "回答普通问题时保持简洁、直接。"
                "当前请求没有启用代码工具，也没有扫描代码仓库；"
                "不要声称已经读取、修改或测试了文件。"
            ),
        }
    ]

    for item in conversation_history or []:
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": message})

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except KeyboardInterrupt:
        return ChatResult(status="cancelled", error="已取消当前对话")
    except APIConnectionError as error:
        return ChatResult(
            status="api_error",
            error=f"连接 DeepSeek API 失败：{error}",
        )
    except APIStatusError as error:
        return ChatResult(
            status="api_error",
            error=(
                f"DeepSeek API 返回 HTTP {error.status_code}："
                f"{error.message}"
            ),
        )

    answer = response.choices[0].message.content or "模型没有返回文字"
    return ChatResult(status="completed", answer=answer)
