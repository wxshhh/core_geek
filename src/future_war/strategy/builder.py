"""建造规划（工作包 14，方案 M8）：武器布局排序与升级券使用。

接口不传蓝/黄可建造区坐标，``WorldView`` 按基地几何**推断**（工作包 9）。本模块：

- ``preferred_weapon_cells``：把推断出的蓝色格按「靠前（离基地远）+ 稳定序」排序，
  让武器尽量前出迎敌；经济模块据此挑选建造位。
- ``upgrade_order``：读 ``build.upgrade_order`` 配置，给出升级优先级。
- ``plan_upgrades``：角色背包有升级券且身旁有可升级建筑时，发出 ``use`` 指令。

升级券在本地模拟器中不生效（sim/README 明确 stub），故其逻辑以合成视图单测覆盖。
仅用标准库。
"""

from __future__ import annotations

from typing import Final

from future_war.config import Config
from future_war.models import Action, Pos, Role, RoleCommand, enum_to_str
from future_war.core.world_map import chebyshev
from future_war.core.world_view import WorldView

MAX_LEVEL: Final = 3
_WEAPON_KINDS: Final = frozenset({"gatling", "railgun", "rocket"})
_MOBILE_KINDS: Final = frozenset({"pioneer", "worker"})

# 升级券 → (目标类别, 起始等级, 目标等级)
_VOUCHERS: Final = {
    "WeaponUpgradeVoucher1": ("weapon", 1, 2),
    "WeaponUpgradeVoucher2": ("weapon", 2, 3),
    "StationUpgradeVoucher1": ("station", 1, 2),
    "StationUpgradeVoucher2": ("station", 2, 3),
    "WallUpgradeVoucher1": ("wall", 1, 2),
    "WallUpgradeVoucher2": ("wall", 2, 3),
}


def preferred_weapon_cells(view: WorldView, config: Config | None = None) -> tuple[Pos, ...]:
    """蓝色可建造格排序：离基地越远越靠前（前出迎敌），同距按 (x,y) 稳定。"""
    cells = view.blue_build_cells()
    base = view.base_pos()
    if base is None:
        return tuple(sorted(cells, key=lambda c: (c.x, c.y)))
    return tuple(sorted(cells, key=lambda c: (-chebyshev(c, base), c.x, c.y)))


def upgrade_order(config: Config | None = None) -> tuple[str, ...]:
    """升级优先级（``build.upgrade_order``）；缺失返回空。"""
    value = config.get("build.upgrade_order") if config is not None else None
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def plan_upgrades(
    view: WorldView, config: Config | None = None
) -> dict[int, RoleCommand]:
    """为持有升级券且身旁有可升级建筑的角色发出 ``use`` 指令。"""
    order = upgrade_order(config)
    commands: dict[int, RoleCommand] = {}
    for role in _mobile(view):
        voucher = _pick_voucher(role, order)
        if voucher is None:
            continue
        target = _upgrade_target(view, role, voucher)
        if target is not None:
            commands[role.id] = RoleCommand(
                action=Action.USE, name=voucher, targetPos=(target,)
            )
    return commands


def _pick_voucher(role: Role, order: tuple[str, ...]) -> str | None:
    owned = [item for item in role.backpack if item in _VOUCHERS]
    if not owned:
        return None
    if not order:
        return min(owned)
    rank = {name: i for i, name in enumerate(order)}
    return min(owned, key=lambda name: (rank.get(name, len(order)), name))


def _upgrade_target(view: WorldView, role: Role, voucher: str) -> Pos | None:
    category, from_level, to_level = _VOUCHERS[voucher]
    candidates = [
        building
        for building in view.own_roles()
        if _matches(building, category)
        and building.level == from_level
        and chebyshev(role.pos, building.pos) <= 1
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda b: (b.pos.x, b.pos.y)).pos


def _matches(building: Role, category: str) -> bool:
    kind = enum_to_str(building.roleType)
    if category == "weapon":
        return kind in _WEAPON_KINDS
    return kind == category


def _mobile(view: WorldView) -> tuple[Role, ...]:
    return tuple(
        r for r in view.own_roles() if enum_to_str(r.roleType) in _MOBILE_KINDS
    )
