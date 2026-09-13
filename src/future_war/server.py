"""HTTP 服务入口：《未来战争》v1.0 bot 的判题轮询服务。

判题系统每回合向本服务 POST 当前战场状态（docs/接口文档.md §1），
本服务返回 Response（§2.1）:

    {"roleCommandMap": {...}, "prompt": "...", "executeCmd": "..."}

do_POST 经 StrategyBot 规划后返回 roleCommandMap；任何解析/规划失败都降级为空指令
的合法 Response，进程绝不崩溃（任务书 §八）。约束：连接 10s / 响应 5s 超时；
进程崩溃即判负。

**超时留证（X-04）**：判题器 5s 内收不到响应即判超时，等真的超时就没有证据了。
两条路径都记 `[ERROR] X-04`：

1. **回合太慢**——`do_POST` 测端到端耗时（读请求 → 规划 → 落盘日志），达到
   `server.slow_round_ms`（默认 3000ms = 5s 预算的 60%）就在**发送响应之前**记
   一条，含 `round_no` 与实测毫秒数；
2. **读请求/请求体超时**——stdlib 的 `handle_one_request` 会静默吞掉
   `TimeoutError` 并直接断开连接（既不回包也不留日志），这里在连接收尾时补记
   X-04，并尽力回一个合法空 Response（判题器对空指令不计队伍异常）。
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Final, TypedDict

from future_war.config import Config, load_config, parse_profile_arg
from future_war.models import parse_request, remove_trailing_commas, serialize_response
from future_war.observability.events import EventCode, Phase, phase_of
from future_war.observability.round_metrics import RoundObserver
from future_war.strategy import StrategyBot

HOST: Final = "0.0.0.0"
DEFAULT_PORT: Final = 8080
# 与判题器 5s 响应预算匹配的 socket 读写上限（docs/任务书.md §八）
SOCKET_TIMEOUT_SECONDS: Final = 5.0
# 回合耗时达到该值即记一条 X-04「接近超时」（config server.slow_round_ms）。
# 默认 3000ms = 5s 预算的 60%：既留足余量，又能在真超时前留下证据。
SLOW_ROUND_MS_DEFAULT: Final = 3000


class RoleCommand(TypedDict):
    """单角色指令（接口 §2.2）。脚手架阶段仅保留必选字段，其余字段见工作包 2。"""

    action: str


class JudgeResponse(TypedDict):
    """Response 顶层结构（接口 §2.1）。"""

    roleCommandMap: dict[int, RoleCommand]
    prompt: str
    executeCmd: str


EMPTY_RESPONSE: Final[JudgeResponse] = {
    "roleCommandMap": {},
    "prompt": "",
    "executeCmd": "",
}


class JudgeHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer，但跳过启动时的反向 DNS 查询。

    http.server.HTTPServer.server_bind 会调用 socket.getfqdn("0.0.0.0")，
    该调用回退为对本机 hostname 的反向 DNS 解析。在判题内网/沙盒等
    无 DNS 环境下会阻塞 30s+，导致 `bash run.sh` 启动远超判题器
    连接预算（10s）。这里直接使用绑定地址，不做任何域名解析。
    """

    observer: RoundObserver | None = None
    bot: StrategyBot | None = None
    slow_round_ms: int = SLOW_ROUND_MS_DEFAULT

    def server_bind(self) -> None:
        """完成 bind 并登记 server_name/port，跳过 getfqdn。"""
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class JudgeRequestHandler(BaseHTTPRequestHandler):
    """处理判题器 POST：读取 JSON 请求体，返回合法 Response JSON。"""

    timeout = SOCKET_TIMEOUT_SECONDS

    def setup(self) -> None:
        super().setup()
        reader = _TimeoutAwareReader(self.rfile)
        self.rfile = reader  # type: ignore[assignment]
        self._reader = reader
        self._response_sent = False
        self._timeout_reported = False

    def handle(self) -> None:
        """连接级收尾：读请求/请求体超时 → 补一个合法空 Response 并记 X-04。

        背景：``BaseHTTPRequestHandler.handle_one_request`` 内部
        ``except TimeoutError`` 只 ``log_error`` + 关连接就返回（3.10~3.14 皆然），
        既不回包也不留我们的日志，所以 ``handle_timeout`` 这个钩子**永远不会被
        调用**（它是 ``socketserver.BaseServer`` 的钩子，不在 handler 上）。判题器
        在那种情况下既收不到响应、我们也查不到任何痕迹。这里用 ``rfile`` 代理
        埋下的信号，在连接收尾时把这两件事都补上。
        """
        started = time.perf_counter()
        try:
            super().handle()
        finally:
            if self._reader.timed_out:
                self._on_read_timeout(time.perf_counter() - started)

    def handle_timeout(self) -> None:
        """兜底钩子：若某实现真的调用它，走与 handle() 同一条「留证 + 回包」路径。"""
        self._on_read_timeout(SOCKET_TIMEOUT_SECONDS)

    def do_POST(self) -> None:
        """POST 任意路径：解析请求体，规划指令，落盘回合并返回 Response。"""
        started = time.perf_counter()
        request = self._parse_request(self._read_body())
        response = self._respond(request)
        self._observe(request, response)
        self._warn_if_slow(started, request)
        self._send_response(response)

    def _on_read_timeout(self, elapsed_s: float) -> None:
        """读请求/请求体超时：先记一条 X-04 留证，再尽力回一个合法空 Response。

        判题器对「空指令」不计队伍异常，而被掐断的连接会记成响应超时/格式错误，
        所以即使连接可能已经关闭也要试着回包（失败只告警）。

        注意：读请求行就超时的情况下 ``parse_request`` 从未执行，
        ``request_version`` / ``requestline`` **没有类级默认值**（stdlib 只在
        ``handle_one_request``/``parse_request`` 里赋值），直接发响应会
        AttributeError —— 这里先把最小 HTTP 状态补齐，保证判题器能解析。
        """
        if self._timeout_reported:
            return
        self._timeout_reported = True
        self._emit(
            EventCode.X_04,
            f"request read timed out (socket budget {SOCKET_TIMEOUT_SECONDS:.0f}s)",
            elapsedMs=int(elapsed_s * 1000),
        )
        if self._response_sent:
            return
        self.request_version = "HTTP/1.1"  # 没读到请求行 → 用 1.1 状态行（1.0 无法解析）
        self.requestline = getattr(self, "requestline", "") or ""
        try:
            self._send_response(EMPTY_RESPONSE)
        except (OSError, ValueError) as exc:
            # 连接已关闭 → BrokenPipe/ConnectionReset；wfile 已关 → ValueError
            print(
                f"[future-war] WARN timeout response not sent ({exc})",
                file=sys.stderr,
                flush=True,
            )

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length else 0
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    def _parse_request(self, body: bytes) -> dict[str, object] | None:
        """解析请求体为 dict；畸形输入返回 None 并记一条 X-02 异常行。"""
        if not body:
            return None
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._emit(EventCode.X_02, f"undecodable request body: {exc}")
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            try:
                data = json.loads(remove_trailing_commas(text))
            except json.JSONDecodeError as exc:
                self._emit(EventCode.X_02, f"malformed request body: {exc}")
                return None
        return data if isinstance(data, dict) else None

    def _respond(self, request: dict[str, object] | None) -> dict[str, object]:
        """运行策略 Bot 得到本回合指令；任何失败都回退为空指令（§八）。"""
        bot = getattr(self.server, "bot", None)
        if bot is None or request is None:
            return EMPTY_RESPONSE
        try:
            response = bot(parse_request(request))
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK — 进程绝不能崩溃（§八）
            self._emit(EventCode.X_01, f"strategy error: {exc}")
            return EMPTY_RESPONSE
        return serialize_response(response)

    def _observe(
        self, request: dict[str, object] | None, response: dict[str, object]
    ) -> None:
        """写入本回合日志与指标；畸形请求已由 _parse_request 记异常行。"""
        observer = getattr(self.server, "observer", None)
        if observer is None or request is None:
            return
        round_no = _round_no_of(request)
        observer.observe(round_no if round_no is not None else 0, request, response)
        self._trace(round_no if round_no is not None else 0, request, response)

    def _trace(
        self, round_no: int, request: dict[str, object], response: dict[str, object]
    ) -> None:
        """每回合一条 ``D-02``：规划器摘要 + 本回合指令 + 上回合执行结果。

        真机上队友看不到 ``logs/`` 目录，平台捕获的 stderr 是唯一通道；这条行把
        「这回合为什么没建东西 / 上回合那条建造到底成没成」压成一行，是排查
        「一整天没建墙」这类问题的主要依据。
        """
        observer = getattr(self.server, "observer", None)
        if observer is None:
            return
        notes = getattr(getattr(self.server, "bot", None), "last_notes", ())
        message = " ".join(str(note) for note in notes) if notes else "-"
        observer.emit(
            EventCode.D_02,
            message,
            round_no=round_no,
            phase=phase_of(round_no),
            cmds=_command_summary(response.get("roleCommandMap")),
            results=_result_summary(request.get("lastRoundRoleActionResults")),
        )

    def _warn_if_slow(self, started: float, request: dict[str, object] | None) -> None:
        """回合耗时逼近 5s 响应预算 → 一条 X-04（任务书 §八）。

        测的是「读请求 → 规划 → 落盘」的端到端耗时，并在 `_send_response` **之前**
        落盘：日志先于响应可见，即使随后发送阻塞/失败也留得下证据。
        """
        threshold = getattr(self.server, "slow_round_ms", SLOW_ROUND_MS_DEFAULT)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if threshold <= 0 or elapsed_ms < threshold:
            return
        round_no = _round_no_of(request)
        self._emit(
            EventCode.X_04,
            f"response near {SOCKET_TIMEOUT_SECONDS:.0f}s budget: {elapsed_ms:.0f}ms",
            round_no=round_no,
            phase=phase_of(round_no) if round_no is not None else Phase.NONE,
            elapsedMs=int(elapsed_ms),
            thresholdMs=threshold,
        )

    def _emit(
        self,
        code: EventCode,
        message: str,
        *,
        round_no: int | None = None,
        phase: Phase = Phase.NONE,
        **fields: object,
    ) -> None:
        """异常/告警行：记结构化事件并向 stderr 告警。

        畸形请求不计入队伍异常（§八）——异常计数由判题器负责，这里只是留证。
        """
        observer = getattr(self.server, "observer", None)
        if observer is not None:
            observer.emit(code, message, round_no=round_no, phase=phase, **fields)
        print(f"[future-war] WARN {message}", file=sys.stderr, flush=True)

    def _send_response(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self._response_sent = True


class _TimeoutAwareReader:
    """``rfile`` 代理：把「这次读超时了」记下来，再把异常原样抛出。

    为什么要代理：``handle_one_request`` 会自己吞掉 ``TimeoutError``，外层拿不到
    任何信号；有了这个标记，连接收尾时就能区分
    「读超时」（``timed_out=True``，要回包留证）与
    「对端正常关闭」（``readline()`` 返回 ``b""``，不该回包）。
    """

    __slots__ = ("_stream", "timed_out")

    def __init__(self, stream: object) -> None:
        self._stream = stream
        self.timed_out = False

    def readline(self, *args: object) -> bytes:
        return self._guard(self._stream.readline, args)  # type: ignore[attr-defined]

    def read(self, *args: object) -> bytes:
        return self._guard(self._stream.read, args)  # type: ignore[attr-defined]

    def _guard(self, func: object, args: tuple[object, ...]) -> bytes:
        try:
            return func(*args)  # type: ignore[operator]
        except TimeoutError:
            self.timed_out = True
            raise

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)  # close()/flush() 等原样透传


