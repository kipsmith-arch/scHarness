# Loop 设计文档

## 1. 设计目标

实现一个**极简通用的 agent loop**,不感知任何领域知识。loop 做四件事:

1. **加载 skill** — 从 skill 获取 system prompt、工具 schema、工具执行方式
2. **跑循环** — LLM ↔ tool 循环,function calling 驱动,不做上下文管理(messages 只增不减)
3. **记轨迹** — 保存完整 conversation
4. **通用记忆(笔记本)** — 注册内置笔记本工具(write_note / retrieve_notes),拼接 loop 通用提示。完全被动:读写时机由 LLM 决定,loop 不解析内容(见 `rag_design.md`)

LLM 需要哪些信息(知识、工具、决策流程)完全由 **skill** 决定;记忆是 loop 自带的通用项,同样不含领域内容。loop 不硬编码任何领域内容。

---

## 2. 职责划分

### 2.1 Loop 的职责

| 职责 | 说明 |
|---|---|
| 加载 skill | 经 `skill_loader` 从**标准 skill 包**派生 system_prompt / tool_schemas / tool_runtime(见 §3) |
| 注册工具 | 注册 skill 声明的工具(name + schema + 执行函数)+ loop 内置笔记本工具(write_note / retrieve_notes) |
| 拼接系统提示 | `loop 通用提示(含笔记本用法指导)+ skill.system_prompt` 合成系统消息 |
| 运行 agent 循环 | LLM → tool_calls → 执行 → result → LLM,循环直到 LLM 不再调工具 |
| 记录 conversation | 完整 messages 数组写入 `conversation.jsonl`,不截断 |
| 生命周期管理 | session 开始/结束、recursion limit、中断恢复 |

### 2.2 Skill 的职责(loop 不管的事)

| 职责 | 说明 |
|---|---|
| 领域知识 | 知识文件的内容、system prompt 怎么写 |
| 工具声明 | 有哪些工具、name/description/parameters、怎么执行 |
| 结构化日志 | 工具内部写什么日志、什么格式 — loop 和 LLM 都不感知 |
| 决策流程 | SOP 步骤、决策点 — 全在 skill 里指导 LLM |

skill 不感知 loop 内置的笔记本,也不决定其内容(记录什么、何时记录由 LLM 自主)。

### 2.3 核心原则

```
loop = 通用运行器(加载 skill + 内置记忆 → 跑循环 → 记轨迹)
skill = 领域规范(知识 + 工具 + prompt)
```

loop 换一个 skill 就能跑完全不同的任务,不需要改 loop 代码。

---

## 3. Loop 对 Skill 的接口需求

loop 通过**标准 skill 加载器**(`harness/skill_loader.py`)从标准 skill 包派生三样东西,不感知领域内容。标准包形态与派生规则见 `implementation_plan.md` §1 / §3.2:

```
skills/<name>/
├── SKILL.md     YAML frontmatter(name/description)+ body
└── scripts/     每个脚本实现 --dump-schema 自描述

frontmatter → name / description(metadata)
body        → system_prompt
scripts/*.py → 执行 `python <script> --dump-schema` 输出
               {subcommand, args[{name,type,required,default,help}]}
               → 聚合成 tool_schemas(OpenAI function-calling 格式)
               → 同时生成 tool_runtime:{name:{type:"subprocess",script,subcommand}}
可选 frontmatter `functions:` 声明 type="function" 工具(module.func 相对 scripts/ 解析)
```

- **单一事实源** = 每个脚本的 argparse;派生自动对齐,无手工 JSON 漂移。
- 工具名 = `{script_stem}__{subcommand}`(双下划线分隔;函数名必须匹配 `^[a-zA-Z0-9_-]+$`,含点会被 API 拒绝)。
- 新增工具类型(function/builtin/http…)只需扩展 dispatcher 与加载器,不动 skill 格式。

loop 从 skill 获取三样东西:

| 接口 | 类型 | 给谁 | 说明 |
|---|---|---|---|
| `system_prompt` | string | LLM(进 messages) | 完整的 system prompt,loop 不关心内容 |
| `tool_schemas` | list[dict] | LLM(function calling) | 工具的 name + description + parameters |
| `tool_runtime` | dict[name → spec] | loop(执行用) | 每个工具的执行方式,LLM 看不到 |

具体 skill 怎么组织文件、怎么生成这三样东西,是 skill 设计阶段的事。

除 skill 提供的三样东西外,loop 自带两样,**不由 skill 提供**:

| 接口 | 类型 | 给谁 | 说明 |
|---|---|---|---|
| `base_prompt` | string | LLM(进 messages) | loop 通用提示(角色 + 笔记本用法指导),与 `skill.system_prompt` 拼接成系统消息 |
| `notebook_tools` | schemas + runtime | LLM + loop | 内置 `write_note` / `retrieve_notes` 工具(见 `rag_design.md` §2),dispatcher 按 `type="builtin"` 执行 |

