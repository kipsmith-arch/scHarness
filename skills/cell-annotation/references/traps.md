# 陷阱清单与反例(skill references 版)

> 本文档把 6 大陷阱与 SOP 常见症状按**决策点**组织,每条给出"表面读数 → 真实含义 → 反例"。
> 素材来源:`knowledge/metrics_interpretation.md` §附、`knowledge/cell-annotation-sop.md` 常见症状速查、`design/experiment_design.md` §4.1。
> 用途:做判断前(尤其 13 个决策点)快速回顾容易误判的地方;SKILL.md §4 是摘要,本文件是完整版。

## 目录(TOC)

- [陷阱 1:植物 mt/cp 与动物不同(→ qc_threshold)](#陷阱-1植物-mtcp-与动物不同)
- [陷阱 2:层级本体下的并列不是模糊(→ candidate_gap / candidate_disambiguate)](#陷阱-2层级本体下的并列不是模糊)
- [陷阱 3:小样本时 ratio 会骗人(→ candidate_gap)](#陷阱-3小样本时-ratio-会骗人)
- [陷阱 4:pct1 高不等于好 marker(→ label_confirm / marker_quality)](#陷阱-4pct1-高不等于好-marker)
- [陷阱 5:silhouette 低不一定是聚类错(→ clustering_quality)](#陷阱-5silhouette-低不一定是聚类错)
- [陷阱 6:批次熵低不一定是批次效应(→ batch_effect)](#陷阱-6批次熵低不一定是批次效应)
- [常见症状速查(SOP)](#常见症状速查sop)
- [按决策点索引](#按决策点索引)

---

## 陷阱 1:植物 mt/cp 与动物不同(→ qc_threshold)

- **表面读数**:pct_counts_mt 的 p99 高,直接套默认 max_mt_pct=15 截尾;或发现"没有线粒体基因被匹配"。
- **真实含义**:植物线粒体基因前缀是 `ATMG`,叶绿体是 `ATCG`;`MT-` 前缀(动物/血液命名)匹配不到植物基因。默认 max_mt_pct=15 是动物/血液参考,植物根要看 `pct_counts_chloroplast`,而且根与叶等组织的参考也不同(肝脏天然高 mt 同理)。
- **反例**:一个拟南芥根数据集,按动物习惯只看线粒体,发现 ATMG 命中极少、过滤几乎没有作用,于是放松阈值——但真正的问题是高叶绿体(ATCG)细胞混入,应该用 `--cp-pattern ^ATCG` 与 `--max-chloroplast-pct` 过滤。另一个反例:`filter_genes` 的 n_mt_removed / n_cp_removed 为 0,不是"没有这类基因",而是前缀正则没匹配上(检查 `--mt-pattern` / `--cp-pattern`)。
- **正确做法**:先确认数据物种与组织,植物场景同时读 mt 与 cp 两个指标;设阈值前看分布(p99 vs p90、双峰谷底),不套动物默认值。

## 陷阱 2:层级本体下的并列不是模糊(→ candidate_gap / candidate_disambiguate)

- **表面读数**:first_count == second_count(如 lateral root cap 36 vs root cap 36),看起来"势均力敌、真模糊",送 step5 细分。
- **真实含义**:植物根本体中 root cap ⊃ lateral root cap(父子关系),子类共享父类的 marker,first_count 相等是正常现象,不是混合群体。
- **反例**:簇 0 的两个候选 "lateral root cap" 与 "root cap" 各 36 个支持 marker。若当"真模糊"细分,子聚类只会把同一类细胞机械切开。正确做法是查 `first_second_ancestor_overlap`——两者是父子关系,选更具体的 "lateral root cap"。同义词(一个类型的多个名字)同理,不属于 step5 的 refine 对象。
- **正确做法**:并列时先看 KG 本体证据(ancestors)或用生物学知识判断父子/同义;确认无关系再判 ambiguous_true 走细化。

## 陷阱 3:小样本时 ratio 会骗人(→ candidate_gap)

- **表面读数**:count_ratio = first_count / second_count = 2.0,"两倍优势,第一候选可信"。
- **真实含义**:ratio 是相对量,小样本时夸大差距。first_count=2、second_count=1 只差 1 个 marker,证据极其薄弱,不足以定 high。
- **反例**:稀有簇 first=2 vs second=1,count_ratio=2 看着 decisive;另一个簇 first=40 vs second=20,count_ratio 同样 2——两者证据强度完全不同。只看 ratio 会把弱证据当强证据。
- **正确做法**:同时看 count_diff(绝对差距)与 first_count 的绝对值;小样本时更依赖 count_diff 与 marker 表达的独立验证。

## 陷阱 4:pct1 高不等于好 marker(→ label_confirm / marker_quality)

- **表面读数**:top marker pct1=0.85,"大部分细胞都表达,好 marker,标签有支撑"。
- **真实含义**:管家基因在所有细胞都表达,pct1 同样高。marker 必须同时满足"簇内高、簇外低",只看 pct1 会把管家基因当好 marker。
- **反例**:簇 1 的 top-3 marker 里出现 RPL/RPS/核糖体基因,pct1=0.9;查 pct2 发现其他簇也 0.85 表达——特异性为零,标签证据不成立。若 label_confirm 只看 pct1 就 confirmed,注释被管家基因带偏(所有簇都注释成同一类)。
- **正确做法**:pct1 与 pct2 一起看(pct1-pct2 大才特异),top marker 列表先排除管家基因(RPL/RPS/MT-/HSP/Histone);canonical marker 不表达也是标签错的信号。

## 陷阱 5:silhouette 低不一定是聚类错(→ clustering_quality)

- **表面读数**:silhouette_overall.mean=0.15,"聚类质量差,需要重聚类"。
- **真实含义**:发育连续谱等真实生物学结构(细胞沿梯度连续变化)silhouette 天然低——不是聚类无效,是数据本身连续。对这类数据反复调分辨率重聚类,反而破坏真实结构。
- **反例**:根发育梯度上的过渡态细胞,任何分辨率下 silhouette 都偏低;机械地"silhouette 低 → clustering_adjust → 降分辨率重跑"只会得到更粗、更无意义的簇。对照:若同时存在多个负 silhouette 簇 + 单细胞簇,那才是真需要调整的信号。
- **正确做法**:silhouette 与其他信号(负值簇数、单细胞簇、最大簇占比)一起看;有连续谱先验时接受偏低值,并在 reasoning 里说明。

## 陷阱 6:批次熵低不一定是批次效应(→ batch_effect)

- **表面读数**:per_cluster_batch_entropy 接近 0,"单批次主导,批次效应,需要批次校正"。
- **真实含义**:突变体/条件特异群体天然单批次——比如某簇只含突变体样本,熵就是 0,但这是真实的生物学群体,不是批次污染。做批次校正会把它抹掉。
- **反例**:某簇只含 rhd6 突变体样本,熵=0;直接判 batch_effect 并做批次校正,重聚类后这个突变特异群体消失,真实生物学信号丢失。正确做法是先查该簇对应的样本/基因型/条件注释:对应有意义的条件 → condition_specific;无法解释 → 再考虑批次效应。
- **正确做法**:单批次簇先查条件注释;批次在 kNN 图上的空间结构(Moran's I)是更强的批次效应信号,与熵一起判断。

---

## 常见症状速查(SOP)

| 症状 | 最可能原因 | 第一步排查 | 涉及决策点 |
|---|---|---|---|
| 全部 cluster 都 unknown | marker 没命中 / organ 选错 / 阈值太严 | 看命中率;查 organ 是否对齐 | kg_match / unknown_cluster |
| 所有 cluster 注释成同一种细胞 | 管家基因当 marker / 批次效应 | 看 top marker 是否管家基因;看 cluster 样本组成 | label_confirm / marker_quality / batch_effect |
| 注释粒度太细(一种细胞分 10 类) | 聚类分辨率太高 / refine 太激进 | 降分辨率;检查 refine 触发条件 | resolution_select / refine_effect |
| 少数 cluster 注释很离谱 | 这几个 cluster 是批次/质量产物 | 看 cluster 的样本/质量分布 | batch_effect / clustering_quality |
| canonical marker 不表达 | 标签错了 | 重查 SOP-3/SOP-4 | label_confirm |
| unknown 率 >30% | 阈值太严 / 图谱覆盖不全 / organ 错 | 分层排查(30% 警惕/40% 覆盖不全/50% 标准太严) | global_quality / unknown_cluster |

## 按决策点索引

| 决策点 | 相关陷阱 | 陷阱要点 |
|---|---|---|
| qc_threshold | 陷阱 1 | 植物 mt/cp 前缀与参考值 |
| resolution_select | —(症状:粒度太细) | 平台期选择,结合生物学预期 |
| clustering_quality | 陷阱 5 | 连续谱 silhouette 天然低 |
| batch_effect | 陷阱 6 | 条件特异群体天然单批次 |
| de_method | —(SOP-2 稀有簇) | pseudobulk 样本不足时回退 |
| marker_quality | 陷阱 4 | 管家基因 pct1 也高 |
| kg_match | —(症状:全 unknown) | 先查 organ/ID,再判覆盖 |
| candidate_gap | 陷阱 2、3 | 层级并列非模糊;小样本 ratio 骗人 |
| candidate_disambiguate | 陷阱 2 | 父子/同义 vs 真模糊 |
| refine_effect | —(SOP-5 假分裂) | 子簇类型与父候选无关=假分裂 |
| unknown_cluster | —(症状:全 unknown) | 重叠高=同一类型,低=独立新类型 |
| label_confirm | 陷阱 4 | pct1 必须配 pct2 |
| global_quality | —(症状:unknown 率高) | 分层排查,不直接判数据差 |