def _round_no_of(request: dict[str, object] | None) -> int | None:
    """请求里的回合号；缺失/类型不对（含 bool）返回 None（日志渲染为 `-`）。"""
    round_no = request.get("roundNo") if isinstance(request, dict) else None
    if not isinstance(round_no, int) or isinstance(round_no, bool):
        return None
    return round_no


def _command_summary(commands: object) -> str:
    """本回合指令摘要：``build:1,move:2`` + 建造目标 ``wall@(9,23)``。

    建造目标要单独列出来 —— 「这回合到底往哪儿砌了墙」是排查建造问题的第一现场。
    """
    if not isinstance(commands, dict) or not commands:
        return "none"
    counts: dict[str, int] = {}
    builds: list[str] = []
    for command in commands.values():
        if not isinstance(command, dict):
            continue
        action = command.get("action")
        action = action if isinstance(action, str) else "?"
        counts[action] = counts.get(action, 0) + 1
        if action != "build":
            continue
        name = command.get("name")
        name = name if isinstance(name, str) else "?"
        targets = command.get("targetPos")
        if isinstance(targets, list) and targets and isinstance(targets[0], dict):
            pos = targets[0]
            builds.append(f"{name}@({pos.get('x')},{pos.get('y')})")
    text = ",".join(f"{key}:{counts[key]}" for key in sorted(counts))
    return f"{text}+{'+'.join(builds)}" if builds else text


