# 决策提示设计：阈值私有化与智能体—工具职责划分

> 状态：设计定稿（合并版，取代此前的 `decision_frame_design.md` 与 `agent_tool_division_of_labor.md`）
> 适用对象：`annot_harness`（循环层）与 `skills/cell-annotation`（流水线层）
> 关联文档：`design/loop_design.md`、`design/trajectory_design.md`、`design/tool_design.md`、`skills/cell-annotation/SKILL.md`

---

## 1. 问题陈述

### 1.1 三处待解决的缺陷

**缺陷一：阈值暴露在提示词中，判断层可被复述而不可被检验。**
现行 `SKILL.md` 第 3 节以自然语言向模型描述每个决策点"看什么""判断要点"，阈值与参考区间写在参考文档中。模型因此能够直接复述阈值作为自己的判断，评测无法区分"模型读懂了指标"与"模型背下了阈值"。这是臂③在拟南芥数据集 4 个易错决策点上与臂②决策分布完全一致（R-trap 决策多样性 0/4）的直接成因。

**缺陷二：判断层的动作未被约束为有限集合。**
`decision` 枚举虽已在 `write_judgment__add` 中校验，但该校验只作用于轨迹写入环节，不构成对流程行为的约束：模型可以在枚举内表态，也可以在编排层采取完全不同的动作，二者之间无一致性检查。

**缺陷三：三臂消融中臂②与臂①输出恒等，无法分离"规则"与"模型"的净贡献。**
臂②的规则判定当前只下调置信度而不改变标签与流程走向，导致 Δ(②−①) ≡ 0。实验因此退化为两档对照。

### 1.2 实测依据

下表为两个数据集三臂的轨迹统计（`output/{B1,B1_PRJNA935359}/{arm}/run_log.jsonl`）：

| 数据集 | 臂 | `exec` 记录 | `judgment` 记录 | 比值 | 触发的簇级决策点 |
|---|---|---|---|---|---|
| 拟南芥根（34 簇） | ① 默认 | 45 | 111 | 2.47 | candidate_gap 34 / disambiguate 34 / label_confirm 34 |
| 拟南芥根 | ② 规则 | 45 | 111 | 2.47 | 同① |
| 拟南芥根 | ③ 模型 | 59 | 92 | 1.56 | candidate_gap 34 / disambiguate 8 / **refine_effect 7** / label_confirm 34 |
| 高粱根（29 簇） | ① 默认 | 52 | 97 | 1.87 | candidate_gap 29 / disambiguate 29 / label_confirm 29 |
| 高粱根 | ② 规则 | 52 | 97 | 1.87 | 同① |
| 高粱根 | ③ 模型 | 74 | 91 | 1.23 | candidate_gap 26 / disambiguate 18 / **refine_effect 11** / label_confirm 26 |

三点观察：

1. **`refine_effect` 仅在臂③出现**（拟南芥 7 条、高粱 11 条），臂①臂②为零。判断层确实改变了流程走向，而不只是改变标签——这是判断层价值的一处正向证据。
2. **表态次数是操作次数的 1.2–2.5 倍**，且簇级决策点要求逐簇写一条（`scope.type=cluster`，合并会被工具拒绝）。模型有大量轮次耗在逐簇确认上，这正是"模型看起来只在打勾"的实证来源。比值偏高应作为设计约束加以控制。
3. 全部 14 个决策点中，10 个 session 级决策点各只产生 1 条记录，而 4 个簇级决策点产生了 90% 以上的记录量。**决策点的密度分布不均衡，是工具调用预算被表态挤占的根因。**

---

## 2. 智能体与命令行工具集的职责划分

### 2.1 归责判据

> 一项职责能否在**不理解生物学语义**的前提下，由**确定性程序**完成？
> 能，归工具；不能，归模型。

**工具回答"是什么"，模型回答"意味着什么"与"接下来做什么"。**

