# config/ — 配置中心与版本戳（工作包 7）

所有策略旋钮集中于此，**不要**把可调参数散落在代码里（方案 §4.3）。
每次迭代只动一处（one knob per iteration），用开关控制，便于归因与回滚。

## 为什么是 JSON 而不是 YAML（与方案 §4.3 的偏差）

方案原文写的是 `config/default.yaml`，本实现改用 **JSON**：

- pyproject 承诺**运行时零第三方依赖**（仅 Python 标准库）；YAML 需要 PyYAML，
  JSON 由标准库 `json` 直接提供。
- 代价：JSON 不支持注释 → 键名语义集中记录在本 README（即本文件），键名稳定不变。

## 文件布局

| 文件 | 作用 |
| --- | --- |
| `default.json` | 全部策略旋钮的基准值（单点真源，稳定键名） |
| `aggressive.json` | 示例 profile（进攻倾向：开启召唤骚扰/角色狙击、降低应急金） |
| `<profile>.json` | 任意命名的预设，只写与 default 不同的键 |

## 加载与合并顺序

后加载者覆盖先加载者；object 做**深度合并**（只覆盖出现的键），标量/数组整体替换：

1. 内置默认 `DEFAULT_CONFIG`（`src/future_war/config.py`，与 `default.json` 同构，文件损坏时兜底）
2. `config/default.json`
3. `config/<profile>.json`
4. `FUTURE_WAR_*` 环境变量（最终覆盖）

## Profile 选择

- 命令行：`--profile <name>` 或 `--profile=<name>`（位置任意，见下例）
  - `bash run.sh 18080 --profile aggressive`
  - `python3 -m future_war.server 18080 --profile aggressive`
- 环境变量：`FUTURE_WAR_PROFILE=aggressive`（argv 优先于 env）
- 命名限制：`[A-Za-z0-9_-]`（防御路径穿越）；profile 文件缺失或名称非法 → 告警并回退默认，不崩溃。

## 环境变量覆盖

- 前缀：`FUTURE_WAR_`；键名小写化，嵌套用**双下划线** `__`：
  - `FUTURE_WAR_LOG_LEVEL=DEBUG` → `log.level`（软嵌套，见下）
  - `FUTURE_WAR_COMBAT__TARGET_PRIORITY='["boss","large"]'` → `combat.target_priority`
- 嵌套解析规则：`__` 是硬嵌套分隔；段内 `_` 是键名的一部分（如 `day1_max_weapons`）。
  若整段不是键名，加载器尝试按 `_` **软嵌套**（`log_level` → `log.level`），
  无法解析则按字面键名应用并告警。
- 值按 **JSON 字面量**解析（数字→int/float、`true/false`→bool、`[...]`→list），
  解析失败则作为纯字符串（如 `DEBUG`）。
- 未知键：告警（防拼写错误）但仍生效。
- `FUTURE_WAR_PROFILE` 为保留名（选择 profile，不是配置键）。

## 版本戳

启动时打印一次（`server.py` main），格式 `commit / config-hash / profile`：

```
[future-war] stamp 3f2a1b0 / 9c41d7e2ab03 / aggressive
```

| 部分 | 来源 |
| --- | --- |
| commit | `git rev-parse --short HEAD`；不可用（非 git 仓库 / git 缺失）回退 `unknown` |
| config-hash | 有效配置（合并+env 之后）的规范化 JSON 的 sha256 前 12 位——改任一旋钮都会变 |
| profile | 生效的 profile 名；无 profile 时为 `default` |

`Config.stamp()` 返回该字符串；`Config.get("a.b.c")` 按点分路径取值。

## 键文档

### log — 日志（方案 §4.1）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `log.level` | string | `"EVENT"` | 结构化日志级别：`DIGEST` / `EVENT` / `DECISION` / `TRACE` |
| `log.trace_enabled` | bool | `false` | TRACE 明细行开关 |
| `log.echo_stderr` | bool | `true` | 把事件流镜像到 stderr。**真机上拿不到 `logs/` 目录，平台捕获的 stderr 是唯一可见通道**，所以默认开；回显与文件写健康无关（日志文件写不了也照常回显） |

### server — HTTP 服务与超时留证（任务书 §八；事件码 `X-04`）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `server.slow_round_ms` | int | `3000` | 回合耗时阈值（毫秒）：达到即记一条 `[ERROR] X-04`（带 roundNo 与实测 ms）。判题器响应预算是 5s，默认取 60%；设 `0` 或负数关闭该告警 |

