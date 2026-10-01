import json
import re
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mini_pi.verification import (
    display_command,
    run_verification,
)
from mini_pi.workspace_state import (
    changed_files,
    disallowed_changes,
    matching_changes,
    normalize_rule,
    snapshot_workspace,
)


CASE_ID_PATTERN = re.compile(
    r"^[a-z0-9_-]+$"
)

MAX_CAPTURE_LENGTH = 20_000


def validate_string_list(
    value: Any,
    field_name: str,
    case_id: str,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not all(
            isinstance(item, str) and item
            for item in value
        )
    ):
        raise ValueError(
            f"{case_id} 的 {field_name} "
            "必须是字符串数组"
        )

    return tuple(value)


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    title: str
    task: str
    template: str
    test_command: tuple[str, ...]
    timeout_seconds: int
    command_timeout_seconds: int
    max_repairs: int
    max_context_chars: int
    max_file_chars: int
    protected_paths: tuple[str, ...]
    allowed_changed_files: tuple[str, ...]

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
    ) -> "EvaluationCase":
        required = {
            "id",
            "title",
            "task",
            "template",
            "test_command",
        }

        missing = required - value.keys()

        if missing:
            raise ValueError(
                "评测任务缺少字段："
                f"{sorted(missing)}"
            )

        case_id = value["id"]

        if (
            not isinstance(case_id, str)
            or not CASE_ID_PATTERN.fullmatch(
                case_id
            )
        ):
            raise ValueError(
                f"非法评测任务 ID："
                f"{case_id!r}"
            )

        test_command = validate_string_list(
            value["test_command"],
            "test_command",
            case_id,
        )

        if not test_command:
            raise ValueError(
                f"{case_id} 的 test_command "
                "不能为空"
            )

        timeout = value.get(
            "timeout_seconds",
            300,
        )

        command_timeout = value.get(
            "command_timeout_seconds",
            60,
        )

        max_repairs = value.get(
            "max_repairs",
            2,
        )

        max_context_chars = value.get(
            "max_context_chars",
            30_000,
        )

        max_file_chars = value.get(
            "max_file_chars",
            10_000,
        )

        if (
            not isinstance(timeout, int)
            or timeout < 1
        ):
            raise ValueError(
                f"{case_id} 的 timeout_seconds "
                "必须大于 0"
            )

        if (
            not isinstance(
                command_timeout,
                int,
            )
            or command_timeout < 1
        ):
            raise ValueError(
                f"{case_id} 的 "
                "command_timeout_seconds "
                "必须大于 0"
            )

        if (
            not isinstance(max_repairs, int)
            or max_repairs < 0
        ):
            raise ValueError(
                f"{case_id} 的 max_repairs "
                "不能小于 0"
            )

        if (
            not isinstance(max_context_chars, int)
            or max_context_chars < 1_000
        ):
            raise ValueError(
                f"{case_id} 的 max_context_chars "
                "不能小于 1000"
            )

        if (
            not isinstance(max_file_chars, int)
            or max_file_chars < 500
            or max_file_chars > max_context_chars
        ):
            raise ValueError(
                f"{case_id} 的 max_file_chars "
                "必须介于 500 和 max_context_chars 之间"
            )

        protected = validate_string_list(
            value.get(
                "protected_paths",
                [],
            ),
            "protected_paths",
            case_id,
        )

        allowed = validate_string_list(
            value.get(
                "allowed_changed_files",
                [],
            ),
            "allowed_changed_files",
            case_id,
        )

        for rule in (
            *protected,
            *allowed,
        ):
            normalize_rule(rule)

        return cls(
            case_id=case_id,
            title=str(value["title"]),
            task=str(value["task"]),
            template=str(value["template"]),
            test_command=test_command,
            timeout_seconds=timeout,
            command_timeout_seconds=(
                command_timeout
            ),
            max_repairs=max_repairs,
            max_context_chars=max_context_chars,
            max_file_chars=max_file_chars,
            protected_paths=protected,
            allowed_changed_files=allowed,
        )