> **工具集的构成（三层）**：
>
> 1. **原子操作层**：每个原子操作对应具体业务中的一个步骤（如细胞注释中的"获取标志基因"），其返回包括两部分——**该步骤的观测指标**，以及**这些指标的解读方法**（该指标度量什么、取值如何阅读、常见取值意味着什么）。原子操作回答"这一步算出了什么、这些数该怎么读"，**不给处置建议**。
> 2. **工具层**：**工具负责组织本次调用所涉原子操作的返回**，并在其上做两件事——**依据本领域阈值表检测当前指标所处的区间**，以及**把该区间预定义的提示**（区间状态、定性分档、建议动作、动作后果、所需参数）封装进返回包络。工具回答"按本领域文献，处在这一区间时通常建议怎么做"，**用以指导模型的下一步**。
> 3. **模型层**：在提示所指的动作集内取哪一项，由模型在 L5 表态。**工具不作判定。**
>
> 指标与解读方法随原子操作走，阈值与提示随领域走：领域更换时替换的是阈值表与提示文案，原子操作的实现与工具接口形态不变。

### 2.2 七层职责表

| 层 | 职责 | 承担者 | 细胞注释场景中的实例 |
|---|---|---|---|
| L0 | 计算与测量 | **工具独占** | 质控统计、双细胞检测、聚类、差异表达、本体评分、知识图谱检索 |
| L1 | 流程编排 | **模型独占** | 下一步调用哪个原子操作、是否补做细分与复核、何时终止 |
| L2 | 参数化 | **模型主导，工具校验** | 分辨率、物种、观测列名、DE 方法、过滤阈值的具体取值 |
| L3 | 阈值检测与提示封装 | **工具独占** | 检测指标落入哪个已标定区间，按该区间预定义内容封装提示与动作集；**只报区间，不给结论** → 产出 `decision_prompt.status` |
| L4 | 语义解释与命名 | **模型独占** | 判定第 7 簇为根毛细胞，并写明支持证据与反证 |
| L5 | 决策点表态 | **模型表态**，工具提供动作集与后果 | 否决"放宽质控阈值"，并引用观测项说明理由 |
| L6 | 综合与交付 | **模型独占** | 低置信标记、复核请求、结论表述与不确定性说明 |

工具承担 L0 与 L3。L1、L2、L4、L5、L6 由模型完成。L3 报出当前区间与该区间预定义的建议；采纳哪一项由模型在 L5 表态。`status` 表示阈值覆盖状况。

### 2.3 决策提示的位置

- 工具与模型之间的常态接口是 `{"status":"ok","data":{...}}`（`common.py:emit_ok`），`data` 中给出全部指标测量结果；
- `decision_prompt` 是可选附加字段，仅在 L3 被触发时与返回包络并列出现；
- 未被触发的调用不返回决策提示，模型按 L4 解释、按 L1 编排下一步。

决策提示在决策点处要求模型表态，流程编排仍由模型完成。

### 2.4 类比：编译器警告

| 编译器 | 本设计 |
|---|---|
| 编译与优化由编译器完成 | 计算与阈值判定由工具完成（L0/L3）|
| 编译产出目标代码 | 工具产出 `data` 与指标 |
| 在特定位置发出警告，要求作者显式确认 | 在决策点触发时附加决策提示，要求模型表态（L5）|
| 写程序的仍是作者 | 编排、参数化、命名、交付仍在模型（L1/L2/L4/L6）|

### 2.5 四条硬约束

**约束一：决策点必须稀疏，簇级决策点支持批量表态。**
按 1.2 节的实测，表态/操作比值在 1.2–2.5 之间，已属偏高。规则如下：

- 每个阶段至多保留 1–2 个必需决策点；可由确定性条件判定的分支一律下沉到 L3，不占表态预算；
- 簇级决策点支持一次覆盖一组簇：`scope.type = "clusters"`，`scope.cluster_ids = [...]`，并允许附 `exceptions`（例外的簇单独表态）。`REQUIRED_SCOPE` 中 `cluster` 类型相应扩展为 `cluster | clusters`，`write_judgment.py` 与 `scripts/validate_log.py` 同步放宽校验。

**约束二：动作后果必须可参数化。**
`action_consequences` 每一项都要写明"执行该动作需要由调用方给出哪些参数"。否则模型在 L5 选中动作后回到 L2 时无从填参，L5 与 L2 断链。

**约束三：返回内容只陈述区间与预定义建议。** 决策提示写出落在哪个已标定区间，以及该区间预定义的建议动作及其后果。"应当、必须、应立即"只出现在模型的表态记录中。

