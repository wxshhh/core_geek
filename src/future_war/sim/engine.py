"""回合结算引擎：把两队指令推进世界状态（任务书 §4.4/§4.5.4/§八）。

结算顺序（§4.4「武器攻击 > 机器人移动」+ 文档化补充，见 README）：

1. 指令执行：经济/建造/拆除/采集立即结算；移动/攻击只产生意图
2. 武器攻击结算（瞄准机器人移动前位置；伤害入队）
3. 机器人移动与攻击（先于角色移动——§4.4 未规定次序，README 注明）
4. 角色移动与碰撞（§4.5.4：目标受阻/争夺/互换 → 双方停留）
5. 回合末统一结算伤害与死亡（§4.4）

指令级失败（规则性，如碰撞/落点无目标）只标记该指令无效，不计队伍异常（§八注）。
"""

from __future__ import annotations

from future_war.models import RoleCommand
from future_war.sim import actions, combat, robots
from future_war.sim.layout import respawn_mine_cell
from future_war.sim.rules import (
    MINE_CHARGES,
    is_night_round,
    respawn_round_of,
    round_in_day,
)
from future_war.sim.world import Mine, Unit, World

TEAM_ORDER: tuple[str, ...] = ("challenger", "defender")

# 各动作的执行器路由（无状态）：move/attack 走意图队列，其余立即结算
_UNIMPLEMENTED_ACTIONS = frozenset({"acceptTask", "submitAnswer", "summonTreasure"})


def begin_round(world: World) -> None:
    """回合开始维护：推进回合号、冷却递减、波次/清理/矿区重生/角色复活。"""
    world.round_no += 1
    round_no = world.round_no
    # 火箭冷却递减（发射后 3 回合空窗，§4.5.1）
    for team in TEAM_ORDER:
        for unit in world.teams[team].units.values():
            if unit.cooldown > 0:
                unit.cooldown -= 1
    if round_in_day(round_no) == 0:
        robots.clear_robots(world)  # 次日早第 1 回合清除残余机器人（§4.7.3）
    if round_in_day(round_no) == 70:
        robots.spawn_night_wave(world)  # 夜晚第 1 回合统一生成（§4.7.3）
    # 上回合枯竭矿区重生（§4.1「下回合会随机刷新」）
    occupied = set(world.mines)
    for ore in world.respawn_queue:
        cell = respawn_mine_cell(world.layout, world.rng, occupied)
        world.mines[cell] = Mine(ore=ore, charges=MINE_CHARGES)
        occupied.add(cell)
    world.respawn_queue.clear()
    # 角色复活：次日白天开始后 20 回合，背包保留（§4.5.2）
    for team in TEAM_ORDER:
        for unit in world.teams[team].units.values():
            if not unit.alive and unit.death_day > 0 and respawn_round_of(unit.death_day) == round_no:
                _respawn(world, team, unit)


def _respawn(world: World, team: str, unit: Unit) -> None:
    bx, by = world.layout.base_pos[team]
    unit.alive = True
    unit.death_day = 0
    unit.hp = unit.max_hp
    unit.x, unit.y = bx, by  # 简化：直接复活在基地左上角（README）


def resolve_round(
    world: World,
    commands: dict[str, dict[int, RoleCommand]],
    invalid_ids: dict[str, set[int]],
) -> dict[str, dict[int, bool]]:
    """结算一个回合；返回每队 {角色ID: 指令是否生效}。"""
    results: dict[str, dict[int, bool]] = {team: {} for team in TEAM_ORDER}
    for team in TEAM_ORDER:
        for uid in sorted(invalid_ids.get(team, ())):
            results[team][uid] = False
    move_intents: list[actions.MoveIntent] = []
    attack_intents: list[combat.AttackIntent] = []
    for team in TEAM_ORDER:
        for uid, cmd in commands.get(team, {}).items():
            unit = world.teams[team].units.get(uid)
            if unit is None or not unit.alive:
                results[team][uid] = False
                continue
            results[team][uid] = _execute(world, team, unit, cmd, move_intents, attack_intents)
    # 2. 武器攻击（瞄准移动前位置）
    hit_weapons = combat.resolve_weapon_attacks(world, attack_intents)
    for team in TEAM_ORDER:
        for uid in hit_weapons:
            if uid in world.teams[team].units:
                results[team][uid] = True
    # 3. 机器人移动与攻击（先于角色移动）
    if is_night_round(world.round_no):
        robots.move_robots(world)
    # 4. 角色移动碰撞结算（§4.5.4）
    moved = _resolve_moves(world, move_intents)
    for team, uid in moved:
        results[team][uid] = True
    # 矿区存量扣减（多工人同采共享，§4.1）
    _settle_mines(world)
    # 5. 回合末统一结算伤害与死亡
    combat.apply_damage(world)
    return results


