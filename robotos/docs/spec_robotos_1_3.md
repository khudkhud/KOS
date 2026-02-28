# RobotOS 1.3 MVP 实现说明

本目录实现遵循 1.3 规格并做了 MVP 裁剪：

- `control/`: Session API facade、Message Stream、ContextBuilder、Planner stub、StrategyPlugin
- `kernel/`: Executor(BT风格 tick)、ActionSupervisor、LeaseManager、PolicyGate、OSMStore
- `skills/`: nav/dialog/monitor 模拟技能
- `schemas/`: message/plan/exec_graph/osm_event/tool_registry 的 JSON schema

## 当前已实现硬闭环

1. Session 治理：
   - `SessionService.create/submit_intent/cancel/pause/resume/get`
   - Session 状态推进：CREATED -> PLANNING -> EXECUTING -> SUCCEEDED/FAILED/CANCELED
2. Kernel 执行：
   - Compiler 注入 AcquireLease/Timeout/Retry/ReleaseLease
   - Executor tick 驱动 ActionSupervisor + LeaseManager
   - cancel 实现 `halt_subtree + cancel action + release leases`
3. OSM 回放基础：
   - append-only `event_log`
   - projections: session/action/lease/intent/request
   - `get(version=None)` 快照查询

## 下一版建议

- 将 `ControlAPI` 换成真实 HTTP 服务（FastAPI/Starlette）
- OSM 增加持久化（SQLite/RocksDB）与 replay CLI
- ActionSupervisor 对接真实 DDSAction middleware
- 加入 Preempt、PAUSED 恢复 checkpoint、更强的策略插件
