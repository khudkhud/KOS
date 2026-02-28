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
