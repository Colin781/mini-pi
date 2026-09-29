# Mini Pi

Mini Pi 是一个用于学习 Coding Agent 工作原理的 Python 项目。

它通过 DeepSeek Tool Calls 实现一个基本的代码修改循环：

1. 接收用户任务
2. 浏览项目文件
3. 搜索相关代码
4. 读取文件
5. 运行测试
6. 修改代码
7. 再次运行测试
8. 展示 Git diff

## 功能

- 工作目录隔离
- 文件列表
- 文本搜索
- 分段读取文件
- 精确文本替换
- 受限测试命令
- Git diff
- 人工确认
- 工具层单元测试
- 固定验收任务

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```