def _execute(
    world: World,
    team: str,
    unit: Unit,
    cmd: RoleCommand,
    moves: list[actions.MoveIntent],
    attacks: list[combat.AttackIntent],
) -> bool:
    """路由执行一条指令；返回是否生效。"""
    action = str(cmd.action)
    if action in _UNIMPLEMENTED_ACTIONS:
        return False  # 任务系统未实现（README）
    match action:
        case "move":
            intent = actions.try_move(world, unit, cmd)
            if intent is None:
                return False
            moves.append(intent)
            return False  # 生效与否取决于碰撞结算，由 _resolve_moves 置真
        case "attack":
            intent = combat.queue_attack(world, team, unit, cmd)
            if intent is None:
                return False
            attacks.append(intent)
            return False  # 命中与否由 resolve_weapon_attacks 置真
        case "collect":
            return actions.do_collect(world, team, unit, cmd)
        case "sell":
            return actions.do_sell(world, team, unit, cmd)
        case "buy":
            return actions.do_buy(world, team, unit, cmd)
        case "build":
            return actions.do_build(world, team, unit, cmd)
        case "remove":
            return actions.do_remove(world, team, unit, cmd)
        case "drop":
            return actions.do_drop(world, team, unit, cmd)
        case "use":
            return actions.do_use(world, team, unit, cmd)
        case unreachable:
            raise AssertionError(f"unknown action {unreachable!r}")


def _settle_mines(world: World) -> None:
    """矿区存量扣减与枯竭重生排队（§4.1）。"""
    for (x, y), harvest in sorted(world.mine_harvest.items()):
        mine = world.mines.get((x, y))
        if mine is None:
            continue
        mine.charges -= harvest
        if mine.charges <= 0:
            del world.mines[(x, y)]
            world.respawn_queue.append(mine.ore)
            world.events.append(f"mine_depleted ore={mine.ore} at=({x},{y})")
    world.mine_harvest.clear()


def _resolve_moves(world: World, moves: list[actions.MoveIntent]) -> set[tuple[str, int]]:
    """角色移动碰撞结算（§4.5.4）。返回实际移动成功的 (team, uid)。"""
    movers: dict[tuple[str, int], actions.MoveIntent] = {(m.team, m.uid): m for m in moves}
    claims: dict[tuple[int, int], list[actions.MoveIntent]] = {}
    for m in moves:
        claims.setdefault((m.tx, m.ty), []).append(m)
    swapped: set[tuple[str, int]] = set()
    for m in moves:
        if (m.team, m.uid) in swapped:
            continue
        for other in claims.get((m.x, m.y), []):
            if other is m:
                continue
            # 对方从我当前位置出发且目标是我的目标 → 位置互换 → 双方停留
            if (other.x, other.y) == (m.tx, m.ty):
                swapped.add((m.team, m.uid))
                swapped.add((other.team, other.uid))
    moved: set[tuple[str, int]] = set()
    for m in moves:
        key = (m.team, m.uid)
        if key in swapped:
            continue
        if len(claims[(m.tx, m.ty)]) > 1:
            continue  # 目标点争夺（§4.5.4②）
        if not _target_free(world, m, movers):
            continue  # 目标点受阻（§4.5.4①）
        unit = world.teams[m.team].units[m.uid]
        unit.x, unit.y = m.tx, m.ty
        moved.add(key)
    return moved


def _target_free(
    world: World, m: actions.MoveIntent, movers: dict[tuple[str, int], actions.MoveIntent]
) -> bool:
    """移动目标是否可进入：无障碍物/静止角色/机器人（§4.5.4①）。"""
    if world.static_blocked(m.tx, m.ty):
        return False
    if world.robot_at(m.tx, m.ty) is not None:
        return False
    blocker = world.role_at(m.tx, m.ty)
    if blocker is not None and (blocker[0], blocker[1].uid) not in movers:
        return False
    return True
