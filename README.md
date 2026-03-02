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

## Skill 进程化（真实 DDS 场景）

```bash
python -m robotos.skills.runner --tool nav.goto --duration-ms 1200 --backend cyclonedds
```

## DDS Backend 选择

```bash
ROBOTOS_DDS_BACKEND=inmemory python -m robotos.app
ROBOTOS_DDS_BACKEND=cyclonedds python -m robotos.app
```

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
- 在当前实现中，新增了 `TaskExecutionAgent` 与 `LongRangeNavigationAgent`，通过 MessageStream 的 `REQ_NAV_PLAN`/`NAV_EXEC_DONE` 完成跨 Agent 协作。
