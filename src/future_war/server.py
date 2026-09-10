"""HTTP 服务入口：《未来战争》v1.0 bot 的判题轮询服务。

判题系统每回合向本服务 POST 当前战场状态（docs/接口文档.md §1），
本服务返回 Response（§2.1）:

    {"roleCommandMap": {...}, "prompt": "...", "executeCmd": "..."}

当前为脚手架阶段：任意请求均返回空 roleCommandMap 的合法 Response。
策略与游戏逻辑在后续工作包接入 do_POST 的处理链。
约束（docs/任务书.md §八）：连接 10s / 响应 5s 超时；进程崩溃即判负。
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Final, TypedDict

from future_war.config import load_config, parse_profile_arg
from future_war.models import parse_request, remove_trailing_commas, serialize_response
from future_war.observability.events import EventCode
from future_war.observability.round_metrics import RoundObserver
from future_war.strategy import StrategyBot

HOST: Final = "0.0.0.0"
DEFAULT_PORT: Final = 8080
# 与判题器 5s 响应预算匹配的 socket 读写上限（docs/任务书.md §八）
SOCKET_TIMEOUT_SECONDS: Final = 5.0


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

    def server_bind(self) -> None:
        """完成 bind 并登记 server_name/port，跳过 getfqdn。"""
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class JudgeRequestHandler(BaseHTTPRequestHandler):
    """处理判题器 POST：读取 JSON 请求体，返回合法 Response JSON。"""

    timeout = SOCKET_TIMEOUT_SECONDS

    def do_POST(self) -> None:
        """POST 任意路径：解析请求体，规划指令，落盘回合并返回 Response。"""
        request = self._parse_request(self._read_body())
        response = self._respond(request)
        self._observe(request, response)
        self._send_response(response)

    def handle_timeout(self) -> None:
        """读/写超时仍返回合法 Response，避免被判「响应格式错误」（§八）。"""
        try:
            self._send_response(EMPTY_RESPONSE)
        except OSError:
            print(
                "[future-war] WARN timeout response not sent (connection closed)",
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

    def _respond(self, request: dict[str, object] | None) -> JudgeResponse:
        """运行策略 Bot 得到本回合指令；任何失败都回退为空指令（§八）。"""
        bot = getattr(self.server, "bot", None)
        if bot is None or request is None:
            return EMPTY_RESPONSE
        try:
            response = bot(parse_request(request))
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK — 进程绝不能崩溃（§八）
            self._emit(EventCode.X_01, f"strategy error: {exc}")
            return EMPTY_RESPONSE
        return serialize_response(response)  # type: ignore[return-value]

    def _observe(
        self, request: dict[str, object] | None, response: JudgeResponse
    ) -> None:
        """写入本回合日志与指标；畸形请求已由 _parse_request 记异常行。"""
        observer = getattr(self.server, "observer", None)
        if observer is None or request is None:
            return
        round_no = request.get("roundNo")
        if not isinstance(round_no, int) or isinstance(round_no, bool):
            round_no = 0
        observer.observe(round_no, request, response)

    def _emit(self, code: EventCode, message: str) -> None:
        """畸形请求不计入队伍异常（§八）：记结构化行并向 stderr 告警。"""
        observer = getattr(self.server, "observer", None)
        if observer is not None:
            observer.emit(code, message)
        print(f"[future-war] WARN {message}", file=sys.stderr, flush=True)

    def _send_response(self, payload: JudgeResponse) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def resolve_port(port_arg: str | None) -> int:
    """从命令行参数解析端口；缺省时使用 DEFAULT_PORT。"""
    return DEFAULT_PORT if port_arg is None else int(port_arg)


def create_server(
    port: int,
    observer: RoundObserver | None = None,
    bot: StrategyBot | None = None,
) -> JudgeHTTPServer:
    """构建监听 HOST:port 的多线程 HTTP 服务（工作线程随主进程退出）。"""
    server = JudgeHTTPServer((HOST, port), JudgeRequestHandler)
    server.daemon_threads = True
    server.observer = observer
    server.bot = bot
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
    server = create_server(port, observer, bot)
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