判题器 5s 内收不到响应就判超时，**等真的超时就没有证据了**——本阈值的作用是在
逼近预算时先留下可 grep 的痕迹（`grep 'X-04' logs/*.log`）。它只影响日志，
不影响任何策略行为。调高（如 `4500`）→ 只在真正危险时告警；调低（如 `1000`）
→ 排查性能回归时更敏感。

`X-04` 有两个来源，本键**只管第一个**：① 回合耗时达阈值（带 roundNo）；
② 读请求/请求体超时（socket 层，无 roundNo，由 `server.py` 的连接收尾逻辑记，
并补回一个合法空 Response）。

### build — 建造（方案 §3.1/§3.2）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `build.order` | list | `["weapons","walls","upgrades"]` | 建造阶段顺序 |
| `build.day1_max_weapons` | int | `3` | 第 1 天建武器数（全局上限 3 座，任务书 §4.5.1） |
| `build.weapon_mix` | object | `{rocket:1, railgun:2, gatling:0}` | 武器配比（火箭/电磁狙击炮/加特林，方案 §3.4） |
| `build.upgrade_order` | list | `["rocket_l3","railgun_l3","base_l2","base_l3","wall_l2"]` | 升级券使用优先级 |
| `build.chokepoint_count` | int | `2` | 期望 chokepoint 入口数 |
| `build.wall_labyrinth_depth` | int | `3` | 围墙浅迷宫深度（拖延而非整圈，方案 §3.2） |
| `build.wall_max` | int | `12` | 本局围墙目标数上限（只建基地最内圈，避免围死自己） |
| `build.wall_enabled` | bool | `true` | 围墙总开关；关闭即退回「纯武器防线」基线 |
| `build.wall_probe_from` | int | `45` | 白天第几回合起进入「专门铺墙」时段：之前留给经济（挖矿→贩卖→买券），之后专心试黄区。把两者放在同一优先级会互相饿死（真机实测：墙 0/12 且金币恒为 0） |
| `build.wall_probe_budget` | int | `12` | 每天允许的「探路」失败次数：可建造区是推断的，先用有限次试错探明真区域。本地模拟器 3 个种子实测第 1 天建成的墙数：`6` → 0/0/3，`12` → 3/0/3，`24` → 3/6/3。石头够就调大 |

### combat — 战斗（方案 §3.3/§4.4）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `combat.target_priority` | list | `["boss","large","medium","small"]` | 目标优先级（高→低） |
| `combat.overkill_avoidance` | bool | `true` | 多武器协同防同目标溢出（方案 §3.3） |
| `combat.rocket_aoe_threshold` | int | `3` | 火箭 AoE 开火的集群规模阈值 |
| `combat.controller_pairing` | string | `"range_first"` | 操控者↔武器配对策略（§4.4 C-01/C-02 相关旋钮） |
| `combat.attack_cooldown_turns` | int | `3` | 火箭冷却回合数（任务书 §4.5.1） |
| `combat.staging_night_rounds` | int | `25` | 夜晚前几个回合内，无操控者的武器也会派人过去（机器人从刷出到摸到基地要走十几回合）。设 0 关闭 |

### economy — 经济（方案 §3.1）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `economy.sell_hold_ratio` | float | `0.3` | 库存持有比例（等峰值再卖的持仓上限） |
| `economy.emergency_reserve` | int | `100` | 应急金币保留（范围炸弹/眩晕法宝应对 BOSS 夜） |
| `economy.budget_ratios` | object | `{weapon_upgrade:0.5, base_upgrade:0.3, wall_upgrade:0.2}` | 金币预算分配比（和为 1） |
| `economy.stone_reserve` | int | `4` | 手里常备的修墙石头数，超过的部分可以卖（不留给修墙的石头会被卖光，墙就永远建不起来）。本地模拟器 3 个种子实测第 1 天围墙数：储备 `1` → 1/6/2 · `2` → 2/8/3 · `4` → 6/9/5 · `6` → 7/11/5 · `12` → 7/4/6；储备越大墙越多、金币越少 |
| `economy.sell_batch` | int | `5` | 背够这么多矿石才专程跑一趟小贩；不足就地继续挖（一趟十来回合只换 1 金币不划算） |
| `economy.vendor_peak_window` | int | `5` | 价格峰值判定窗口（回合数） |
| `economy.dusk_return` | int | `70` | 白天第几回合起停止施工、转入黄昏就位。**默认 70 = 白天结束时**，即白天干满、就位交给夜晚（`combat.staging_night_rounds`）。设成 40 会白丢后面 30 个白天回合 |

