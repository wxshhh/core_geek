"""本地游戏模拟器 + mock 判题器（工作包 3）。

这是《未来战争》规则的**可扩展、明确简化子集**实现，用于本地回归与自对弈，
**不追求与官方判题器 100% 一致**。所有简化点集中记录于
`src/future_war/sim/README.md`，此处仅列摘要：

- 地图为**合成地图**（蓝/黄可建造区、机器人出生点为自定；基地/小贩/武器
  商店/任务点坐标对齐 docs/request.txt 样例）。
- 机器人数量曲线、出生位置、寻路为自定（任务书 §4.7.3 未定义）。
- 加特林/电磁炮只对机器人生效；围墙/建筑不阻挡弹道；火箭可命中任意单位。
- 任务系统（acceptTask/submitAnswer/summonTreasure）、升级券、眩晕/炸弹、
  世界新闻/价格波动未实现；score1 恒为 0（文档化 stub）。
- 结算次序补充：机器人移动先于角色移动（§4.4 只规定了攻击 > 机器人移动）。

用法::

    from future_war.sim import World, make_layout, run_match, bot_from_spec

    world = World(seed=42, layout=make_layout())
    report = run_match(
        world,
        {"challenger": bot_from_spec("scripted:defense"),
         "defender": bot_from_spec("scripted:idle")},
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

确定性：同 seed + 同 Bot 动作序列 → 输出逐字节一致（random.Random + 稳定
迭代序）。仅用标准库，无第三方运行时依赖。
"""

from __future__ import annotations

from future_war.sim.bots import IdleBot, ScriptedBot
from future_war.sim.engine import begin_round, resolve_round
from future_war.sim.judge import TeamDriver, run_match
from future_war.sim.layout import MapLayout, initial_mines, make_layout
from future_war.sim.protocol import BotError, BotFn, HttpBot
from future_war.sim.registry import BotSpecError, bot_from_spec
from future_war.sim.request_view import build_request
from future_war.sim.score import (
    MatchReport,
    TeamOutcome,
    TeamScore,
    compute_team_scores,
    decide_winner,
)
from future_war.sim.world import Robot, TeamState, Unit, World
from future_war.sim.writers import MatchLog, ReplayWriter

__all__ = [
    "BotError",
    "BotFn",
    "BotSpecError",
    "HttpBot",
    "IdleBot",
    "MapLayout",
    "MatchLog",
    "MatchReport",
    "ReplayWriter",
    "Robot",
    "ScriptedBot",
    "TeamDriver",
    "TeamOutcome",
    "TeamScore",
    "TeamState",
    "Unit",
    "World",
    "begin_round",
    "bot_from_spec",
    "build_request",
    "compute_team_scores",
    "decide_winner",
    "initial_mines",
    "make_layout",
    "resolve_round",
    "run_match",
]
