import argparse
from pathlib import Path

from mini_pi.evaluation import (
    EvaluationRunner,
    summarize,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mini Pi 可复现评测系统"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "list",
        help="列出所有评测任务",
    )

    reset_parser = subparsers.add_parser(
        "reset",
        help="重置评测工作区",
    )

    reset_parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="只重置指定任务，可重复使用",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="批量运行评测",
    )

    run_parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="只运行指定任务，可重复使用",
    )

    run_parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="每个任务重复运行次数",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    runner = EvaluationRunner(
        project_root=PROJECT_ROOT
    )

    try:
        cases = runner.select_cases(
            getattr(args, "case_ids", None)
        )
    except (
        OSError,
        ValueError,
    ) as error:
        parser.error(str(error))

    if args.command == "list":
        for case in cases:
            print(f"{case.case_id}: {case.title}")

        return 0

    if args.command == "reset":
        for case in cases:
            workspace = runner.prepare_workspace(
                case,
                attempt=1,
            )

            print(
                f"已重置 {case.case_id}："
                f"{workspace.relative_to(PROJECT_ROOT)}"
            )

        return 0

    if args.command == "run":
        if args.repeat < 1:
            parser.error("--repeat 必须大于 0")

        result_directory = (
            runner.create_result_directory()
        )

        records = []

        for case in cases:
            for attempt in range(
                1,
                args.repeat + 1,
            ):
                print(
                    f"\n运行评测：{case.case_id} "
                    f"({attempt}/{args.repeat})"
                )

                record = runner.run_case(
                    case=case,
                    attempt=attempt,
                    result_directory=(
                        result_directory
                    ),
                )

                records.append(record)

                state = (
                    "PASS"
                    if record.success
                    else "FAIL"
                )

                print(
                    f"{state} | "
                    f"{record.elapsed_seconds:.3f}s | "
                    f"{record.rounds} 轮 | "
                    f"{record.repair_attempts} 次修复 | "
                    f"{record.tool_calls} 次工具调用"
                )

        json_path, markdown_path = (
            runner.write_reports(
                records,
                result_directory,
            )
        )

        summary = summarize(records)

        print("\n========== 评测汇总 ==========")
        print(
            f"成功率：{summary['success_rate']}%"
        )
        print(
            "平均耗时："
            f"{summary['average_elapsed_seconds']} 秒"
        )
        print(
            "平均轮数："
            f"{summary['average_rounds']}"
        )
        print(
            "平均修复次数："
            f"{summary['average_repair_attempts']}"
        )
        print(
            "平均工具调用次数："
            f"{summary['average_tool_calls']}"
        )
        print(f"JSON 报告：{json_path}")
        print(f"Markdown 报告：{markdown_path}")

        return 0 if summary["failed_runs"] == 0 else 1

    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