### consumables — 消耗品（任务书 §4.6.3）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `consumables.medicine_hp_ratio` | float | `0.5` | 低于该血量比例就喝生命药剂（10 金回满） |
| `consumables.wall_hp_ratio` | float | `0.4` | 围墙低于该血量比例就用修复包（10 金回满） |
| `consumables.bomb_min_robots` | int | `2` | 3×3 内至少几只机器人才值得投范围炸弹（100 金 / 100 伤害） |
| `consumables.dizzy_min_robots` | int | `3` | 3×3 内至少几只机器人才值得用眩晕法宝（100 金 / 眩晕 5 回合） |

### defense — 防御（方案 §3.2）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `defense.boss_emergency_rounds` | int | `2` | BOSS 夜提前回防回合数 |
| `defense.base_hp_alert_ratio` | float | `0.5` | 基地血量告警比例（掉到该比例以下视为吃紧） |
| `defense.wall_repair_threshold` | float | `0.4` | 围墙修复包使用阈值（血量比例） |

### offense — 进攻（方案 §3.5，均为开关式）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `offense.enabled` | bool | `false` | 进攻总开关（默认防御优先） |
| `offense.base_snipe_enabled` | bool | `true` | 火箭 L3 空闲冷却时轰敌基地（施压打法） |
| `offense.summon_harass_enabled` | bool | `true` | 余钱买机器人召唤令换击杀分（§4.6.3：中型 15 金/分最优） |
| `offense.summon_reserve` | int | `100` | 召唤保留金：只花超出该值的部分，绝不挤占武器/基地升级 |
| `offense.summon_daily_cap` | int | `10` | 每天召唤令上限（任务书 §4.6.3） |
| `offense.role_snipe_enabled` | bool | `true` | 视野内狙杀敌方角色（默认只在对方残血时出手） |
| `offense.role_snipe_hp_ratio` | float | `0.3` | 角色狙击的血量阈值：只补刀「一发能收掉」的目标 |
| `offense.retreat_hp_ratio` | float | `0.35` | 残血后撤阈值；设 `0` 关闭。阵亡 = 20 回合无武器操控（§4.5.2） |
| `offense.all_in_score_gap` | int | `300` | 积分落后该值以上时升级为持续推家 |

### tasks — 任务（方案 §3.6）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `tasks.self_evolution_enabled` | bool | `true` | 自进化类任务开关 |
| `tasks.treasure_enabled` | bool | `true` | 寻宝开关 |
| `tasks.reasoning_enabled` | bool | `true` | 新闻推理开关（无直接动作，转经济提示） |
| `tasks.treasure_probe_cap` | int | `4` | 寻宝探测次数上限（盲目召唤即消耗物品，§5.2） |
| `tasks.timeout_margin_rounds` | int | `10` | 任务超时安全余量（超时前提前放弃，§4.4 T-03） |

### llm — LLM 配额（方案 §3.7）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `llm.daily_quota` | int | `3` | 每游戏日 LLM 配额（接口 §1.7，errorCode 5） |
| `llm.free_window_enabled` | bool | `true` | 任务期间免费窗口利用开关（方案 §3.7） |

### nav — 寻路（§4.4 N-02/N-03 相关旋钮）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `nav.replan_interval` | int | `5` | 重规划间隔（工人空转/打转时的重新寻路频率） |
| `nav.collision_avoidance` | bool | `true` | 碰撞规避（任务书 §4.5.4） |

### features — 功能开关

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `features.replay_enabled` | bool | `true` | 回合落盘/回放（方案 §4.2） |
| `features.metric_line_enabled` | bool | `true` | 每回合 `[METRIC]` 机器可读指标行（方案 §4.2） |

### world — 可建造区推断（任务书 §4.1 未给坐标表）

| 键 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `world.inference.blue_radius` | int | `3` | 蓝色（武器）可建造区推断半径：到基地块切比雪夫距离 1..R |
| `world.inference.yellow_radius` | int | `6` | 黄色（围墙）可建造区推断半径（与蓝区相减，蓝区优先） |

推断必然不精确：判题器的 `lastRoundRoleActionResults` 反馈会把非法格逐个证伪并
从候选集中剔除（`world_map.apply_build_feedback`），因此这两个半径只影响
「第一次尝试」的命中率，不影响正确性。

## 稳定性约定

- 键名**稳定、只增不改**；改语义时新增键并保留旧键向后兼容。
- 新行为优先走 config 开关，默认关闭 → 一键回退（回滚=改回开关）。
- 变更随附：变更点 / 预期影响 / 观察方式（事件码）/ 回滚方式（方案 §4.5）。
