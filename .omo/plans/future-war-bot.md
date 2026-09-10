# future-war-bot — 《未来战争》v1.0 参赛实现方案

## TL;DR (For humans)

**你会得到什么：** 一个用 Python 实现的长期驻留 HTTP 智能体（Bot），能接收每回合战场状态、返回三角色指令，并具备完整的「白天经济+任务 / 夜晚武器防守」策略闭环；同时配套一个本地游戏模拟器、mock 判题器，以及一套**可观测性体系**（固定格式日志 + 比赛摘要 + 诊断字典），让你能在公司内网用一句话把现象反馈给我。

**为什么这样设计：** 三条承重决策——① **可观测性优先**：公司内网数据不可外传、GitHub 是唯一通道、我看不到真实对局，所以代码必须自带稳定日志与自检摘要，把「观察→一句话→定位→改动」的闭环压到最短；② **规则驱动为主、LLM+沙盒只用于三类认知任务**（价格推理、寻宝推断、自进化任务），因为沙盒仅在任务期间可用、LLM 每游戏日仅 3 次且有 1 回合延迟；③ **防御优先、进攻为辅**，因为基地被毁即判负、生存分高达 550。

**它不会做什么：** 不做强化学习/神经网络训练；不依赖外网；不硬编码具体任务答案或地图坐标；不为冲分牺牲健壮性（进程崩溃=直接判负）。

**Effort:** XL
**Risk:** High — 最大不确定性是机器人出生位置与寻路规则未定义（任务书 §4.7.3）；其次是「真实对局数据不可外传」导致我只能依赖你的观测摘要。
**需要你拍板的点：** ①武器组合默认「1 火箭 + 2 电磁炮」是否认可；②是否接受「首局当侦察局」的学习式布局；③三类任务是否都要做；④日志/报告模板是否符合你的反馈习惯。

你的下一步：审核本方案 → 确认后即可 `$start-work` 按 Wave 0 开始执行。完整执行细节见下。

---

> TL;DR (machine): XL effort, High risk. Deliver Python HTTP bot + local simulator + observability (fixed logs/digest/diagnosis dict) + strategy/task/LLM stack; 6 waves, observability-first, defense-first.

## 一、比赛理解与关键机制摘要

> 所有规则均引用 `docs/任务书.md`（简称《任务书》）与 `docs/接口文档.md`（简称《接口》）。

### 1.1 目标与胜负
- 两队在 41×32 同一张地图对抗，无先后手；每场两轮（上/下半场）互换位置。总积分 = 任务分 `score1` + 击杀分 `score2` + 生存分 `score3`（《任务书》§六）。
- 结束条件：双方异常响应各达 5 次 / 1300 回合耗尽 / 双方基地均被摧毁（§七）。
- 胜负：半场结束按积分高者胜；基地先后被毁则先毁者判负；同回合同时被毁且积分相同为平局。胜 3 分、平 1 分、负 0 分。
- **生存分 `score3` 最高 550**（10×day 累加，基地未被摧毁系数=1），基地被毁当天起系数=0。→ **保基地是硬约束**。

### 1.2 时间与回合
- 白天 70 回合 + 夜晚 60 回合 = 1 天 130 回合，上限 1300 回合（10 天）（§4.2）。
- 每回合双方所有角色**同时**调度；每角色每回合至多 1 条指令。
- 结算顺序：**武器攻击 > 机器人移动**，伤害回合末统一结算（§4.4）。→ 攻击瞄准的是**机器人移动前的位置**。

### 1.3 地图与视野
- 坐标原点左下角，X 右 Y 上（§4.1）。挑战者基地在左上、防守者在右下（§三 + `request.txt` 样例：挑战者 station (10,24)、防守者 (30,10)）。
- 可建造区：蓝色只可建武器、黄色只可建围墙（§4.1）。
- 矿区随机刷新、不在可建造区，每矿可采 10 次后消失下回合随机重刷；不足平分时每工人各得 1（§4.1）。
- 视野：己方所有单位视野 4 且全局共享；**机器人全图可见**；**敌方基地/围墙全局可见**；其余敌方单位需入视野（§4.3）。

