# 《未来战争》v1.0 Bot

长期驻留的 HTTP 服务：判题系统每回合 POST 当前战场状态，本服务返回调度指令。
Response 顶层结构：`roleCommandMap` / `prompt` / `executeCmd`（见 `docs/接口文档.md` §2）。

**当前为脚手架阶段**：任意请求均返回空 `roleCommandMap` 的合法 Response；
策略与游戏逻辑在后续工作包接入（见 `.omo/plans/future-war-bot.md` 工作包 1）。

## 运行

```bash
bash run.sh 18080    # 监听 0.0.0.0:18080；缺省 8080
```

## 手动冒烟

```bash
curl -s -X POST localhost:18080 -d @docs/request.txt
# → {"roleCommandMap": {}, "prompt": "", "executeCmd": ""}

curl -s -X POST localhost:18080 -d 'not json'
# → 同上（畸形请求体不会导致崩溃，仅向 stderr 告警）
```

## 测试

```bash
python3 -m pytest tests/ -q    # 需 pytest（见 pyproject.toml dev 依赖）
python3 tests/test_server.py   # 零依赖备用运行器（无 pytest 时可用）
```

测试在临时端口（port 0）启动真实服务，POST `docs/request.txt`，断言
HTTP 200 + 响应含 `roleCommandMap` 键，并在结束时干净关闭服务。

## 目录

- `src/future_war/server.py` — HTTP 入口（标准库 `http.server.ThreadingHTTPServer`）
- `src/future_war/{models,core,observability}/` — 预留子包
- `tests/` — 集成测试
- `docs/` — 比赛规范（只读，勿改）
- `run.sh` — 启动脚本
