# RobotOS 1.3 vNext (Python)

这版实现了面向“可落地”的下一步能力：

- 可选 DDS backend：`inmemory` / `cyclonedds` / `fastdds(预留适配)`
- Tool Registry 文件化加载 + `tool_registry.schema.json` 运行时校验
- Preempt（PAUSE/CANCEL 分支）与 checkpoint snapshot/restore
- FastAPI HTTP API
- OSM 事件日志持久化（JSONL）与 replay CLI

## 运行 Demo

```bash
python -m robotos.app
python -m robotos.app --cancel
python -m robotos.app --preempt
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

## DDS Backend 选择

```bash
ROBOTOS_DDS_BACKEND=inmemory python -m robotos.app
ROBOTOS_DDS_BACKEND=cyclonedds python -m robotos.app
```

## 测试

```bash
python -m pytest -q
```