def _result_summary(results: object) -> str:
    """上回合 ``lastRoundRoleActionResults`` 摘要：``ok=1,fail=2``。

    ``fail`` 基本等价于「那条建造/动作被判定非法」——把推断错的格子暴露在日志里。
    """
    if not isinstance(results, dict) or not results:
        return "none"
    ok = sum(1 for value in results.values() if value is True)
    failed = sum(1 for value in results.values() if value is False)
    other = len(results) - ok - failed
    text = f"ok:{ok},fail:{failed}"
    return f"{text},other:{other}" if other else text


def resolve_port(port_arg: str | None) -> int:
    """从命令行参数解析端口；缺省时使用 DEFAULT_PORT。"""
    return DEFAULT_PORT if port_arg is None else int(port_arg)


def resolve_slow_round_ms(config: Config | None) -> int:
    """`server.slow_round_ms` 配置读取：非法/缺失回退默认；`0`/负数 = 关闭告警。

    parse-don't-validate：bool（JSON `true`）与字符串都不是合法阈值，一律回退
    默认——否则 `"3000"` 这类值会让比较静默失效或抛 TypeError。
    """
    value = config.get("server.slow_round_ms") if config is not None else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return SLOW_ROUND_MS_DEFAULT
    return int(value)


def create_server(
    port: int,
    observer: RoundObserver | None = None,
    bot: StrategyBot | None = None,
    *,
    slow_round_ms: int = SLOW_ROUND_MS_DEFAULT,
) -> JudgeHTTPServer:
    """构建监听 HOST:port 的多线程 HTTP 服务（工作线程随主进程退出）。"""
    server = JudgeHTTPServer((HOST, port), JudgeRequestHandler)
    server.daemon_threads = True
    server.observer = observer
    server.bot = bot
    server.slow_round_ms = slow_round_ms
    return server


