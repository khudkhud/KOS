# RobotOS 1.3 MVP 实现说明（vNext）

本目录实现遵循 1.3 规格并做了可落地增强：

- `control/`: Session API facade、Message Stream、ContextBuilder、Planner stub、StrategyPlugin
- `kernel/`: Executor(BT风格 tick)、ActionSupervisor、LeaseManager、PolicyGate、OSMStore
- `kernel/action/dds.py`: DDSAction-like transport（in-memory broker + goal/feedback/result/cancel）
- `schemas/`: message/plan/exec_graph/osm_event/tool_registry 的 JSON schema（已接入运行时校验）

## 本版补齐点

1. DDSAction 路径（可替换）
   - Kernel 不再直调 skill 对象，而是通过 `DDSActionClient` 发送 goal/cancel
   - SkillServer 通过 topic 收 goal/cancel，并回 feedback/result
   - 主题形态：`/<tool>/action/{goal,feedback,result,cancel}`（tool 名做安全映射）

2. Schema 边界校验（硬约束）
   - MessageStream.publish 校验 `message.schema.json`
   - Planner 输出校验 `plan_json.schema.json`
   - Compiler 输入/输出分别校验 `plan_json` 与 `exec_graph`
   - OSM append_event 校验 `osm_event.schema.json`

3. 执行观测增强
   - OSM 中增加 `ACTION_FEEDBACK` 事件，便于调试与回放

## 下一版建议

- 将 InMemoryDDSBroker 替换为真实 DDS middleware（Cyclone/FastDDS）
- 完成工具注册中心文件化加载 + `tool_registry.schema.json` 运行时校验
- 实现 Preempt（PAUSE/CANCEL 策略分支）和 checkpoint 恢复
- 引入真实 HTTP API（FastAPI）与 OSM 持久化 replay CLI