@dataclass(slots=True)
class EvaluationRecord:
    case_id: str
    title: str
    attempt: int
    success: bool
    agent_status: str
    agent_exit_code: int
    verification_exit_code: int
    elapsed_seconds: float
    rounds: int
    tool_calls: int
    repair_attempts: int
    files_read: int
    unique_files_read: int
    context_chars: int
    search_calls: int
    patches_applied: int
    checkpoints_created: int
    checkpoints_restored: int
    trace_path: str | None
    workspace: str
    final_answer: str | None
    agent_error: str | None
    changed_files: list[str]
    protected_violations: list[str]
    disallowed_changes: list[str]
    agent_stdout_tail: str
    verification_output: str

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(self)


def clip_tail(
    text: str,
    limit: int = MAX_CAPTURE_LENGTH,
) -> str:
    if len(text) <= limit:
        return text

    return (
        "... 前面的输出已截断 ...\n"
        + text[-limit:]
    )


def summarize(
    records: list[EvaluationRecord],
) -> dict[str, Any]:
    total = len(records)

    passed = sum(
        record.success
        for record in records
    )

    if total == 0:
        return {
            "total_runs": 0,
            "passed_runs": 0,
            "failed_runs": 0,
            "success_rate": 0.0,
            "average_elapsed_seconds": 0.0,
            "average_rounds": 0.0,
            "average_tool_calls": 0.0,
            "average_repair_attempts": 0.0,
            "average_files_read": 0.0,
            "average_unique_files_read": 0.0,
            "average_context_chars": 0.0,
            "average_search_calls": 0.0,
            "average_patches_applied": 0.0,
            "average_checkpoints_restored": 0.0,
        }

    return {
        "total_runs": total,
        "passed_runs": passed,
        "failed_runs": total - passed,
        "success_rate": round(
            passed / total * 100,
            2,
        ),
        "average_elapsed_seconds": round(
            statistics.mean(
                record.elapsed_seconds
                for record in records
            ),
            3,
        ),
        "average_rounds": round(
            statistics.mean(
                record.rounds
                for record in records
            ),
            2,
        ),
        "average_tool_calls": round(
            statistics.mean(
                record.tool_calls
                for record in records
            ),
            2,
        ),
        "average_repair_attempts": round(
            statistics.mean(
                record.repair_attempts
                for record in records
            ),
            2,
        ),
        "average_files_read": round(
            statistics.mean(record.files_read for record in records),
            2,
        ),
        "average_unique_files_read": round(
            statistics.mean(record.unique_files_read for record in records),
            2,
        ),
        "average_context_chars": round(
            statistics.mean(record.context_chars for record in records),
            2,
        ),
        "average_search_calls": round(
            statistics.mean(record.search_calls for record in records),
            2,
        ),
        "average_patches_applied": round(
            statistics.mean(record.patches_applied for record in records),
            2,
        ),
        "average_checkpoints_restored": round(
            statistics.mean(
                record.checkpoints_restored
                for record in records
            ),
            2,
        ),
    }


