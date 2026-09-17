"""工作包 18 测试：自对弈调参框架。

Given/When/Then 风格；运行方式（二选一）：
    python3 -m pytest tests/ -q
    python3 tests/test_self_play.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from future_war.config import load_config  # noqa: E402
from future_war.sim.score import MatchReport, TeamOutcome  # noqa: E402

_spec = importlib.util.spec_from_file_location("self_play", _ROOT / "scripts" / "self_play.py")
self_play = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(self_play)


def _outcome(total: int) -> TeamOutcome:
    return TeamOutcome(
        score1=0,
        score2=0,
        score3=total,
        total=total,
        kills={},
        gold=0,
        base_hp=1500,
        base_alive=True,
        base_destroy_day=0,
        exceptions=0,
        scheduled=True,
    )


def _report(winner: str, challenger_total: int, defender_total: int) -> MatchReport:
    return MatchReport(
        seed=0,
        rounds_played=1,
        end_reason="max_rounds",
        teams={
            "challenger": _outcome(challenger_total),
            "defender": _outcome(defender_total),
        },
        winner=winner,
    )


def test_summarize_counts_outcomes_and_averages() -> None:
    """Given 三局（胜/负/平），When 汇总，Then 计数与平均分正确。"""
    reports = [
        _report("challenger", 300, 100),
        _report("defender", 50, 200),
        _report("draw", 100, 100),
    ]
    summary = self_play.summarize(reports)
    assert summary["games"] == 3
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["draws"] == 1
    assert summary["winRate"] == round(1 / 3, 4)
    assert summary["avgScore"] == round((300 + 50 + 100) / 3, 2)


def test_summarize_handles_empty() -> None:
    """Given 无对局，When 汇总，Then 指标为零且不崩溃。"""
    summary = self_play.summarize([])
    assert summary["games"] == 0
    assert summary["winRate"] == 0.0
    assert summary["avgScore"] == 0.0


def test_run_batch_produces_one_report_per_seed() -> None:
    """Given 两个 seed 与短对局，When 批量跑，Then 每 seed 一份报告。"""
    reports = self_play.run_batch([0, 1], "idle", load_config(), max_rounds=150)
    assert len(reports) == 2
    assert all(isinstance(r, MatchReport) for r in reports)
    assert all("challenger" in r.teams for r in reports)


# ------------------------------------------------------------------ 端到端防线回归


def _play(seed: int, rounds: int, config):
    """跑指定回合数的 StrategyBot vs ScriptedBot，返回过程快照。"""
    from future_war.sim import World, make_layout, engine, ScriptedBot
    from future_war.sim.judge import TEAM_ORDER, TeamDriver, build_request
    from future_war.sim.protocol import normalize_response, parse_commands
    from future_war.strategy import StrategyBot

    world = World(seed=seed, layout=make_layout())
    bots = {"challenger": StrategyBot(config), "defender": ScriptedBot()}
    drivers = {t: TeamDriver(team=t, bot=bots[t]) for t in TEAM_ORDER}
    snapshots = []
    while world.round_no < rounds:
        engine.begin_round(world)
        commands = {}
        for team in TEAM_ORDER:
            d = drivers[team]
            payload = normalize_response(d.bot(build_request(world, team, d)))
            cmds, _, _ = parse_commands(payload)
            commands[team] = cmds
        results = engine.resolve_round(world, commands, {t: set() for t in TEAM_ORDER})
        for team in TEAM_ORDER:
            drivers[team].last_results = dict(results[team])
        base = world.base("challenger")
        snapshots.append(
            {
                "round": world.round_no,
                "weapons": len(world.weapons("challenger")),
                "base_hp": max(0, base.hp) if base and base.alive else 0,
                "commands": commands["challenger"],
            }
        )
    return snapshots


def _play(seed: int, rounds: int, config, defender=None):
    """跑指定回合数的 StrategyBot vs 对手，返回过程快照。

    快照字段：回合号、己方武器数、基地血量、双方指令、以及**移动前**的己方单位
    坐标（判定「原地」要拿指令目标与该角色当回合起点比，因此在 resolve 前采集）。
    对手缺省 ``ScriptedBot``（朴素防御），回归测试传 ``IdleBot``。
    """
    from future_war.sim import IdleBot, ScriptedBot, World, engine, make_layout
    from future_war.sim.judge import TEAM_ORDER, TeamDriver, build_request
    from future_war.sim.protocol import normalize_response, parse_commands
    from future_war.strategy import StrategyBot

    world = World(seed=seed, layout=make_layout())
    bots = {
        "challenger": StrategyBot(config),
        "defender": defender if defender is not None else ScriptedBot(),
    }
    drivers = {t: TeamDriver(team=t, bot=bots[t]) for t in TEAM_ORDER}
    snapshots = []
    while world.round_no < rounds:
        engine.begin_round(world)
        # resolve 之前的坐标 = 本回合角色起点；resolve 之后坐标已变，无法再判「原地」
        positions = {u.uid: (u.x, u.y) for u in world.alive_units("challenger")}
        commands = {}
        for team in TEAM_ORDER:
            d = drivers[team]
            payload = normalize_response(d.bot(build_request(world, team, d)))
            cmds, _, _ = parse_commands(payload)
            commands[team] = cmds
        results = engine.resolve_round(world, commands, {t: set() for t in TEAM_ORDER})
        for team in TEAM_ORDER:
            drivers[team].last_results = dict(results[team])
        base = world.base("challenger")
        snapshots.append(
            {
                "round": world.round_no,
                "weapons": len(world.weapons("challenger")),
                "base_hp": max(0, base.hp) if base and base.alive else 0,
                "commands": commands["challenger"],
                "all_commands": commands,
                "positions": positions,
            }
        )
    return snapshots


def _engaged_role_ids(commands, positions) -> set[int]:
    """本回合「被安排了非原地动作」的己方角色 ID。

    「原地」有两种等价写法：根本不发这条指令，或发 ``move`` 到当前所在格；
    两者都不算「有人在动」，所以都要排除——这正是「工人零指令不动」回归的形态。
    """
    engaged: set[int] = set()
    for uid, cmd in commands.items():
        action = str(cmd.action)
        if action == "move":
            target = cmd.targetPos[0] if cmd.targetPos else None
            if target is None or (target.x, target.y) == positions.get(uid):
                continue
        engaged.add(uid)
    return engaged


def test_first_night_has_three_weapons_and_low_base_damage() -> None:
    """Given 第 1 天白天，When 跑到第一晚结束，Then 3 座武器就位且基地守住第一晚。

    回归点：旧实现在第 1 天只能建成 2 座武器、夜里只有 1 座有人操控，
    第一晚基地就被刷出的机器人推平（用户实测现象）。

    掉血阈值按**几何修正后**的实测重标：旧断言 ``>= 1400`` 是拿错误矩形蓝区
    （正压在机器人来袭的南侧走廊上）量出来的；改成「距基地 1 环」的真几何后，
    武器散在基地四周、南侧门户不再天然被封，seed 0..4 首夜结束血量为
    1225/1430/1225/1440/1350。故此处只断言「守住第一晚、掉血不到 1/3」，
    不再钉死一个随种子漂移的魔数。
    """
    snapshots = _play(seed=0, rounds=130, config=load_config())
    day_one = [s for s in snapshots if s["round"] <= 70]
    assert day_one[-1]["weapons"] == 3, "第 1 天结束前必须建满 3 座武器"
    first_night = [s for s in snapshots if 70 < s["round"] <= 130]
    assert first_night
    base_hp = first_night[-1]["base_hp"]
    assert base_hp >= 1000, f"第一晚基地掉血过多、已被打穿防线: {base_hp}"


# 已删除：test_first_night_three_weapons_fire（原断言「第一晚同回合 3 座武器开火」）。
# 删除理由（几何修正后已无意义，且它从来不是 Bot 行为不变式）：该断言实质在考
# 「合成地图把 3 座武器摆在机器人唯一来袭走廊上」这一旧矩形几何的巧合。改成正确
# 的 1 环蓝区后，武器分布在基地四周，同时开火的武器数由机器人波次与选位共同决定：
# seed 0/1/2 实测同时开火峰值为 2/3/1，连「每座武器至少开火过一次」也只在 seed 1
# 成立（seed 0 只有 2 座、seed 2 只有 1 座开过火）。任何重写后的期望值都只能是对
# 单个种子调出来的魔数，考不出 Bot 对错；「3 座武器建满 + 首夜基地不被打穿」由
# test_first_night_has_three_weapons_and_low_base_damage 覆盖，真正要防的
# 「角色整局零指令」由下方 test_every_day_round_moves_at_least_one_role 覆盖。


def test_every_day_round_moves_at_least_one_role() -> None:
    """Given StrategyBot vs IdleBot 跑满 130 回合，When 逐回合看己方指令，
    Then 每个白天回合至少一个己方角色被安排了非原地动作、3 个角色整局都动过，且不抛异常。

    回归点（本测试存在的唯一理由）：planner 曾在「争抢败者」分支里把 move 整条删掉，
    两个工人于是整局不发指令、原地不动，直到上线后才被人眼发现。只断言「不抛异常」
    抓不到这种静默回归——必须断言「白天一定有人在动」；再补一条「每个角色整局至少
    动过一次」，才能钉住「两个工人整局不动」那种只丢部分角色的形态。
    夜间（71..130）允许零指令：角色已就位时不发指令是设计如此，不算回归。
    反向验证：临时改 planner 丢弃全局 move，本测试即变红。
    """
    from future_war.sim import IdleBot
    from future_war.sim.rules import is_day_round
    from future_war.strategy import StrategyBot  # noqa: F401 — 经 _play 间接驱动，声明依赖

    snapshots = _play(seed=1, rounds=130, config=load_config(), defender=IdleBot())
    assert snapshots and snapshots[-1]["round"] == 130, "整局未跑满 130 回合"

    idle_rounds = []
    engaged_by_role: dict[int, int] = {}
    for snap in snapshots:
        if not is_day_round(snap["round"]):
            continue
        engaged = _engaged_role_ids(snap["commands"], snap["positions"])
        if not engaged:
            idle_rounds.append(snap["round"])
        for uid in engaged:
            engaged_by_role[uid] = engaged_by_role.get(uid, 0) + 1

    assert not idle_rounds, f"白天出现无人动作的回合（工人零指令回归）: {idle_rounds}"
    assert len(engaged_by_role) == 3, (
        f"整局只有 {sorted(engaged_by_role)} 动过，应有 3 个角色各自动过"
        "（争抢败者的 move 被整条删掉时，这里会只剩部分角色）"
    )
    assert min(engaged_by_role.values()) >= 10, (
        f"有角色整局几乎不动: {engaged_by_role}"
    )


def test_full_match_has_no_structural_command_error_and_stable_wall_target() -> None:
    """Given 本地模拟器跑一整局 StrategyBot vs ScriptedBot（seed 0，130 回合），
    When 逐回合解析我方响应并读 D-02 notes，
    Then 结构性指令错误恒为 0（= 真机 errorCode 4 为 0）且围墙分母恒定不变。

    这条把 issue #2 的两件事一起钉住：

    - **A**：判题器口径的「指令错误」只由结构问题触发（任务书 §八）。我们跑满
      130 回合，解析器一条结构问题都没报 —— 说明建墙/修墙路径上没有非法指令，
      线上那 3 次 ``errors=1`` 不是 errorCode 4；同时**规则性失败**（对非黄区格
      build）必然存在（本局也会出现），它按 §八不计异常，正是两者的分界线。
    - **B**：``walls=N/<分母>`` 的分母必须恒定。旧实现拿「剩余候选格」当分母，
      每砌一堵墙分母就少 1，于是出现线上日志里的 ``walls=7/6 (done)``（分子大于
      分母、误判完工停工）。
    """
    from future_war.sim import ScriptedBot, World, engine, make_layout
    from future_war.sim.judge import TEAM_ORDER, TeamDriver, build_request
    from future_war.sim.protocol import normalize_response, parse_commands
    from future_war.strategy import StrategyBot

    config = load_config()
    world = World(seed=0, layout=make_layout())
    bot = StrategyBot(config)
    bots = {"challenger": bot, "defender": ScriptedBot()}
    drivers = {t: TeamDriver(team=t, bot=bots[t]) for t in TEAM_ORDER}
    problems: list[str] = []
    denominators: set[str] = set()
    build_outside_yellow: list[tuple[int, int]] = []
    while world.round_no < 130:
        engine.begin_round(world)
        commands = {}
        for team in TEAM_ORDER:
            d = drivers[team]
            payload = normalize_response(d.bot(build_request(world, team, d)))
            cmds, issues, _dropped = parse_commands(payload)
            if team == "challenger":
                problems.extend(issues)
                for note in getattr(bot, "last_notes", ()):
                    if note.startswith("walls=") and "/" in note:
                        denominators.add(note.split("/", 1)[1])
                # 取我方**自己推断的**黄区（bot._model 是跨回合世界模型，测试内直接读它）
                yellow = bot._model.view().yellow_build_cells()
                for cmd in cmds.values():
                    if str(cmd.action) == "build" and cmd.name == "wall":
                        if cmd.targetPos and cmd.targetPos[0] not in yellow:
                            build_outside_yellow.append(cmd.targetPos[0].to_dict())
            commands[team] = cmds
        results = engine.resolve_round(world, commands, {t: set() for t in TEAM_ORDER})
        for team in TEAM_ORDER:
            drivers[team].last_results = dict(results[team])

    assert not problems, f"出现结构性指令错误（真机 errorCode 4）: {problems}"
    assert denominators == {"12"}, f"围墙分母必须恒定（线上事故是 6/8/12 抖动）: {denominators}"
    assert not build_outside_yellow, (
        f"build wall 的目标必须落在自己推断的黄区内: {build_outside_yellow}"
    )


def main() -> int:
    """零依赖测试运行器：执行全部 test_* 函数并报告。

    必须留在文件**最末尾**：本函数在 ``__main__`` 下于模块导入时立即执行，
    靠 ``globals()`` 收集用例；写在中间会静默漏跑其后的用例。
    """
    test_funcs = [
        obj
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for test in test_funcs:
        try:
            test()
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK — 运行器顶层边界：收集失败并继续
            failed += 1
            print(f"FAIL {test.__name__}: {exc!r}", file=sys.stderr)
        else:
            print(f"PASS {test.__name__}")
    print(f"{len(test_funcs) - failed}/{len(test_funcs)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

