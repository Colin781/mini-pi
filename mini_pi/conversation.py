from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversationWindow:
    messages: list[dict[str, str]]
    summary: str
    summarized_message_count: int
    context_chars: int


def _summary_lines(messages: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for message in messages:
        role = "用户" if message.get("role") == "user" else "助手"
        content = " ".join(message.get("content", "").split())
        if len(content) > 500:
            content = content[:497] + "..."
        if content:
            lines.append(f"- {role}：{content}")
    return lines


def build_conversation_window(
    history: list[dict[str, str]],
    *,
    max_chars: int,
    summary: str = "",
    summarized_message_count: int = 0,
    force: bool = False,
    keep_recent_messages: int = 12,
) -> ConversationWindow:
    if max_chars < 1_000:
        raise ValueError("max_chars 不能小于 1000")
    count = min(max(0, summarized_message_count), len(history))
    remaining = history[count:]
    total = len(summary) + sum(len(item.get("content", "")) for item in remaining)

    if not force and total <= max_chars:
        selected = [
            {"role": item["role"], "content": item.get("content", "")}
            for item in remaining
        ]
        if summary:
            selected.insert(
                0,
                {
                    "role": "user",
                    "content": "此前会话摘要：\n" + summary,
                },
            )
        return ConversationWindow(selected, summary, count, total)

    keep = min(len(remaining), max(2, keep_recent_messages))
    cutoff = len(remaining) - keep
    if cutoff <= 0 and not force:
        cutoff = max(0, len(remaining) - 2)

    older = remaining[:cutoff]
    recent = remaining[cutoff:]
    lines = []
    if summary:
        lines.append(summary)
    lines.extend(_summary_lines(older))
    new_summary = "\n".join(lines)
    summary_limit = max(500, max_chars // 3)
    if len(new_summary) > summary_limit:
        separator = "\n... 中间摘要已截断 ...\n"
        side = (summary_limit - len(separator)) // 2
        new_summary = (
            new_summary[:side]
            + separator
            + new_summary[-side:]
        )

    recent_budget = max_chars - len(new_summary) - 20
    selected_reversed: list[dict[str, str]] = []
    used = 0
    for item in reversed(recent):
        content = item.get("content", "")
        if used + len(content) > recent_budget:
            break
        selected_reversed.append(
            {"role": item["role"], "content": content}
        )
        used += len(content)
    selected = list(reversed(selected_reversed))
    if selected and selected[0]["role"] == "assistant":
        selected.pop(0)
    if new_summary:
        selected.insert(
            0,
            {
                "role": "user",
                "content": "此前会话摘要：\n" + new_summary,
            },
        )

    return ConversationWindow(
        messages=selected,
        summary=new_summary,
        summarized_message_count=count + cutoff,
        context_chars=len(new_summary) + used,
    )