**约束四：L3 只检测与提示。** 工具给出区间状态、预定义提示与动作后果，不改参数、不改流程。执行经 L1/L2 回到模型。

### 2.6 有穷性

动作仅作用于当前步参数或下一步参考。未经显式授权，不得默认回退，不得重跑整个流程。该上界约束回退范围；模型在给定动作集内如何选择，不受此限。

### 2.7 两条例外

- **检索归工具，消歧归模型。** 工具返回知识图谱候选与评分，不返回排序结论；在多个候选间取舍需要生物学语义，属 L4。
- **越界判定归工具，越界后的处置含权衡。** "是否越界"是确定性的（L3）；"越界后是收紧还是放宽、以精度换召回还是反之"含质量—成本权衡，故工具只给默认动作与各动作后果，选择权留在模型（L5）。

---

## 3. 阈值私有化

### 3.1 原则

> 阈值不进入提示词，只在命令行工具集内部；触发后在返回内容中给出**明确的自然语言提示**，指导模型表态。

四条规定：

1. `observations` 只给该指标已声明的分档（`band`，词表随指标与领域而定，见 3.5）与一句自然语言概括，不返回原始数值，不返回阈值；
2. 参与执行的只有落在 `allowed_actions` 内的动作标记。推理文本只进入轨迹。智能体可以提出集合之外的动作并陈述理由；该提议在执行前被拒绝，不产生副作用，并以 `override_rejected` 记入轨迹；
3. 阈值表按物种分文件存放，带内容哈希版本号，版本随判断记录写入轨迹。修改阈值时不改提示词或技能文档；
4. `allowed_actions` 与 `status` 分开：候选动作集在决策点声明处一次性枚举，不随本次取值收缩，也不因 `status` 裁剪。`status` 只表示推荐项是否存在。

### 3.2 声明式条件表

阈值由数值改为条件表达式，使"是否覆盖全部输入区间"成为可静态检查的性质。此处还须把**候选动作集**与**推荐项**分开声明：前者随决策点一次性枚举，后者由本次命中的条件决定。

```yaml
# skills/cell-annotation/thresholds/Arabidopsis_thaliana.yaml
version: 2026.09.1
species: Arabidopsis_thaliana

# 候选动作集：随决策点一次性枚举，恒定不变，不随本次取值收缩
actions:
  qc_threshold:      [accept_qc, rerun_qc_stricter, relax_qc]
  resolution_select: [accept_resolution, clustering_adjust, halve_resolution]

thresholds:
  qc_threshold:
    - when: "frac_doublets >= 0.08"
      recommend: rerun_qc_stricter        # 仅推荐，不取消其余候选
      hint: "双细胞比例偏高，按本领域文献通常建议收紧过滤后重跑质控"
    - when: "frac_doublets < 0.02"
      recommend: accept_qc
      hint: "双细胞比例处于低位，可按当前参数继续"
    - when: "0.02 <= frac_doublets < 0.08"
      recommend: null                     # 落入无共识区间：候选集照给，推荐项为空
      status: residual
      hint: "双细胞比例处于灰区，是否收紧取决于后续聚类是否出现混合信号簇"
  resolution_select:
    - when: "n_clusters_derivative < 0.15 and adjacent_ari_min >= 0.85"
      recommend: accept_resolution
    - when: "0.15 <= n_clusters_derivative < 0.40"
      recommend: null
      status: residual
      hint: "簇数随分辨率仍在变化，需结合已知细胞类型数判断平台位置"
```

同一决策点的 `actions` 在全部条件项之间共享。条件项只填写 `recommend`，不得在条件项里声明新动作。`recommend: null` 时 `status` 为 `residual`，候选集、各动作后果与所需参数仍全量返回。

未覆盖区间在构建期检出（见 3.4）。现行 `SKILL.md` 只给指标路径与定性要点，模型无法判断所处区间；条件表把这件事前移到构建期。

### 3.3 分物种与版本化

- 文件命名 `<species>.yaml`，未匹配到物种时返回 `status = uncovered`，`band` 一律 `unknown`；
- 版本号与内容哈希写入决策提示的 `threshold_version`，并随 `judgment` 记录入轨迹，保证任何一条历史判断都可复现其判定所依据的阈值版本；
- 阈值的文献出处（`source`）随条件项一并声明，构成"过程知识可溯源"的证据链。

