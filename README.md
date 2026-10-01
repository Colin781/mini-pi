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
- 启动时自动生成仓库概览
- 基于 Python AST 的符号和引用搜索
- 大文件分段读取与上下文字符预算
- 文件读取量、上下文量和搜索次数指标
- unified diff 补丁编辑与冲突检测
- 文件检查点、失败回滚和事务式修复
- 自动允许、需要确认和拒绝三级命令策略
- 默认关键文件保护
- 可完整复盘的 JSONL 运行轨迹

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

## 安全编辑与运行追踪

Agent 优先使用 `apply_patch` 提交 unified diff。所有文件和 hunk 都会先在内存中验证，任意上下文冲突都会拒绝整个补丁，不会留下部分修改。

每次初始修改和自动修复都会创建文件检查点。验收失败并达到修复限制、发生 API 异常、用户取消或检测到策略违规时，当前尝试会恢复到检查点。

命令分为三个级别：测试、静态检查和只读 Git 命令自动允许；安装依赖和网络请求需要确认；危险删除、shell 和 Git 写操作直接拒绝。所有命令都使用参数数组运行，超时后终止整个进程组。

每次 CLI 运行默认在 `~/.mini-pi/traces/` 写入 JSONL 轨迹。也可以指定路径：

```bash
python agent.py \
  --workspace task_fixture \
  --trace run.trace.jsonl \
  "修复 calculator.py"
```

轨迹包括模型请求、工具调用、补丁、检查点、验收、自动修复和最终状态，并自动隐藏 API key、token 和密码字段。

## 代码库理解与上下文预算

Agent 启动时会生成紧凑的 Repository summary，其中包括文件树、Python 包、测试目录、配置文件、Git 状态、文件大小和关键符号。模型可以使用以下工具定位代码：

- `find_symbol`：查找函数、方法和类定义
- `find_references`：查找名称引用、属性访问和导入关系
- `read_file`：按照起止行读取大文件片段

默认单轮代码上下文最多 30000 个字符，单个文件最多 10000 个字符。可以通过 CLI 调整：

```bash
python agent.py \
  --workspace task_fixture \
  --max-context-chars 30000 \
  --max-file-chars 10000 \
  "修复订单总额计算"
```

### 评测指标

- 成功率
- 平均运行时间
- 平均 Agent 轮数
- 平均工具调用次数
- 平均自动修复次数
- 平均读取文件数
- 平均上下文字符数
- 平均搜索次数
- 平均补丁次数
- 平均检查点恢复次数
- Agent 退出状态
- 验收测试退出状态
- 受保护文件违规和越界修改
