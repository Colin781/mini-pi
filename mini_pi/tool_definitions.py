TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "repository_summary",
            "description": (
                "重新扫描代码库并返回项目类型、文件树、"
                "测试目录、配置、Git 状态和关键 Python 符号。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "列出工作目录或指定子目录中的文件。"
                "当不知道项目结构或文件位置时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "相对于工作目录的子目录。"
                            "默认值为当前工作目录。"
                        ),
                    }
                },
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": (
                "在工作目录的文本文件中搜索字符串，"
                "返回匹配文件、行号和对应代码。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要搜索的文本",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "搜索范围，相对于工作目录。"
                            "可以是文件或目录，默认搜索整个工作目录。"
                        ),
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写，默认 false",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大匹配数量，默认 50",
                    },
                },
                "required": ["query"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": (
                "使用 Python AST 查找函数、异步函数、方法或类的定义，"
                "返回文件路径和精确行号。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "符号名或限定名，例如 run_agent",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大结果数量，默认 50",
                    },
                },
                "required": ["name"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": (
                "使用 Python AST 查找符号的名称引用、属性访问和导入关系。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "需要查找引用的符号名",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大结果数量，默认 50",
                    },
                },
                "required": ["name"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取工作目录中的文本文件。"
                "可以指定开始行和结束行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于工作目录的文件路径",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "开始行号，从 1 开始，默认 1",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号，默认 200",
                    },
                },
                "required": ["path"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "replace_text",
            "description": (
                "在文件中精确替换一段文本。"
                "old 文本必须在文件中恰好出现一次。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于工作目录的文件路径",
                    },
                    "old": {
                        "type": "string",
                        "description": "需要被替换的原文本",
                    },
                    "new": {
                        "type": "string",
                        "description": "替换后的新文本",
                    },
                },
                "required": ["path", "old", "new"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": (
                "事务式应用 unified diff。所有 hunk 必须与当前文件上下文匹配，"
                "任何冲突都会拒绝整个补丁且不修改文件。优先使用此工具修改代码。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": (
                            "完整 unified diff，包含 ---、+++ 和 @@ 行"
                        ),
                    }
                },
                "required": ["patch"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "create_checkpoint",
            "description": "复制当前工作区文件并创建可恢复的检查点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "检查点标签，默认 manual",
                    }
                },
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "restore_checkpoint",
            "description": (
                "恢复指定检查点；省略 ID 时恢复最近的检查点。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "checkpoint_id": {
                        "type": "string",
                        "description": "create_checkpoint 返回的检查点 ID",
                    }
                },
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "在工作目录中运行受限制的测试命令。"
                "command 必须是字符串数组。"
                "目前只允许 Python unittest 或 pytest。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "array",
                        "items": {
                            "type": "string",
                        },
                        "description": (
                            "命令及参数，例如 "
                            '["python", "-m", "unittest", '
                            '"discover", "-s", "tests", "-v"]'
                        ),
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "命令超时秒数，默认 60",
                    },
                },
                "required": ["command"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": (
                "查看工作目录的 Git 状态和代码差异。"
                "修改完成后使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]
