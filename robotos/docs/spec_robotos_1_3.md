# RobotOS 1.3 vNext 实现说明

## 本版新增可落地能力

1. **DDS middleware 抽象层 + skill 进程化入口**
   - `DDSBroker` 协议 + `create_broker`
   - `InMemoryDDSBroker`（测试/本地）
   - `CycloneDDSBroker`（真实中间件适配）
   - `FastDDSBroker`（预留组织级 bridge）
   - `python -m robotos.skills.runner ...` 可作为独立 skill 进程

2. **Tool Registry 文件化 + schema 校验**
   - `robotos/config/tool_registry.json`
   - `ToolRegistry.from_json_file()` + `tool_registry.schema.json` 校验

3. **P0 执行一致性增强**
   - Preempt 两阶段事件：`PREEMPT_PHASE1_START` / `PREEMPT_PHASE2_COMPLETE`
   - Action epoch fencing：抢占后旧 result 会被忽略
   - cancel 收敛时按 session 批量 cancel active actions

4. **Checkpoint + Resume**
   - Tick 过程中持续更新 `session.bt_checkpoint`
   - `restore_checkpoint()` 用于 resume

5. **真实 HTTP API**
   - `control/api/http.py` 使用 FastAPI 暴露 `/v1/sessions/*` 与 `/v1/preempt`

6. **OSM 持久化 replay + projection rebuild**
   - `OSMStore(persist_path=...)` 自动 JSONL 追加
   - 启动时可 replay + rebuild projections
   - `python -m robotos.cli replay --path ...` 回放

## 下一步建议

- 将 CycloneDDS topic 定义改为稳定 IDL/TypeObject 并补 QoS 配置矩阵
- 对 FastDDS 增加正式 Python/C++ bridge（当前为预留 hook）
- OSM 从 JSONL 升级为事务型存储（SQLite/Postgres）
- 引入 preempt quiesce 超时与 skill watchdog restart 策略
