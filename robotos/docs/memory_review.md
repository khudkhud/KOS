# Memory 架构评审（当前实现）

## 1. Memory 分层
系统在 `MemoryStore` 中实现了四层记忆：

- `short_term`：按 `session_id` 分桶的短时记忆，带 TTL。
- `long_term_user`：按 `user_id` 存储长期用户偏好。
- `contextual`：按位置（例如 `home`）存储上下文快照。
- `world_memory`：世界模型相关事实与语义拓扑（`semantic_topologies` / `active_topology_ids`）。

## 2. 记忆如何“构建”

### 2.1 启动阶段构建
`build_system()` 会初始化 `MemoryStore`，并把它注入到：

- `PathNavigationAgent`（用于语义目标解析与路径规划）
- `OSMWorldProjector`（用于把事件流投影到世界记忆）

### 2.2 运行阶段写入
在 demo 主流程里：

- 用户提交意图后写入 short-term（`intent`）
- 写入用户长期偏好（如语言）
- 写入当前环境 context（如电量、网络）

此外，`PathNavigationAgent` 在导航成功后会把 `navigation_last_result` 写回 world memory，形成闭环。

### 2.3 事件投影构建世界记忆
`OSMWorldProjector.project()` 会遍历 OSM 事件，汇总：

- `session_summary`：按 session 的状态摘要
- `latest_risks`：最近 action 风险结果（status/error_code）
- `last_explain_trace`：最新 explain trace

这些都被写入 `world_memory`，相当于“从事件日志构建长期可查询视图”。

## 3. 记忆如何“利用”

### 3.1 导航侧利用语义记忆
`PathNavigationAgent.plan_and_execute()` 通过 `resolve_semantic_target()` 从激活拓扑中解析语义目标：

- 支持按 `topology_ids` 范围搜索
- 支持最小置信度过滤 `min_confidence`
- 同一目标多版本冲突时，按 `(confidence, updated_at)` 选择最佳节点

### 3.2 拓扑演进与融合
`MemoryStore` 支持：

- `upsert_semantic_node()`：增量更新区域拓扑节点
- `set_active_topologies()`：切换激活拓扑集合
- `merge_topologies()`：把多个区域图融合成新的统一图（可加桥接节点）

并通过 `_refresh_flat_topology()` 维护兼容旧接口的 `semantic_topology` 扁平视图。

### 3.3 会话上下文利用
`ContextBuilder.build()` 当前主要从 `OSMStore` 生成 planning context（session、租约、最近失败），并未直接读取 `MemoryStore` 的四层数据。

这意味着：

- 记忆与 planner context 目前是“并行存在”
- 记忆主要由导航与世界投影链路消费

## 4. 生命周期与治理

- `short_term` 支持 `cleanup_expired()` 过期清理
- `long_term_user` 支持 `erase_user()` 擦除用户数据
- `world_memory` 当前为内存态（进程内）结构，无独立持久化层

## 5. 现状总结

当前系统的记忆实现是“多层内存 + 事件投影 + 语义拓扑解析”的组合：

1. 在控制面与 demo 流程中主动写入短期/长期/上下文记忆；
2. 在世界模型侧通过 OSM 事件投影维护可查询事实；
3. 在执行面由 PNA 消费语义记忆完成目标解析与路径决策；
4. 在治理面提供 TTL 清理与用户擦除基础能力。

整体方向正确，且已具备拓扑融合与置信度仲裁等“可扩展记忆推理”能力，但 planner 侧尚未充分消费这套 MemoryStore，需要后续进一步打通。
