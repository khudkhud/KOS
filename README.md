# RobotOS 1.3 MVP (Python)

可运行的最小实现，覆盖你给出的 RobotOS 1.3 关键闭环：

- Session 治理（创建/提交 intent/cancel/pause/resume）
- Kernel 执行（tick + executable graph + action + lease + policy）
- OSM（append-only 事件日志 + projections + snapshot）
- Agent 因果通信（Event/Request/Proposal 三类消息）

## 运行

```bash
python -m robotos.app
python -m robotos.app --cancel
```

## 测试

```bash
python -m pytest -q
```
