"""内置默认配置（工作包 7）——纯数据表，无逻辑。

与 `config/default.json` 保持同构：default.json 损坏/缺失时以此为最终兜底
（加载合并语义见 future_war.config 模块 docstring）。
同构关系由 tests/test_config.py::test_default_json_matches_builtin_defaults 守护。
"""

from __future__ import annotations

from typing import Final, TypeAlias

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)

DEFAULT_CONFIG: Final[dict[str, JsonValue]] = {
    "log": {"level": "EVENT", "trace_enabled": False, "echo_stderr": True},
    "server": {"slow_round_ms": 3000},
    "build": {
        "order": ["weapons", "walls", "upgrades"],
        "day1_max_weapons": 3,
        "weapon_mix": {"rocket": 1, "railgun": 2, "gatling": 0},
        "upgrade_order": ["rocket_l3", "railgun_l3", "base_l2", "base_l3", "wall_l2"],
        "chokepoint_count": 2,
        "wall_labyrinth_depth": 3,
        "wall_max": 12,
        "wall_enabled": True,
        "wall_probe_budget": 12,
        "wall_probe_from": 45,
    },
    "combat": {
        "target_priority": ["boss", "large", "medium", "small"],
        "overkill_avoidance": True,
        "rocket_aoe_threshold": 3,
        "controller_pairing": "range_first",
        "attack_cooldown_turns": 3,
        "staging_night_rounds": 25,
    },
    "consumables": {
        "medicine_hp_ratio": 0.5,
        "wall_hp_ratio": 0.4,
        "bomb_min_robots": 2,
        "dizzy_min_robots": 3,
    },
    "economy": {
        "sell_hold_ratio": 0.3,
        "emergency_reserve": 100,
        "budget_ratios": {
            "weapon_upgrade": 0.5,
            "base_upgrade": 0.3,
            "wall_upgrade": 0.2,
        },
        "stone_reserve": 4,
        "sell_batch": 5,
        "vendor_peak_window": 5,
        "dusk_return": 70,
    },
    "defense": {
        "boss_emergency_rounds": 2,
        "base_hp_alert_ratio": 0.5,
        "wall_repair_threshold": 0.4,
    },
    "offense": {
        "enabled": False,
        "base_snipe_enabled": True,
        "summon_harass_enabled": True,
        "summon_daily_cap": 10,
        "summon_reserve": 100,
        "role_snipe_enabled": True,
        "role_snipe_hp_ratio": 0.3,
        "retreat_hp_ratio": 0.35,
        "all_in_score_gap": 300,
    },
    "tasks": {
        "self_evolution_enabled": True,
        "treasure_enabled": True,
        "reasoning_enabled": True,
        "treasure_probe_cap": 4,
        "timeout_margin_rounds": 10,
    },
    "llm": {"daily_quota": 3, "free_window_enabled": True},
    "nav": {"replan_interval": 5, "collision_avoidance": True},
    "world": {"inference": {"blue_radius": 3, "yellow_radius": 6}},
    "features": {"replay_enabled": True, "metric_line_enabled": True},
}