### 3.4 覆盖性静态检查

构建期对每张条件表执行：

1. **区间完备性**：各 `recommended` 与 `residual` 条件的输入域并集是否覆盖该指标的定义域；
2. **互斥性**：条件之间是否存在重叠（重叠时按顺序取首个命中，需显式声明优先级）；
3. **动作合法性**：条件项的 `recommend` 必须取自该决策点声明的候选动作集 `actions`，且任何条件项都不得声明候选集之外的动作；
4. **悬空引用**：条件中引用的指标路径是否存在于 `design/operations_metrics_catalog.md`。
5. **候选集恒定**：同一决策点的全部条件项共享同一份候选动作集，运行期返回的 `allowed_actions` 必须与该声明逐项一致，不得依命中分支裁剪；违者阻断构建。

任一检查失败即阻断构建。这使得"某决策点从未被触发"（1.2 节中 `refine_effect` 在臂①臂②为零的情形）从运行期才发现的问题，前移为构建期可检出的缺陷。

### 3.5 定性分档的声明规范

全领域若共用 `low` / `marginal` / `high` / `unknown`，分档不携带方向：偏差型指标的偏少／偏多无法表达，标称型指标没有顺序。

分档按推荐项映射来划。决策点 d 的阈值表把输入域 X 分成若干条件单元，推荐项映射为 φ: X → A_d ∪ {⊥}。⊥ 表示 residual，即该取值没有共识推荐。分档词表取 φ 的同值类：

> x₁ 与 x₂ 同档 ⟺ φ(x₁) = φ(x₂)

由此得到粒度的两条边界。**下界为动作可分辨性**：导向不同动作的两个取值不得并入同一档，否则模型无从区分。**上界为隐私性**：分档不得细至可由多次调用反推阈值数值。二者之间取最粗的划分。按此定义，分档相对动作集而言不损失任何信息，而数值仍留在工具内部。

每张阈值表用同一套声明格式：`type`、`polarity`、`bands` 枚举，以及 `band → action` 映射。词按场景更换。按指标语义分为三类：

| 类型 | 适用 | 偏好命名 | 例 |
|---|---|---|---|
| 单调型 monotone | 有方向好坏的指标 | 部位 + 程度 | `separation_poor` / `adequate` / `clear` |
| 偏差型 deviation | 与期望值比较多寡的指标 | 部位 + 偏离方向 | `cluster_count_deficit` / `consistent` / `excess` |
| 标称型 nominal | 无顺序的形态或结构 | 部位 + 形态名 | `specificity_singlet` / `codominant` / `dispersed` |

词用领域命名：`doublets_excessive` 自带方向与部位，`high` 不携带这些信息。`summary` 用一句话补上词表表达不了的内容；这句话不能做静态检查。

```yaml
# 随指标声明分档词表（与 3.2 的条件表同文件）
metrics:
  frac_doublets:
    type: monotone            # monotone / deviation / nominal
    polarity: lower_better
    bands: [doublets_acceptable, doublets_borderline, doublets_excessive]
  n_clusters_vs_known:
    type: deviation
    bands: [cluster_count_deficit, cluster_count_consistent, cluster_count_excess]
  marker_specificity_pattern:
    type: nominal
    bands: [specificity_singlet, specificity_codominant, specificity_dispersed]
```

构建期在 3.4 的五项检查之外再增加四条：① 条件表中被引用的每个指标均已声明词表；② 每个条件单元映射到唯一的 band；③ `band → action` 是函数，同一 band 不得映射至两个动作；④ 词表覆盖该指标定义域，未覆盖部分显式落入 `uncovered`。

### 3.6 信息粒度的受控调节

以动作纤维为粒度的基线之外，可按需开启两个可选字段。二者均不改变分档本身，且**默认关闭**。

| 字段 | 取值 | 泄露代价 | 用途 |
|---|---|---|---|
| `margin` | `near` / `clear` | 极低 | 标明本次取值是否贴近档位边界，供模型决定是否否决推荐项 |
| `trend` | `rising` / `stable` / `dropping` | 无（与阈值无关） | 该指标相对上一轮参数的走向，用于判断上一轮调整是否生效；工具需持有跨步状态 |

