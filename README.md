# 《未来战争》v1.0 参赛 Bot

长期驻留的 HTTP 服务：判题系统每回合 POST 战场状态，本服务返回调度指令
（`roleCommandMap` / `prompt` / `executeCmd`，见 `docs/接口文档.md` §2）。
**零第三方运行时依赖**（仅 Python 标准库）。

- **制胜策略（比赛规则 → 落地）**：`docs/策略设计.md`
- 实现方案：`.omo/plans/future-war-bot.md`
- 诊断字典（一句话反馈用）：`docs/诊断字典.md`
- 配置键说明：`config/README.md`

## 运行

**平台入口（项目根目录）**：

```bash
python3 main3.py 18080             # 平台约定：main3.py 读取第一个参数作为 port
```

其他等价入口：

```bash
bash run.sh 18080                  # Linux/macOS（判题器样例：bash run.sh port）
python run.py 18080                # 跨平台（不依赖 PYTHONPATH）
run.bat 18080                      # Windows
```

所有入口都监听 `0.0.0.0:<port>`，port 缺省 8080；启动打印版本戳
`[future-war] stamp <commit> / <config-hash> / <profile>`。

**平台跑不起来时先自检**（会打印 Python 版本、导入、启动、请求响应的逐项结果）：

```bash
python3 scripts/selfcheck.py
```

启动/导入失败也会写入 `logs/run.log`（平台不显示 stdout/stderr 时可用）。

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
python3 tests/test_nav.py          # 零依赖备用运行器（任选一个测试文件）
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
| `strategy/` | 经济/战斗/建造/任务/寻宝/推理/对手/进攻/LLM/沙盒/规划（含黄昏就位、围墙防线、建造反馈修正） |
| `observability/` | 结构化日志、稳定事件码、每回合 `[METRIC]`/`[DIGEST]` 行 |
| `config/` | 集中配置与版本戳 |

## 工作流（内网 → 一句话反馈）

1. 内网 `git pull` → `bash run.sh <port>` → 平台跑对局。
2. 读日志：每回合两行 —— `[METRIC] M-01`（金币/血量/存活）与 `[DIGEST] D-02`
   （建造摘要 + 本回合指令 + 上回合执行结果）；事件按 `[TAG] CODE` 可 grep。
   日志同时在 `logs/` 下（JSONL 为回放事实源）**和 stderr**（`log.echo_stderr`
   默认开）。平台不给你看 `logs/` 目录时，直接看平台日志里的 `[METRIC]` /
   `[DIGEST]` 行即可。
3. 按 `docs/诊断字典.md` 模板发开发者一句话，如
   `版本=<commit> 第3天夜 基地掉到400 证据=[COMBAT]C-05×12`。
4. 开发者按「现象→模块→事件码→参数」定位并修改。

## 打包提交

版本统一由仓库根 `VERSION` 文件管理（单一真源）。打包脚本是**跨平台**的
Python 脚本（仅标准库，Windows/macOS/Linux 通用）：

```bash
python scripts/package.py                  # 默认：整个项目连同顶层目录 CoreGeek/ 一起打包
python scripts/package.py 0.2.0            # 临时覆盖版本
python scripts/package.py --prefix MyDir   # 换一个顶层目录名
python scripts/package.py --flat           # 不要顶层目录（main3.py 直接在归档根）
```

归档结构（默认 `CoreGeek/`）：

```
CoreGeek/
  main3.py  run.sh  run.py  run.bat  VERSION  BUILD_INFO.txt
  src/  config/  docs/  scripts/  tests/
```

- **Windows**：`python scripts\package.py`
- **macOS / Linux**：`python scripts/package.py`（或便捷入口 `bash scripts/package.sh`）

产物：`dist/future-war-bot-<version>.tar.gz` + `.sha256` 校验文件；包内含
`CoreGeek/BUILD_INFO.txt`（version/commit/built），并自动排除 `.git/`、`__pycache__/`、
`logs/`、`.venv/`、`.omo/evidence/` 等开发产物。

## 目录

- `src/future_war/` — Bot 源码（`server` / `models` / `core` / `strategy` / `observability`）
- `tests/` — 17 套件（零依赖运行器 + pytest 双兼容）
- `scripts/` — `selfcheck.py`（启动自检）、`package.py`（打包）
- `config/` — `default.json` + profile + README
- `docs/` — 比赛规范（只读）+ 策略设计 + 诊断字典 + 接口文档
- `run.sh` — 启动脚本
