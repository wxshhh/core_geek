"""《未来战争》v1.0 bot 包。

布局：
- server.py         判题器每回合轮询的 HTTP 入口（工作包 1）
- config.py         配置中心与版本戳（工作包 7）
- models/           接口数据模型与编解码（工作包 2，预留）
- core/             世界模型 / 规划 / 寻路 / 战斗 / 经济（预留）
- observability/    结构化日志 / 机器可读指标 / 版本戳（预留）
"""

from typing import Final

__version__: Final = "0.1.0"