### 1.4 单位与建筑
| 单位 | 上限 | 攻击距离 L1/L2/L3 | 血量 L1/L2/L3 | 攻击力 L1/L2/L3 | 造价 |
| --- | --- | --- | --- | --- | --- |
| 基地 | 1 | / | 1500/3000/4500 | / | / |
| 加特林 | 3（三类共） | 3/5/7 | 1000/1500/2000 | 10×1/×2/×3（90°锥内多目标） | 25 金币 |
| 电磁狙击炮 | 3（三类共） | 6/8/10 | 1000/1500/2000 | 10/20/30（单目标，能量穿透） | 25 金币 |
| 火箭发射台 | 3（三类共） | 10/15/**全图** | 1000/1500/2000 | 20×1/×2/×3（AoE，3回合冷却） | 25 金币 |
| 围墙 | 无限 | / | 1000/1500/2000 | / | 石头×1 |

- 武器工事全局最多 3 座；新建为 L1，覆盖同格旧武器（§4.5.1）。
- 三种武器都需**角色站在周围 1 格内操控**才能攻击（§4.4）。
- 角色：开拓者 HP200/背包40/1 个；工人 HP220/背包100/2 个；阵亡后**次日白天开始后 20 回合**在基地复活且背包保留（§4.5.2）。
- 初始：角色×3、基地×1、金币 75（§4.5.3）。

### 1.5 移动与碰撞（§4.5.4）
- 距离用**切比雪夫距离** `max(|dx|,|dy|)`；角色可走 8 方向。
- 碰撞三情形：目标点受阻 / 目标点争夺（多单位移动至同一格）/ 位置互换。
- 结果：与角色碰撞双方停留原地；与障碍物碰撞仅己方移动角色停留；与机器人碰撞双方停留。

### 1.6 中立单位与商店（§4.6）
- 小贩：卖矿石换金币，价格随世界新闻波动。
- 武器商店（全局共享）：卖**升级券**（武器/围墙/基地 L1→L2、L2→L3）、**消耗品**（围墙修复包/生命药剂/眩晕法宝/范围炸弹/机器人召唤令）、**任务用品**（6 种，随地图变化）。
- 升级券需在目标建筑周围 1 格内使用并指定坐标；满级不生效且不消耗；升级后建筑回满血（§4.6.3）。
- 召唤令给**对方**下个夜晚加机器人，每天最多 10 张。

### 1.7 机器人（§4.7）
| 机器人 | 攻击力 | 攻击距离 | HP | 积分 |
| --- | --- | --- | --- | --- |
| 小型 | 5 | 3 | 40 | 1 |
| 中型 | 10 | 3 | 60 | 2 |
| 大型 | 20 | 3 | 500 | 4 |
| BOSS | 40 | 3 | 800 | 10 |

- 夜晚第 1 回合统一出现，数量随天数增加；攻击阻挡其移动的单位；次晨第 1 回合清除残余。
- 召唤令叠加在基础浪潮之上。

### 1.8 任务（§5、§六）
| 类型 | 通道 | 交互 |
| --- | --- | --- |
| 推理类 | 世界新闻·官方消息 | 被动：新闻→矿石价格/可采性变化 |
| 长上下文类 | 世界新闻·民间传闻 | 被动：累积情报→推断宝藏地点/物品/时间→召唤 |
| 自进化类 | 任务点 | 主动：开拓者接取→沙盒交互→提交答案 |

- 自进化任务点各队 2 个；任务结束后需等 30 回合刷新；结束条件=完成/超时/离开任务点 1 格/开拓者死亡（§五）。
- `score1` 完成 = 奖励 + `5 × 标准回合/(实际-接取)`；部分完成 = 奖励 × 通过率（正确字段/全量字段）。
- 宝藏每张图仅 1 个；同回合双方均满足则都获得（§5.2）。

### 1.9 接口与认知资源（《接口》）
- 判题器每回合 POST `Request`，Bot 返回 `Response{roleCommandMap, prompt, executeCmd}`。
- **异步**：`prompt` 本回合提交 → 下回合 `llmResp` 返回；`executeCmd` 本回合提交 → 下回合 `lastCmdResult` 返回。
- **LLM 配额**：每游戏日 3 次（次日第 1 回合重置），`errorCode 5` 表示超限；**自进化任务期间不限次且不计入**。
- **沙盒**：`executeCmd` **仅在任务期间可用**，≤15s，无外网；结果格式 `[exitCode:N]\n<输出>`，超时 `[TIMEOUT]`，超 64KB 追加 `[TRUNCATED]`。
- **异常**：连接 10s / 响应 5s 超时；异常 5 次后不再调度；异常退出不再拉起。指令合法但执行失败**不计异常**。

---

## 二、总体架构

Bot 为**单进程长期驻留服务**（`bash run.sh port` 启动一次），状态跨全部 1300 回合在内存中持续累积，并落盘日志用于回放/调试。**响应必须 <5s，进程绝不能崩溃**（崩溃即判负，§八）。

| # | 模块 | 职责 | 关键输入 → 输出 |
| --- | --- | --- | --- |
| M1 | HTTP 服务层 | 解析/校验 JSON、路由、序列化、硬性 5s 预算 | Request → Response |
| M2 | 世界模型/状态存储 | 规范化类型化状态 + 全历史（地图、角色、机器人、价格、新闻、敌方观测） | 跨回合累积 |
| M3 | 对手建模 | 跟踪敌方可见单位、推断策略、识别敌方推家意图 | 敌方 roles（视野4 + 全局基地/墙） |
| M4 | 规划器/策略引擎 | 每回合目标选择 + 角色→动作分配（大脑） | 世界状态 → 各角色意图 |
| M5 | 寻路与碰撞 | 41×32 网格 BFS/A*，8 方向，切比雪夫，碰撞预测（§4.5.4） | 意图 → `move` |
| M6 | 战斗控制器 | 夜晚：武器↔操控者配对、目标选择、防溢出集火 | 机器人 roles → `attack` |
| M7 | 经济管理器 | 采矿路线、库存、卖出时机、买/升级预算 | 价格/商店 → `collect`/`sell`/`buy` |
| M8 | 建造规划器 | 围墙/武器布局、chokepoint、升级顺序 | 可建造区 → `build`/`use`(券) |
| M9 | 任务求解器（自进化 Agent） | 沙盒+LLM 循环、SOP/技能库 | `phaseTask` → `executeCmd`/`prompt`/`submitAnswer` |
| M10 | 寻宝模块 | 累积传闻→推断地点/物品/时间，用结果码探测 | `folkLegends`、`lastSummonTreasureResult` |
| M11 | 推理引擎 | 解析官方新闻→价格/可采性预测 | `officialNews` → 经济提示 |
| M12 | LLM 管理器 | 3/日配额、异步 prompt↔llmResp 关联、模板 | → `prompt` |
| M13 | 沙盒执行器 | 构造 `executeCmd`、解析 `lastCmdResult` | → `executeCmd` |
| M14 | **可观测性层** | 结构化日志、事件码、比赛摘要、异常自检、配置中心、版本戳 | 全局横切 |
| M15 | 持久化/回放 | 每回合落盘，支持确定性回放与摘要重算 | 磁盘 |

**关键架构事实（决定了模块边界）：**
1. `executeCmd`（沙盒）**仅任务期间可用**（《接口》§2.1）→ 通用计算（寻路/经济/战斗/对手建模）**必须**跑在 Bot 自身代码里，沙盒不能当通用算力。
2. `prompt`/`llmResp` 与 `executeCmd`/`lastCmdResult` 都是**异步一回合往返** → 各模块需以状态机处理「已发出、待响应」，不能阻塞等待。
3. 任务期间 LLM 免费 → LLM 管理器支持「空闲窗口内塞入非任务问题」的策略。
4. **可观测性层是横切关注点**：所有模块必须通过统一日志接口输出带事件码的结构化记录（见 §四）。

---

## 三、制胜策略

### 3.1 经济（采矿、卖出时机、建造顺序）
- **资源价值**：石头=1、铁=3、铜=5 金币（基础价）。铜是现金作物；石头专供围墙（1 墙=1 石头）；铁居中。
- **采矿路线**：矿区 10 次采集后随机重刷（§4.1）→ 每回合重算「最近矿」分配；两工人分派不同矿；**规避同格/同目标碰撞**（§4.5.4 会取消移动）。矿量不足平分时每工人仍得 1，故宁派工人勿闲置。
- **卖出时机**：库存持有到**价格峰值**再卖；`vendorShopList` 每回合给实时价 → 追踪价格序列，结合推理引擎预测峰值（§5.1 塌方例：停采期铁价涨、恢复后回落）。不过度囤积，金币即时转化为升级。
- **建造顺序**（起始 75 金，武器 25 金/座，上限 3 座，§4.5.1）：
  1. **第 1 天**：在基地附近蓝色区建满 3 座武器（共 75 金）——它们是全部攻防手段且机器人逐日增强（§4.7.3）。`build` 直接扣金，无需跑商店。
  2. **第 1–2 天**：采石头→搭围墙骨架（chokepoint）。
  3. **第 2 天起**：采铜/铁→**优先把火箭升 L3**（100+150 金，全图射程是战略解锁）。
  4. **随后**：电磁炮→L3（各 250 金）、基地→L2/L3（100+150 金，血量缓冲）、围墙→L2（20 金/座）。
  5. **保留应急金**（范围炸弹 100、眩晕法宝 100）应对 BOSS 夜。

### 3.2 基地防御布局
- 概念：**围墙承伤**（机器人攻击阻挡单位，§4.7.3）+ **墙后武器输出**。机器人射程 3 → 射程 >3 的武器可安全风筝：**电磁炮 6/8/10**、**火箭 10/15/全图** 安全；加特林 3 会与机器人换血。
- 布局：把机器人导入 1–2 个 **chokepoint**；每个入口放 **1 座电磁炮**（穿透来袭纵列，§4.5.4）；**火箭居中**做 AoE + 全图进攻。围墙做成浅迷宫**拖延**而非整圈（费石头且挡自己，§4.1）。
- 操控配对：3 角色 ↔ 3 武器（上限 3，§4.5.1）。夜晚三角色各站武器周围 1 格（§4.4）；开拓者配火箭（最灵活）、两工人配电磁炮。

### 3.3 昼夜行为
- **白天（70 回合）**：工人采矿+建造；开拓者跑自进化任务+寻宝；商店买升级/消耗品。攻击白天非法（§4.4）→ 纯经济/建设窗口。
- **夜晚（60 回合）**：三角色全部操武器。瞄准机器人**当前**位置（攻击先于机器人移动、回合末结算，§4.4）。优先级 BOSS(800,10) > 大型(500,4) > 中型(60,2) > 小型(40,1)；多武器**协同避免同目标溢出**。BOSS 群备眩晕/炸弹（眩晕 5 回合 / 100 AoE，§4.6.3）。
- **昼夜切换**：夜晚前把开拓者撤回基地；**不要**在天晚时接任务（离开任务点即失败，§五）。

### 3.4 武器选择
**推荐 1×火箭 + 2×电磁炮，均升 L3。**
- 火箭：爆发最高（20/40/60），AoE，**不被墙阻挡**（「指哪打哪」§4.5.4），L3 全图 → 唯一可靠的推家/狙角色武器；缺点 3 回合冷却。
- 电磁炮：无冷却、穿透、L3 30 伤害/回合 → 清波可靠 DPS 骨干。
- 加特林：**跳过**——射程 3 与机器人换血，且 90° 锥约束增加瞄准脆弱性（§4.5.4）。
- 权衡：2×火箭+1×电磁炮爆发/推家更强但清波弱；仅在决定打「基地竞速」时选火箭偏重。

### 3.5 是否/何时进攻敌方基地
- **默认（防御优先）**：不搞孤注一掷的强推。敌基地 1500–4500 血（§4.5.1），单火箭 L3 需 25–75 轮齐射（100–300 回合），是漫长且高暴露的竞速，会牺牲击杀分。
- **施压打法（推荐）**：拥有火箭 L3 后，**无机器人威胁的每个冷却就轰敌基地**。几乎零风险（火箭在自家发射、不需视野、导弹不被阻挡），迫使对方花金币升/修基地，若对方忽视可直接取胜。
- **升级为推家的条件**：积分落后（半场平局看总分，§七）或观测到敌基地低血/未升级。
- **骚扰替代**：余钱买**机器人召唤令**（§4.6.3：小20/中30/大100/BOSS200，每天≤10）灌对方下夜——间接进攻且不暴露；后期领先时封盘最佳。
- **狙敌方角色（可选）**：火箭狙敌方开拓者以阻断其任务/寻宝；仅在其进入视野 4 时可行（§4.3），优先级低于基地与防守。

### 3.6 任务策略
- **自进化类（最高 ROI）**：见 §四。速度快有加成（`5×标准/(实际-接取)`），通用 Agent + 技能库加速。
- **长上下文寻宝**：宝藏含大量积分+金币，值得投入但设上限。
- **推理类**：无直接动作，转化为卖出时机与采矿路线调整。
- **调度**：开拓者白天优先接自进化任务（早接早解，避免跨夜超时），有富余窗口做寻宝；夜晚回防。

### 3.7 LLM 预算策略（3 次/日 + 异步）
- **分配**：
  1. **日初新闻包**（1 次）：把 `officialNews` + `folkLegends` 一起喂，要结构化 JSON `{价格影响, 寻宝线索}`——一次调用覆盖两任务。
  2. **寻宝推断**（1 次，偶尔，传闻积累后）。
  3. **机动**（1 次）：高层战略转向（如「现在是否推家」）。
- **免费窗口利用**：`phaseTask` 非空（自进化任务进行中）时 LLM 不限次不计入（《接口》§1.7）→ 任务期间顺带跑寻宝/战略分析。权衡：拖延任务会损失速度加成，仅在有排队问题时延长。
- **异步处理**：`prompt` 本回合→`llmResp` 下回合；LLM 管理器把响应当状态机事件处理，提前一回合发送。

---

## 四、调试与迭代工作流（可观测性优先）

### 4.0 工作流约束与设计目标
- 比赛在**公司内网**进行，**内部数据不可外传**；GitHub 是唯一双向通道：我 push 代码 → 你在公司 `git pull` → 公司平台跑对战 → 你观察日志 → **用一句话**告诉我改动点 → 我改 → 再 push，循环优化。
- 因此：**我看不到任何真实对局数据**。一切诊断必须靠「稳定日志 + 你的一句话」完成。
- 设计目标：把「观察 → 一句话 → 定位 → 改动 → 验证」的闭环压到**最短、最不易失真**。

### 4.1 结构化日志（固定格式，可 grep）
- 单文件 `logs/match_<utc>_<commit>.log`，稳定、自解释、**无需任何工具**即可阅读。
- 四级：`DIGEST`（每场/每天摘要）、`EVENT`（关键事件）、`DECISION`（决策原因）、`TRACE`（细节，开关控制）。
- 固定标签：`[INIT][ECON][NAV][BUILD][COMBAT][TASK][TREASURE][LLM][SANDBOX][OPP][ERROR][ANOMALY][DIGEST]`。
- 单行格式：`<round> <phase> <TAG> <CODE> <message> <k=v ...>`，例：
  - `0085 D [ECON] E-01 collect worker=10010 target=stone@(4,24) got=1 gold=20`
  - `0120 N [COMBAT] C-03 attack weapon=10040 ctrl=10011 target=(4,5) dmg=20 cd=3`
  - `0130 - [DIGEST] day=1 score=… gold=45 kills=6 baseHP=1500 deaths=0 errors=0`
- **稳定事件码**：每个决策点/异常一个固定 code，便于你一句话引用（如「日志里 E-01 重复了 40 回合」）。

### 4.2 结构化日志是唯一必需的诊断产物（你方内部 LLM 负责总结）
- **唯一硬要求：输出完整、稳定、机器可读的结构化日志**；你方用内部大模型对日志做总结/异常识别，我方**不再自建重型「摘要/异常自检」**。
- 因此日志必须**字段完整、格式稳定、单行自解释**，便于 LLM 直接归纳（每回合状态、决策、结果、错误全部落盘，含 judge `errors[]`）。
- 我方仅保留一行**机器可读关键指标**（`[METRIC]` 行：gold/kills/score/baseHP/rolesAlive/errors 等），方便 LLM 快速定位；不做人工摘要渲染。
- 支持 `replay` 模式：对已记录对局重跑（无需重跑平台）。

### 4.3 配置中心与版本戳（让改动可归因）
- 所有策略旋钮集中在 `config/default.yaml`（建造顺序、武器配比、各类阈值、预算比例、日志级别、功能开关），带注释与**稳定键名**。
- 支持 `--profile` 预设与 env 覆盖；启动打印 `commit / config-hash / profile`。
- 每次改动**只动一处**（one knob per iteration），用开关控制，便于归因与回滚。

### 4.4 诊断字典与报告协议（把现象映射到参数）
- `docs/诊断字典.md`：**现象 → 模块 → 事件码 → 可调参数** 的查找表。
- `REPORT_TEMPLATE.md`：一句话报告模板：
  `[版本=<commit>][第N天/昼或夜][现象=<...>][证据=<TAG CODE>]`
  例：`版本=a1b2c3 第3天夜 基地掉到400 只建了1座武器 工人10012卡在(5,5) 证据=[ECON]E-01×40`
- 常见现象预置映射（示意）：
  | 现象 | 模块 | 事件码 | 旋钮 |
  | --- | --- | --- | --- |
  | 工人原地打转/空转 | NAV | N-02/N-03 | `nav.replan_interval`、碰撞规避 |
  | 武器夜里不开火 | COMBAT | C-01/C-02 | 操控者配对、射程校验 |
  | 金币不足升级慢 | ECON | E-04 | 采矿路线、卖出时机、预算比例 |
  | 任务老超时 | TASK | T-03 | 任务调度、超时余量 |
  | 基地夜里掉血快 | COMBAT/BUILD | C-05/B-02 | 布局、围墙、目标优先级 |
  | LLM 报 errorCode 5 | LLM | L-01 | 配额策略 |

### 4.5 迭代纪律
- 每个改动随附：变更点、预期影响、**如何观察验证**（对应事件码）、回滚方式。
- 提交信息关联你的报告（如 `fix(combat): pair controller by range (#R7)`），便于追溯。
- 优先小步、可回滚、向后兼容；新行为尽量走 config 开关，默认可一键退回。

### 4.6 与本地模拟器的关系
- 本地模拟器是**逻辑回归**工具（我在本地验证正确性；你在公司内也可 pull 后本地复现），但它**不保证与官方平台一致**。
- **真实对局观测是唯一事实来源**；模拟器用于「改动前不引入明显回归」。

### 4.7 工作流示意
```
我 push ──▶ 公司 git pull ──▶ 内网平台跑对战 ──▶ 你读固定日志/摘要
   ▲                                                   │
   └──────────── 你一句话反馈 ◀────────────────────────┘
```

---

## Scope

### Must have
- Python Bot：接口层、世界模型、规划器、寻路/碰撞、经济、战斗、建造、任务求解、寻宝、推理、LLM/沙盒管理（M1–M13）。
- **可观测性体系（M14，横切）**：结构化日志与事件码、机器可读指标行、配置中心与版本戳、诊断字典。
- 本地游戏模拟器 + mock 判题器 + 自对弈 + 记录回放（离线可复现）。
- 三类任务实现与联动。
- 进攻策略（火箭狙击 / 召唤骚扰 / 角色狙击）。
- 健壮性：5s 超时预算、异常/畸形输入容错、沙盒 TIMEOUT/TRUNCATED 处理、LLM 配额自管理。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **不做**强化学习/神经网络训练；策略为规则+启发式，LLM 仅用于三类认知任务。
- **不依赖外网**（沙盒无外网；Bot 不假设外部服务可用）。
- **不硬编码**具体任务答案、地图坐标、机器人出生点（地图/任务/刷新随机）。
- **不为分数牺牲健壮性**：任何未捕获异常导致进程退出=直接判负（§八）。
- **不修改**官方判题器/接口契约；不引入不可控的第三方运行时依赖（需先确认《编译运行环境说明》）。
- **不依赖人工传数据**：任何诊断信息必须能从固定日志/摘要中读出，不假设能外传原始数据。

---

## Verification strategy

> 零人工介入——所有验证均为可执行/可复现。

- **Test decision:** tests-after 为主 + 关键路径 TDD（模拟器规则、寻路、碰撞、接口编解码、经济决策、目标选择、日志格式先写测试）。框架：`pytest`。
- **本地模拟器 = 第一公民**：自实现《任务书》规则（或可扩展子集）+ mock 判题器（发 Request、校验 Response、驱动回合），用固定随机种子。
- **日志契约测试**：断言关键事件码在给定场景下出现/不出现，保证日志格式稳定（诊断字典依赖它）。
- **自对弈**：跑两份 Bot（挑战者/防守者）互打，度量胜率与分数差，比较策略变体（武器配比/建造顺序/推家阈值）。
- **记录回放**：每回合落盘 `(Request, Response, Result)`，支持确定性重放与摘要重算；用 `docs/request.txt`/`docs/response.txt` 做 fixture。
- **Mock LLM / Mock 沙盒**：桩化 `llmResp` 与 `lastCmdResult` 保证可复现；另用脚本化沙盒单测任务求解循环。
- **故障注入**：5s 响应超时、畸形 Request、全部 `lastSummonTreasureResult` 码（0/1/2/3/4）、`[TIMEOUT]`/`[TRUNCATED]`、LLM 配额溢出。
- **Evidence:** `.omo/evidence/task-<N>-future-war-bot.<ext>`（测试日志/对局回放/摘要/分数报告）。

---

## Execution strategy

### Parallel execution waves
> 每 Wave 目标 5–8 个工作包；Wave 之间按依赖串行，Wave 内尽量并行。

- **Wave 0 — 基础设施与可观测性**（1–8）：脚手架、接口模型、模拟器/mock 判题器、回放、结构化日志、日志完整性/机器可读指标、配置中心、诊断字典。
- **Wave 1 — Bot 基线**（9–13）：世界模型、寻路/碰撞、基础经济、基础防御、端到端存活。
- **Wave 2 — 策略引擎**（14–18）：建造规划、经济优化、战斗协同、对手建模、自对弈调参。
- **Wave 3 — 任务系统**（19–22）：推理、寻宝、自进化 Agent、任务调度。
- **Wave 4 — 认知集成**（23–26）：LLM 管理器、沙盒执行器、免费窗口利用、认知鲁棒性。
- **Wave 5 — 进攻与调优**（27–31）：火箭狙击、召唤骚扰、角色狙击、端到端调优、容错加固。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 脚手架 | — | 全部 | — |
| 2 接口模型 | 1 | 9–13, 23–26 | 3, 4 |
| 3 模拟器/mock判题器 | 1 | 13, 18, 30 | 2, 4 |
| 4 回放系统 | 1 | 5, 6, 18, 30 | 2, 3 |
| 5 结构化日志 | 4 | 全部（横切） | 6, 7, 8 |
| 6 日志完整性/指标 | 4, 5 | 30 | 5, 7, 8 |
| 7 配置中心与版本戳 | 1 | 全部（横切） | 5, 6, 8 |
| 8 诊断字典/报告协议 | 5, 7 | 交付 | 5, 6, 7 |
| 9 世界模型 | 2 | 10–13, 14–18, 19–22 | — |
| 10 寻路/碰撞 | 9 | 11, 12, 14–18, 19–22 | 11, 12 |
| 11 基础经济 | 9, 10 | 15 | 12 |
| 12 基础防御 | 9, 10 | 16 | 11 |
| 13 端到端存活 | 3, 11, 12 | 14–18 | — |
| 14 建造规划 | 13 | 16, 27–31 | 15, 17 |
| 15 经济优化 | 11, 13 | 19, 27–31 | 14, 17 |
| 16 战斗协同 | 12, 13 | 27 | 14, 15 |
| 17 对手建模/侦察 | 13 | 27, 28, 29 | 14, 15 |
| 18 自对弈调参 | 3, 4, 13 | 30 | — |
| 19 推理类 | 15, 23 | 30 | 20, 22 |
| 20 寻宝 | 10, 23 | 30 | 19, 22 |
| 21 自进化 Agent | 23, 24 | 22, 30 | 19, 20 |
| 22 任务调度 | 10, 19, 20, 21 | 30 | — |
| 23 LLM 管理器 | 2 | 19, 20, 21 | 24 |
| 24 沙盒执行器 | 2 | 21 | 23 |
| 25 免费窗口利用 | 21, 23 | 30 | 26 |
| 26 认知鲁棒性 | 23, 24 | 30 | 25 |
| 27 火箭狙击 | 16, 17, 23 | 30 | 28, 29 |
| 28 召唤骚扰 | 15, 17 | 30 | 27, 29 |
| 29 角色狙击 | 16, 17 | 30 | 27, 28 |
| 30 端到端调优 | 全部 | 交付 | — |
| 31 容错加固 | 13, 26 | 交付 | 30 |

---

## Todos

> 实现 + 测试 = 一个工作包；不拆分。每个工作包含：References（执行者无访谈上下文）、可执行的验收标准、happy + failure QA 场景与证据路径、Commit。

### Wave 0 — 基础设施与可观测性

- [x] 1. 项目脚手架与运行入口
  What to do / Must NOT do: 建立 Python 项目结构（`src/`、`tests/`、`scripts/`、`config/`、`logs/`），`run.sh port` 启动 HTTP 服务（监听 0.0.0.0:port，最小依赖，优先标准库 `http.server` 或 `FastAPI`），依赖锁定；**不要**引入需外网或重型运行时依赖（待《编译运行环境说明》确认）。
  Parallelization: Wave 0 | Blocked by: — | Blocks: 全部
  References: 《接口》开篇「样例：bash run.sh port」；《任务书》§八 超时约束。
  Acceptance criteria: `bash run.sh 8080` 后 `curl -s -X POST localhost:8080 -d @docs/request.txt` 返回可解析 JSON（可先返回空 `roleCommandMap`）。
  QA scenarios: happy=`curl` 得 200 且 JSON 合法；failure=端口占用/畸形 body 时服务不崩溃并返回合法 JSON。Evidence `.omo/evidence/task-1-future-war-bot.log`
  Commit: Y | chore(scaffold): add python bot skeleton and run.sh

- [x] 2. 接口数据模型与编解码
  What to do / Must NOT do: 用 Pydantic/dataclass 定义 `Request`/`Response`/`RoleCommand`/`Role`/`Zone`/`Robot`/`PlayerTask`/`WorldNews`/`Error` 等；JSON 解析与序列化；字段缺省容错（如 `num` 缺省 1、可选字段缺失）。**不要**在解析层抛未捕获异常。
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 9–13, 23–26
  References: 《接口》§1.1–§1.7、§2.1–§2.3；fixtures `docs/request.txt`、`docs/response.txt`。
  Acceptance criteria: 用 `docs/request.txt` 反序列化成功且关键字段（roundNo、teamOur.roles、robot.roles、vendorShopList、weaponShopList）一致；构造 `docs/response.txt` 结构可序列化。
  QA scenarios: happy=fixture 往返 round-trip 相等；failure=删字段/加未知字段仍能解析（或明确报可捕获错误）。Evidence `.omo/evidence/task-2-future-war-bot.json`
  Commit: Y | feat(api): add request/response models and codec

- [x] 3. 本地游戏模拟器 + mock 判题器
  What to do / Must NOT do: 实现《任务书》规则核心（地图/视野/移动碰撞/采集/建造/攻击结算/昼夜/机器人波次/任务点/寻宝结果码）或可扩展子集；mock 判题器按回合发 Request、收 Response、推进状态、记录结果。**不要**追求与官方 100% 一致（明确标注简化点）。
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 13, 18, 30
  References: 《任务书》§4.1–§4.8、§5、§六、§七；《接口》§1/§2。
  Acceptance criteria: 跑一场脚本化对局（固定种子）产出分数报告与最终状态；给定固定输入序列输出确定（同种子可复现）。
  QA scenarios: happy=两假 Bot 对局正常结束并给出 `score1/2/3`；failure=Bot 返回非法指令/超时被正确标记且不计入队伍异常（§八）。Evidence `.omo/evidence/task-3-future-war-bot.replay`
  Commit: Y | feat(sim): add local game simulator and mock judge

- [x] 4. 日志与回放系统
  What to do / Must NOT do: 每回合落盘 `(roundNo, Request, Response, Result)` 到 `logs/`；提供回放脚本可确定性重放并 diff。**不要**把日志写入热路径导致 5s 超时。
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 5, 6, 18, 30
  References: 《接口》§1.1（`lastRoundRoleActionResults`、`lastCmdResult`）；本方案 §4.2（replay 模式）。
  Acceptance criteria: 一次对局产生完整可回放日志；回放脚本对同日志两次重放输出一致。
  QA scenarios: happy=回放得分与原始一致；failure=截断/损坏日志被检测并报错而非崩溃。Evidence `.omo/evidence/task-4-future-war-bot.log`
  Commit: Y | feat(logging): add round logger and deterministic replay

- [x] 5. 结构化日志与事件码体系
  What to do / Must NOT do: 实现分层日志（`DIGEST`/`EVENT`/`DECISION`/`TRACE`）+ 固定标签 + **稳定事件码** + 单行键值格式 `<round> <phase> <TAG> <CODE> <msg> <k=v>`；级别可配。**不要**用自由文本导致无法 grep；不要把日志写热路径拖慢响应。
  Parallelization: Wave 0 | Blocked by: 4 | Blocks: 全部（横切）
  References: 本方案 §4.1；《接口》§1.1（errors/lastRoundRoleActionResults/lastCmdResult）。
  Acceptance criteria: 跑一场模拟对局生成日志，任意 `[COMBAT]`/`[ECON]` 事件可按 code 检索；TRACE 关闭时无 TRACE 行。
  QA scenarios: happy=日志可 grep 且字段稳定；failure=日志写入异常时降级不崩溃。Evidence `.omo/evidence/task-5-future-war-bot.log`
  Commit: Y | feat(observability): add structured logging and event codes

- [x] 6. 日志完整性与机器可读指标（供内部 LLM 总结）
  What to do / Must NOT do: 确保每回合关键状态/决策/结果/错误**全部落盘且格式稳定**（便于你方内部 LLM 直接归纳）；每回合额外输出一行机器可读 `[METRIC]`（gold/kills/score/baseHP/rolesAlive/errors 等）。**不要**自建重型人工摘要/异常检测渲染（改由你方内部 LLM 负责）；不要遗漏任何回合。
  Parallelization: Wave 0 | Blocked by: 4, 5 | Blocks: 30
  References: 本方案 §4.2；《任务书》§六（积分分解）、§4.7；《接口》§1.1（errors/lastRoundRoleActionResults/lastCmdResult）。
  Acceptance criteria: 一场对局每个回合都有完整日志与一行 `[METRIC]`；字段可被脚本解析为表格。
  QA scenarios: happy=日志无缺回合、指标可解析；failure=异常回合也完整记录（含 judge errors[]）。Evidence `.omo/evidence/task-6-future-war-bot.metrics.csv`
  Commit: Y | feat(observability): ensure complete structured logs and metric lines

- [x] 7. 配置中心与版本戳
  What to do / Must NOT do: 集中 `config/default.yaml` 策略参数（建造顺序/武器配比/阈值/预算比例/日志级别/功能开关），带注释与稳定键名；支持 `--profile` 与 env 覆盖；启动打印 `commit / config-hash / profile`。**不要**把可调参数散落在代码里。
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 全部（横切）
  References: 本方案 §4.3；工作包 1。
  Acceptance criteria: 改一个 YAML 值即可改变行为；启动日志含版本戳。
  QA scenarios: happy=参数生效；failure=非法配置时回退默认并告警。Evidence `.omo/evidence/task-7-future-war-bot.log`
  Commit: Y | feat(config): add central config and version stamp

- [x] 8. 诊断字典与报告协议
  What to do / Must NOT do: 编写 `docs/诊断字典.md`（现象→模块→事件码→可调参数）+ `REPORT_TEMPLATE.md`（用户一句话报告模板），覆盖常见症状。**不要**假设用户能外传日志/数据。
  Parallelization: Wave 0 | Blocked by: 5, 7 | Blocks: 交付
  References: 本方案 §4.4；工作包 5/6/7。
  Acceptance criteria: 按模板填一句话即可定位到模块与参数（用 ≥3 个示例现象验证）。
  QA scenarios: happy=示例现象映射到正确模块；failure=未知现象有「需要更多信息」的引导。Evidence `.omo/evidence/task-8-future-war-bot.md`
  Commit: Y | docs(observability): add diagnosis dictionary and report protocol

### Wave 1 — Bot 基线

- [x] 9. 世界模型与跨回合状态存储
  What to do / Must NOT do: 规范化状态：静态地图（zones、基地、可建造区推断）、动态（角色/机器人/建筑/金币/价格）、历史（新闻、价格序列、机器人数量、敌方观测、任务结果）；提供查询接口。**不要**在每回合重建丢失历史。
  Parallelization: Wave 1 | Blocked by: 2 | Blocks: 10–13, 14–18, 19–22
  References: 《接口》§1.2–§1.6；《任务书》§4.3（视野）、§4.1（地图）。
  Acceptance criteria: 连续喂入多回合 Request，历史字段（价格序列、新闻列表、机器人计数）正确累积。
  QA scenarios: happy=多回合状态一致；failure=某回合字段缺失时用上回合/默认值兜底且不崩溃。Evidence `.omo/evidence/task-9-future-war-bot.json`
  Commit: Y | feat(core): add world model and cross-round state

- [x] 10. 寻路与碰撞规避
  What to do / Must NOT do: 网格 BFS/A*，8 方向，切比雪夫距离；障碍=建筑/角色/机器人/中立单位/任务点/矿区（§4.1）；预测并规避目标点受阻/争夺/互换三类碰撞（§4.5.4）。**不要**产生与已知障碍重叠的移动目标。
  Parallelization: Wave 1 | Blocked by: 9 | Blocks: 11, 12, 14–18, 19–22
  References: 《任务书》§4.5.4、§4.1「以下元素均会阻挡角色移动」。
  Acceptance criteria: 随机障碍网格中从 A 到 B 返回合法路径且每步相邻（切比雪夫=1）；模拟多角色同目标时无人被判定非法移动。
  QA scenarios: happy=路径可达且无碰撞；failure=目标被围死时返回「原地等待」而非非法移动。Evidence `.omo/evidence/task-10-future-war-bot.txt`
  Commit: Y | feat(nav): add pathfinding and collision avoidance

- [x] 11. 基础经济（采集/贩卖/建造）
  What to do / Must NOT do: 工人就近采矿（石头/铁/铜）、在小贩旁批量贩卖、在蓝色区建造武器（`build` 扣金）、在黄色区建墙（耗石头）。**不要**让工人闲置或反复无效移动。
  Parallelization: Wave 1 | Blocked by: 9, 10 | Blocks: 15
  References: 《任务书》§4.4、§4.5.1、§4.6.1；《接口》§2.3。
  Acceptance criteria: 模拟对局中金币与背包随采矿/贩卖单调合理变化；第 1 天结束前建成 ≥2 座武器。
  QA scenarios: happy=采集→贩卖金币增加；failure=背包满/金币不足时 `buy`/`build` 失败被识别且不报异常。Evidence `.omo/evidence/task-11-future-war-bot.replay`
  Commit: Y | feat(economy): add mining, selling and building basics

- [x] 12. 基础防御（夜晚武器操控）
  What to do / Must NOT do: 夜晚为每座武器分配 1 名角色操控（站周围 1 格）；按机器人当前坐标选目标 `attack`（含 `controllerId`、`targetPos`）；火箭按冷却发火。**不要**白天攻击（非法）、不要重复操控同一武器。
  Parallelization: Wave 1 | Blocked by: 9, 10 | Blocks: 16
  References: 《任务书》§4.4（攻击仅夜/需操控/结算顺序）、§4.5.4；《接口》§2.2–§2.3。
  Acceptance criteria: 模拟夜间至少一次合法攻击并造成机器人掉血；`lastRoundRoleActionResults` 对应角色为 true。
  QA scenarios: happy=机器人被击杀计入 `score2`；failure=无目标时保持待命且不产生非法指令。Evidence `.omo/evidence/task-12-future-war-bot.replay`
  Commit: Y | feat(combat): add night weapon control baseline

- [x] 13. 端到端存活基线
  What to do / Must NOT do: 整合 9–12，在模拟器中完成「建武器→采资源→夜晚防守」闭环并存活到第 N 天，并输出日志与摘要。**不要**在此阶段引入任务/LLM/进攻。
  Parallelization: Wave 1 | Blocked by: 3, 11, 12 | Blocks: 14–18
  References: 《任务书》§4.7（波次）、§六（生存分）；本方案 §4.1/§4.2（日志/摘要）。
  Acceptance criteria: 固定种子下 Bot 在模拟器中存活 ≥3 天且基地未被摧毁；无未捕获异常；产出可读日志与摘要。
  QA scenarios: happy=存活并累计 `score3`；failure=某夜防线崩溃时能观测并输出诊断（不崩溃）。Evidence `.omo/evidence/task-13-future-war-bot.replay`
  Commit: Y | feat(core): integrate survivable end-to-end baseline

### Wave 2 — 策略引擎

- [x] 14. 建造规划（武器布局 + chokepoint + 升级顺序）
  What to do / Must NOT do: 依据可建造区与地形生成武器/围墙布局（入口 chokepoint、火箭居中、电磁炮守入口）；实现升级券使用顺序（火箭 L3 优先）。**不要**用整圈围墙堵死自己角色路径。
  Parallelization: Wave 2 | Blocked by: 13 | Blocks: 16, 27–31
  References: 《任务书》§4.1、§4.5.1、§4.5.4、§4.6.3。
  Acceptance criteria: 布局在模拟器中合法（全在对应色区）、角色可达武器操控位、升级顺序按预算执行。
  QA scenarios: happy=布局生效并提升存活天数；failure=蓝/黄区用错导致建造失败被检测并纠正。Evidence `.omo/evidence/task-14-future-war-bot.replay`
  Commit: Y | feat(build): add layout planner and upgrade sequencing

- [x] 15. 经济优化（路线 + 卖出时机 + 预算）
  What to do / Must NOT do: 动态最近矿分配、双工人分流防碰撞、按预测价格择时卖出、按优先级分配金币（武器升级>基地>围墙）。**不要**囤积到错过升级窗口。
  Parallelization: Wave 2 | Blocked by: 11, 13 | Blocks: 19, 27–31
  References: 《任务书》§4.1、§4.6.1、§5.1、§4.5.1。
  Acceptance criteria: 相同地图下优化后金币/升级进度优于基线；无因碰撞导致的连续空转。
  QA scenarios: happy=收入速率提升且按时升级；failure=价格预测错误时回退到基础价策略。Evidence `.omo/evidence/task-15-future-war-bot.replay`
  Commit: Y | feat(economy): optimize routing, sell timing and budget

- [x] 16. 战斗协同（多武器 + 防溢出 + 配对）
  What to do / Must NOT do: 多武器目标分配避免同目标溢出伤害；火箭 AoE 打集群、电磁炮穿透打纵列；BOSS/大型优先；武器-角色最优配对。**不要**让多武器重复瞄准将被击杀的低血目标。
  Parallelization: Wave 2 | Blocked by: 12, 13 | Blocks: 27
  References: 《任务书》§4.5.1、§4.5.4、§4.7.2、§4.4。
  Acceptance criteria: 相同波次下击杀数与剩余基地血量优于基线；伤害浪费率下降。
  QA scenarios: happy=一波机器人全清；failure=目标超出射程/被墙挡（电磁炮/加特林）时改选合法目标。Evidence `.omo/evidence/task-16-future-war-bot.replay`
  Commit: Y | feat(combat): add multi-weapon coordination

- [x] 17. 对手建模与侦察适配
  What to do / Must NOT do: 记录敌方可见单位轨迹与全局可见的基地/围墙变化，推断敌方武器配比/推家意图；从首局观测学习机器人出生位置与路径并据此调整布局。**不要**假设固定的机器人出生点。
  Parallelization: Wave 2 | Blocked by: 13 | Blocks: 27, 28, 29
  References: 《任务书》§4.3（视野/全局可见）、§4.7.3（行为未定义点）。
  Acceptance criteria: 模拟器可注入「未知出生点」场景，Bot 观测后布局调整并提升存活率。
  QA scenarios: happy=出生点改变后布局自适应；failure=无观测时用保守默认布局。Evidence `.omo/evidence/task-17-future-war-bot.replay`
  Commit: Y | feat(opponent): add opponent modeling and recon adaptation

- [x] 18. 自对弈调参框架
  What to do / Must NOT do: 用模拟器跑 Bot vs Bot（同/异策略），批量对比变体（武器配比/建造顺序/推家阈值），输出胜率与分数报告。**不要**用单次结果下结论（需多种子统计）。
  Parallelization: Wave 2 | Blocked by: 3, 4, 13 | Blocks: 30
  References: 工作包 3/4 产物；《任务书》§六/§七（评分）。
  Acceptance criteria: 一条命令跑 N 局多种子并输出可比较的指标表。
  QA scenarios: happy=变体对比稳定可复现；failure=对局异常时记录并继续下一局。Evidence `.omo/evidence/task-18-future-war-bot.csv`
  Commit: Y | test(tuning): add self-play evaluation harness

### Wave 3 — 任务系统

- [x] 19. 推理类（新闻→价格/可采性）
  What to do / Must NOT do: 解析 `officialNews` 为结构化影响（矿种、方向、持续、是否停采），联动经济（预囤涨价矿、避开停采矿、停采前抢采）。**不要**依赖精确量级（规则未给）。
  Parallelization: Wave 3 | Blocked by: 15, 23 | Blocks: 30
  References: 《任务书》§4.8、§5.1；《接口》§1.6。
  Acceptance criteria: 给定塌方类新闻，Bot 在停采期正确改采他矿并在涨价期卖出囤货。
  QA scenarios: happy=价格预测方向正确；failure=解析失败回退基础价策略。Evidence `.omo/evidence/task-19-future-war-bot.json`
  Commit: Y | feat(tasks): add official-news price reasoning

- [ ] 20. 长上下文寻宝
  What to do / Must NOT do: 跨天累积 `folkLegends`，推断宝藏地点/所需物品/开启时间；用 `lastSummonTreasureResult`（2=地点/时间错，3=物品错）做探测，必要时穷举物品子集；设投入上限。**不要**盲目反复召唤（合法即消耗物品）。
  Parallelization: Wave 3 | Blocked by: 10, 23 | Blocks: 30
  References: 《任务书》§5.2；《接口》§1.1（结果码）、§2.3（summonTreasure 消耗规则）。
  Acceptance criteria: 构造的线索集中能推断出正确召唤并获宝藏；探测次数受上限约束。
  QA scenarios: happy=正确召唤得奖励；failure=错误地点返回 2、错误物品返回 3 且据此更新假设。Evidence `.omo/evidence/task-20-future-war-bot.json`
  Commit: Y | feat(tasks): add long-context treasure hunt

- [ ] 21. 自进化 Agent（沙盒 + LLM 循环 + 技能库）
  What to do / Must NOT do: `acceptTask`→读 `phaseTask`→规划→`executeCmd` 沙盒探索→读 `lastCmdResult`/`llmResp`→迭代→`submitAnswer`；把成功流程抽象为 SOP/技能存库，同类任务复用加速。**不要**硬编码具体任务；不要离开任务点导致任务中断。
  Parallelization: Wave 3 | Blocked by: 23, 24 | Blocks: 22, 30
  References: 《任务书》§5.3、§五（结束条件）、§六（score1 速度加成）；《接口》§2.1、§1.7。
  Acceptance criteria: 脚本化沙盒任务集上，第 2 个同类任务比第 1 个显著更快完成（技能复用生效）且答案正确。
  QA scenarios: happy=任务完成且通过率高；failure=`[TIMEOUT]`/`[TRUNCATED]`/答案错误时降级重试或提交最优答案。Evidence `.omo/evidence/task-21-future-war-bot.json`
  Commit: Y | feat(tasks): add self-evolution sandbox agent

- [ ] 22. 任务调度
  What to do / Must NOT do: 开拓者日程：白天早接自进化任务、避免跨夜超时、有富余做寻宝、夜晚回防。**不要**在天晚时接任务或在任务中离点。
  Parallelization: Wave 3 | Blocked by: 10, 19, 20, 21 | Blocks: 30
  References: 《任务书》§4.2、§五、§5.2；《接口》§1.3.2（`timeoutRounds`）。
  Acceptance criteria: 模拟中开拓者按时接/交任务且从未因离开任务点导致失败。
  QA scenarios: happy=任务在超时前提交；failure=时间不足时提前放弃避免无效消耗。Evidence `.omo/evidence/task-22-future-war-bot.replay`
  Commit: Y | feat(tasks): add pioneer task scheduling

### Wave 4 — 认知集成

- [ ] 23. LLM 管理器（配额 + 异步 + 模板）
  What to do / Must NOT do: 自维护 3/日配额计数（日初重置）、异步 `prompt`→`llmResp` 关联为状态机、prompt 模板与结构化响应解析、失败回退。**不要**超额调用（避免 errorCode 5）或阻塞等待响应。
  Parallelization: Wave 4 | Blocked by: 2 | Blocks: 19, 20, 21
  References: 《接口》§1.7（配额/errorCode 5）、§2.1（prompt/llmResp）。
  Acceptance criteria: 模拟中配额计数正确、超限前不再发 prompt；异步响应正确配对。
  QA scenarios: happy=3 次调用后暂停至次日；failure=LLM 返回空/异常时走回退逻辑。Evidence `.omo/evidence/task-23-future-war-bot.json`
  Commit: Y | feat(llm): add quota and async LLM manager

- [ ] 24. 沙盒执行器
  What to do / Must NOT do: 构造 `executeCmd`、解析 `lastCmdResult`（`[exitCode:N]`/`[TIMEOUT]`/`[TRUNCATED]`）、管理沙盒会话/文件状态；仅任务期间使用。**不要**在非任务回合发 `executeCmd`。
  Parallelization: Wave 4 | Blocked by: 2 | Blocks: 21
  References: 《接口》§2.1、§1.1（`lastCmdResult` 格式）。
  Acceptance criteria: 各类返回格式被正确解析并暴露退出码/输出/截断标记。
  QA scenarios: happy=正常命令解析；failure=TIMEOUT/TRUNCATED 被识别并触发分片重试。Evidence `.omo/evidence/task-24-future-war-bot.json`
  Commit: Y | feat(sandbox): add sandbox executor and result parser

- [ ] 25. 免费 LLM 窗口利用
  What to do / Must NOT do: 在 `phaseTask` 非空期间，把排队的战略/寻宝分析搭车发出（不限次不计入）；控制延长任务的代价。**不要**为蹭免费窗口而显著损失速度加成或导致任务超时。
  Parallelization: Wave 4 | Blocked by: 21, 23 | Blocks: 30
  References: 《接口》§1.7；《任务书》§六（速度加成）。
  Acceptance criteria: 任务期间额外认知请求不计入日配额；任务仍按时完成。
  QA scenarios: happy=免费窗口内完成额外分析且配额未变；failure=窗口不可用时走常规配额。Evidence `.omo/evidence/task-25-future-war-bot.json`
  Commit: Y | feat(llm): exploit free LLM window during tasks

- [ ] 26. 认知任务鲁棒性
  What to do / Must NOT do: 对 LLM/沙盒的解析失败、空响应、超时、截断做回退（规则兜底/重试/降级）；统一超时预算不拖垮 5s 响应。**不要**让认知模块异常传播导致进程崩溃。
  Parallelization: Wave 4 | Blocked by: 23, 24 | Blocks: 30
  References: 《接口》§1.7、§2.1；《任务书》§八。
  Acceptance criteria: 故障注入下 Bot 仍每回合按时返回合法 Response。
  QA scenarios: happy=注入故障后仍稳定；failure=连续故障时降级为纯规则模式。Evidence `.omo/evidence/task-26-future-war-bot.log`
  Commit: Y | fix(robustness): harden cognitive task handling

### Wave 5 — 进攻与调优

- [ ] 27. 火箭全图狙击敌方基地
  What to do / Must NOT do: 火箭 L3 就绪且无机器人威胁时按冷却轰击敌方基地（`attack` 目标为敌基地全局可见坐标，不需视野、导弹不被阻挡）；落后积分时升级为持续推家。**不要**在防守吃紧时抽走火箭火力。
  Parallelization: Wave 5 | Blocked by: 16, 17, 23 | Blocks: 30
  References: 《任务书》§4.5.1（L3 全图）、§4.5.4（导弹不被阻挡）、§4.3（敌基地全局可见）、§七。
  Acceptance criteria: 模拟中火箭对敌基地造成持续伤害；对方基地血量单调下降。
  QA scenarios: happy=命中并压低敌基地血；failure=防守告警时自动切回清波。Evidence `.omo/evidence/task-27-future-war-bot.replay`
  Commit: Y | feat(offense): add rocket base sniping

- [ ] 28. 机器人召唤令骚扰
  What to do / Must NOT do: 余钱按每天≤10 张购买召唤令灌对方下夜；优先高性价比（大/BOSS）在关键夜晚。**不要**挤占防守升级必需的金币。
  Parallelization: Wave 5 | Blocked by: 15, 17 | Blocks: 30
  References: 《任务书》§4.6.3（召唤令/每天上限）、§4.7.3。
  Acceptance criteria: 模拟中对方夜间机器人数量因召唤令增加。
  QA scenarios: happy=召唤生效；failure=金币不足/超上限时跳过且不报错。Evidence `.omo/evidence/task-28-future-war-bot.replay`
  Commit: Y | feat(offense): add robot summon harassment

- [ ] 29. 敌方角色狙击
  What to do / Must NOT do: 当敌方开拓者/工人进入视野（4）时，用火箭/电磁炮优先狙杀以阻断其任务/经济；优先级低于基地与防守。**不要**为狙角色浪费关键冷却而漏防。
  Parallelization: Wave 5 | Blocked by: 16, 17 | Blocks: 30
  References: 《任务书》§4.3、§4.5.2（阵亡复活 20 回合）、§4.5.4。
  Acceptance criteria: 模拟中视野内敌开拓者被击杀后其任务链中断。
  QA scenarios: happy=狙杀成功；failure=目标脱离视野时放弃并回归防守。Evidence `.omo/evidence/task-29-future-war-bot.replay`
  Commit: Y | feat(offense): add enemy role sniping

- [ ] 30. 端到端调优与回归
  What to do / Must NOT do: 用自对弈框架做策略终调（武器配比/建造顺序/推家阈值/任务优先级），跑全量回归；输出最终策略参数与报告。**不要**为单一场景过拟合。
  Parallelization: Wave 5 | Blocked by: 全部 | Blocks: 交付
  References: 工作包 18 框架；《任务书》§六/§七。
  Acceptance criteria: 多种子自对弈胜率/分数稳定且优于基线；全部测试通过。
  QA scenarios: happy=回归全绿；failure=退化时定位到具体工作包并回滚。Evidence `.omo/evidence/task-30-future-war-bot.csv`
  Commit: Y | tune(strategy): finalize parameters after self-play

- [ ] 31. 容错与超时加固
  What to do / Must NOT do: 全局异常捕获、5s 响应预算、畸形/缺失字段兜底、超时降级为安全默认指令；压力测试确保不崩溃。**不要**用裸 `except` 吞异常而不记录。
  Parallelization: Wave 5 | Blocked by: 13, 26 | Blocks: 交付
  References: 《任务书》§八；《接口》全篇。
  Acceptance criteria: 注入畸形请求/超时/缺字段，Bot 始终在 5s 内返回合法 Response 且进程存活。
  QA scenarios: happy=压力下零崩溃；failure=极端输入降级但不退出。Evidence `.omo/evidence/task-31-future-war-bot.log`
  Commit: Y | fix(robustness): add global fault and timeout hardening

---

## Final verification wave

> 全部 Todo 完成后并行执行；全部 APPROVE 并等待用户明确确认后才宣布完成。

- [ ] F1. 方案符合性审计：逐条核对实现是否覆盖 Scope 的 Must have、是否违反 Must NOT have。
- [ ] F2. 代码质量评审：结构、可读性、无 AI slop、无 `except` 吞异常、类型清晰。
- [ ] F3. 真实手动 QA：在模拟器中完整跑一场多天对局，验证存活、任务、寻宝、进攻链路，并确认**日志与摘要可直接支撑一句话反馈**。
- [ ] F4. 范围保真：确认未引入 Scope OUT 内容（RL/外网/硬编码/改判题器）。

## Commit strategy

- 每个工作包一次提交，遵循 Conventional Commits（`feat/fix/chore/test/tune`）。
- Wave 边界打 tag（`wave-0` … `wave-5`）便于回滚与对比。
- **每次优化提交关联你的报告编号**（如 `fix(combat): pair controller by range (#R7)`），便于追溯。
- 不提交密钥/大文件；日志/回放放 `logs/` 与 `.omo/evidence/`。
- 未经明确要求不 push；主分支保持可运行。

## Success criteria

- **功能**：Bot 通过官方接口契约；能完整存活 10 天；三类任务可执行；进攻策略可用。
- **可观测**：任何现象都能从固定日志/摘要中读出，并可用一句话反馈定位到模块与参数。
- **质量**：模拟器多种子自对弈下胜率/积分稳定优于基线；全测试通过；5s 响应预算与零崩溃。
- **可迭代**：本地模拟器+回放+自对弈闭环可用，策略可量化对比；改动小步可回滚。
- **交付**：方案文档 + 可运行 Bot + 测试/回放证据 + 诊断字典齐备。

---

## 五、风险与待确认项

| # | 风险/歧义 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R1 | 机器人出生位置与寻路目标未定义（§4.7.3 仅「夜晚出现/攻击阻挡单位」；每个机器人有 `targetTeam`） | **最高**：直接决定防御布局正确性 | 首局当侦察局；布局数据驱动；围墙可重规划（工作包 17） |
| R2 | 新闻→价格波动量级未给（仅定性） | 中：影响卖出最优时机 | 只做方向性预测；不拟合量级（工作包 19） |
| R3 | 寻宝线索→坐标/物品/时间的映射确定性未知 | 中高：可能无法稳定破解 | 用结果码 2 vs 3 探测；设投入上限（工作包 20） |
| R4 | 自进化任务内容「动态变更」、无边界 | 高：不能硬编码 | 通用 Agent 循环 + 技能库（工作包 21） |
| R5 | 基地 2×2 的 4 格朝向未明确定义 | 中：影响围墙锚定 | 实测确认（工作包 3/9） |
| R6 | 机器人数量增长曲线、召唤令叠加语义未明确 | 中：影响升级节奏 | 从 `robot.roles` 每晚学习（工作包 9/17） |
| R7 | 缺失《编译运行环境说明》 | 中：运行时/依赖/沙盒版本约束未知 | 向主办方确认；Bot 尽量用标准库、锁定依赖（工作包 1） |
| R8 | LLM 模型/上下文/质量未知 | 中：影响认知任务设计 | prompt 稳健、token 精简、可回退（工作包 26） |
| R9 | 配图（`image.huawei.com`）无法访问 | 低中：建造区/攻击范围示意图缺失 | 以文字规则为准；`request.txt` 样例校准；必要时向主办方索取 |
| R10 | `request.txt` 样例数值与任务书表格不一致（如加特林 range=4/电磁炮=7 vs 表格 3/6） | 低：样例为演示数据 | 以《任务书》正式规则为准；解析层不硬编码数值 |
| R11 | **真实对局数据不可外传**，我只能靠你的一句话反馈 | 高：诊断信息损失、误判 | 固定日志+事件码+摘要自检+诊断字典（§四，工作包 5–8）；报告模板标准化 |

---

## 六、待你审核的关键决策

1. **武器组合**：默认「1 火箭 + 2 电磁炮」（火箭 L3 优先）——是否认可，或偏好更激进的双火箭推家流？
2. **首局定位**：接受把第一场当「侦察局」，用观测到的机器人出生/路径反哺布局吗？
3. **任务优先级**：三类任务全做，但排期上自进化类（收益最高/最复杂）是否优先于寻宝？
4. **日志与报告格式**：§四 的日志格式（`<round> <phase> <TAG> <CODE> ...`）与报告模板是否符合你的反馈习惯？需要增减哪些字段？
5. **诊断字典覆盖**：你预计最常观察哪些现象？我按你的反馈优先完善映射表。
6. **落盘位置**：本方案当前在 `.omo/plans/future-war-bot.md`（与 `$start-work` 集成）。是否需要在仓库 `docs/` 下再放一份可见版本？
