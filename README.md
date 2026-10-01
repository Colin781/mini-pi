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
- Agent 结束后的强制自动验收
- 验收失败后的自动修复重试
- 受保护文件和允许修改范围检查
- 修复次数、验收结果和事件追踪

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 可复现评测

评测系统使用不可变模板创建新的工作区。Agent 不会直接修改模板。

### 列出评测任务

```bash
python evaluate.py list
```

### 重置所有评测工作区

```bash
python evaluate.py reset
```

### 运行全部评测

```bash
python evaluate.py run
```

### 运行指定任务

```bash
python evaluate.py run --case task_01_calculator
```

### 每道题重复运行三次

```bash
python evaluate.py run --repeat 3
```

### 同时选择多道题

```bash
python evaluate.py run \
  --case task_01_calculator \
  --case task_02_inventory \
  --repeat 3
```

每次运行都会在 `evaluation/results/` 下产生一个独立目录，其中包含：

- 每次 Agent 运行的 JSON 报告
- `summary.json`
- `summary.md`

成功与否由评测系统在 Agent 结束后独立运行测试判断，不依赖 Agent 自己声称任务是否完成。

## 自动验证与修复

```bash
python agent.py \
  --workspace task_fixture \
  --verify-command "python -m unittest discover -s tests -v" \
  --max-repairs 2 \
  --protected-path tests \
  --allowed-change calculator.py \
  "修复 calculator.py 中的 add 函数"
```

Agent 给出答案后会独立运行 `--verify-command`。如果验收失败，错误输出会被发送回模型，模型可以继续修复，直到测试通过或达到 `--max-repairs`。

`--protected-path` 用于保护测试文件，`--allowed-change` 用于限制本次任务允许修改的文件。两个参数都可以重复使用。

### 评测指标

- 成功率
- 平均运行时间
- 平均 Agent 轮数
- 平均工具调用次数
- 平均自动修复次数
- Agent 退出状态
- 验收测试退出状态
- 受保护文件违规和越界修改
