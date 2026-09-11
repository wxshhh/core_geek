# 《未来战争》v1.0 参赛 Bot

长期驻留的 HTTP 服务：判题系统每回合 POST 战场状态，本服务返回调度指令
（`roleCommandMap` / `prompt` / `executeCmd`，见 `docs/接口文档.md` §2）。
**零第三方运行时依赖**（仅 Python 标准库）。

- 实现方案：`.omo/plans/future-war-bot.md`
- 诊断字典（一句话反馈用）：`docs/诊断字典.md`
- 调优报告：`docs/调优报告.md`
- 模拟器说明与简化点：`src/future_war/sim/README.md`
- 配置键说明：`config/README.md`

## 运行

```bash
bash run.sh 18080                  # 监听 0.0.0.0:18080；缺省 8080
bash run.sh 18080 --profile aggressive   # 可选 profile（见 config/）
```

启动打印版本戳：`[future-war] stamp <commit> / <config-hash> / <profile>`。

## 手动冒烟

```bash
curl -s -X POST localhost:18080 -d @docs/request.txt
# → {"roleCommandMap": {"10010": {"action": "move", ...}, ...}, "prompt": "", "executeCmd": ""}

curl -s -X POST localhost:18080 -d 'not json'
# → 合法空指令（畸形请求不崩溃，记一条 [ERROR] X-02）
```

## 测试

```bash
python3 -m pytest tests/ -q        # 需 pytest（dev 依赖）
python3 tests/test_end_to_end.py   # 零依赖备用运行器（无 pytest 时）
```

## 架构

```
server.py ── StrategyBot ── planner.plan_turn（昼夜编排）
                               ├─ 白天: economy(采矿/贩卖/建造/回防) + task_agent(自进化) + treasure
                               └─ 夜晚: combat(武器操控/优先级/防溢出) + offense(狙击/骚扰)
   core/     WorldModel(跨回合状态) + world_map + nav(寻路/碰撞)
   observability/  RoundLogger(JSONL) + StructuredLogger(事件码) + RoundObserver([METRIC])
```

| 模块 | 职责 |
| --- | --- |
| `server.py` | HTTP 入口、请求解析、Bot 调度、异常兜底（进程绝不崩溃） |
| `models/` | 接口 Request/Response 类型与编解码（宽松解析） |
| `core/` | 世界模型、可建造区推断、寻路与碰撞规避 |
| `strategy/` | 经济/战斗/建造/任务/寻宝/推理/对手/进攻/LLM/沙盒/规划 |
| `observability/` | 结构化日志、稳定事件码、每回合 `[METRIC]` 指标行、回放 |
| `sim/` | 本地模拟器 + mock 判题器（离线回归/自对弈，**明确简化子集**） |
| `config/` | 集中配置与版本戳 |

## 工作流（内网 → 一句话反馈）

1. 内网 `git pull` → `bash run.sh <port>` → 平台跑对局。
2. 读日志：每回合一行 `[METRIC] M-01 ...`；事件按 `[TAG] CODE` 可 grep
   （`logs/` 下，JSONL 为回放事实源）。
3. 按 `docs/诊断字典.md` 模板发开发者一句话，如
   `版本=<commit> 第3天夜 基地掉到400 证据=[COMBAT]C-05×12`。
4. 开发者按「现象→模块→事件码→参数」定位并修改。

## 自对弈调参

```bash
python3 scripts/self_play.py --seeds 8 --opponent scripted
python3 scripts/run_sim.py --seed 42 --challenger http:http://127.0.0.1:18080 --defender idle
```

## 打包提交

版本统一由仓库根 `VERSION` 文件管理（单一真源）：

```bash
bash scripts/package.sh            # 生成 dist/future-war-bot-<version>.tar.gz
bash scripts/package.sh 0.2.0      # 临时覆盖版本
```

产物：`dist/future-war-bot-<version>.tar.gz` + `.sha256` 校验文件；包内含
`BUILD_INFO.txt`（version/commit/built），并自动排除 `.git/`、`__pycache__/`、
`logs/`、`.venv/`、`.omo/evidence/` 等开发产物。

## 目录

- `src/future_war/` — Bot 源码（`server` / `models` / `core` / `strategy` / `observability` / `sim`）
- `tests/` — 22 套件、252 测试（零依赖运行器 + pytest 双兼容）
- `scripts/` — `run_sim.py`（跑模拟对局）、`self_play.py`（批量自对弈）、`replay.py`（回放）
- `config/` — `default.json` + profile + README
- `docs/` — 比赛规范（只读）+ 诊断字典 + 调优报告
- `run.sh` — 启动脚本
