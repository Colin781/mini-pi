import argparse
import json
import os
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from openai import APIConnectionError, APIStatusError, OpenAI
from dotenv import load_dotenv

from mini_pi.reporting import AgentRunReport
from mini_pi.tool_definitions import TOOL_DEFINITIONS
from mini_pi.tools import ToolError, ToolExecutor

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
- 不要猜测文件路径。
- 如果不知道项目结构，先调用 list_files。
- 如果需要定位函数、类或文本，调用 search_text。
- 修改文件前先调用 read_file 阅读相关代码。
- 在修改前尽量先运行测试，确认问题存在。
- 修改代码时调用 replace_text。
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
    final_answer: str | None = None,
    error: str | None = None,
    verification: VerificationResult | None = None,
    changed: list[str] | None = None,
    protected_violations: list[str] | None = None,
    invalid_changes: list[str] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> AgentRunReport:
    return AgentRunReport(
        schema_version=2,
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

def run_agent(args: argparse.Namespace, *, client: Any | None = None,) -> AgentRunReport:
    root = args.workspace.resolve()

    started_at = datetime.now(
        timezone.utc
    ).isoformat()

    started_monotonic = time.monotonic()

    events: list[dict[str, Any]] = []

    def configuration_error(
        message: str,
    ) -> AgentRunReport:
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
        protected_rules = tuple(
            normalize_rule(rule)
            for rule in args.protected_path
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
        if args.yes:
            print(
                f"\n自动批准：{description}"
            )
            return True

        print(
            f"\n请求执行：{description}"
        )

        return (
            input(
                "是否允许？输入 y 确认："
            )
            .strip()
            .lower()
            == "y"
        )

    executor = ToolExecutor(
        root=root,
        confirm=confirm_action,
    )

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": args.task,
        },
    ]

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

        return create_report(
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
            final_answer=final_answer,
            error=error,
            verification=last_verification,
            changed=changes,
            protected_violations=protected,
            invalid_changes=invalid,
            events=events,
        )

    for step in range(
        1,
        args.max_steps + 1,
    ):
        rounds = step

        print(
            f"\n========== 第 {step} 轮 =========="
        )

        try:
            response = (
                client
                .chat
                .completions
                .create(
                    model=args.model,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                    extra_body={
                        "thinking": {
                            "type": "disabled"
                        }
                    },
                )
            )
        except APIConnectionError as error:
            return finish(
                status="api_error",
                exit_code=1,
                error=(
                    "连接 DeepSeek API 失败："
                    f"{error}"
                ),
            )
        except APIStatusError as error:
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

                    result = executor.execute(
                        name=tool_name,
                        arguments=arguments,
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

            return finish(
                status="invalid_solution",
                exit_code=1,
                error="；".join(details),
            )

        if not args.verify_command:
            print(
                "\n========== 工作区状态 =========="
            )
            print(executor.git_diff())

            return finish(
                status="completed",
                exit_code=0,
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

        events.append(
            {
                "type": "repair_started",
                "round": step,
                "attempt": (
                    repair_attempts
                ),
            }
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
        return finish(
            status="invalid_solution",
            exit_code=1,
            error=(
                "达到轮数限制时"
                "检测到非法文件修改"
            ),
        )

    if args.verify_command:
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

        if last_verification.passed:
            return finish(
                status="success",
                exit_code=0,
            )

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