def main(argv: Sequence[str] | None = None) -> int:
    """入口：`python -m future_war.server [port] [--profile <name>]`；port 缺省 8080。"""
    args = list(sys.argv[1:]) if argv is None else list(argv)
    profile, positional = parse_profile_arg(args)
    config = load_config(profile=profile)
    observer = RoundObserver.from_config(config)
    observer.emit(EventCode.I_01, "startup", stamp=config.stamp(), profile=config.profile)
    print(f"[future-war] stamp {config.stamp()}", file=sys.stderr, flush=True)
    bot = StrategyBot(config)
    port_arg = positional[0] if positional else None
    try:
        port = resolve_port(port_arg)
    except ValueError:
        print(
            f"[future-war] ERROR invalid port {port_arg!r}: expected an integer",
            file=sys.stderr,
        )
        return 2
    if not 0 <= port <= 65535:
        print(f"[future-war] ERROR port {port} out of range 0-65535", file=sys.stderr)
        return 2
    server = create_server(port, observer, bot, slow_round_ms=resolve_slow_round_ms(config))
    print(
        f"[future-war] listening on {HOST}:{port} (pid={os.getpid()})",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[future-war] received Ctrl+C, shutting down", file=sys.stderr, flush=True)
    finally:
        server.server_close()
        observer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
