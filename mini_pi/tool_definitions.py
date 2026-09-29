TOOL_DEFINITIONS = [
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
                    }
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