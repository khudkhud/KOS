# RobotOS 1.3 vNext (Python)

这版实现了面向“可落地”的下一步能力：

- 可选 DDS backend：`inmemory` / `cyclonedds` / `fastdds(预留适配)`
- Tool Registry 文件化加载 + `tool_registry.schema.json` 运行时校验
- Preempt 两阶段（phase1 quiesce + phase2 handover）与 checkpoint snapshot/restore
- Action epoch fencing（避免抢占后旧结果污染）
- FastAPI HTTP API
- OSM 事件日志持久化（JSONL）+ projection rebuild + replay CLI

## 运行 Demo

```bash
python -m robotos.app
python -m robotos.app --cancel
python -m robotos.app --preempt
python -m robotos.app --target-gone
python -m robotos.demo_agent_comm
```

## 启动 HTTP API

```bash
uvicorn robotos.app:build_http_app --factory --host 0.0.0.0 --port 8000
```

## OSM 持久化与回放

```bash
ROBOTOS_OSM_PERSIST=/tmp/robotos_events.jsonl python -m robotos.app
python -m robotos.cli replay --path /tmp/robotos_events.jsonl
```

## 同机多进程消息协同（新增）

```bash
ROBOTOS_MESSAGE_PERSIST=/tmp/robotos_messages.jsonl python -m robotos.app
```

启用后 `MessageStream` 会把消息写入本地 JSONL 日志；同机的其他进程可挂载同一路径并通过 `poll_new(consumer_id=...)` 增量消费，实现单机多进程协同与恢复。

P0 能力补充：
- outbox：发布消息先入 outbox，再统一 flush。
- event log replay：支持按日志偏移重放（`replay(from_line=...)`）。
- 幂等消费键：每条消息包含 `idempotency_key`，消费者可持久化去重状态。

## Skill 进程化（真实 DDS 场景）

```bash
python -m robotos.skills.runner --tool nav.goto --duration-ms 1200 --backend cyclonedds
```

## DDS Backend 选择

```bash
ROBOTOS_DDS_BACKEND=inmemory python -m robotos.app
ROBOTOS_DDS_BACKEND=cyclonedds python -m robotos.app
```


## 控制面安全基线（新增）

- HTTP API 需携带 `x-api-key`。
- 通过环境变量 `ROBOTOS_API_KEYS` 配置 token->actor/role 映射（JSON）。
- 角色最小权限：`admin` / `operator` / `viewer`。
- cancel/preempt 等高风险操作会记录 `CONTROL_AUDIT` 与治理总线审计消息。

示例：

```bash
export ROBOTOS_API_KEYS='{"admin-token":{"actor":"ops_oncall","role":"admin"}}'
```

## 消息与事件可靠性 SLA（新增）


## Lease 资源策略（按当前机器人形态）

- `hpu`：单资源独占，资源竞争可等待（soft=1000ms, hard=1000ms）；1s内空闲即执行，超出1s返回超时。
- `base`（底盘/运动控制）：单资源独占，竞争失败立即返回 `BASE_BUSY`（由上层决定 replan/重试）。
- `camera`：30fps 常驻发布流，任务通过订阅使用，不走独占 lease（lease bypass）。
- `mic`（麦克风）：通常为常驻采集流，任务订阅音频，不做独占 lease（lease bypass）。
- `speaker`（扬声器）：播放/TTS 需要串行，采用独占 lease；竞争失败立即返回 `SPEAKER_BUSY`。

说明：
- Lease 管理器会在运行时做过期清扫，并向请求队列写入 `LEASE_EXPIRE_*` 提示，供上层策略决定 fail/retry/degrade。


- MessageStream 对持久化消息提供 **at-least-once** 交付语义。
- 业务侧副作用必须按 `idempotency_key` 做幂等（跨重启通过 `consumer_id` 状态文件维持）。
- OSM/Message JSONL 可重放；消费者必须接受重复消息并做去重。

## 测试

```bash
python -m pytest -q
```


## 家庭场景自动闭环示例

