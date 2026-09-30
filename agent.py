import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from openai import APIConnectionError, APIStatusError, OpenAI
from dotenv import load_dotenv

from mini_pi.reporting import AgentRunReport
from mini_pi.tool_definitions import TOOL_DEFINITIONS
from mini_pi.tools import ToolError, ToolExecutor

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

def create_report(*, status: str, exit_code: int, task: str, workspace: Path, model: str, started_at: str,
                  started_monotonic: float, rounds: int, tool_calls: int, final_answer: str | None = None, error: str | None = None,
                  ) -> AgentRunReport:
    return AgentRunReport(
        schema_version=1,
        status=status,
        exit_code=exit_code,
        task=task,
        workspace=str(workspace),
        model=model,
        started_at=started_at,
        elapsed_seconds=round(
            time.monotonic() - started_monotonic,
            3,
        ),
        rounds=rounds,
        tool_calls=tool_calls,
        final_answer=final_answer,
        error=error,
    )

def run_agent(args: argparse.Namespace) -> AgentRunReport:
    root = args.workspace.resolve()

    started_at = datetime.now(timezone.utc).isoformat()
    started_monotonic = time.monotonic()

    if not root.exists():
        return create_report(
            status="configuration_error",
            exit_code=2,
            task=args.task,
            workspace=root,
            model=args.model,
            started_at=started_at,
            started_monotonic=started_monotonic,
            rounds=0,
            tool_calls=0,
            error=f"工作目录不存在：{root}",
        )

    if not root.is_dir():
        return create_report(
            status="configuration_error",
            exit_code=2,
            task=args.task,
            workspace=root,
            model=args.model,
            started_at=started_at,
            started_monotonic=started_monotonic,
            rounds=0,
            tool_calls=0,
            error=f"工作目录不是文件夹：{root}",
        )

    if args.max_steps < 1:
        return create_report(
            status="configuration_error",
            exit_code=2,
            task=args.task,
            workspace=root,
            model=args.model,
            started_at=started_at,
            started_monotonic=started_monotonic,
            rounds=0,
            tool_calls=0,
            error="--max-steps 必须大于 0",
        )

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return create_report(
            status="configuration_error",
            exit_code=2,
            task=args.task,
            workspace=root,
            model=args.model,
            started_at=started_at,
            started_monotonic=started_monotonic,
            rounds=0,
            tool_calls=0,
            error="没有找到 DEEPSEEK_API_KEY",
        )

    def confirm_action(description: str) -> bool:
        if args.yes:
            print(f"\n自动批准：{description}")
            return True

        print(f"\n请求执行：{description}")
        answer = input(
            "是否允许？输入 y 确认："
        ).strip().lower()

        return answer == "y"

    executor = ToolExecutor(
        root=root,
        confirm=confirm_action,
    )

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        timeout=90,
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

    print(f"工作目录：{root}")
    print(f"模型：{args.model}")
    print(f"任务：{args.task}")

    for step in range(1, args.max_steps + 1):
        print(f"\n========== 第 {step} 轮 ==========")

        try:
            response = client.chat.completions.create(
                model=args.model,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                extra_body={
                    "thinking": {
                        "type": "disabled",
                    }
                },
            )
        except APIConnectionError as error:
            return create_report(
                status="api_error",
                exit_code=1,
                task=args.task,
                workspace=root,
                model=args.model,
                started_at=started_at,
                started_monotonic=started_monotonic,
                rounds=step,
                tool_calls=tool_call_count,
                error=f"连接 DeepSeek API 失败：{error}",
            )
        except APIStatusError as error:
            return create_report(
                status="api_error",
                exit_code=1,
                task=args.task,
                workspace=root,
                model=args.model,
                started_at=started_at,
                started_monotonic=started_monotonic,
                rounds=step,
                tool_calls=tool_call_count,
                error=(
                    f"DeepSeek API 返回 HTTP "
                    f"{error.status_code}：{error.message}"
                ),
            )

        message = response.choices[0].message
        tool_calls = message.tool_calls or []

        messages.append(
            serialize_assistant_message(message)
        )

        if not tool_calls:
            final_answer = (
                message.content or "模型没有返回文字"
            )

            print("\n========== Agent 最终回答 ==========")
            print(final_answer)

            print("\n========== 工作区状态 ==========")
            print(executor.git_diff())

            return create_report(
                status="completed",
                exit_code=0,
                task=args.task,
                workspace=root,
                model=args.model,
                started_at=started_at,
                started_monotonic=started_monotonic,
                rounds=step,
                tool_calls=tool_call_count,
                final_answer=final_answer,
            )

        for call in tool_calls:
            tool_call_count += 1
            tool_name = call.function.name

            print(f"\n调用工具：{tool_name}")

            try:
                arguments = json.loads(
                    call.function.arguments
                )

                if not isinstance(arguments, dict):
                    raise ToolError(
                        "工具参数必须是 JSON 对象"
                    )

                result = executor.execute(
                    name=tool_name,
                    arguments=arguments,
                )
            except json.JSONDecodeError as error:
                result = (
                    f"工具参数不是有效 JSON：{error}"
                )
            except ToolError as error:
                result = f"工具执行失败：{error}"
            except KeyboardInterrupt:
                return create_report(
                    status="cancelled",
                    exit_code=130,
                    task=args.task,
                    workspace=root,
                    model=args.model,
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    rounds=step,
                    tool_calls=tool_call_count,
                    error="用户中止了操作",
                )

            print_tool_result(result)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )

    return create_report(
        status="max_steps_reached",
        exit_code=2,
        task=args.task,
        workspace=root,
        model=args.model,
        started_at=started_at,
        started_monotonic=started_monotonic,
        rounds=args.max_steps,
        tool_calls=tool_call_count,
        error="已达到最大循环次数",
    )



def main() -> int:
    # 必须在创建参数解析器前加载，才能读取 DEEPSEEK_MODEL。
    load_dotenv(Path(__file__).with_name(".env"))

    parser = build_parser()
    args = parser.parse_args()

    report = run_agent(args)

    if report.error:
        print(f"\n错误：{report.error}")

    print("\n========== 运行统计 ==========")
    print(f"状态：{report.status}")
    print(f"Agent 轮数：{report.rounds}")
    print(f"工具调用次数：{report.tool_calls}")
    print(f"运行耗时：{report.elapsed_seconds:.3f} 秒")

    if args.report:
        report.write_json(args.report)
        print(f"报告已写入：{args.report.resolve()}")

    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())