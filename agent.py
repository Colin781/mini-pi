import argparse
import json
import os
import shlex
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from openai import APIConnectionError, APIStatusError, OpenAI
from dotenv import load_dotenv

from mini_pi.context import ContextLimits, ContextManager
from mini_pi.checkpoints import CheckpointManager
from mini_pi.protection import DEFAULT_PROTECTED_PATHS
from mini_pi.reporting import AgentRunReport
from mini_pi.tool_definitions import TOOL_DEFINITIONS
from mini_pi.tools import ToolError, ToolExecutor
from mini_pi.tracing import JsonlTraceWriter, NullTraceWriter

from mini_pi.verification import (
    VerificationError,
    VerificationResult,
    display_command,
    normalize_verification_command,
    run_verification,
)
from mini_pi.workspace_state import (
    changed_files,
    disallowed_changes,
    matching_changes,
    normalize_rule,
    snapshot_workspace,
)

SYSTEM_PROMPT = """
你是一个运行在本地代码仓库中的 Coding Agent。

你的目标是：
1. 理解用户任务。
2. 定位相关代码。
3. 运行测试复现问题。
4. 进行最小范围修改。
5. 再次运行测试。
6. 向用户说明修改内容和测试结果。

工作规则：
- 开始时先阅读自动提供的 Repository summary，不要盲目遍历整个仓库。
- 定位 Python 函数或类时优先调用 find_symbol。
- 分析调用方和导入关系时调用 find_references。
- 只有符号搜索不足时再使用 search_text 或 list_files。
- 修改文件前先调用 read_file 阅读相关代码。
- 大文件只读取相关行范围，不要重复读取整个文件。
- 在修改前尽量先运行测试，确认问题存在。
- 修改代码时优先调用 apply_patch，提交包含上下文的 unified diff。
- 补丁冲突后重新读取相关行，不要绕过冲突或修改受保护文件。
- create_checkpoint 和 restore_checkpoint 可用于手动保存或恢复工作区。
- 一次只做必要的最小修改。
- 修改完成后必须运行测试。
- 完成前调用 git_diff 查看最终变更。
- 如果工具执行失败，分析失败原因并尝试修正。
- 如果测试仍然失败，不要假装任务已经完成。
- 最终回答必须包含：问题原因、修改内容、测试结果。
""".strip()

def parse_command_text(value: str) -> tuple[str, ...]:
    try:
        parts = tuple(shlex.split(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error

    if not parts:
        raise argparse.ArgumentTypeError(
            "验收命令不能为空"
        )

    return parts

def serialize_assistant_message(message: Any) -> dict[str, Any]:
    """把 SDK 返回的 assistant 消息转换成可再次发送的字典。"""
    result: dict[str, Any] = {
        "role": "assistant",
        "content": message.content,
    }

    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in message.tool_calls
        ]

    return result


def print_tool_result(result: str, preview_length: int = 1000) -> None:
    """只在终端展示工具结果的一部分，避免输出过长。"""
    if len(result) <= preview_length:
        print(result)
        return

    print(result[:preview_length])
    print(f"... 终端预览已截断，完整结果长度为{len(result)}个字符")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="基于 DeepSeek 的最小 Coding Agent"
    )

    parser.add_argument(
        "task",
        help="交给 Agent 的编程任务"
    )

    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="Agent 可以访问的项目目录",
    )

    parser.add_argument(
        "--model",
        default=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
        help="DeepSeek 模型名称，默认使用 deepseek-flash",
    )

    parser.add_argument(
        "--max-steps",
        type=int,
        default=15,
        help="最大 Agent 循环次数，默认 15",
    )

    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=30_000,
        help="单轮代码上下文字符预算，默认 30000",
    )

    parser.add_argument(
        "--max-file-chars",
        type=int,
        default=10_000,
        help="单个文件读取字符上限，默认 10000",
    )

    parser.add_argument(
        "--max-repairs",
        type=int,
        default=2,
        help=(
            "自动验收失败后的最大修复次数，"
            "默认 2"
        ),
    )

    parser.add_argument(
        "--verify-command",
        type=parse_command_text,
        help=(
            "自动验收命令，例如 "
            '"python -m unittest '
            'discover -s tests -v"'
        ),
    )

    parser.add_argument(
        "--command-timeout",
        type=int,
        default=60,
        help=(
            "单次自动验收的超时秒数，"
            "默认 60"
        ),
    )

    parser.add_argument(
        "--protected-path",
        action="append",
        default=[],
        help=(
            "禁止 Agent 修改的文件或目录，"
            "可重复使用"
        ),
    )

    parser.add_argument(
        "--allowed-change",
        action="append",
        default=[],
        help=(
            "允许 Agent 修改的文件或目录，"
            "可重复使用；不传表示不限制"
        ),
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="自动同意文件修改和测试命令",
    )

    parser.add_argument(
        "--report",
        type=Path,
        help="将运行指标写入指定 JSON 文件",
    )

    parser.add_argument(
        "--trace",
        type=Path,
        help="将完整运行轨迹写入 JSONL 文件",
    )

    return parser