更细的粒度（如返回相对比值"约为参考值的 1.6 倍"）已接近泄露阈值数值，仅在审计模式下开放，不进入正常运行。

粒度每细一档，多轮调用反推阈值的风险就上升一档，所以泄露预算要写明，默认保持关闭。分档变细会提高模型否决推荐项的能力，并可能改变 Δ(③−②)，实验里把它记为自变量。

---

## 4. 决策提示规范

### 4.1 位置与触发条件

返回包络在 `data` 之外并列增加 `decision_prompt`：

```json
{
  "status": "ok",
  "data": { "...": "常规测量结果" },
  "decision_prompt": { "...": "仅在 L3 触发时出现" }
}
```

触发条件：本次调用的产物覆盖了一个或多个已声明决策点的观测指标。未覆盖则不出现，模型按常态编排。决策点的位置（在哪个步骤之后检查、检查哪些指标）由编译环节写入技能包。运行时工具只在这些位置做一次查表。

### 4.2 字段定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `decision_point` | string | 决策点标识，取值于 `REQUIRED_SCOPE` |
| `status` | enum | `recommended`（有推荐项）／ `residual`（无推荐项）／ `uncovered`（无标定）。**只回答“推荐项是否存在”，与候选集无关** |
| `observations` | list | 观测项，每项含 `metric`（指标名）、`band`（定性分档，取值于该指标声明的词表，见 3.5）、`summary`（自然语言概括），可选带 `margin` 与 `trend`（见 3.6，默认不返回）；**不含数值与阈值** |
| `allowed_actions` | list | **候选动作集**：在决策点声明处一次性枚举，**不随本次取值收缩、不因 `status` 裁剪**，任何触发情形下均全量返回；对模型是提示，对执行层是输出域边界 |
| `recommended_action` | string \| null | **推荐项**：须取自同一候选集，回答“按本领域文献此时通常建议怎么做”；`residual` 与 `uncovered` 时为 `null`。**非空不排除其余候选**，只表示该席位的默认值 |
| `action_consequences` | list | 每项含 `action`、`effect`、以及 `requires_params`（由调用方给定的参数清单） |
| `override_policy` | object | 是否允许否决、否决时是否需要 `override_reason` |
| `threshold_version` | string | 阈值表版本与内容哈希 |
| `scope` | object | `type` 取 `session` / `cluster` / `clusters`；`clusters` 时附 `cluster_ids` 与 `exceptions` |

### 4.3 三种 status 决定模型的义务

| status | 含义 | 工具给出 | 模型的义务 |
|---|---|---|---|
| `recommended` | 落入已标定区间，文献给出推荐项 | 候选集全量 + 推荐项 + 各动作后果与所需参数 | 采纳推荐项、径取其余候选，或显式否决并引用观测项、填写 `override_reason` |
| `residual` | 灰区或指标冲突，文献未给出推荐项 | 候选集全量，`recommended_action` 为 `null` | 在候选集内选一项并写明推理 |
| `uncovered` | 该物种/形态下无阈值标定 | 同上，`band` 一律 `unknown` | 同上，须自行判断并说明依据 |

任何状态下都返回全部候选动作及其后果，推荐项只是其中的默认值。判断层改写走向的路径有两条：推荐项缺失时的选择，以及推荐项存在时的否决。否决率按决策点统计，用来回查阈值标定。

### 4.4 完整示例

```json
{
  "status": "ok",
  "data": { "n_clusters": 34, "silhouette_overall": { "mean": 0.18 } },
  "decision_prompt": {
    "decision_point": "clustering_quality",
    "status": "residual",
    "observations": [
      { "metric": "silhouette_overall.mean", "band": "separation_insufficient",
        "margin": "near",
        "summary": "整体轮廓系数偏低，簇间分离不充分，且临近分档边界" },
      { "metric": "adjacent_ari_min", "band": "resolution_stability_high",
        "summary": "相邻分辨率间簇划分高度一致" }
    ],
    "allowed_actions": ["clustering_accept", "clustering_adjust"],
    "recommended_action": null,
    "action_consequences": [
      { "action": "clustering_accept",
        "effect": "按当前分辨率进入 marker 发现阶段",
        "requires_params": [] },
      { "action": "clustering_adjust",
        "effect": "更换分辨率后重新聚类并重新评估质量",
        "requires_params": ["target_resolution"] }
    ],
    "override_policy": { "allow_override": true, "require_reason": true },
    "threshold_version": "Arabidopsis_thaliana@2026.09.1#a7f3c1",
    "scope": { "type": "session" }
  }
}
```

