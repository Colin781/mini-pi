import ast
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


IGNORED_DIRECTORIES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".mini-pi",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

CONFIG_NAMES = {
    ".env.example",
    ".gitignore",
    "Dockerfile",
    "Makefile",
    "mypy.ini",
    "pyproject.toml",
    "pytest.ini",
    "requirements.txt",
    "ruff.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
}

TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True, slots=True)
class FileInfo:
    path: str
    size: int
    lines: int


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    name: str
    qualified_name: str
    kind: str
    path: str
    line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class ReferenceInfo:
    name: str
    kind: str
    path: str
    line: int
    code: str


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, path: str, lines: list[str]) -> None:
        self.path = path
        self.lines = lines
        self.scope: list[str] = []
        self.symbols: list[SymbolInfo] = []
        self.references: list[ReferenceInfo] = []

    def _code(self, line: int) -> str:
        if 1 <= line <= len(self.lines):
            return self.lines[line - 1].strip()
        return ""

    def _symbol(self, node: ast.AST, name: str, kind: str) -> None:
        qualified = ".".join([*self.scope, name])
        self.symbols.append(
            SymbolInfo(
                name=name,
                qualified_name=qualified,
                kind=kind,
                path=self.path,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
            )
        )

    def _reference(self, node: ast.AST, name: str, kind: str) -> None:
        self.references.append(
            ReferenceInfo(
                name=name,
                kind=kind,
                path=self.path,
                line=node.lineno,
                code=self._code(node.lineno),
            )
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._symbol(node, node.name, "class")
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = "method" if self.scope else "function"
        self._symbol(node, node.name, kind)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = "method" if self.scope else "async_function"
        self._symbol(node, node.name, kind)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Name(self, node: ast.Name) -> None:
        self._reference(node, node.id, "name")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._reference(node, node.attr, "attribute")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            visible_name = alias.asname or alias.name.split(".")[0]
            self._reference(node, visible_name, "import")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self._reference(
                node,
                alias.asname or alias.name,
                "import",
            )


class RepositoryIndex:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.files: list[FileInfo] = []
        self.symbols: list[SymbolInfo] = []
        self.references: list[ReferenceInfo] = []
        self.refresh()

    def iter_paths(self) -> Iterator[Path]:
        for current, directories, filenames in os.walk(self.root):
            directories[:] = sorted(
                directory
                for directory in directories
                if directory not in IGNORED_DIRECTORIES
                and not directory.startswith(".")
            )

            current_path = Path(current)

            for filename in sorted(filenames):
                if filename.startswith(".") and filename not in CONFIG_NAMES:
                    continue

                path = current_path / filename

                if path.is_symlink() or not path.is_file():
                    continue

                yield path

    def refresh(self) -> None:
        files: list[FileInfo] = []
        symbols: list[SymbolInfo] = []
        references: list[ReferenceInfo] = []

        for path in self.iter_paths():
            relative = path.relative_to(self.root).as_posix()
            size = path.stat().st_size
            line_count = 0

            if path.suffix.lower() in TEXT_SUFFIXES or path.name in CONFIG_NAMES:
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    content = ""

                line_count = len(content.splitlines())

                if path.suffix == ".py" and content:
                    try:
                        tree = ast.parse(content, filename=relative)
                    except SyntaxError:
                        tree = None

                    if tree is not None:
                        visitor = _PythonVisitor(relative, content.splitlines())
                        visitor.visit(tree)
                        symbols.extend(visitor.symbols)
                        references.extend(visitor.references)

            files.append(
                FileInfo(
                    path=relative,
                    size=size,
                    lines=line_count,
                )
            )

        self.files = sorted(files, key=lambda item: item.path)
        self.symbols = sorted(
            symbols,
            key=lambda item: (item.path, item.line, item.name),
        )
        self.references = sorted(
            references,
            key=lambda item: (item.path, item.line, item.name, item.kind),
        )

    def find_symbols(self, query: str, max_results: int = 50) -> list[SymbolInfo]:
        normalized = query.casefold()
        exact = [
            item
            for item in self.symbols
            if item.name.casefold() == normalized
            or item.qualified_name.casefold() == normalized
        ]
        partial = [
            item
            for item in self.symbols
            if normalized in item.name.casefold()
            or normalized in item.qualified_name.casefold()
        ]

        ordered = [*exact]
        seen = {
            (item.path, item.line, item.qualified_name)
            for item in exact
        }

        for item in partial:
            identity = (item.path, item.line, item.qualified_name)
            if identity not in seen:
                ordered.append(item)
                seen.add(identity)

        return ordered[:max_results]

    def find_references(
        self,
        symbol: str,
        max_results: int = 50,
    ) -> list[ReferenceInfo]:
        normalized = symbol.casefold()
        matches = [
            item
            for item in self.references
            if item.name.casefold() == normalized
        ]

        unique: list[ReferenceInfo] = []
        seen: set[tuple[str, int, str]] = set()

        for item in matches:
            identity = (item.path, item.line, item.kind)
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(item)

        return unique[:max_results]

    def _git_status(self) -> str:
        try:
            result = subprocess.run(
                ["git", "status", "--short", "--", "."],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unavailable"

        if result.returncode != 0:
            return "not a git repository"

        return result.stdout.strip() or "clean"

    def summary(self, max_chars: int = 10_000) -> str:
        python_files = [item for item in self.files if item.path.endswith(".py")]
        package_directories = sorted(
            {
                str(Path(item.path).parent)
                for item in self.files
                if Path(item.path).name == "__init__.py"
                and str(Path(item.path).parent) != "."
            }
        )
        test_directories = sorted(
            {
                Path(item.path).parts[0]
                for item in self.files
                if Path(item.path).parts
                and (
                    Path(item.path).parts[0] == "tests"
                    or Path(item.path).parts[0].startswith("test")
                )
            }
        )
        entry_points = [
            item.path
            for item in python_files
            if len(Path(item.path).parts) == 1
        ]
        config_files = [
            item.path
            for item in self.files
            if Path(item.path).name in CONFIG_NAMES
        ]

        lines = [
            "Repository summary:",
            "- Project type: Python" if python_files else "- Project type: unknown",
            f"- Source packages: {', '.join(package_directories) or 'none detected'}",
            f"- Tests: {', '.join(test_directories) or 'none detected'}",
            f"- Entry points: {', '.join(entry_points) or 'none detected'}",
            f"- Config: {', '.join(config_files) or 'none detected'}",
            f"- Git status: {self._git_status()}",
            f"- {len(self.files)} files, {sum(item.lines for item in self.files):,} lines",
            "",
            "File tree (path | bytes | lines):",
        ]

        for item in self.files[:120]:
            lines.append(f"- {item.path} | {item.size} | {item.lines}")

        if len(self.files) > 120:
            lines.append(f"- ... {len(self.files) - 120} more files")

        lines.extend(["", "Key Python symbols:"])

        for symbol in self.symbols[:80]:
            lines.append(
                f"- {symbol.path}:{symbol.line} "
                f"{symbol.kind} {symbol.qualified_name}"
            )

        if len(self.symbols) > 80:
            lines.append(f"- ... {len(self.symbols) - 80} more symbols")

        rendered = "\n".join(lines)

        if len(rendered) <= max_chars:
            return rendered

        return rendered[: max_chars - 55] + "\n... repository summary truncated by context budget"