def create_report(
    *,
    status: str,
    exit_code: int,
    args: argparse.Namespace,
    workspace: Path,
    started_at: str,
    started_monotonic: float,
    rounds: int,
    tool_calls: int,
    repair_attempts: int = 0,
    files_read: int = 0,
    unique_files_read: int = 0,
    context_chars: int = 0,
    search_calls: int = 0,
    patches_applied: int = 0,
    checkpoints_created: int = 0,
    checkpoints_restored: int = 0,
    trace_path: str | None = None,
    final_answer: str | None = None,
    error: str | None = None,
    verification: VerificationResult | None = None,
    changed: list[str] | None = None,
    protected_violations: list[str] | None = None,
    invalid_changes: list[str] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> AgentRunReport:
    return AgentRunReport(
        schema_version=4,
        status=status,
        exit_code=exit_code,
        task=args.task,
        workspace=str(workspace),
        model=args.model,
        started_at=started_at,
        elapsed_seconds=round(
            time.monotonic() - started_monotonic,
            3,
        ),
        rounds=rounds,
        tool_calls=tool_calls,
        repair_attempts=repair_attempts,
        files_read=files_read,
        unique_files_read=unique_files_read,
        context_chars=context_chars,
        search_calls=search_calls,
        patches_applied=patches_applied,
        checkpoints_created=checkpoints_created,
        checkpoints_restored=checkpoints_restored,
        trace_path=trace_path,
        final_answer=final_answer,
        error=error,
        verification=(
            verification.to_dict()
            if verification
            else None
        ),
        changed_files=changed or [],
        protected_violations=(
            protected_violations or []
        ),
        disallowed_changes=(
            invalid_changes or []
        ),
        events=events or [],
    )

def repair_prompt(
    *,
    result: VerificationResult,
    repair_attempt: int,
    max_repairs: int,
    diff: str,
) -> str:
    return (
        "自动验收失败，请继续修复。"
        "不要只解释，必须使用工具修改代码"
        "并重新测试。\n\n"
        f"修复次数："
        f"{repair_attempt}/{max_repairs}\n"
        f"验收命令："
        f"{display_command(result.command)}\n"
        f"退出码：{result.exit_code}\n\n"
        f"验收输出：\n"
        f"{result.output}\n\n"
        f"当前工作区差异：\n"
        f"{diff}"
    )

def run_agent(
    args: argparse.Namespace,
    *,
    client: Any | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> AgentRunReport:
    root = args.workspace.resolve()

    started_at = datetime.now(
        timezone.utc
    ).isoformat()

    started_monotonic = time.monotonic()

    events: list[dict[str, Any]] = []
    trace: JsonlTraceWriter | NullTraceWriter = NullTraceWriter()

    if args.trace:
        try:
            trace = JsonlTraceWriter(args.trace)
        except OSError as error:
            return create_report(
                status="configuration_error",
                exit_code=2,
                args=args,
                workspace=root,
                started_at=started_at,
                started_monotonic=started_monotonic,
                rounds=0,
                tool_calls=0,
                error=f"无法创建 JSONL 轨迹：{error}",
            )

    trace.write(
        "run_started",
        task=args.task,
        workspace=str(root),
        model=args.model,
    )

    def configuration_error(
        message: str,
    ) -> AgentRunReport:
        trace.write(
            "run_finished",
            status="configuration_error",
            exit_code=2,
            error=message,
        )
        return create_report(
            status="configuration_error",
            exit_code=2,
            args=args,
            workspace=root,
            started_at=started_at,
            started_monotonic=(
                started_monotonic
            ),
            rounds=0,
            tool_calls=0,
            error=message,
            trace_path=(
                str(trace.path)
                if trace.path
                else None
            ),
            events=events,
        )

    if not root.exists():
        return configuration_error(
            f"工作目录不存在：{root}"
        )

    if not root.is_dir():
        return configuration_error(
            f"工作目录不是文件夹：{root}"
        )

    if args.max_steps < 1:
        return configuration_error(
            "--max-steps 必须大于 0"
        )

    if args.max_repairs < 0:
        return configuration_error(
            "--max-repairs 不能小于 0"
        )

    if args.command_timeout < 1:
        return configuration_error(
            "--command-timeout 必须大于 0"
        )

    try:
        context_limits = ContextLimits(
            max_context_chars=args.max_context_chars,
            max_file_chars=args.max_file_chars,
        )
    except ValueError as error:
        return configuration_error(str(error))

    try:
        protected_rules = tuple(
            dict.fromkeys(
                normalize_rule(rule)
                for rule in (
                    *DEFAULT_PROTECTED_PATHS,
                    *args.protected_path,
                )
            )
        )

        allowed_rules = tuple(
            normalize_rule(rule)
            for rule in args.allowed_change
        )

        if args.verify_command:
            normalize_verification_command(
                args.verify_command
            )

        baseline = snapshot_workspace(root)
    except (
        OSError,
        ValueError,
        VerificationError,
    ) as error:
        return configuration_error(
            str(error)
        )

    if client is None:
        api_key = os.environ.get(
            "DEEPSEEK_API_KEY"
        )

        if not api_key:
            return configuration_error(
                "没有找到 DEEPSEEK_API_KEY"
            )

        client = OpenAI(
            api_key=api_key,
            base_url=(
                "https://api.deepseek.com"
            ),
            timeout=90,
        )

    def confirm_action(
        description: str,
    ) -> bool:
        trace.write(
            "approval_requested",
            description=description,
            automatic=args.yes,
        )

        if args.yes:
            print(
                f"\n自动批准：{description}"
            )
            trace.write(
                "approval_resolved",
                description=description,
                approved=True,
                automatic=True,
            )
            return True

        print(
            f"\n请求执行：{description}"
        )

        approved = (
            input(
                "是否允许？输入 y 确认："
            )
            .strip()
            .lower()
            == "y"
        )

        trace.write(
            "approval_resolved",
            description=description,
            approved=approved,
            automatic=False,
        )
        return approved

    def confirm_sensitive_action(
        description: str,
    ) -> bool:
        trace.write(
            "approval_requested",
            description=description,
            automatic=False,
            sensitive=True,
        )

        if args.yes:
            print(
                "\n自动运行模式拒绝需要人工确认的操作："
                f"{description}"
            )
            trace.write(
                "approval_resolved",
                description=description,
                approved=False,
                automatic=True,
                sensitive=True,
            )
            return False

        print(f"\n高风险操作：{description}")
        approved = (
            input("是否允许？输入 y 确认：")
            .strip()
            .lower()
            == "y"
        )
        trace.write(
            "approval_resolved",
            description=description,
            approved=approved,
            automatic=False,
            sensitive=True,
        )
        return approved

    checkpoint_manager = CheckpointManager(root)

    executor = ToolExecutor(
        root=root,
        confirm=confirm_action,
        confirm_sensitive=confirm_sensitive_action,
        protected_paths=protected_rules,
        checkpoint_manager=checkpoint_manager,
        trace=trace,
    )

    executor.create_checkpoint("initial_attempt")
    active_checkpoint_id = (
        checkpoint_manager.latest().checkpoint_id
    )

    context = ContextManager(
        task=args.task,
        repository_files=[
            item.path
            for item in executor.repository.files
        ],
        limits=context_limits,
    )

    repository_summary = executor.repository.summary(
        max_chars=min(
            10_000,
            args.max_context_chars // 2,
        )
    )
    context.register_summary(repository_summary)
    trace.write(
        "repository_summarized",
        summary=repository_summary,
        context_chars=len(repository_summary),
    )

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    for history_message in conversation_history or []:
        role = history_message.get("role")
        content = history_message.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        messages.append(
            {
                "role": role,
                "content": content,
            }
        )

    messages.append(
        {
            "role": "user",
            "content": (
                f"{args.task}\n\n"
                f"{repository_summary}"
            ),
        }
    )

    tool_call_count = 0
    repair_attempts = 0
    rounds = 0
    final_answer: str | None = None

    last_verification: (
        VerificationResult | None
    ) = None

    print(f"工作目录：{root}")
    print(f"模型：{args.model}")
    print(f"任务：{args.task}")

    if args.verify_command:
        print(
            "自动验收："
            f"{display_command(args.verify_command)}"
        )

    def workspace_result(
    ) -> tuple[
        list[str],
        list[str],
        list[str],
    ]:
        current = snapshot_workspace(root)

        changes = changed_files(
            baseline,
            current,
        )

        protected = matching_changes(
            changes,
            protected_rules,
        )

        invalid = disallowed_changes(
            changes,
            allowed_rules,
        )

        return changes, protected, invalid

    def restore_active_checkpoint(reason: str) -> str | None:
        try:
            executor.restore_checkpoint(active_checkpoint_id)
        except ToolError as error:
            trace.write(
                "checkpoint_restore_failed",
                checkpoint_id=active_checkpoint_id,
                reason=reason,
                error=str(error),
            )
            return str(error)

        trace.write(
            "attempt_rolled_back",
            checkpoint_id=active_checkpoint_id,
            reason=reason,
        )
        return None

    def finish(
        *,
        status: str,
        exit_code: int,
        error: str | None = None,
    ) -> AgentRunReport:
        (
            changes,
            protected,
            invalid,
        ) = workspace_result()

        report = create_report(
            status=status,
            exit_code=exit_code,
            args=args,
            workspace=root,
            started_at=started_at,
            started_monotonic=(
                started_monotonic
            ),
            rounds=rounds,
            tool_calls=tool_call_count,
            repair_attempts=repair_attempts,
            files_read=context.metrics.files_read,
            unique_files_read=len(
                context.metrics.unique_files
            ),
            context_chars=context.metrics.context_chars,
            search_calls=context.metrics.search_calls,
            patches_applied=executor.stats.patches_applied,
            checkpoints_created=executor.stats.checkpoints_created,
            checkpoints_restored=executor.stats.checkpoints_restored,
            trace_path=(
                str(trace.path)
                if trace.path
                else None
            ),
            final_answer=final_answer,
            error=error,
            verification=last_verification,
            changed=changes,
            protected_violations=protected,
            invalid_changes=invalid,
            events=events,
        )

        trace.write(
            "run_finished",
            status=status,
            exit_code=exit_code,
            rounds=rounds,
            tool_calls=tool_call_count,
            repair_attempts=repair_attempts,
            patches_applied=executor.stats.patches_applied,
            checkpoints_created=executor.stats.checkpoints_created,
            checkpoints_restored=executor.stats.checkpoints_restored,
            error=error,
        )
        checkpoint_manager.cleanup()
        return report

    for step in range(
        1,
        args.max_steps + 1,
    ):
        rounds = step

        print(
            f"\n========== 第 {step} 轮 =========="
        )

        model_started = time.monotonic()
        trace.write(
            "model_started",
            round=step,
        )

        try:
            response = (
                client
                .chat
                .completions
                .create(
                    model=args.model,
                    messages=context.prepare_messages(messages),
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                    extra_body={
                        "thinking": {
                            "type": "disabled"
                        }
                    },
                )
            )
        except KeyboardInterrupt:
            trace.write(
                "model_failed",
                round=step,
                duration_ms=round(
                    (time.monotonic() - model_started) * 1000
                ),
                error="用户中止了模型请求",
            )
            restore_active_checkpoint("user_cancelled")
            return finish(
                status="cancelled",
                exit_code=130,
                error="用户中止了操作",
            )
        except APIConnectionError as error:
            trace.write(
                "model_failed",
                round=step,
                duration_ms=round(
                    (time.monotonic() - model_started) * 1000
                ),
                error=str(error),
            )
            restore_active_checkpoint("api_connection_error")
            return finish(
                status="api_error",
                exit_code=1,
                error=(
                    "连接 DeepSeek API 失败："
                    f"{error}"
                ),
            )
        except APIStatusError as error:
            trace.write(
                "model_failed",
                round=step,
                duration_ms=round(
                    (time.monotonic() - model_started) * 1000
                ),
                error=str(error),
            )
            restore_active_checkpoint("api_status_error")
            return finish(
                status="api_error",
                exit_code=1,
                error=(
                    "DeepSeek API 返回 HTTP "
                    f"{error.status_code}："
                    f"{error.message}"
                ),
            )

        message = (
            response.choices[0].message
        )

        tool_calls = (
            message.tool_calls or []
        )

        trace.write(
            "model_finished",
            round=step,
            duration_ms=round(
                (time.monotonic() - model_started) * 1000
            ),
            tool_call_count=len(tool_calls),
            message=serialize_assistant_message(message),
        )

        messages.append(
            serialize_assistant_message(
                message
            )
        )

        events.append(
            {
                "type": "model_response",
                "round": step,
                "tool_call_count": len(
                    tool_calls
                ),
            }
        )

        if tool_calls:
            for call in tool_calls:
                tool_call_count += 1

                tool_name = (
                    call.function.name
                )

                print(
                    f"\n调用工具：{tool_name}"
                )

                tool_started = time.monotonic()
                trace.write(
                    "tool_started",
                    round=step,
                    tool=tool_name,
                )

                try:
                    arguments = json.loads(
                        call.function.arguments
                    )

                    if not isinstance(
                        arguments,
                        dict,
                    ):
                        raise ToolError(
                            "工具参数必须是 JSON 对象"
                        )

                    trace.write(
                        "tool_arguments",
                        round=step,
                        tool=tool_name,
                        arguments=arguments,
                    )

                    result = executor.execute(
                        name=tool_name,
                        arguments=arguments,
                    )

                    result = context.observe_tool_result(
                        name=tool_name,
                        arguments=arguments,
                        result=result,
                    )

                    event_status = (
                        "completed"
                    )
                except json.JSONDecodeError as error:
                    result = (
                        "工具参数不是有效 JSON："
                        f"{error}"
                    )
                    event_status = "error"
                except ToolError as error:
                    result = (
                        "工具执行失败："
                        f"{error}"
                    )
                    event_status = "error"
                except KeyboardInterrupt:
                    trace.write(
                        "tool_finished",
                        round=step,
                        tool=tool_name,
                        status="cancelled",
                        duration_ms=round(
                            (time.monotonic() - tool_started) * 1000
                        ),
                    )
                    restore_active_checkpoint("user_cancelled")
                    return finish(
                        status="cancelled",
                        exit_code=130,
                        error="用户中止了操作",
                    )

                events.append(
                    {
                        "type": "tool_call",
                        "round": step,
                        "tool": tool_name,
                        "status": event_status,
                    }
                )

                trace.write(
                    "tool_finished",
                    round=step,
                    tool=tool_name,
                    status=event_status,
                    duration_ms=round(
                        (time.monotonic() - tool_started) * 1000
                    ),
                    result_chars=len(result),
                    result=result,
                )

                print_tool_result(result)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result,
                    }
                )

            continue

        final_answer = (
            message.content
            or "模型没有返回文字"
        )

        print(
            "\n========== Agent 回答 =========="
        )
        print(final_answer)

        (
            changes,
            protected,
            invalid,
        ) = workspace_result()

        if protected or invalid:
            details: list[str] = []

            if protected:
                details.append(
                    "受保护文件被修改："
                    + ", ".join(protected)
                )

            if invalid:
                details.append(
                    "修改超出允许范围："
                    + ", ".join(invalid)
                )

            events.append(
                {
                    "type": (
                        "policy_violation"
                    ),
                    "round": step,
                }
            )

            trace.write(
                "policy_violation",
                round=step,
                protected_files=protected,
                disallowed_files=invalid,
            )
            restore_active_checkpoint("policy_violation")

            return finish(
                status="invalid_solution",
                exit_code=1,
                error="；".join(details),
            )

        if not args.verify_command:
            if getattr(args, "show_diff", True):
                print(
                    "\n========== 工作区状态 =========="
                )
                print(executor.git_diff())

            return finish(
                status="completed",
                exit_code=0,
            )

        trace.write(
            "verification_started",
            round=step,
            command=list(args.verify_command),
        )

        last_verification = (
            run_verification(
                root=root,
                command=(
                    args.verify_command
                ),
                timeout_seconds=(
                    args.command_timeout
                ),
            )
        )

        context.observe_diagnostics(
            last_verification.output
        )

        events.append(
            {
                "type": "verification",
                "round": step,
                "exit_code": (
                    last_verification
                    .exit_code
                ),
                "passed": (
                    last_verification
                    .passed
                ),
                "elapsed_seconds": (
                    last_verification
                    .elapsed_seconds
                ),
            }
        )

        trace.write(
            (
                "verification_passed"
                if last_verification.passed
                else "verification_failed"
            ),
            round=step,
            exit_code=last_verification.exit_code,
            timed_out=last_verification.timed_out,
            duration_ms=round(
                last_verification.elapsed_seconds * 1000
            ),
            output=last_verification.output,
        )

        print(
            "\n========== 自动验收 =========="
        )

        print(
            "退出码："
            f"{last_verification.exit_code}"
        )

        print_tool_result(
            last_verification.output
        )

        if last_verification.passed:
            if getattr(args, "show_diff", True):
                print(
                    "\n========== 工作区状态 =========="
                )
                print(executor.git_diff())

            return finish(
                status="success",
                exit_code=0,
            )

        if (
            repair_attempts
            >= args.max_repairs
        ):
            restore_active_checkpoint(
                "maximum_repair_attempts_reached"
            )
            return finish(
                status=(
                    "verification_failed"
                ),
                exit_code=1,
                error=(
                    "自动验收失败，"
                    "已达到最大修复次数"
                ),
            )

        repair_attempts += 1

        executor.create_checkpoint(
            f"repair_{repair_attempts}_start"
        )
        active_checkpoint_id = (
            checkpoint_manager.latest().checkpoint_id
        )

        events.append(
            {
                "type": "repair_started",
                "round": step,
                "attempt": (
                    repair_attempts
                ),
            }
        )

        trace.write(
            "repair_started",
            round=step,
            attempt=repair_attempts,
            checkpoint_id=active_checkpoint_id,
        )

        messages.append(
            {
                "role": "user",
                "content": repair_prompt(
                    result=(
                        last_verification
                    ),
                    repair_attempt=(
                        repair_attempts
                    ),
                    max_repairs=(
                        args.max_repairs
                    ),
                    diff=executor.git_diff(),
                ),
            }
        )

    (
        changes,
        protected,
        invalid,
    ) = workspace_result()

    if protected or invalid:
        trace.write(
            "policy_violation",
            round=rounds,
            protected_files=protected,
            disallowed_files=invalid,
            reason="max_steps_reached",
        )
        restore_active_checkpoint("policy_violation_at_max_steps")
        return finish(
            status="invalid_solution",
            exit_code=1,
            error=(
                "达到轮数限制时"
                "检测到非法文件修改"
            ),
        )

    if args.verify_command:
        trace.write(
            "verification_started",
            round=rounds,
            command=list(args.verify_command),
            reason="max_steps_reached",
        )

        last_verification = (
            run_verification(
                root=root,
                command=(
                    args.verify_command
                ),
                timeout_seconds=(
                    args.command_timeout
                ),
            )
        )

        context.observe_diagnostics(
            last_verification.output
        )

        events.append(
            {
                "type": "verification",
                "round": rounds,
                "exit_code": (
                    last_verification
                    .exit_code
                ),
                "passed": (
                    last_verification
                    .passed
                ),
                "elapsed_seconds": (
                    last_verification
                    .elapsed_seconds
                ),
                "reason": (
                    "max_steps_reached"
                ),
            }
        )

        trace.write(
            (
                "verification_passed"
                if last_verification.passed
                else "verification_failed"
            ),
            round=rounds,
            exit_code=last_verification.exit_code,
            timed_out=last_verification.timed_out,
            duration_ms=round(
                last_verification.elapsed_seconds * 1000
            ),
            reason="max_steps_reached",
            output=last_verification.output,
        )

        if last_verification.passed:
            return finish(
                status="success",
                exit_code=0,
            )

    restore_active_checkpoint("max_steps_reached")
    return finish(
        status="max_steps_reached",
        exit_code=2,
        error="已达到最大循环次数",
    )


