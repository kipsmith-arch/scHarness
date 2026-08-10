- source_spec: `_bmad-output/implementation-artifacts/spec-p2-pipeline-scripts.md`
  summary: run_log.jsonl 追加无并发锁(seq 计数与追加非原子),并发进程会冲突
  evidence: common.append_log 先整文件数行再追加;P6 阶段若并行跑多个 session 写同一 run_log 需加锁或按目录隔离
- source_spec: `_bmad-output/implementation-artifacts/spec-p2-pipeline-scripts.md`
  summary: append_log 每次追加 O(n) 数行(47 记录量级可忽略),且中途失败会留下无收尾 op 的半截 exec 序列(设计按 run_id 前缀查询可容忍)
  evidence: trajectory_design §4.2 明确"按 seq 排序,最新=当前",残缺序列不影响查询;P6 导出脚本前可加失败标记