### 3.1 工具执行方式

`tool_runtime` 中每个工具的 spec 需要告诉 loop 怎么执行:

| type | 说明 | 执行方式 |
|---|---|---|
| `subprocess` | 调 CLI 脚本 | `subprocess.run([script, subcommand, ...args])`,解析 stdout 最后一行 JSON |
| `function` | 调 Python 函数 | `importlib` 导入指定模块路径,调函数 |

新增工具类型(如 `http`)只需在 loop 加一个 dispatcher。

### 3.2 subprocess 的 stdout 契约

subprocess 类型的工具,脚本需在 stdout 最后一行输出 JSON,loop 解析后透传给 LLM:

```json
{"status": "ok", "data": {...}}
```

失败时:

```json
{"status": "error", "error": "...", "stderr_tail": "..."}
```

`data` 的具体内容由 skill 决定,loop 不解析其内容,原样透传。

---

## 4. 架构总览

```
┌─────────────────────── 通用 Agent Loop ───────────────────────┐
│                                                                │
│  输入: skill(system_prompt + tool_schemas + tool_runtime)      │
│        + 用户 task message                                     │
│                                                                │
│  系统消息 = loop 通用提示(含笔记本用法)+ skill.system_prompt    │
│                                                                │
│  ┌─────────────┐    tool_calls    ┌──────────────────────┐    │
│  │     LLM     │ ──────────────→ │  Tool dispatcher     │    │
│  │ (OpenAI FC) │                 │  ├ skill 工具:       │    │
│  │             │ ←── result ──── │  │  subprocess /     │    │
│  │             │                 │  │  function          │    │
│  │             │                 │  └ loop 内置:        │    │
│  └─────────────┘                 │     write_note /     │    │
│       │                          │     retrieve_notes   │    │
│  no tool_calls → END             └──────────────────────┘    │
│                                                                │
│  输出: conversation.jsonl (完整 messages)                      │
│        + 笔记本 notes.jsonl (仅 LLM 主动写入时)                │
│                                                                │
└────────────────────────────────────────────────────────────────┘
         │ tool 内部自行处理(副作用)
         ▼
┌─────────────────── Skill 定义的副作用 ─────────────────────────┐
│                                                                │
│  工具内部可以写日志、写文件、调外部服务                         │
│  这些副作用全由 skill 决定,loop 和 LLM 都不感知                │
└────────────────────────────────────────────────────────────────┘
```

### 4.1 数据流

```
1. loop 从 skill 获取 system_prompt + tool_schemas + tool_runtime,并入内置 base_prompt + 笔记本工具
2. loop 发 {base_prompt + system + user task} 给 LLM
3. LLM 返回 tool_calls
4. loop 按 tool_runtime 声明的 type 执行每个 tool_call:
   - subprocess → subprocess.run() → 解析 stdout JSON → 返回给 loop
   - function → 调函数 → 返回给 loop
   - builtin → 调笔记本工具(notebook.py)→ 返回给 loop
5. loop 把 result 包成 ToolMessage 追加到 messages
6. loop 发全部 messages 给 LLM
7. 重复 3-6 直到 LLM 不再调工具
8. loop 写 conversation.jsonl
```

---

## 5. Loop 实现(LangGraph)

### 5.1 State

```python
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    base_prompt: str              # loop 通用提示(含笔记本用法指导)
    system_prompt: str
    tool_schemas: list[dict]      # skill 工具 + 笔记本工具合并
    tool_runtime: dict            # skill 工具 + 笔记本工具合并
    project_dir: str
    notes_path: str               # 笔记本路径(env RAG_NOTES_DIR 或 project_dir/notes.jsonl)
    session_id: str
```

### 5.2 Graph

```python
from langgraph.graph import StateGraph, END

def call_model(state: AgentState) -> dict:
    """调 LLM。"""
    response = llm.invoke(state["messages"], tools=state["tool_schemas"])
    return {"messages": [response]}

def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    return "tools" if last.tool_calls else END

def call_tools(state: AgentState) -> dict:
    """按 tool_runtime 声明的 type 分发执行。"""
    results = []
    for tc in state["messages"][-1].tool_calls:
        spec = state["tool_runtime"][tc["name"]]
        result = dispatch(spec, tc["args"], state)
        results.append(ToolMessage(content=json.dumps(result), tool_call_id=tc["id"]))
    return {"messages": results}

workflow = StateGraph(AgentState)
workflow.add_node("agent", call_model)
workflow.add_node("tools", call_tools)
workflow.set_entry_point("agent")
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")
app = workflow.compile()
```