### 4.5 表态协议

- 模型在 `allowed_actions` 内选择一项，通过 `write_judgment__add` 写入，携带 `run_ref` 指向本次执行；
- 采纳推荐动作时可不填理由；否决时必须填 `override_reason` 并引用 `observations` 中的具体项；
- 簇级决策点支持批量：一次表态覆盖 `scope.cluster_ids`，`exceptions` 中的簇另行单独表态；
- 否决率按决策点统计，用于反向检验阈值标定质量（否决率持续偏高的决策点应重新标定或降级为 `residual`）。

### 4.6 越界处理

模型提出候选集之外的动作时，循环层在执行前拒绝该提议，不产生副作用，并在轨迹中记一条 `override_rejected`。该提议连同理由登记为技能修订请求，回到编译环节，供补候选动作或阈值表。

有推荐项时回退至推荐项；无推荐项时回退至该决策点声明的保守动作，并要求模型重新表态。

---

## 5. 对现有实现的改造清单

| # | 改造项 | 涉及文件 | 说明 |
|---|---|---|---|
| 1 | 新增阈值表目录与加载器 | `skills/cell-annotation/thresholds/*.yaml`、`scripts/common.py` | 按物种加载，`env_or_default` 之外新增 `load_thresholds(species)` |
| 2 | 实现条件判定引擎 | `scripts/common.py` 新增 `build_decision_prompt()` | 输入指标字典 + 决策点名，输出决策提示或 `None` |
| 3 | `emit_ok` 支持并列 `decision_prompt` | `common.py:emit_ok`（约 837 行） | 保持 `data` 结构不变，向后兼容 |
| 4 | 触发点接入 | 各 `stepN_*.py` 中覆盖决策点指标的操作 | 目前仅 L0 计算，补 L3 判定 |
| 5 | `scope` 扩展为 `clusters` | `scripts/trajectory_schema.py`、`write_judgment.py`、`scripts/validate_log.py` | 支持批量表态与例外清单 |
| 6 | 动作集与枚举对齐 | `SKILL.md` §3 的 `decision` 枚举 ↔ 阈值表 `allowed_actions` | 构建期一致性检查（3.4 第 3 项）|
| 7 | `SKILL.md` 去阈值化 | `skills/cell-annotation/SKILL.md`、`references/metrics.md` | 描述改为"看什么分档、工具会给什么提示"，删去具体参考数值 |
| 8 | 覆盖性静态检查脚本 | `scripts/` 或 `experiments/` 新增 `check_thresholds.py` | 3.4 五项与 3.5 四条，构建期阻断 |
| 9 | 循环层越界拒绝 | `annot_harness/` 工具执行前校验 | 拒绝 + 回退推荐项（无推荐项时取保守动作）+ 记 `override_rejected` + 登记技能修订请求 |
| 10 | ρ 统计 | `scripts/validate_log.py` 或评估脚本 | 按决策点、按数据集统计三态占比（见 §6）|

---

## 6. ρ：阈值可判定率

**定义**：推荐可用率

> ρ = #{recommended_action ≠ null} / #{decision_prompt}

ρ 是带有预定义推荐项的决策实例占全部决策实例的比例，也就是文献共识覆盖到的比例。

**联合指标**：与之并列统计**否决率** r_o，即推荐项存在但被模型显式否决的比例。二者联合给出**有效判断占比**：

> η = (1 − ρ) + ρ · r_o

η 是最终走向由判断层决定的决策实例占比。ρ、r_o、η 都从轨迹里按决策点、按数据集统计：每条决策提示自带 `status` 与 `recommended_action`，对应表态在 `judgment` 记录中。

**预期与用途**：

