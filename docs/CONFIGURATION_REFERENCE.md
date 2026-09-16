# 配置选项与默认值参考

> 本文档汇总本仓库所有可配置项（CLI 参数 + 环境变量 + 硬编码常量）及其默认值、优先级和取值范围。
>
> 收集范围：`annot_harness/`、`skills/cell-annotation/scripts/`、`scripts/`（root 层）、`experiments/`、`pytest.ini`、`.env`、`dataset/init.py`。
>
> 不计入：Python 模块内部局部常量（仅在函数体内使用、不通过任何接口暴露的配置）。

---

## 目录

1. [全局约定](#1-全局约定)
   1. [优先级链（CLI > 环境变量 > 默认值）](#11-优先级链cli--环境变量--默认值)
   2. [日志与产物路径](#12-日志与产物路径)
   3. [pytest 标记](#13-pytest-标记)
2. [环境变量](#2-环境变量)
   1. [harness 自身配置](#21-harness-自身配置)
   2. [skill 配置（cell-annotation 示例）](#22-skill-配置cell-annotation-示例)
   3. [进程级临时变量](#23-进程级临时变量)
3. [harness 层（agent loop）](#3-harness-层agent-loop)
   0. [annot_harness/config.py — harness 自身配置的 dotenv 加载器](#30-harnessconfigpy--harness-自身配置的-dotenv-加载器)
   1. [annot_harness/session.py — CLI `python -m annot_harness.session`](#31-harnesssessionpy--cli-python--m-harnesssession)
   2. [annot_harness/notebook.py — 笔记本 / RAG](#32-harnessnotebookpy--笔记本--rag)
   3. [annot_harness/dispatcher.py — 工具分发](#33-harnessdispatcherpy--工具分发)
   4. [annot_harness/skill_loader.py — 技能加载](#34-harnessskill_loaderpy--技能加载)
   5. [annot_harness/loop.py — Agent 状态](#35-harnesslooppy--agent-状态)
4. [cell-annotation skill — 流水线脚本](#4-cell-annotation-skill--流水线脚本)
   1. [公共参数 `common.py`](#41-公共参数-commonpy)
   2. [step1_prepare.py](#42-step1_preparepy)
   3. [step2_markers.py](#43-step2_markerspy)
   4. [step3c_kg.py](#44-step3c_kgpy)
   5. [step4_judge.py](#45-step4_judgepy)
   6. [step5_refine.py](#46-step5_refinepy)
   7. [step6_validate.py](#47-step6_validatepy)
   8. [step7_diagnose.py](#48-step7_diagnosepy)
   9. [write_judgment.py](#49-write_judgmentpy)
   10. [trajectory_schema.py（硬编码 schema）](#410-trajectory_schemapy硬编码-schema)
5. [scripts/ — eval / 标注生成工具](#5-scripts--eval--标注生成工具)
   1. [validate_log.py](#51-validate_logpy)
   2. [evaluate_annotations.py](#52-evaluate_annotationspy)
   3. [build_gt_cells.py](#53-build_gt_cellspy)
   4. [build_label_map.py](#54-build_label_mappypy)
6. [experiments/ — B1 实验框架](#6-experiments--b1-实验框架)
   1. [scripted_driver.py](#61-scripted_driverpy)
   2. [judges/default_judge.py](#62-judgesdefault_judgepy)
   3. [judges/rule_judge.py](#63-judgesrule_judgepy)
   4. [evaluate_cell_level.py](#64-evaluate_cell_levelpy)
   5. [bootstrap_test.py](#65-bootstrap_testpy)
   6. [analyze_traps.py](#66-analyze_trapspy)
7. [dataset/init.py](#7-datasetinitpy)
8. [B1 决策枚举（arm-default 表）](#8-b1-决策枚举arm-default-表)

---

## 1. 全局约定

### 1.1 优先级链（CLI > 环境变量 > 默认值）

文档统一约定，源自 `tool_design.md §10`：

```
CLI flag > env var > hardcoded default
```

具体到每个配置项，下文会标注：

| 来源 | 含义 |
|---|---|
| **CLI** | argparse `--flag value` |
| **env** | `os.environ.get(...)` 或 `os.environ[...]` |
| **const** | 模块级 Python 常量（不可在运行时改变） |

### 1.2 日志与产物路径

| 路径 | 默认 | 含义 |
|---|---|---|
| `<project-dir>/run_log.jsonl` | CLI `--project-dir`（默认 `"output"`）下的固定文件名 | 唯一轨迹文件（NDJSON，append-only）。所有 step 脚本与 `write_judgment.py` 都向这里追加。 |
| `<project-dir>/notes.jsonl` | 同上，或被 `RAG_NOTES_DIR` 覆盖 | 跨会话笔记本 |
| `<project-dir>/<step>_<name>/...` | 由 `common.STEP_DIRS` 决定 | step 产物目录：见下表 |

`common.STEP_DIRS`（硬编码，所有 step 默认子目录名）：

| step 名 | 子目录 |
|---|---|
| step1_prepare | `step1_prepare` |
| step2_markers | `step2_markers` |
| step3a_kg_precheck | `step3a_kg_precheck` |
| step3b_cross_species_map | `step3b_cross_species_map` |
| step3c_kg | `step3c_kg` |
| step4_rank | `step4_rank` |
| step5_refine | `step5_refine` |
| step6_validate | `step6_validate` |
| step7_diagnose | `step7_diagnose` |

### 1.3 pytest 标记

来自 `pytest.ini`：

| 项 | 值 | 含义 |
|---|---|---|
| `testpaths` | `annot_harness/tests` | pytest 收集目录 |
| `addopts` | `-q` | 默认静默模式 |
| `smoke` marker | (无配置) | 标记需要外部 LLM API 的测试（手动跑） |

---

## 2. 环境变量

环境变量按“拥有者”分成两层：

- **harness 自身配置**（§2.1）——项目级，只服务 `annot_harness/` 包。**`annot_harness/config.py` 是唯一入口**。
- **skill 配置**（§2.2）——以 skill 为单位，各 skill 自负责。cell-annotation 的 Neo4j 连接 / KG 查询参数 / gene-mapping 文件路径都在这里。
- **进程级临时变量**（§2.3）——运行时透传，不走配置文件。

### 2.1 harness 自身配置

由 `<project-root>/.env` 管理（gitignored）与 `<project-root>/.env.example` 模板（跟踪进 git）。`annot_harness/__init__.py` 在导入时调用 `annot_harness/config.py:load_dotenv()` 一次性加载。完整规则见 `annot_harness/config.py:load_dotenv`。

```
读取顺序：.env.example  （提供默认值的模板，跟踪进 git）
          ↓
        .env           （用户本地配置，gitignored；可覆盖模板）
          ↓
        os.environ     （shell / 父进程设置的运行时变量，优先级最高）
```

> CI / 测试跳过自动加载：`export ANNOT_HARNESS_SKIP_DOTENV=1`（在任何 `harness` 导入之前设置；旧名 `SC_HARNESS_SKIP_DOTENV` 仍可用）。
>
> `.env.example` 提供全部受支持 key 的清单与示例值。**默认不提供 `OPENAI_API_KEY` 等敏感项的默认值**——代码在没有显式值时不会被加载，避免硬编码。

#### LLM / OpenAI 兼容网关

来自 `annot_harness/session.py:build_llm`：

| 变量 | 类型 | 默认 | 用途 |
|---|---|---|---|
| `OPENAI_API_KEY` | str | **无**（必须显式设置） | LLM API key |
| `OPENAI_BASE_URL` | str | **无**（不设置则使用 OpenAI 默认 endpoint） | 自定义网关 endpoint |
| `OPENAI_MODEL` | str | **无**（缺省走 `gpt-4o-mini`） | LLM 模型名；与 `llm_config["model"]` 共用同一个字段 |
| `OPENAI_MAX_RETRIES` | int (str) | `"6"` | LangChain ChatOpenAI 重试次数（兼容 dcsapi 等不稳定网关） |

**优先级链（model 字段）**：`llm_config["model"]` > `OPENAI_MODEL` > `DEFAULT_MODEL`
**优先级链（api_key/base_url）**：`llm_config[...]` > 对应 `OPENAI_*` 环境变量

#### 笔记本 / RAG

来自 `annot_harness/session.py`（`build_llm`）：

| 变量 | 类型 | 默认 | 用途 |
|---|---|---|---|
| `OPENAI_API_KEY` | str | **无**（必须显式设置） | LLM API key |
| `OPENAI_BASE_URL` | str | **无**（不设置则使用 OpenAI 默认 endpoint） | 自定义网关 endpoint |
| `OPENAI_MODEL` | str | **无**（缺省走 `gpt-4o-mini`） | LLM 模型名；与 `llm_config["model"]` 共用同一个字段 |
| `OPENAI_MAX_RETRIES` | int (str) | `"6"` | LangChain ChatOpenAI 重试次数（兼容 dcsapi 等不稳定网关） |

**优先级链（model 字段）**：`llm_config["model"]` > `OPENAI_MODEL` > `DEFAULT_MODEL`
**优先级链（api_key/base_url）**：`llm_config[...]` > 对应 `OPENAI_*` 环境变量

### 笔记本 / RAG

来自 `annot_harness/notebook.py`、`annot_harness/session.py`：

| 变量 | 类型 | 默认 | 用途 |
|---|---|---|---|
| `RAG_NOTES_DIR` | str (path) | `<project-dir>/notes.jsonl` | 跨会话笔记本路径；也决定 Chroma 持久化目录 `<dir>/notes_chroma/` |
| `RAG_EMBEDDING` | str | `""`（即默认本地 sentence-transformers） | 嵌入提供者开关，见下表 |

`RAG_EMBEDDING` 取值（`Embedder.configure`）：

| 取值 | 行为 |
|---|---|
| `""` / 未设置 / `"local"` | 默认本地模型 `all-MiniLM-L6-v2` + Chroma |
| `"local:<model>"` | 指定本地 sentence-transformers 模型 + Chroma |
| `"off"` / `"none"` / `"bm25"` | 禁用向量检索；笔记本工具降级到 BM25 |
| 其他任意串 | 视为裸模型名；若本地缓存缺失，则尝试在线下载（仅当显式请求时） |

### 2.2 skill 配置（cell-annotation 示例）

各 skill 自负责自己需要的环境变量。设计上**是 skill 自己拥有（owner）而不是 harness 拥有**：

- skill 脚本（cell-annotation：`skills/cell-annotation/scripts/common.py`）在 import 时调用 `load_skill_dotenv()` 加载 `<skill>/.env`。
- harness 不知道这个 skill 的任何配置 key——保持 domain-agnostic。
- LLM 调用工具时**看不到**这些参数（`--dump-schema` 被 `argparse.SUPPRESS` 隐藏）；运维可以在 CLI 上临时覆盖。

以 cell-annotation 为例，配置在 `skills/cell-annotation/.env`（gitignored；模板在同名的 `.env.example`，跟踪进 git）。加载器由 `common.load_skill_dotenv()` 提供，跳过变量为 `CELL_ANNOTATION_SKIP_DOTENV`。

**配置范围**：放**环境类**参数（Neo4j 连接、BLAST subject 库路径）。任务类参数（`--organ` `--species` `--species-type` `--strict-organ` `--query-fasta`）是 LLM 调用时决定的，仍在 argparse 中。其它调优参数（`--min-confidence` `--max-ancestor-hops`）目前**没有 env 变量**，仅以代码默认 + CLI 临时覆盖。详见 `skills/cell-annotation/SKILL.md` 与 `references/kg-schema.md`。

> 注：基因名映射（`KG_GENE_MAP_PATH`）从 skill 中移除——该功能属数据处理责任，由用户上游完成。

#### cell-annotation 配置 key 一览

| 变量 | 类型 | 默认 | 用途 |
|---|---|---|---|
| `NEO4J_URI` | str | `"bolt://localhost:7687"` | Neo4j Bolt URI（step3c_kg 、build_label_map 使用） |
| `NEO4J_USER` | str | `"neo4j"` | Neo4j 用户名 |
| `NEO4J_PASSWORD` | str | **无默认**（必须显式设置） | Neo4j 密码；不设则连接被拒 |
| `CELL_ANNOTATION_BLASTDB_DIR` | str | `~/.cache/annot-harness/cell-annotation/blastdb` | BLAST subject 库解压根（其下为 `prot/`）；仅 `--provider blastp` 使用 |
| `CELL_ANNOTATION_BLASTDB_URL` | str | xener `blastdb.zip` URL | subject zip；HEAD 为 404，skill 用 GET |
| `CELL_ANNOTATION_BLASTDB_SHA256` | str | `8dd83c925f8f18d7e3f2cf626ce780548085321ed501805247932c5b56dab1c3` | zip hex；空则拒绝安装 |
| `CELL_ANNOTATION_QUERY_FASTA` | str | 无 | `--query-fasta` 默认路径 |

CLI 覆盖（运维临时调试用）：`step3c_kg query` 仍然接受隐藏的 `--uri/--user/--password/--min-confidence/--max-ancestor-hops` 参数（`--help` 可看；`--dump-schema` 不包含）。任务类参数 `--organ` `--species` `--species-type` `--strict-organ` 仍在 schema 中。

#### 未来增加 skill 配置 key 的流程

1. 在 skill 自己的 `<skill>/.env.example` 加 key + 注释
2. 在 skill 的 `scripts/common.py:SKILL_DOTENV_KEYS` 元组同步
3. **不要**加到 `annot_harness/config.py:RECOGNIZED_KEYS` ——harness 不需要知道
4. skill 脚本运行时读 `os.environ.get(...)`（同 `harness` 的读取模式）
5. 如果该参数**仅环境类**（服务/资源调优、不带生物决策），在 argparse 中加 `help=argparse.SUPPRESS` 隐藏（`common.arg_spec` 会自动跳过），CLI 仍可临时覆盖。
6. 写 `annot_harness/tests/test_<skill>_dotenv.py` 验证加载逻辑

### 2.3 进程级临时变量

不走任何配置文件，由代码在运行时设/读。与§2.1 / §2.2 区别：这些是**进程内协调**，不是用户配置。

### 实验层

| 变量 | 类型 | 默认 | 用途 | 引用 |
|---|---|---|---|---|
| `PROJECT_DIR` | str | **无** | `experiments/judges/rule_judge.py` 调用子进程时透传 `--project-dir`（自动注入 + 恢复） | `rule_judge.py:274-284` |

### 进程级环境（dispatcher 注入）

| 变量 | 类型 | 默认 | 用途 |
|---|---|---|---|
| `PYTHONIOENCODING` | str | `"utf-8"` | 调度子进程时若未设置则注入；解决 Windows cp1252 输出 emoji 报错 |

### 配置加载器控制

| 变量 | 类型 | 默认 | 用途 | 引用 |
|---|---|---|---|---|
| `ANNOT_HARNESS_SKIP_DOTENV` | enum | `0` | `=1` 时 `annot_harness/config.py` 跳过整个 dotenv 加载逻辑，跳到从调用者使用 `annot_harness.config.load_dotenv(override=True)` 才能改变环境。CI、单元测试、以及不希望 .env 被隐式读取的场景必须设。旧名 `SC_HARNESS_SKIP_DOTENV` 同等生效。 | `annot_harness/config.py` |

---

## 3. harness 层（agent loop）

### 3.0 annot_harness/config.py — harness 自身配置的 dotenv 加载器

**仅负责 harness 包自己的配置**（LLM 网关 4 项 + 笔记本/RAG 2 项 = 6 项；详见 §2.1）。Skill 配置不是 harness 的责任——cell-annotation 的 Neo4j 连接、KG 参数等由 skill 自己的 `scripts/common.py:load_skill_dotenv()` 加载（详见 §2.2）。harness 保持 domain-agnostic。

`annot_harness/__init__.py` 导入 `config` 子模块，触发一次性的加载。

| API | 用途 |
|---|---|
| `RECOGNIZED_KEYS` | 6 项 harness 受支持 key 的元组（与 `.env.example` 一一对应） |
| `load_dotenv(override=False)` | 主动加载；可重复调用。`override=True` 让 `.env` 覆盖 shell env（仅供测试使用） |
| `get(key, default=None)` | 薄包装 `os.environ.get`；不强制但便于统一 grep |

加载逻辑：

1. 如果 `ANNOT_HARNESS_SKIP_DOTENV=1`（或旧名 `SC_HARNESS_SKIP_DOTENV=1`），立即返回 `[]`。
2. 记录入口时哪些 RECOGNIZED_KEYS 已在 `os.environ`（“shell pre-existing”）。
3. 读取 `.env.example`：仅填充 *未* 设置的 key；记录哪些 key 被模板填充了。
4. 读取 `.env`：填充未设置的 key；**升级** 被模板填充的 key；但**绝不覆盖** shell pre-existing 的 key。
5. `override=True` 时：重新读取两个文件，以文件覆盖一切（CI / 测试专用）。

加载器项目根定位：`Path(__file__).resolve().parent.parent`——**不**依赖 `sys.path`。`import annot_harness.config` 唯一的前提是调用者的 `sys.path` 能看到 `annot_harness/` 包的父目录。harness 包内的模块由 `annot_harness/__init__.py` 负责；外部脚本（`build_label_map.py`）不导入它，从父进程 / shell 获取环境。Skill 脚本不读 harness 配置（它们不需要），由自己的 `scripts/common.py:load_skill_dotenv()` 负责 skill 配置加载（§2.2）。

文件名 / 路径：

| 路径 | 作用 |
|---|---|
| `<project-root>/.env.example` | 跟踪进 git 的模板，提供推荐默认值 |
| `<project-root>/.env` | gitignored；本地用户秘密（如 Neo4j 密码） |

### 3.1 annot_harness/session.py — CLI `python -m annot_harness.session`

CLI 入口：

```
python -m annot_harness.session --skill <path> --project-dir <path> [其它]
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--skill` | str (path) | **必填** | skill 包目录（应包含 `SKILL.md` + `scripts/`） |
| `--project-dir` | str (path) | **必填** | 本次运行的输出目录（`conversation.jsonl` 与 `notes.jsonl` 默认写在这里） |
| `--task` | str | `"请简单回复:回声测试通过"` | 用户任务文本（首条 HumanMessage） |
| `--model` | str | `None`（→ `OPENAI_MODEL` → `gpt-4o-mini`） | 覆盖默认模型 |
| `--max-turns` | int | `100` | LangGraph recursion limit |
| `--resume` | flag (store_true) | `False` | 若 `conversation.jsonl` 存在则继续；否则从头开始 |
| `--dump-skill` | flag (store_true) | `False` | 打印从 SKILL.md 派生的 interfaces（`system_prompt` / `tool_schemas` / `tool_runtime`）并退出 |

模块级常量（不可在 CLI 覆盖）：

| 常量 | 值 | 含义 |
|---|---|---|
| `DEFAULT_MODEL` | `"gpt-4o-mini"` | 见上 `--model` 优先级链 |
| session_id 格式 | `"sess-{YYYYMMDD-HHMMSS}-{rand:04d}"` | 自动生成 |

### 3.2 annot_harness/notebook.py — 笔记本 / RAG

模块级常量：

| 常量 | 值 | 含义 |
|---|---|---|
| `DEFAULT_EMBEDDING_MODEL` | `"all-MiniLM-L6-v2"` | sentence-transformers 本地模型名 |
| `_COLLECTION_NAME` | `"notes"` | Chroma collection 名 |
| `_INDEX_VERSION` | `2` | metadata schema 版本号（bumps 时触发重建） |

BM25 参数（硬编码）：

| 参数 | 值 |
|---|---|
| `k1` | `1.5` |
| `b` | `0.75` |
| IDF 平滑 | `+1`（`(N - df + 0.5) / (df + 0.5) + 1`） |

笔记本工具参数（来自 `NOTEBOOK_TOOL_SCHEMAS`，`annot_harness/loop.py`）：

#### `write_note`

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `content` | string | ✅ | – | 笔记正文（建议 "什么情况 → 做了什么 → 结果/理由" 句式） |
| `tags` | array of string | 否 | – | 标签，便于按类检索 |

#### `retrieve_notes`

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | ✅ | – | 检索意图（中文/英文均可，混合 tokenizer） |
| `tags` | array of string | 否 | – | 限定在带这些标签的笔记里 |
| `top_k` | integer | 否 | `5` | 返回笔记数上限 |

`Notebook.retrieve` 内 `top_k` 兜底：

```python
top_k = int(args.get("top_k", 5) or 5)  # False / 0 都会被替换为 5
```

### 3.3 annot_harness/dispatcher.py — 工具分发

| 常量 / 参数 | 值 | 含义 |
|---|---|---|
| `subprocess timeout` | `float(spec.get("timeout", 3600))` 秒 | 单次工具子进程超时；可在工具的 `tool_runtime` 中覆盖；skill 包内 7 个 step 脚本 + `write_judgment.py` 均未指定，故全部默认 3600s |
| `PYTHONIOENCODING` 注入 | `"utf-8"` | 仅当未设置时注入 |

### 3.4 annot_harness/skill_loader.py — 技能加载

模块级常量：

| 常量 | 值 |
|---|---|
| `_SCHEMA_FLAG` | `"--dump-schema"` |
| `--dump-schema` 子进程 timeout | `120` 秒 |
| `_TYPE_MAP` | `{string→string, integer→integer, number→number, boolean→boolean, array→array, object→object}` |

工具命名规则：

```python
f"{script_stem}__{subcommand}"  # 双下划线连接，避免与 step.op 命名冲突
```

### 3.5 annot_harness/loop.py — Agent 状态

仅两段文字常量（prompt 字面量）：

| 常量 | 说明 |
|---|---|
| `LOOP_BASE_PROMPT` | 循环层 system prompt（包含跨会话笔记本指引、中文输出约定、错误处理规则） |
| `NOTEBOOK_TOOL_SCHEMAS` | 笔记本工具的 OpenAI function-calling schema |
| `NOTEBOOK_TOOL_RUNTIME` | 笔记本工具的运行时 spec（type=builtin） |

`AgentState` 字段（TypedDict）：

| 字段 | 类型 |
|---|---|
| `messages` | `Annotated[list[BaseMessage], add_messages]` |
| `base_prompt` | `str` |
| `system_prompt` | `str` |
| `tool_schemas` | `list[dict]` |
| `tool_runtime` | `dict` |
| `project_dir` | `str` |
| `notes_path` | `str` |
| `session_id` | `str` |

---

## 4. cell-annotation skill — 流水线脚本

所有 step 脚本通过 `--dump-schema` 自描述 args；下表 `stepN_name` 节列出每个脚本所有 CLI 参数（来自 `argparse.add_argument`，若 dump-schema 与源码不一致，以源码为准）。

> 通用参数由 `common.add_common_args()` / `common.add_neo4j_args()` 注入，已在 §4.1 列出。

### 4.1 公共参数 (`common.py`)

#### `add_common_args(parser)` — 所有 step + write_judgment 都注入

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--project-dir` | str | `"output"` | 项目目录（含 `run_log.jsonl` 与各 step 数据目录） |
| `--input` | str (path) | `None` | 输入 h5ad 路径；缺省时按 step 约定自动寻找 |

#### `add_neo4j_args(parser)` — 仅 step3c_kg 注入

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--uri` | str | `None` → `os.environ["NEO4J_URI"]` → `"bolt://localhost:7687"` | Neo4j URI |
| `--user` | str | `None` → `os.environ["NEO4J_USER"]` → `"neo4j"` | Neo4j 用户名 |
| `--password` | str | `None` → `os.environ["NEO4J_PASSWORD"]` | Neo4j 密码（无默认值；不设则拒绝连接） |

#### 其它公共默认值

| 来源 | 名称 | 值 | 含义 |
|---|---|---|---|
| `STEP_DIRS`（硬编码 dict） | step1_prepare | `"step1_prepare"` | step 子目录名 |
| | step2_markers | `"step2_markers"` | |
| | step3a_kg_precheck | `"step3a_kg_precheck"` | |
| | step3b_cross_species_map | `"step3b_cross_species_map"` | |
| | step3c_kg | `"step3c_kg"` | |
| | step4_rank | `"step4_rank"` | |
| | step5_refine | `"step5_refine"` | |
| | step6_validate | `"step6_validate"` | |
| | step7_diagnose | `"step7_diagnose"` | |
| `resolve_batch_key` | default | `"Orig.ident"` | 批次列默认名 |
| `resolve_batch_key` | 备选 | `"Dataset"`、`"sample"`、`"batch"`、`"Libraries"` | 自动识别顺序（仅当 `--batch-key` 未指定时） |
| `check_positive` 校验 | kind | `"int"` 或 `"float"` | 校验某 arg > 0；用于 `cmd_run` 前置 |
| `parse_float_list` | 分隔符 | `,`（逗号） | `"0.4,0.6,0.8"` → `[0.4, 0.6, 0.8]` |

### 4.2 step1_prepare.py

**3 个子命令：`metrics` / `run` / `recluster`**

#### `qc` 子参数（`add_qc_args` 注入到三个子命令）：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--organ` | str | `"root"` | 组织（透传到产物元数据） |
| `--batch-key` | str | `"Orig.ident"` | 批次列名（用于 `batch_mixing` / `Moran's I`） |
| `--mt-pattern` | str (regex) | `"^(ATMG|MT-)"` | 线粒体基因前缀正则 |
| `--cp-pattern` | str (regex) | `"^ATCG"` | 叶绿体基因前缀正则（植物特化） |
| `--seed` | int | `0` | 随机种子（Leiden + UMAP） |

#### `run` 独有参数（在 qc 之上）：

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--min-genes` | int | `300` | 细胞最小检测基因数 |
| `--max-mt-pct` | float | `15.0` | 线粒体占比上限 (%) |
| `--max-chloroplast-pct` | float | `15.0` | 叶绿体占比上限 (%)（植物特化） |
| `--min-cells` | int | `3` | 基因最小表达细胞数 |
| `--expected-doublet-rate` | float | `0.06` | scrublet 期望双峰率 |
| `--target-sum` | float | `1e4` | 归一化 target_sum |
| `--n-top-genes` | int | `2000` | HVG 数量 |
| `--hvg-batch-key` | str | `None` | 分批选 HVG 的批次列（可选） |
| `--n-comps` | int | `50` | PCA 主成分数（实际运行 `min(n_comps, n_obs, n_vars)`） |
| `--n-neighbors` | int | `15` | kNN 邻居数 |
| `--n-pcs` | int | `30` | kNN 使用的 PC 数 |
| `--resolution-list` | str (csv) | `"0.4,0.6,0.8,1.0,1.2"` | Leiden 分辨率列表（逗号分隔） |
| `--target-resolution` | str | `None` | 选中的分辨率（缺省自动拐点） |

`run` 内嵌校验（`check_positive`）：

- 必须为正整数：`min_genes`、`min_cells`、`n_top_genes`、`n_comps`、`n_neighbors`、`n_pcs`
- 必须为正浮点：`expected_doublet_rate`

#### `recluster` 独有参数（在 qc 之上）：

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--resolution-list` | str (csv) | `"0.4,0.6,0.8,1.0,1.2"` | Leiden 分辨率列表 |
| `--target-resolution` | str | `None` | 选中的分辨率（缺省自动拐点） |
| `--n-neighbors` | int | `15` | kNN 邻居数 |
| `--n-pcs` | int | `30` | kNN 使用的 PC 数 |

#### 隐式硬编码常量（在 `op_*` 函数内）

| 项 | 值 | 含义 |
|---|---|---|
| 聚类质量 silhouette 抽样上限 | `max_silhouette_samples = 10000` | 簇数过多时降采样 |
| UMAP trustworthiness 抽样上限 | `5000` | 大数据降采样 |
| UMAP `n_neighbors` (trustworthiness) | `min(15, len(idx)-1)` | |
| UMAP `n_knn` | `min(16, len(idx)-1)` | continuity 计算 |
| `n_overlapping_clusters_umap` 判据 | 两簇在 UMAP 上 bounding box 都重叠 | |
| scaling | `max_value=10` | `sc.pp.scale` |
| HVG `flavor` | `"seurat"` | `sc.pp.highly_variable_genes` |
| Leiden `flavor` | `"igraph"` | `sc.tl.leiden` |
| Scrublet fallback | `error` 写日志但保留全部细胞 | |
| 双峰判据 | `bimodality_coefficient > 0.555` | 触发 valley detection |

### 4.3 step2_markers.py

**1 个子命令：`run`**

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--n-genes` | int | `500` | 每簇 DE top-N 基因 |
| `--min-pct1` | float | `0.5` | marker 最小 pct1 |
| `--max-pct1` | float | `0.9` | marker 最大 pct1（排除管家基因） |
| `--min-pct1-pct2` | float | `0.25` | 最小 pct1-pct2 特异性 |
| `--top-n` | int | `30` | 每簇最终保留 marker 数 |
| `--rare-threshold` | float | `0.05` | 稀有簇判定阈值（细胞比例） |
| `--use-pseudobulk-for-rare` | flag | `False` | 稀有簇切换 pseudobulk DE |
| `--pseudobulk-min-samples` | int | `2` | pseudobulk 每组的样本数下限 |
| `--batch-key` | str | `"Orig.ident"` | 样本列名（pseudobulk 聚合用） |

校验：

- 必须为正整数：`n_genes`、`top_n`、`pseudobulk_min_samples`
- 必须为正浮点：`rare_threshold`
- 范围约束：`0 ≤ min_pct1 < max_pct1 ≤ 1`；`0 ≤ min_pct1_pct2 ≤ 1`

#### 内部硬编码

| 项 | 值 | 含义 |
|---|---|---|
| DE method | `"wilcoxon"` | `sc.tl.rank_genes_groups` |
| `min(cluster_size)` 才能 DE | `2` | singleton 簇跳过（无 DE 可能） |
| BH-FDR 阈值计数 | `0.05 / 0.01 / 0.001` | `n_significant` 三档 |
| pseudobulk 聚合方式 | log1p-CPM（counts→CPM×1e6→log1p） | |
| `multipletests(method="fdr_bh")` | 是 | pvalue 校正 |
| top-N pseudobulk 输出 | `min(500, n_genes)` | |

### 4.4 step3c_kg.py

**2 个子命令：`query` / `test-connection`**

#### `query`

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--organ` | str | **必填**（无默认值；不提供则 fail-fast） | 目标 organ（如 `root` / `brain` / `leaf`） |
| `--species` | str | `None` | 物种（信息性，对应 `g.Species`） |
| `--species-type` | str | `"Plant"` | 物种类型过滤（对应 `g.Species_type`） |
| `--min-confidence` | float | `0.0` | 关系置信度下限（`r.relation_confidence >= $min_conf`） |
| `--strict-organ` | flag | `False` | 严格按 organ 过滤命中 |
| `--max-ancestor-hops` | int | `3` | ontology_relation 祖先最大跳数；`<=0` 跳过 hierarchy 查询 |

> step3c_kg **不做基因 ID 映射**。`adata.var_names` 原样查 KG。TAIR locus → symbol 等 ID 转换需在进入 pipeline 前完成（用户责任）。

#### `test-connection`

无独有参数；只调用 `op_connect` 报告 provenance。

#### 内部硬编码

| 项 | 值 | 含义 |
|---|---|---|
| `ORGAN_STATUS_PRIORITY` | `root:0, partial:1, unknown:2, mismatch:3` | 候选 organ 排序 |
| `_organ_status` target 标准化 | `strip().title()` | |
| query batching | 每 500 个 query name 一批 | 避免 Cypher 单查询过长 |
| Cypher 缺置信度处理 | 缺失视为 `1.0`（无约束） | |
| max-ancestor-hops 内部归一化 | `max(int(max_hops), 1)` | `<=0` 时跳过整个 `query_hierarchy` op |
| `kg_version` 来源 | Neo4j Server (`dbms.components()`) | 代理指标 |

### 4.5 step4_judge.py

**1 个子命令：`run`**

无独有 CLI 参数；所有逻辑来自 `kg_hits.json`。

#### 内部硬编码

| 项 | 值 | 含义 |
|---|---|---|
| `_cluster_sort_key` | 纯数字簇按 int 排，否则 lex | 簇排序 |
| 候选精简数 | top-15（不带 markers） | `candidates` 字段 |
| 决策视图 candidates | 前 15 | `_cluster_decision_view` |
| 决策视图 supporting_markers | 前 10 | `_cluster_decision_view` slim |

### 4.6 step5_refine.py

**1 个子命令：`run`**

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--subcluster-resolution` | float | `0.5` | 子聚类分辨率 |
| `--min-cells` | int | `100` | 可细分的最小父簇细胞数（SOP-5A） |
| `--subcluster-n-pcs` | int | `15` | 子聚类 PCA 主成分数 |
| `--subcluster-n-neighbors` | int | `15` | 子聚类 kNN 邻居数 |
| `--sub-de-n-genes` | int | `50` | 子簇 DE top-N |
| `--sub-top-n` | int | `10` | 每子簇保留 marker 数 |
| `--min-pct1` | float | `0.5` | 子簇 marker 最小 pct1 |
| `--max-pct1` | float | `0.9` | 子簇 marker 最大 pct1 |
| `--min-pct1-pct2` | float | `0.25` | 子簇最小 pct1-pct2 |

校验：

- 必须为正整数：`min_cells`、`subcluster_n_pcs`、`subcluster_n_neighbors`、`sub_de_n_genes`、`sub_top_n`
- 范围约束：`0 ≤ min_pct1 < max_pct1 ≤ 1`；`0 ≤ min_pct1_pct2 ≤ 1`
- 触发细化：`first_count <= second_count`（definitional trigger）
- 跳过细化：父簇 `n_cells < min_cells`；子聚类只产生 1 个簇（homogeneous parent）

#### 内部硬编码

| 项 | 值 | 含义 |
|---|---|---|
| Leiden `flavor` | `"igraph"` | `_subcluster_leiden` |
| 子 PCA solver | `arpack` if `n_comp < min(n_obs, n_vars)` else `full` | |
| 灰区定义 | `specificity ∈ [0.1, min_diff)` | |
| 未标注簇集合 | `annotations[c].status == "no_candidates"` | 用于 `unknown_overlap` |

### 4.7 step6_validate.py

**2 个子命令：`run` / `report`**

#### `run`

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--top-n-markers` | int | `3` | 每簇验证的 top marker 数 |
| `--backed` | flag | `False` | backed 模式按列读 raw.X |
| `--no-violin` | flag | `False` | 跳过小提琴图生成 |

校验：必须为正整数 `top_n_markers`

#### `report`

无独有参数。

#### 内部硬编码

| 项 | 值 | 含义 |
|---|---|---|
| `_confidence_evidence` | high/medium/low 三档 | "high" 需 `first_count>15 and count_ratio>=2`；"medium" 需 `count_ratio>=1.5`；否则 low |
| `report.md` 章节顺序 | 簇按数字/字典序排序 | |
| `_meta.organ` 来源 | `qc_metrics.json["organ"]`（由 step1 透传），缺省 fallback 为 `"root"` | |
| `_meta.confidence_rule` | `"evidence-based: high if first_count>15 and count_ratio>=2; medium if count_ratio>=1.5; else low (LLM may override)"` | |
| fallback 标记完整度 | `refined_annotations` 的簇必须在 h5ad.leiden 中（H6 guard） | 否则 fail |

### 4.8 step7_diagnose.py

**1 个子命令：`run`**

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--batch-key` | str | `None` | 批次列名（缺省走 `resolve_batch_key` 自动识别） |

#### 内部硬编码

| 项 | 值 | 含义 |
|---|---|---|
| `_REQUIRED_META` | `["kg_source", "kg_version", "organ", "annotation_date", "scanpy_version"]` | `_meta` 必填字段 |
| h5ad 加载数 | **0** | 仅读 `obs_snapshot.csv` + 各 step JSON |

### 4.9 write_judgment.py

**3 个子命令：`add` / `session-start` / `session-end`**

#### `add`（judgment 留痕）

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--project-dir` | str | `"output"` | 项目目录 |
| `--decision-point` | str (enum) | **必填** | 13 个决策点之一（见 §4.10） |
| `--decision` | str (enum) | **必填** | 该决策点的 decision 枚举值 |
| `--scope-type` | str (enum) | **必填** | `session` 或 `cluster`（由 `REQUIRED_SCOPE` 决定哪个对哪个决策点合法） |
| `--cluster-id` | str | `None` | `scope-type=cluster` 时必填 |
| `--run-ref` | str (regex) | **必填** | 形如 `{step}.{op}#{attempt}`，正则 `^[A-Za-z0-9_]+\.[A-Za-z0-9_]+#[1-9][0-9]*$` |
| `--inputs` | str (JSON) | **必填** | JSON 数组 `[{path, value}, ...]`；每项必须含 `path` + `value` |
| `--confidence` | str (enum) | **必填** | `high` / `medium` / `low` |
| `--action` | str | **必填** | 后续动作指令 |
| `--reasoning` | str | **必填** | 自然语言推理链（非空） |

#### `session-start`

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--project-dir` | str | `"output"` | 项目目录 |
| `--session-id` | str | **必填** | 会话标识（如 `sess-20260811-SRP171040`） |
| `--dataset` | str (JSON) | **必填** | JSON 对象：`{id, h5ad_path, n_cells_raw, n_genes_raw, organism, organ, ...}` |

#### `session-end`

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--project-dir` | str | `"output"` | 项目目录 |
| `--final-summary` | str (JSON) | **必填** | JSON 对象：`{n_clusters, n_unknown, unknown_rate, n_unique_labels, run_count, judgment_count}` |

#### 内部硬编码

| 项 | 值 | 含义 |
|---|---|---|
| `DECISION_ENUMS`（13 项） | 见 §4.10 | decision 枚举词汇表 |
| `VALID_SCOPES` | `{"session", "cluster"}` | |
| `VALID_CONFIDENCE` | `{"high", "medium", "low"}` | |
| `RUN_ID_RE` | `^[A-Za-z0-9_]+\.[A-Za-z0-9_]+#[1-9][0-9]*$` | run_ref 格式 |

### 4.10 trajectory_schema.py（硬编码 schema）

`REQUIRED_SCOPE`：决策点 → 必需 scope 类型（共 13 项，9 session + 4 cluster）：

| decision_point | required scope |
|---|---|
| `qc_threshold` | session |
| `resolution_select` | session |
| `clustering_quality` | session |
| `batch_effect` | session |
| `de_method` | session |
| `marker_quality` | session |
| `kg_match` | session |
| `unknown_cluster` | session |
| `global_quality` | session |
| `candidate_gap` | cluster |
| `candidate_disambiguate` | cluster |
| `refine_effect` | cluster |
| `label_confirm` | cluster |

`DECISION_ENUMS`（13 项决策点对应合法 decision 集合，hardcoded in `write_judgment.py`）：

| decision_point | 合法 decision 集合 |
|---|---|
| `qc_threshold` | `{threshold_set, threshold_default}` |
| `resolution_select` | `{resolution_chosen}` |
| `clustering_quality` | `{clustering_accept, clustering_adjust}` |
| `batch_effect` | `{batch_effect, condition_specific, well_mixed}` |
| `de_method` | `{wilcoxon, pseudobulk_all, pseudobulk_rare}` |
| `marker_quality` | `{markers_accept, markers_adjust_filter, markers_fail}` |
| `kg_match` | `{id_match_ok, id_mismatch_gene_key, id_mismatch_organ}` |
| `candidate_gap` | `{first_decisive, ambiguous_parent_child, ambiguous_synonym, ambiguous_true, unknown}` |
| `candidate_disambiguate` | `{ambiguous_parent_child, ambiguous_synonym, ambiguous_true}` |
| `refine_effect` | `{refine_effective, refine_ineffective, refine_skipped, refine_autocorr_low}` |
| `unknown_cluster` | `{single_unknown_type, multiple_unknown_types}` |
| `label_confirm` | `{label_confirmed, label_downgraded, label_unknown}` |
| `global_quality` | `{quality_good, quality_acceptable, quality_poor}` |

---

## 5. scripts/ — eval / 标注生成工具

### 5.1 validate_log.py

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `run_log` | str (positional) | **必填** | `run_log.jsonl` 路径 |
| `--project-dir` | str | `None` | 若给出，`run_log` 参数视为 project-dir 内的文件名 |
| `--mode` | enum | `"auto"` | 校验形态：`auto` / `e2e` / `mini` |

`mode` 取值：

| mode | 含义 |
|---|---|
| `auto` | 按记录推断（e2e=有 exec / mini=仅 judgment） |
| `e2e` | 完整轨迹校验 |
| `mini` | 单决策点会话（只验 judgment） |

校验失败返回码：

- `0` = 无问题
- `1` = 有错误（warning 仍为 0）

### 5.2 evaluate_annotations.py

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `project_dir` | str (positional) | **必填** | 跑测产物目录 |
| `--gt-csv` | str | `"experiments/gt_cells.csv"` | 真值 CSV |
| `--label-map` | str | `"experiments/label_map.json"` | 标签映射 JSON |
| `--out` | str | `None` | 评估报告 JSON 输出路径；缺省写入 `<project_dir>/evaluation_report.json` |

### 5.3 build_gt_cells.py

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--index-csv` | str | `"dataset/index/SRP171040.h5ad.csv"` | 输入真值 CSV |
| `--h5ad` | str | `"dataset/h5ad/SRP171040.h5ad"` | 输入 h5ad（用于对齐校验） |
| `--out` | str | `"experiments/gt_cells.csv"` | 输出真值 CSV |
| `--expected-n` | int | `33956` | 期望条码数（不匹配则不写产物） |

### 5.4 build_label_map.py

起稿 **GT → Ontology 钉表**，不再生成 predicted×true pair 表。

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--true-labels` | list of str | 拟南芥 12 类 | GT 字符串列表 |
| `--dataset-id` | str | `"SRP171040"` | 写入 `_meta.dataset` |
| `--out` | str | `"experiments/gt_ontology.json"` | 钉表 JSON（`verified: false`） |
| `--aliases-out` | str | `None` | 可选：全球别名初稿 |
| `--uri` | str | `None` → `NEO4J_URI` → `"bolt://localhost:7687"` | Neo4j URI |
| `--user` | str | `None` → `NEO4J_USER` → `"neo4j"` | Neo4j 用户 |
| `--password` | str | `None` → `NEO4J_PASSWORD` | Neo4j 密码 |

模块级硬编码：`TRUE_LABELS` / `PIN_HINTS` / `ALIAS_SEED`（见源码）。`Unknown` 不钉。

---

## 6. experiments/ — B1 实验框架

### 6.1 scripted_driver.py

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--project-dir` | str | **必填** | 工作目录（包含 `run_log.jsonl` + per-step JSONs） |
| `--raw` | str | `None` | 原始 h5ad 路径；缺省时 step1 会失败（仅当产物已存在时可省略） |
| `--skip` | list of str | `()` | 跳过的子命令标记，如 `--skip "step1_prepare.run"` |

### 6.2 judges/default_judge.py

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--project-dir` | str | **必填** | 工作目录 |

无 CLI 阈值；策略常量见 §8。

### 6.3 judges/rule_judge.py

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--project-dir` | str | **必填** | 工作目录 |

运行时自动注入 + 恢复 `PROJECT_DIR` 环境变量（透传给子进程）。

### 6.4 evaluate_cell_level.py

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--arms` | list of str | **必填**（`nargs="+"`） | 格式 `<name>=<project_dir>`（可多个），如 `arm1=output/B1/arm1_default` |
| `--gt-csv` | str | `"experiments/gt_cells.csv"` | 真值 CSV |
| `--gt-ontology` | str | `"experiments/gt_ontology.json"` | GT 字符串 → Ontology.Name 钉表 |
| `--aliases` | str | `"experiments/kg_term_aliases.json"` | 预测词变体 → 规范 Ontology.Name |
| `--max-ancestor-hops` | int | `3` | 图谱祖先查询跳数 |
| `--label-map` | str | 停用 | 传入则非零退出 |
| `--out` | str | `None` | 评估报告 JSON 路径 |

图谱不可达时只接受别名 exact/synonym，报告 `kg_hierarchy: skipped`。

### 6.5 bootstrap_test.py

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--per-cell` | str | **必填** | `evaluation_report.per_cell.json`（由 `evaluate_cell_level.py` 产出） |
| `--arms` | list of str | **必填**（`nargs="+"`） | 要比较的 arm 名称（按顺序生成 pairwise） |
| `--n-boot` | int | `1000` | bootstrap 重采样数 |
| `--seed` | int | `0` | 随机种子 |
| `--label-map` | str | 忽略 | 兼容旧调用；计分用 per_cell.relation |
| `--out` | str | **必填** | 输出路径 |

### 6.6 analyze_traps.py

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `--eval-report` | str | **必填** | `evaluation_report.json`（由 `evaluate_cell_level` 产出） |
| `--per-cell` | str | **必填** | `evaluation_report.per_cell.json` |
| `--out` | str | **必填** | 输出路径 |

---

## 7. dataset/init.py

无 argparse；无 CLI。模块级常量 / 行为：

| 项 | 值 | 含义 |
|---|---|---|
| `read_h5ad()` 重写 raw 触发条件 | `raw_available and "_index" in adata.raw.var.columns` | 老版本 scanpy 补救 |
| `read_h5ad()` var_names 校验 | 尝试 `.astype(float)`；若 raise `ValueError` 则通过 | 必须是基因符号而非纯数字 |
| 顶层脚本行为 | 遍历 `h5ad/*.h5ad` → 写出 `index/<basename>.csv`（仅保留 `Seurat_clusters` + `Celltype`）→ 删回 obs → 重新写 h5ad | 一次性数据预处理 |

---

## 8. B1 决策枚举（arm-default 表）

`experiments/judges/default_judge.py` 第 35-50 行：固定全 accept 策略。

| decision_point | decision | 备注 |
|---|---|---|
| `qc_threshold` | `threshold_default` | 不读 metrics，用脚本阈值 |
| `resolution_select` | `resolution_chosen` | 选中等分辨率 |
| `clustering_quality` | `clustering_accept` | 全 accept |
| `batch_effect` | `well_mixed` | 不查条件注释 |
| `de_method` | `wilcoxon` | 用默认 wilcoxon |
| `marker_quality` | `markers_accept` | 全 accept |
| `kg_match` | `id_match_ok` | 不查 ID 系统 |
| `unknown_cluster` | `multiple_unknown_types` | 不细分 |
| `global_quality` | `quality_good` | 不总检 |
| `candidate_gap` | `first_decisive` | 永远 first_decisive（不看 gap） |
| `candidate_disambiguate` | `ambiguous_true` | ambiguous true |
| `refine_effect` | `refine_skipped` | 不细分 |
| `label_confirm` | `label_confirmed` | 永远 confirmed，confidence="high" |

`experiments/judges/rule_judge.py` 第 219 行附近的 oracle 阈值：

| 决策 | 触发条件 |
|---|---|
| `qc_threshold` | `p90_cp > 0.05` → `threshold_set`，否则 `threshold_default`（人工定案 oracle） |

其余 12 项策略见 `rule_judge.py` 完整源码（约 314 行）。

---

## 附录 A：路径 / 文件命名约定速查

| 类型 | 路径 | 来源 |
|---|---|---|
| 轨迹 | `<project-dir>/run_log.jsonl` | `common.run_log_path` |
| 会话记录 | `<project-dir>/conversation.jsonl` | `annot_harness/conversation.py` |
| 笔记本 | `<project-dir>/notes.jsonl` 或 `$RAG_NOTES_DIR` | `annot_harness/session.py:98` |
| Chroma 索引 | `<notes_path_parent>/notes_chroma/` | `annot_harness/notebook.py:_chroma_dir_for` |
| step1 产物 | `<project-dir>/step1_prepare/{processed.h5ad, obs_snapshot.csv, var_snapshot.csv, qc_metrics.json, qc_distributions.png}` | `op_write_output` |
| step2 产物 | `<project-dir>/step2_markers/{markers.csv, markers.json}` | `op_write_markers` |
| step3 产物 | `<project-dir>/step3c_kg/{kg_hits.json, kg_source.txt}` | `op_write_hits` |
| step4 产物 | `<project-dir>/step4_judge/annotations.json` | `op_write_annotations` |
| step5 产物 | `<project-dir>/step5_refine/refined_annotations.json` | `op_write_refined` |
| step6 产物 | `<project-dir>/step6_validate/{final_annotations.json, report.md, figures/cluster_*_markers.png}` | `op_write_report` + `op_write_final` + `op_violin_plot` |
| step7 产物 | `<project-dir>/step7_diagnose/{step7_diagnose.json, report.md}` | `cmd_run` |

## 附录 B：默认值速查（最常被改的几项）

| 配置项 | 默认值 | 出现在 |
|---|---|---|
| `--project-dir` | `"output"` | `common.add_common_args`（所有 step + write_judgment） |
| `--batch-key` | `"Orig.ident"` | step1 / step2 / step7（可被覆盖） |
| `--organ` | `"root"` (step1) / **必填** (step3) | step1 与 step3 不一致；step1 默认值会写入 `qc_metrics.json["organ"]`，下游 step6 也从此处读 |
| `--resolution-list` | `"0.4,0.6,0.8,1.0,1.2"` | step1 run + recluster |
| `--seed` | `0` | step1 |
| `--max-mt-pct` / `--max-chloroplast-pct` | `15.0` / `15.0` | step1 run |
| `--min-genes` | `300` | step1 run |
| `--top-n-markers` | `3` | step6 run |
| `--n-top-genes` (HVG) | `2000` | step1 run |
| `--n-comps` (PCA) | `50` | step1 run |
| `--n-pcs` (kNN) | `30` | step1 run / `15` | step5 subcluster |
| `--n-neighbors` (kNN) | `15` | step1 run / step5 subcluster |
| `--expected-doublet-rate` | `0.06` | step1 run |
| `--target-sum` | `1e4` | step1 run |
| `--min-pct1` / `--max-pct1` / `--min-pct1-pct2` | `0.5` / `0.9` / `0.25` | step2 / step5 (subcluster) |
| `--top-n` markers | `30` (step2) / `10` (step5 sub) | step2 / step5 |
| `--rare-threshold` | `0.05` | step2 |
| `--min-confidence` (KG) | `0.0` | step3 |
| `--max-ancestor-hops` | `3` | step3 / build_label_map |
| `--species-type` | `"Plant"` | step3 |
| `--subcluster-resolution` | `0.5` | step5 |
| `--min-cells` (subcluster gate) | `100` | step5 |
| DEFAULT_MODEL | `"gpt-4o-mini"` | harness |
| DEFAULT_EMBEDDING_MODEL | `"all-MiniLM-L6-v2"` | annot_harness/notebook |
| subprocess timeout (skill tools) | `3600` 秒 | annot_harness/dispatcher |
| `--dump-schema` timeout | `120` 秒 | annot_harness/skill_loader |
| OPENAI_MAX_RETRIES | `6` | annot_harness/session |
| BM25 `k1` / `b` | `1.5` / `0.75` | annot_harness/notebook |
| NEO4J_URI 默认 | `"bolt://localhost:7687"` | common / build_label_map（与 `.env` 实际值 `neo4j://10.224.28.66:7688` 不一致；`.env` 优先） |
| NEO4J_USER 默认 | `"neo4j"` | common / build_label_map |
| EXPECTED_N（gt cells） | `33956` | build_gt_cells |
| n_boot（bootstrap） | `1000` | bootstrap_test |
| bootstrap seed | `0` | bootstrap_test |

---

## 附录 C：未通过任何接口暴露的内部常量（仅供参考）

下列值仅在函数体内使用，未通过 CLI/env/常量暴露，文档列出方便调试：

| 模块 | 常量 | 值 | 含义 |
|---|---|---|---|
| `common.py` | `n_bins` in `describe_distribution` | `20` | 直方图 bin 数 |
| `common.py` | `pct_keys` | `[1, 5, 10, 25, 50, 75, 90, 95, 99]` | describe_distribution 输出百分位 |
| `common.py` | `chi2_contingency` 触发 | `len(contingency) > 1 and len(batches) > 1` | batch_mixing 独立性检验 |
| `common.py` | `_modularity` m 阈值 | `m <= 0` 返回 `None` | cluster_quality |
| `common.py` | silhouette degradation 阈值 | `n_clusters < 2 or n < 2` | cluster_quality |
| `common.py` | persistence 阈值 | `Jaccard >= 0.7` 算稳定 | resolution_stability |
| `common.py` | batch_mixing entropy base | `2.0` | shannon |
| `common.py` | fold_change eps | `1e-6` | effect_size |
| `step1_prepare.py` | scrublet fallback | `error → 保留全部细胞` | |
| `step1_prepare.py` | `op_normalize` target_sum | 默认 `1e4` | |
| `step1_prepare.py` | Leiden `random_state` | `args.seed` (=0) | |
| `step1_prepare.py` | UMAP `random_state` | `args.seed` (=0) | |
| `step1_prepare.py` | scaling `max_value` | `10` | |
| `step1_prepare.py` | 拐点判据（自动 choose_resolution） | `max(drops) > 0` → 选最大 drop 之后；否则 middle | |
| `step1_prepare.py` | 簇大小分布 gini | 加在 `total_counts` 之后 | |
| `step2_markers.py` | DE `use_raw` | `True` | 必须 raw 存在 |
| `step2_markers.py` | `min(cluster_size)` 才能 DE | `2` | singleton 跳过 |
| `step2_markers.py` | `multipletests(method="fdr_bh")` | 是 | |
| `step2_markers.py` | DE 缺失 AUC 处理 | `None` | |
| `step2_markers.py` | 灰区定义 | `spec ∈ [0.1, min_diff)` | |
| `step3c_kg.py` | query batching | `500` | |
| `step3c_kg.py` | 缺 confidence 处理 | 视为 `1.0` | |
| `step3c_kg.py` | `--organ` 标准化 | `strip().title()` | `_organ_status` |
| `step4_judge.py` | candidate 精简 | top-15 不带 markers | |
| `step5_refine.py` | sub PCA solver | `arpack if < min else full` | |
| `step6_validate.py` | `_confidence_evidence` 三档 | high/medium/low | |
| `step6_validate.py` | fallback organ | `"root"`（仅当 `qc_metrics.json` 缺失时） | |
| `experiments/judges/rule_judge.py` | `p90_cp > 0.05` oracle | `qc_threshold → threshold_set` | |
| `experiments/evaluate_cell_level.py` | confidence 加权策略 | 见源码 | |

---

## 附录 D：交叉引用

- 设计文档：`design/tool_design.md`（§10 CLI flags > env > defaults）、`design/loop_design.md`（§5 loop 结构）、`design/trajectory_design.md`（§3.1 REQUIRED_SCOPE 由来）
- AGENTS.md 第一节的 "Repo status" 与 "Gitignore gotchas" 给出与本文档交叉的环境信息（数据集路径 / gitignore 行为）
- `annot_harness/loop.py` 的 `NOTEBOOK_TOOL_SCHEMAS` 是循环层工具 schema 唯一来源
- `skills/cell-annotation/scripts/common.py` 是所有 step 脚本共享的 argparse 扩展点