### 5.3 Tool Dispatcher

```python
import subprocess, json, importlib

def dispatch(spec: dict, args: dict, state: AgentState) -> dict:
    """按 tool type 分发执行。"""
    if spec["type"] == "subprocess":
        return run_subprocess(spec, args)
    elif spec["type"] == "function":
        return run_function(spec, args, state)
    elif spec["type"] == "builtin":
        return run_builtin(spec, args, state)   # loop 内置笔记本工具
    else:
        return {"status": "error", "error": f"unknown tool type: {spec['type']}"}

def run_subprocess(spec: dict, args: dict) -> dict:
    cmd = ["python", spec["script"]]
    if "subcommand" in spec:
        cmd.append(spec["subcommand"])
    for k, v in args.items():
        cmd.extend([f"--{k.replace('_', '-')}", str(v)])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        return {"status": "error", "error": f"exit {result.returncode}", "stderr_tail": result.stderr[-2000:]}
    lines = result.stdout.strip().split("\n")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"status": "error", "error": "unparseable stdout", "stdout_tail": result.stdout[-2000:]}

def run_function(spec: dict, args: dict, state: AgentState) -> dict:
    module_path, func_name = spec["function"].rsplit(".", 1)
    module = importlib.import_module(module_path)
    func = getattr(module, func_name)
    return func(args, state)

def run_builtin(spec: dict, args: dict, state: AgentState) -> dict:
    """执行 loop 内置笔记本工具(notebook.py),不涉及 skill。"""
    func = NOTEBOOK_FUNCS[spec["name"]]   # {"write_note": ..., "retrieve_notes": ...}
    return func(args, state)
```

### 5.4 入口

```python
def run_session(skill, project_dir: str, task_message: str, llm_config: dict):
    """通用入口:从 skill 加载 → 拼接内置提示 → 跑 loop → 存轨迹。

    Args:
        skill: skill 实例,提供 system_prompt / tool_schemas / tool_runtime
    """
    tool_schemas = skill.tool_schemas + NOTEBOOK_TOOL_SCHEMAS
    tool_runtime = {**skill.tool_runtime, **NOTEBOOK_TOOL_RUNTIME}
    state = {
        "messages": [
            SystemMessage(content=f"{LOOP_BASE_PROMPT}\n\n{skill.system_prompt}"),
            HumanMessage(content=task_message),
        ],
        "base_prompt": LOOP_BASE_PROMPT,
        "system_prompt": skill.system_prompt,
        "tool_schemas": tool_schemas,
        "tool_runtime": tool_runtime,
        "project_dir": project_dir,
        "notes_path": os.environ.get("RAG_NOTES_DIR") or f"{project_dir}/notes.jsonl",
        "session_id": generate_session_id(),
    }

    final_state = app.invoke(state, config={"recursion_limit": 100})

    save_conversation(f"{project_dir}/conversation.jsonl", final_state["messages"])
```

---

## 6. 轨迹记录

### 6.1 Loop 只负责 conversation.jsonl

loop 唯一的轨迹职责是在 session 结束时把完整 messages 写出:

```jsonl
{"role": "system", "content": "..."}
{"role": "user", "content": "请注释以下数据集..."}
{"role": "assistant", "content": null, "tool_calls": [{"id": "call_1", "name": "xxx", "args": {...}}]}
{"role": "tool", "tool_call_id": "call_1", "content": "{\"status\":\"ok\",\"data\":{...}}"}
{"role": "assistant", "content": "分析结果...", "tool_calls": [{"id": "call_2", "name": "yyy", "args": {...}}]}
...
```

> **笔记本不是轨迹**:loop 内置笔记本(`notes.jsonl`)是 LLM 主动写的记忆,不属于轨迹,与 `conversation.jsonl` / skill 的 `run_log.jsonl` 独立。loop 只负责存与检索,不把它当作轨迹记录。笔记本操作本身(工具调用)已自然记录在 conversation 的 messages 里。

### 6.2 结构化日志由 skill 的工具负责

工具内部可以写自己的结构化日志(如 `run_log.jsonl`),那是 skill 的事。loop 不感知、不解析、不干预。

### 6.3 微调数据

| 来源 | 产出者 | 用途 |
|---|---|---|
| `conversation.jsonl` | loop | 完整 SFT 轨迹(含 tool call 序列) |
| skill 工具内部的日志 | skill | 结构化训练对(具体格式由 skill 设计) |

---

## 7. 错误处理

### 7.1 工具执行失败

```
subprocess returncode != 0 → 返回 {status: "error", stderr_tail: "..."}
function 抛异常 → 返回 {status: "error", error: str(e)}
→ LLM 收到 error,自行决定是否重试
```

