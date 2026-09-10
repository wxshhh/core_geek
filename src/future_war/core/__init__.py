"""策略核心：世界模型与跨回合状态存储（工作包 9）；寻路与碰撞规避（工作包 10）；
规划/战斗/经济（预留）。

用法：
    from future_war.core import WorldModel, plan_move, resolve_moves

    model = WorldModel(logger=logger)          # 进程级单例，跨回合驻留
    view = model.apply_round(request)          # 每回合合并 Request，返回只读快照
    model.note_response_sent(prompt=..., execute_cmd=...)   # 异步请求记账
    next_pos = plan_move(view, 10011, Pos(x, y))            # 单单位寻路下一步
    moves = resolve_moves(view, {10010: a, 10012: b})       # 多单位无碰撞解析
"""

from future_war.core.nav import find_approach_path, next_step, plan_move, resolve_moves
from future_war.core.world import WorldModel
from future_war.core.world_history import HistoryRecorder
from future_war.core.world_map import (
    BuildAttempt,
    BuildableInference,
    BuildableKind,
    StaticMap,
    ZoneIndex,
    apply_build_feedback,
    base_cells,
    build_static_map,
    chebyshev,
    classify_zones,
    in_bounds,
    infer_buildable_cells,
    inference_from_config,
)
from future_war.core.world_merge import FieldCache, has_field, merge_round, station_of
from future_war.core.world_state import (
    DynamicState,
    EnemyObservation,
    HistoryState,
    NewsRecord,
    PendingKind,
    PendingRecord,
    PriceRecord,
    RobotCountRecord,
    TaskEvent,
    TaskEventKind,
    TreasureRecord,
    day_of,
)
from future_war.core.world_view import WorldView

__all__ = [
    "BuildAttempt",
    "BuildableInference",
    "BuildableKind",
    "DynamicState",
    "EnemyObservation",
    "FieldCache",
    "HistoryRecorder",
    "HistoryState",
    "NewsRecord",
    "PendingKind",
    "PendingRecord",
    "PriceRecord",
    "RobotCountRecord",
    "StaticMap",
    "TaskEvent",
    "TaskEventKind",
    "TreasureRecord",
    "WorldModel",
    "WorldView",
    "ZoneIndex",
    "apply_build_feedback",
    "base_cells",
    "build_static_map",
    "chebyshev",
    "classify_zones",
    "day_of",
    "find_approach_path",
    "has_field",
    "in_bounds",
    "infer_buildable_cells",
    "inference_from_config",
    "merge_round",
    "next_step",
    "plan_move",
    "resolve_moves",
    "station_of",
]
