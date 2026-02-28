# RobotOS 1.3 vNext 实现说明

## 本版新增可落地能力

1. **DDS middleware 抽象层**
   - `DDSBroker` 协议 + `create_broker`
   - `InMemoryDDSBroker`（测试/本地）
   - `CycloneDDSBroker`（真实中间件适配）
   - `FastDDSBroker`（预留组织级 bridge）

2. **Tool Registry 文件化 + schema 校验**
   - `robotos/config/tool_registry.json`
   - `ToolRegistry.from_json_file()` + `tool_registry.schema.json` 校验

3. **Preempt + Checkpoint**
   - `Kernel.preempt(low, high, mode=PAUSE|CANCEL)`
   - Tick 过程中持续更新 `session.bt_checkpoint`
   - `restore_checkpoint()` 用于 resume

4. **真实 HTTP API**
   - `control/api/http.py` 使用 FastAPI 暴露 `/v1/sessions/*` 与 `/v1/preempt`

5. **OSM 持久化 replay**
   - `OSMStore(persist_path=...)` 自动 JSONL 追加
   - `python -m robotos.cli replay --path ...` 回放

## 下一步建议

- 将 CycloneDDS topic 定义改为稳定 IDL/TypeObject 并补 QoS 配置
- 对 FastDDS 增加正式 Python/C++ bridge（当前为预留 hook）
- Replay 从“事件查看”升级为“projection 重建 + dry/live run”
- Preempt 增加 Lease 级细粒度让渡与强制 quiesce 超时策略