- 当对话模块发布 `Event: TARGET_GONE`，且 payload 满足 `target in {son, child}`、`source in {mother,father,guardian,family_member}`、`confidence >= 0.7` 时，Strategy 会自动触发 `REQ_CANCEL` 并取消当前 session。


## Agent 间通信可视化 Demo

`python -m robotos.demo_agent_comm` 会运行 `TARGET_GONE` 场景，并输出 Message Stream 的时间线以及 Mermaid sequenceDiagram 文本，便于观察 agent 因果通信。


## Build 评估报告（专业性/实用性/前沿性/扩展性）

```bash
python -m robotos.app --build
```

该命令会输出一份结构化 JSON，包含：
- `professionalism`：工程规范化能力（显式构建参数、schema 校验、工具规模）
- `practicality`：落地实用能力（HTTP、持久化、可选 DDS backend）
- `frontier`：前沿执行语义（两阶段抢占、epoch fencing、checkpoint/restore）
- `extensibility`：扩展能力（可插拔 backend、文件化注册表、消息流与 agent 注册）


## 家庭实体机器人资源与可靠执行约束

当前 build 默认加入了实体机器人约束配置（可在 `BuildOptions` 中覆盖）：
- 会话最大执行时长预算（防止长程任务失控）
- 动作陈旧超时（反馈/结果长期缺失时主动失败收敛）
- 单 session 最大并发动作数（受限算力/功耗下的保守调度）
- 最低启动电量阈值（用于上层策略接入）


## AI-native 实现进展（结合实体机器人约束）

本版在保留 DDS 底层通信前提下，新增了面向智能时代机器人的关键能力：
- Tool/Skill Contract 扩展：支持 `contract_version`、`idempotent`、`compensation_tool`、`rollout_stage`。
- Agent Governance Bus：消息类型扩展为 `Decision/Override/Escalation`，并记录责任链。
- Explainable Trace：任务会写入 `EXPLAIN_TRACE` 事件，用于解释意图与计划阶段。
- Memory OS 雏形：短期记忆、长期用户偏好、情景记忆，并支持过期清理与隐私擦除。
- 模型+技能混部调度器：在资源受限硬件上限制并行模型/技能任务。


## 三层控制与本体闭环（新增）

本版新增了可执行的架构骨架：
- Task ↔ Behavior ↔ Motion 分层契约（时延预算、错误码、可中断、运动安全参数）。
- PNA（Path & Navigation Agent）服务组件，用语义目标驱动路径与 waypoint 执行。
- StateEstimator + SafetySupervisor 组件，执行前先做本体状态与安全闸门评估。
- OSM → WorldMemory projection 管道，将事件账本投影为世界记忆查询视图。


## 感知与规划网络接入策略（YOLO / DepthAnything / NavDP / RoboBrain）

建议采用“算法形态与调用协议分离”的方式：
- YOLO：周期型常驻 **Service**（持续感知，结果写入状态估计）
- DepthAnything：按需 **Skill**（通过 Action 调用）
- NavDP：按需 **Skill**（局部 waypoint 预测，通过 Action 调用）
- RoboBrain：按需 **Skill**（任务规划，通过 Action 调用）

其中 Action 统一承载超时、取消、重试与审计语义。
- Skill=能力，Service=运行形态，Action=调用协议。


## HPU 资源协调策略

- DepthAnything/NavDP/RoboBrain 统一声明 `required_resources=["hpu"]`，由 lease 保证物理互斥。
- Action 下发前对 HPU 模型走双门控：先 scheduler 并发配额，再 lease 物理资源检查。
- 门控冲突时采用“排队+优先级让位”策略：同优先级排队等待，高优先级可触发低优先级让位，并执行延迟重试（degrade policy）。


## Agent 与 Skill 的职责边界（新增）

- Skill：单一能力单元，偏“做一件事”（如 depth 推理、局部 waypoint 预测）。
- Agent：长程任务编排单元，偏“完成一类任务生命周期”（如长程导航：目标解析→路径规划→执行→汇报）。
- 在当前实现中，`TaskExecutionAgent` 作为任务编排的一等实体，直接组织长程导航并发布 `NAV_EXEC_DONE` 汇报，避免为单一链路引入额外 service-agent 封装。