### 7.2 循环保护

LangGraph 的 `recursion_limit` 限制最大轮次(如 100)。超过则强制结束,保存当前 conversation。

### 7.3 中断恢复

session 中断后,`conversation.jsonl` 保留了完整历史。重新启动时可以加载历史 messages 继续(具体恢复策略由 skill 或用户决定)。

---

## 8. 文件结构

```
harness/
├── loop.py             ← 通用 agent loop(LangGraph)
├── dispatcher.py       ← tool dispatcher(subprocess / function / builtin)
├── skill_loader.py     ← 标准 skill 加载器(frontmatter + --dump-schema 派生三接口)
├── session.py          ← 入口:run_session()
├── conversation.py     ← conversation.jsonl 读写
└── notebook.py         ← 内置笔记本工具(write_note / retrieve_notes + 存储 + 索引)
```

loop 只包含上述 6 个文件(`notebook.py` 为 loop 的通用记忆,不属于 skill)。skill、脚本、知识文件都不属于 loop。

---

## 9. 与其他设计文档的关系

```
atomic_operations.md            ← pipeline 有哪些操作 (WHAT)
operations_metrics_catalog.md   ← 每个操作输出什么指标 (WHY)
tool_design.md                  ← 怎么实现,加载策略 (HOW)
trajectory_design.md            ← 指标和判断怎么记录 (LOG) — 属于 skill
rag_design.md                   ← 通用笔记本记忆 (MEM) — loop 内置,不含领域知识
loop_design.md (本文档)         ← 通用 loop 怎么跑 (RUN) — 不含领域知识
```

```
Skill (领域规范)
    │ 提供 system_prompt + tool_schemas + tool_runtime
    ▼
Loop (通用)
    │ 拼接 base_prompt + skill.system_prompt → 跑循环 → 记 conversation
    │
    ├── skill 工具 → subprocess/function 执行(内部副作用由 skill 管)
    └── loop 内置 → 笔记本工具(write_note / retrieve_notes)
                              │
                              ▼
                    conversation.jsonl (loop 产出)
                    笔记本 notes.jsonl (LLM 主动写入)
                    skill 工具内部日志 (skill 产出)
```

---

## 10. 实施计划

| Phase | 内容 | 产出 | 状态 |
|---|---|---|---|
| **L-1** | `loop.py` + `dispatcher.py` + `session.py` + `conversation.py` | 通用 loop | ✅ 已完成 |
| **L-2** | 标准 skill 加载器(`skill_loader.py`)+ 接口协议派生 | 接口规范 + 加载器 | ✅ 已完成 |
| **L-3** | 端到端测试:echo skill(标准格式)验证 loop 通用性 | 验证 | ✅ 已完成 |
| **L-4** | `notebook.py`(write_note / retrieve_notes)+ 通用提示拼接 + env 配置 | 内置记忆(见 `rag_design.md` §8 M-1/M-2) | ✅ 已完成 |
| **L-5** | 会话入口 CLI(`python -m harness.session`,含 `--dump-skill` / `--resume`) | 入口 + 冒烟 | ✅ 已完成 |
| **L-6** | 本文档同步修订(职责/架构图/State 补笔记本字段;skill 接口改述为“从标准包派生”) | 文档一致 | ✅ 已完成 |

技能的具体实现(工具清单、知识文件、日志格式)属于 skill 设计(P2/P3)。

---

## 11. 验证

### 11.1 通用性验证 ✅(P1 已通过)

换一个最小 skill(echo),loop 直接跑通,未改 loop 代码。

### 11.2 接口验证 ✅(P1 已通过)

- echo skill 标准包:frontmatter → name/description;body → system_prompt;`echo__repeat`(subprocess,由 `--dump-schema` 派生)与 `echo_reverse`(function,由 frontmatter 声明)加载成功。
- 缺 frontmatter / 缺 scripts / 缺 SKILL.md 的包均报清晰 SkillError。

### 11.3 笔记本验证 ✅(P1 已通过)

- 最小 skill 下 write_note / retrieve_notes 可注册、可读写、可跨会话检索(前一 session 写的笔记在下一 session 命中,score 0.865)。
- 系统消息 = `base_prompt + skill.system_prompt`,拼接正确。
- 向量检索走 **Chroma 持久化向量库**(`notes_chroma/` 目录):write_note 双写(jsonl + Chroma),retrieve 返回余弦相似度;tags 用 `$contains` 过滤;索引丢失/不一致时自动重建。
- 无 embedding(离线/`RAG_EMBEDDING=off`)时 BM25 兜底可用;Chroma/embedding 加载失败自动降级,工具不失效。
- skill 对笔记本无感知:echo skill 未声明笔记本工具,loop 仍正常注册并提供。