- 预测：拟南芥根 ρ 较高（阈值文献覆盖充分），高粱根 ρ 较低。若成立，"判断层的价值取决于阈值覆盖度"由假设转为结论。
- 工程指导：ρ 高且否决率低的决策点应固化为规则（零成本、确定性）；只有 ρ 低或否决率持续偏高的部分才值得反复调用模型。
- 与 R-trap 对照：拟南芥决策多样性 0/4，高粱 2/4，对应两边 ρ 的高低。
- 若 ρ = 1 且 r_o = 0，判断层没有改过任何决策实例的走向。r_o > 0 时，否决通道仍在改写结果。

---

## 7. 三臂语义的重定义

现行臂②与臂①输出恒等，消融失效。引入决策提示后三臂语义如下：

| 臂 | 决策提示 | 表态（L5） | 度量对象 |
|---|---|---|---|
| ① 基线 | 不启用 | 由模型自主写判断记录（同现行） | 无判断层约束时的自然表现 |
| ② 规则 | 启用 | **不经模型**：由脚本取用预定义提示中的建议动作并执行；`residual` / `uncovered` 取保守动作 | 阈值规则的净贡献 Δ(②−①) |
| ③ 模型 | 启用 | 经模型：`recommended` 可采纳、径取其余候选或否决，`residual` / `uncovered` 由模型在候选集内选择 | 判断层经补充与纠偏两通道的净贡献 Δ(③−②)；结合否决率可拆出纠偏通道的贡献 |

臂②的「不经模型」指跳过 L5 表态。L1 编排与 L2 参数化仍走脚本默认路径。

臂②执行的推荐动作包括改分辨率、重取 marker、触发细分，这些动作会改变流程走向。Δ(②−①) 度量规则，Δ(③−②) 度量判断层。

---

## 8. 风险与监控

| 风险 | 表现 | 缓解 |
|---|---|---|
| 决策点过密 | 模型退化为逐项确认，表态/操作比值升高 | 2.5 约束一：稀疏化 + 批量表态；比值列为监控指标 |
| 阈值标定错误被阈值私有化掩盖 | 模型看不到数值，无法察觉标定错误 | 否决率按决策点监控，超阈值触发重标定 |
| `uncovered` 占比过高 | 判断层在无覆盖区自由发挥，引入噪声 | 统计 ρ；`uncovered` 决策点单独报告，不并入主结论 |
| 动作后果不可参数化 | L5 选完动作后 L2 无从填参 | 3.4 构建期检查 `requires_params` 非空性 |
| 模型在 `recommended` 区频繁否决 | 阈值权威被削弱，等价于阈值无效 | 否决率与 ρ 联合分析，识别需降级为 `residual` 的决策点 |

**监控指标**：表态/操作比值（当前基线 1.23–2.47）、ρ（按决策点与数据集）、各决策点否决率、`uncovered` 占比。

---

## 9. 验收标准

1. **阈值不可见性**：对模型的全部输入（系统提示、工具返回、参考文档）中，不得出现任何被判定为 `recommended` 的区间的数值边界；以字符串扫描与人工抽查双重确认。
2. **覆盖完备性**：`check_thresholds.py` 对全部 14 个决策点通过 3.4 的五项检查与 3.5 的四条分档检查。
3. **动作域封闭**：连续 3 次端到端运行中，`override_rejected` 事件为 0，或全部被正确回退。
4. **消融可分离**：臂②与臂①的最终标签或流程轨迹存在可观测差异，Δ(②−①) ≠ 0。
5. **ρ 可报告**：两个数据集均能给出按决策点的 `recommended` / `residual` / `uncovered` 三态分布。
6. **表态预算**：表态/操作比值较基线下降，簇级批量表态生效后目标区间为 1.0–1.5。

---

## 10. 待决问题

1. **`observations` 是否回传原始数值？** 当前不回传。模型向用户解释时只能复述 `summary`。若改为回传数值、仍不回传阈值，模型可以解释"为什么偏高"，也可以拿数值和阈值争辩。
2. **簇级批量表态的粒度上限**：一次覆盖多少簇、例外清单如何校验，需结合 `refine_effect` 与 `candidate_disambiguate` 的实际触发模式确定。按现有数据，臂③在拟南芥上对 8/34 簇做消歧、7 簇做细分，批量上限按这个量级设定。
