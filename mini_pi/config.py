from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


class ConfigError(ValueError):
    """配置文件格式或配置值无效。"""


@dataclass(slots=True)
class ModelConfig:
    name: str = "deepseek-flash"


@dataclass(slots=True)
class AgentConfig:
    max_steps: int = 15
    max_repairs: int = 2
    max_context_chars: int = 30_000
    max_file_chars: int = 10_000
    command_timeout: int = 60


@dataclass(slots=True)
class ReplSettings:
    mode: str = "auto"
    verbosity: str = "normal"
    conversation_chars: int = 12_000
    persist_sessions: bool = True


@dataclass(slots=True)
class SafetyConfig:
    protected_paths: list[str] = field(default_factory=list)
    allowed_changes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AppConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    repl: ReplSettings = field(default_factory=ReplSettings)
    safety: SafetyConfig = field(default_factory=SafetyConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if not self.model.name.strip():
            raise ConfigError("model.name 不能为空")
        if self.agent.max_steps < 1:
            raise ConfigError("agent.max_steps 必须大于 0")
        if self.agent.max_repairs < 0:
            raise ConfigError("agent.max_repairs 不能小于 0")
        if self.agent.max_context_chars < 1_000:
            raise ConfigError("agent.max_context_chars 不能小于 1000")
        if self.agent.max_file_chars < 500:
            raise ConfigError("agent.max_file_chars 不能小于 500")
        if self.agent.max_file_chars > self.agent.max_context_chars:
            raise ConfigError(
                "agent.max_file_chars 不能大于 agent.max_context_chars"
            )
        if self.agent.command_timeout < 1:
            raise ConfigError("agent.command_timeout 必须大于 0")
        if self.repl.mode not in {"auto", "chat", "agent"}:
            raise ConfigError("repl.mode 必须是 auto、chat 或 agent")
        if self.repl.verbosity not in {"quiet", "normal", "verbose"}:
            raise ConfigError(
                "repl.verbosity 必须是 quiet、normal 或 verbose"
            )
        if self.repl.conversation_chars < 1_000:
            raise ConfigError("repl.conversation_chars 不能小于 1000")


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as stream:
            value = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"无法读取配置 {path}：{error}") from error
    if not isinstance(value, dict):
        raise ConfigError(f"配置根节点必须是 TOML 表：{path}")
    return value


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ConfigError(f"{name} 必须是非空字符串数组")
    return list(value)


def _from_mapping(value: Mapping[str, Any]) -> AppConfig:
    model = value.get("model", {})
    agent = value.get("agent", {})
    repl = value.get("repl", {})
    safety = value.get("safety", {})
    for name, section in (
        ("model", model),
        ("agent", agent),
        ("repl", repl),
        ("safety", safety),
    ):
        if not isinstance(section, Mapping):
            raise ConfigError(f"{name} 必须是 TOML 表")

    try:
        config = AppConfig(
            model=ModelConfig(name=str(model.get("name", "deepseek-flash"))),
            agent=AgentConfig(
                max_steps=int(agent.get("max_steps", 15)),
                max_repairs=int(agent.get("max_repairs", 2)),
                max_context_chars=int(
                    agent.get("max_context_chars", 30_000)
                ),
                max_file_chars=int(agent.get("max_file_chars", 10_000)),
                command_timeout=int(agent.get("command_timeout", 60)),
            ),
            repl=ReplSettings(
                mode=str(repl.get("mode", "auto")),
                verbosity=str(repl.get("verbosity", "normal")),
                conversation_chars=int(
                    repl.get("conversation_chars", 12_000)
                ),
                persist_sessions=bool(repl.get("persist_sessions", True)),
            ),
            safety=SafetyConfig(
                protected_paths=_string_list(
                    safety.get("protected_paths", []),
                    "safety.protected_paths",
                ),
                allowed_changes=_string_list(
                    safety.get("allowed_changes", []),
                    "safety.allowed_changes",
                ),
            ),
        )
    except (TypeError, ValueError) as error:
        raise ConfigError(f"配置值类型不正确：{error}") from error
    config.validate()
    return config


def load_config(
    workspace: Path,
    *,
    user_path: Path | None = None,
    project_path: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> AppConfig:
    root = workspace.expanduser().resolve()
    user_file = user_path or (
        Path.home() / ".config" / "mini-pi" / "config.toml"
    )
    project_file = project_path or (root / ".mini-pi" / "config.toml")
    merged = AppConfig().to_dict()
    env = environment if environment is not None else os.environ
    if env.get("DEEPSEEK_MODEL"):
        merged.setdefault("model", {})["name"] = env["DEEPSEEK_MODEL"]
    _merge(merged, _read_toml(user_file.expanduser()))
    _merge(merged, _read_toml(project_file.expanduser()))

    return _from_mapping(merged)