class EvaluationRunner:
    def __init__(
        self,
        project_root: Path,
    ) -> None:
        self.project_root = (
            project_root.resolve()
        )

        self.evaluation_root = (
            self.project_root
            / "evaluation"
        )

        self.manifest_path = (
            self.evaluation_root
            / "cases.json"
        )

        self.workspaces_root = (
            self.evaluation_root
            / "workspaces"
        )

        self.results_root = (
            self.evaluation_root
            / "results"
        )

        self.agent_path = (
            self.project_root
            / "agent.py"
        )

    def load_cases(
        self,
    ) -> list[EvaluationCase]:
        raw = json.loads(
            self.manifest_path.read_text(
                encoding="utf-8"
            )
        )

        if raw.get("version") != 4:
            raise ValueError(
                "不支持的 cases.json 版本，"
                "当前版本需要 version=4"
            )

        values = raw.get("cases")

        if not isinstance(values, list):
            raise ValueError(
                "cases.json 中的 cases "
                "必须是数组"
            )

        cases = [
            EvaluationCase.from_dict(value)
            for value in values
        ]

        identifiers = [
            case.case_id
            for case in cases
        ]

        if (
            len(identifiers)
            != len(set(identifiers))
        ):
            raise ValueError(
                "评测任务 ID 不能重复"
            )

        for case in cases:
            self.resolve_template(case)

        return cases

    def resolve_template(
        self,
        case: EvaluationCase,
    ) -> Path:
        template = (
            self.project_root
            / case.template
        ).resolve()

        try:
            template.relative_to(
                self.evaluation_root
            )
        except ValueError as error:
            raise ValueError(
                f"{case.case_id} 的模板"
                "超出 evaluation 目录"
            ) from error

        if not template.is_dir():
            raise ValueError(
                f"模板目录不存在：{template}"
            )

        return template

    def select_cases(
        self,
        requested_ids: list[str] | None,
    ) -> list[EvaluationCase]:
        cases = self.load_cases()

        if not requested_ids:
            return cases

        by_id = {
            case.case_id: case
            for case in cases
        }

        unknown = [
            case_id
            for case_id in requested_ids
            if case_id not in by_id
        ]

        if unknown:
            raise ValueError(
                "未知评测任务："
                f"{', '.join(unknown)}"
            )

        return [
            by_id[case_id]
            for case_id in requested_ids
        ]

    def workspace_path(
        self,
        case: EvaluationCase,
        attempt: int,
    ) -> Path:
        return (
            self.workspaces_root
            / case.case_id
            / f"run_{attempt}"
        )

    def remove_workspace(
        self,
        workspace: Path,
    ) -> None:
        resolved = workspace.resolve()

        try:
            relative = (
                resolved.relative_to(
                    self.workspaces_root.resolve()
                )
            )
        except ValueError as error:
            raise ValueError(
                "拒绝删除评测工作区以外的目录"
            ) from error

        if not relative.parts:
            raise ValueError(
                "拒绝删除 workspaces 根目录"
            )

        if resolved.exists():
            shutil.rmtree(resolved)

    def prepare_workspace(
        self,
        case: EvaluationCase,
        attempt: int,
        *,
        initialize_git: bool = True,
    ) -> Path:
        if attempt < 1:
            raise ValueError(
                "attempt 必须从 1 开始"
            )

        template = self.resolve_template(
            case
        )

        workspace = self.workspace_path(
            case,
            attempt,
        )

        self.remove_workspace(workspace)

        workspace.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copytree(
            template,
            workspace,
        )

        if initialize_git:
            self.initialize_git_repository(
                workspace
            )

        return workspace

    def initialize_git_repository(
        self,
        workspace: Path,
    ) -> None:
        subprocess.run(
            [
                "git",
                "init",
                "-q",
            ],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )

        files = sorted(
            str(
                path.relative_to(workspace)
            )
            for path in workspace.rglob("*")
            if (
                path.is_file()
                and ".git" not in path.parts
            )
        )

        if files:
            subprocess.run(
                [
                    "git",
                    "add",
                    "--",
                    *files,
                ],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )

        subprocess.run(
            [
                "git",
                "-c",
                (
                    "user.name="
                    "Mini Pi Evaluation"
                ),
                "-c",
                (
                    "user.email="
                    "mini-pi@example.invalid"
                ),
                "commit",
                "-q",
                "-m",
                "evaluation baseline",
            ],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )

    def create_result_directory(
        self,
    ) -> Path:
        timestamp = datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%SZ"
        )

        suffix = uuid.uuid4().hex[:8]

        result_directory = (
            self.results_root
            / f"{timestamp}_{suffix}"
        )

        result_directory.mkdir(
            parents=True,
            exist_ok=False,
        )

        return result_directory

    def build_agent_command(
        self,
        *,
        case: EvaluationCase,
        workspace: Path,
        report_path: Path,
    ) -> list[str]:
        command = [
            sys.executable,
            str(self.agent_path),
            "--yes",
            "--workspace",
            str(workspace),
            "--report",
            str(report_path),
            "--verify-command",
            display_command(
                case.test_command
            ),
            "--command-timeout",
            str(
                case.command_timeout_seconds
            ),
            "--max-repairs",
            str(case.max_repairs),
            "--max-context-chars",
            str(case.max_context_chars),
            "--max-file-chars",
            str(case.max_file_chars),
        ]

        for path in case.protected_paths:
            command.extend(
                [
                    "--protected-path",
                    path,
                ]
            )

        for path in (
            case.allowed_changed_files
        ):
            command.extend(
                [
                    "--allowed-change",
                    path,
                ]
            )

        command.append(case.task)

        return command

    def run_case(
        self,
        case: EvaluationCase,
        attempt: int,
        result_directory: Path,
    ) -> EvaluationRecord:
        workspace = self.prepare_workspace(
            case,
            attempt,
        )

        baseline = snapshot_workspace(
            workspace
        )

        report_path = (
            result_directory
            / (
                f"{case.case_id}"
                f"_run_{attempt}_agent.json"
            )
        )

        agent_command = (
            self.build_agent_command(
                case=case,
                workspace=workspace,
                report_path=report_path,
            )
        )

        started = time.monotonic()

        try:
            process = subprocess.run(
                agent_command,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=case.timeout_seconds,
            )

            agent_exit_code = (
                process.returncode
            )

            agent_output = (
                process.stdout
                + process.stderr
            )
        except subprocess.TimeoutExpired as error:
            agent_exit_code = 124

            stdout = error.stdout or ""
            stderr = error.stderr or ""

            if isinstance(stdout, bytes):
                stdout = stdout.decode(
                    errors="replace"
                )

            if isinstance(stderr, bytes):
                stderr = stderr.decode(
                    errors="replace"
                )

            agent_output = (
                stdout
                + stderr
                + "\nAgent 评测运行超时"
            )

        wall_elapsed = round(
            time.monotonic() - started,
            3,
        )

        if report_path.exists():
            agent_report = json.loads(
                report_path.read_text(
                    encoding="utf-8"
                )
            )
        else:
            agent_report = {
                "status": "missing_report",
                "elapsed_seconds": (
                    wall_elapsed
                ),
                "rounds": 0,
                "tool_calls": 0,
                "repair_attempts": 0,
                "files_read": 0,
                "unique_files_read": 0,
                "context_chars": 0,
                "search_calls": 0,
                "patches_applied": 0,
                "checkpoints_created": 0,
                "checkpoints_restored": 0,
                "trace_path": None,
                "final_answer": None,
                "error": (
                    "Agent 未生成运行报告"
                ),
            }

        current = snapshot_workspace(
            workspace
        )

        changes = changed_files(
            baseline,
            current,
        )

        protected = matching_changes(
            changes,
            case.protected_paths,
        )

        invalid = disallowed_changes(
            changes,
            case.allowed_changed_files,
        )

        verification = run_verification(
            root=workspace,
            command=case.test_command,
            timeout_seconds=(
                case.command_timeout_seconds
            ),
        )

        success = (
            verification.passed
            and not protected
            and not invalid
        )

        return EvaluationRecord(
            case_id=case.case_id,
            title=case.title,
            attempt=attempt,
            success=success,
            agent_status=str(
                agent_report.get(
                    "status",
                    "unknown",
                )
            ),
            agent_exit_code=agent_exit_code,
            verification_exit_code=(
                verification.exit_code
            ),
            elapsed_seconds=float(
                agent_report.get(
                    "elapsed_seconds",
                    wall_elapsed,
                )
            ),
            rounds=int(
                agent_report.get(
                    "rounds",
                    0,
                )
            ),
            tool_calls=int(
                agent_report.get(
                    "tool_calls",
                    0,
                )
            ),
            repair_attempts=int(
                agent_report.get(
                    "repair_attempts",
                    0,
                )
            ),
            files_read=int(agent_report.get("files_read", 0)),
            unique_files_read=int(
                agent_report.get("unique_files_read", 0)
            ),
            context_chars=int(agent_report.get("context_chars", 0)),
            search_calls=int(agent_report.get("search_calls", 0)),
            patches_applied=int(
                agent_report.get("patches_applied", 0)
            ),
            checkpoints_created=int(
                agent_report.get("checkpoints_created", 0)
            ),
            checkpoints_restored=int(
                agent_report.get("checkpoints_restored", 0)
            ),
            trace_path=agent_report.get("trace_path"),
            workspace=str(
                workspace.relative_to(
                    self.project_root
                )
            ),
            final_answer=agent_report.get(
                "final_answer"
            ),
            agent_error=agent_report.get(
                "error"
            ),
            changed_files=changes,
            protected_violations=protected,
            disallowed_changes=invalid,
            agent_stdout_tail=clip_tail(
                agent_output
            ),
            verification_output=clip_tail(
                verification.output
            ),
        )

    def write_reports(
        self,
        records: list[EvaluationRecord],
        result_directory: Path,
    ) -> tuple[Path, Path]:
        summary = summarize(records)

        json_path = (
            result_directory
            / "summary.json"
        )

        json_path.write_text(
            json.dumps(
                {
                    "schema_version": 4,
                    "generated_at": (
                        datetime.now(
                            timezone.utc
                        ).isoformat()
                    ),
                    "summary": summary,
                    "records": [
                        record.to_dict()
                        for record in records
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        markdown_path = (
            result_directory
            / "summary.md"
        )

        lines = [
            "# Mini Pi Evaluation Report",
            "",
            (
                "- 总运行次数："
                f"{summary['total_runs']}"
            ),
            (
                "- 成功次数："
                f"{summary['passed_runs']}"
            ),
            (
                "- 失败次数："
                f"{summary['failed_runs']}"
            ),
            (
                "- 成功率："
                f"{summary['success_rate']}%"
            ),
            (
                "- 平均耗时："
                f"{summary['average_elapsed_seconds']} 秒"
            ),
            (
                "- 平均 Agent 轮数："
                f"{summary['average_rounds']}"
            ),
            (
                "- 平均修复次数："
                f"{summary['average_repair_attempts']}"
            ),
            (
                "- 平均工具调用次数："
                f"{summary['average_tool_calls']}"
            ),
            (
                "- 平均读取文件数："
                f"{summary['average_unique_files_read']}"
            ),
            (
                "- 平均上下文字符数："
                f"{summary['average_context_chars']}"
            ),
            (
                "- 平均搜索次数："
                f"{summary['average_search_calls']}"
            ),
            (
                "- 平均补丁次数："
                f"{summary['average_patches_applied']}"
            ),
            (
                "- 平均回滚次数："
                f"{summary['average_checkpoints_restored']}"
            ),
            "",
            (
                "| 任务 | 次数 | 成功 | 耗时 "
                "| 轮数 | 修复 | 工具调用 | 文件 | 上下文 | 搜索 |"
            ),
            (
                "|---|---:|:---:|---:|---:|"
                "---:|---:|---:|---:|---:|"
            ),
        ]

        for record in records:
            state = (
                "PASS"
                if record.success
                else "FAIL"
            )

            lines.append(
                f"| {record.case_id} "
                f"| {record.attempt} "
                f"| {state} "
                f"| {record.elapsed_seconds:.3f}s "
                f"| {record.rounds} "
                f"| {record.repair_attempts} "
                f"| {record.tool_calls} "
                f"| {record.unique_files_read} "
                f"| {record.context_chars} "
                f"| {record.search_calls} |"
            )

        markdown_path.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

        return json_path, markdown_path