def main() -> int:
    load_dotenv(
        Path(__file__).with_name(".env")
    )

    parser = build_parser()
    args = parser.parse_args()

    if args.trace is None:
        if args.report:
            args.trace = args.report.with_suffix(
                ".trace.jsonl"
            )
        else:
            args.trace = (
                Path.home()
                / ".mini-pi"
                / "traces"
                / f"{uuid.uuid4().hex}.jsonl"
            )

    report = run_agent(args)

    if report.error:
        print(
            f"\n错误：{report.error}"
        )

    print(
        "\n========== 运行统计 =========="
    )
    print(f"状态：{report.status}")
    print(f"Agent 轮数：{report.rounds}")
    print(
        f"修复次数："
        f"{report.repair_attempts}"
    )
    print(
        f"工具调用次数："
        f"{report.tool_calls}"
    )
    print(f"读取文件次数：{report.files_read}")
    print(f"读取文件数：{report.unique_files_read}")
    print(f"上下文字符数：{report.context_chars}")
    print(f"搜索调用次数：{report.search_calls}")
    print(f"应用补丁次数：{report.patches_applied}")
    print(f"创建检查点次数：{report.checkpoints_created}")
    print(f"恢复检查点次数：{report.checkpoints_restored}")
    if report.trace_path:
        print(f"运行轨迹：{report.trace_path}")
    print(
        f"运行耗时："
        f"{report.elapsed_seconds:.3f} 秒"
    )

    if args.report:
        report.write_json(args.report)

        print(
            "报告已写入："
            f"{args.report.resolve()}"
        )

    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
