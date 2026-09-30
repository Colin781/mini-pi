import json
import os
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


CASE_ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")
MAX_CAPTURE_LENGTH = 20_000


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    title: str
    task: str
    template: str
    test_command: tuple[str, ...]
    timeout_seconds: int

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
                f"评测任务缺少字段：{sorted(missing)}"
            )

        case_id = value["id"]
        if (
            not isinstance(case_id, str)
            or not CASE_ID_PATTERN.fullmatch(case_id)
        ):
            raise ValueError(
                f"非法评测任务 ID：{case_id!r}"
            )

        command = value["test_command"]
        if (
            not isinstance(command, list)
            or not command
            or not all(
                isinstance(part, str) and part
                for part in command
            )
        ):
            raise ValueError(
                f"{case_id} 的 test_command 必须是非空字符串数组"
            )

        timeout = value.get("timeout_seconds", 300)
        if not isinstance(timeout, int) or timeout < 1:
            raise ValueError(
                f"{case_id} 的 timeout_seconds 必须大于 0"
            )

        return cls(
            case_id=case_id,
            title=str(value["title"]),
            task=str(value["task"]),
            template=str(value["template"]),
            test_command=tuple(command),
            timeout_seconds=timeout,
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
    workspace: str
    final_answer: str | None
    agent_error: str | None
    agent_stdout_tail: str
    verification_output: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clip_tail(
    text: str,
    limit: int = MAX_CAPTURE_LENGTH,
) -> str:
    if len(text) <= limit:
        return text

    return "... 前面的输出已截断 ...\n" + text[-limit:]


def sanitized_environment() -> dict[str, str]:
    environment = os.environ.copy()

    sensitive_fragments = (
        "API_KEY",
        "PASSWORD",
        "SECRET",
        "TOKEN",
    )

    for variable_name in list(environment):
        upper_name = variable_name.upper()

        if any(
            fragment in upper_name
            for fragment in sensitive_fragments
        ):
            environment.pop(variable_name, None)

    return environment


def normalize_test_command(
    command: tuple[str, ...],
) -> list[str]:
    normalized = list(command)

    if Path(normalized[0]).name in {
        "python",
        "python3",
    }:
        normalized[0] = sys.executable

    return normalized


def summarize(
    records: list[EvaluationRecord],
) -> dict[str, Any]:
    total = len(records)
    passed = sum(record.success for record in records)
    failed = total - passed

    if total == 0:
        return {
            "total_runs": 0,
            "passed_runs": 0,
            "failed_runs": 0,
            "success_rate": 0.0,
            "average_elapsed_seconds": 0.0,
            "average_rounds": 0.0,
            "average_tool_calls": 0.0,
        }

    return {
        "total_runs": total,
        "passed_runs": passed,
        "failed_runs": failed,
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
    }


class EvaluationRunner:
    def __init__(
        self,
        project_root: Path,
    ) -> None:
        self.project_root = project_root.resolve()
        self.evaluation_root = (
            self.project_root / "evaluation"
        )
        self.manifest_path = (
            self.evaluation_root / "cases.json"
        )
        self.workspaces_root = (
            self.evaluation_root / "workspaces"
        )
        self.results_root = (
            self.evaluation_root / "results"
        )
        self.agent_path = (
            self.project_root / "agent.py"
        )

    def load_cases(self) -> list[EvaluationCase]:
        raw = json.loads(
            self.manifest_path.read_text(
                encoding="utf-8"
            )
        )

        if raw.get("version") != 1:
            raise ValueError(
                "不支持的 cases.json 版本"
            )

        values = raw.get("cases")
        if not isinstance(values, list):
            raise ValueError(
                "cases.json 中的 cases 必须是数组"
            )

        cases = [
            EvaluationCase.from_dict(value)
            for value in values
        ]

        identifiers = [
            case.case_id for case in cases
        ]

        if len(identifiers) != len(set(identifiers)):
            raise ValueError("评测任务 ID 不能重复")

        for case in cases:
            self.resolve_template(case)

        return cases

    def resolve_template(
        self,
        case: EvaluationCase,
    ) -> Path:
        template = (
            self.project_root / case.template
        ).resolve()

        try:
            template.relative_to(self.evaluation_root)
        except ValueError as error:
            raise ValueError(
                f"{case.case_id} 的模板超出 evaluation 目录"
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
                f"未知评测任务：{', '.join(unknown)}"
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
            relative = resolved.relative_to(
                self.workspaces_root.resolve()
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
            raise ValueError("attempt 必须从 1 开始")

        template = self.resolve_template(case)
        workspace = self.workspace_path(
            case,
            attempt,
        )

        self.remove_workspace(workspace)
        workspace.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copytree(template, workspace)

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
            ["git", "init", "-q"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )

        files = sorted(
            str(path.relative_to(workspace))
            for path in workspace.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
        )

        if files:
            subprocess.run(
                ["git", "add", "--", *files],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )

        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Mini Pi Evaluation",
                "-c",
                "user.email=mini-pi@example.invalid",
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

    def create_result_directory(self) -> Path:
        timestamp = datetime.now(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%SZ")

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

        agent_report_path = (
            result_directory
            / f"{case.case_id}_run_{attempt}_agent.json"
        )

        agent_command = [
            sys.executable,
            str(self.agent_path),
            "--yes",
            "--workspace",
            str(workspace),
            "--report",
            str(agent_report_path),
            case.task,
        ]

        started = time.monotonic()

        try:
            agent_process = subprocess.run(
                agent_command,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=case.timeout_seconds,
            )

            agent_exit_code = (
                agent_process.returncode
            )
            agent_output = (
                agent_process.stdout
                + agent_process.stderr
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

        if agent_report_path.exists():
            agent_report = json.loads(
                agent_report_path.read_text(
                    encoding="utf-8"
                )
            )
        else:
            agent_report = {
                "status": "missing_report",
                "elapsed_seconds": wall_elapsed,
                "rounds": 0,
                "tool_calls": 0,
                "final_answer": None,
                "error": "Agent 未生成运行报告",
            }

        verification_command = normalize_test_command(
            case.test_command
        )

        try:
            verification = subprocess.run(
                verification_command,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=60,
                env=sanitized_environment(),
            )

            verification_exit_code = (
                verification.returncode
            )
            verification_output = (
                verification.stdout
                + verification.stderr
            )
        except subprocess.TimeoutExpired as error:
            verification_exit_code = 124

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

            verification_output = (
                stdout
                + stderr
                + "\n验收测试运行超时"
            )

        # 成功以独立验收测试为准，而不是以模型自述为准。
        success = verification_exit_code == 0

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
                verification_exit_code
            ),
            elapsed_seconds=float(
                agent_report.get(
                    "elapsed_seconds",
                    wall_elapsed,
                )
            ),
            rounds=int(
                agent_report.get("rounds", 0)
            ),
            tool_calls=int(
                agent_report.get(
                    "tool_calls",
                    0,
                )
            ),
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
            agent_stdout_tail=clip_tail(
                agent_output
            ),
            verification_output=clip_tail(
                verification_output
            ),
        )

    def write_reports(
        self,
        records: list[EvaluationRecord],
        result_directory: Path,
    ) -> tuple[Path, Path]:
        summary = summarize(records)

        json_path = (
            result_directory / "summary.json"
        )

        json_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generated_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
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
            result_directory / "summary.md"
        )

        markdown_lines = [
            "# Mini Pi Evaluation Report",
            "",
            f"- 总运行次数：{summary['total_runs']}",
            f"- 成功次数：{summary['passed_runs']}",
            f"- 失败次数：{summary['failed_runs']}",
            f"- 成功率：{summary['success_rate']}%",
            (
                "- 平均耗时："
                f"{summary['average_elapsed_seconds']} 秒"
            ),
            (
                "- 平均 Agent 轮数："
                f"{summary['average_rounds']}"
            ),
            (
                "- 平均工具调用次数："
                f"{summary['average_tool_calls']}"
            ),
            "",
            "| 任务 | 次数 | 成功 | 耗时 | 轮数 | 工具调用 |",
            "|---|---:|:---:|---:|---:|---:|",
        ]

        for record in records:
            success_text = (
                "PASS" if record.success else "FAIL"
            )

            markdown_lines.append(
                f"| {record.case_id} "
                f"| {record.attempt} "
                f"| {success_text} "
                f"| {record.elapsed_seconds:.3f}s "
                f"| {record.rounds} "
                f"| {record.tool_calls} |"
            )

        markdown_path.write_text(
            "\n".join(markdown_lines) + "\n",
            encoding="utf-8",
        )

        return json_path, markdown_path