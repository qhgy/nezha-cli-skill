# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "websocket-client"]
# ///
"""哪吒面板 Web Terminal 的 CLI 客户端。

替代浏览器访问 /dashboard/terminal/<id>，直接走面板 API：
  1. POST /api/v1/login                -> JWT token
  2. POST /api/v1/terminal             -> session_id（面板向 agent 下发 TaskTypeTerminalGRPC）
  3. WS   /api/v1/ws/terminal/<id>     -> 双向终端流
     发送帧: b'\x00' + 输入字节 | b'\x01' + JSON {"Cols":N,"Rows":N}（resize）
     接收帧: pty 原始输出

用法（凭据只从环境变量读，不落盘）:
  $env:NEZHA_URL  = "http://18.136.155.90:8008"
  $env:NEZHA_USER = "admin"
  $env:NEZHA_PASS = "..."
  uv run nezha_cli.py exec --server 1 "cd /root/lof-monitor/app && ls -la"
  uv run nezha_cli.py shell --server 1          # 交互模式
"""

import argparse
import json
import os
import re
import sys
import threading
import time

import requests
import websocket


def die(msg: str) -> None:
    print(f"[nezha-cli] {msg}", file=sys.stderr)
    sys.exit(1)


def get_env() -> tuple[str, str, str]:
    """凭据优先级：环境变量 > 脚本同目录 config.json（便携包模式）"""
    url = os.environ.get("NEZHA_URL", "")
    user = os.environ.get("NEZHA_USER", "")
    pwd = os.environ.get("NEZHA_PASS", "")
    if not user or not pwd:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            url = url or cfg.get("url", "")
            user = user or cfg.get("user", "")
            pwd = pwd or cfg.get("password", "")
    url = (url or "http://18.136.155.90:8008").rstrip("/")
    if not user or not pwd:
        die("缺少凭据：设置环境变量 NEZHA_USER/NEZHA_PASS，或在脚本同目录放 config.json")
    return url, user, pwd


def login(url: str, user: str, pwd: str) -> tuple[requests.Session, str]:
    sess = requests.Session()
    sess.trust_env = False  # 面板是直连 IP，忽略系统代理设置
    r = sess.post(f"{url}/api/v1/login",
                  json={"username": user, "password": pwd}, timeout=10)
    if r.status_code != 200:
        die(f"登录失败 HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    token = (body.get("data") or {}).get("token") or body.get("token")
    if not token:
        die(f"登录响应里没有 token: {json.dumps(body)[:200]}")
    return sess, token


def create_terminal(url: str, sess: requests.Session, token: str, server_id: int) -> str:
    # 面板对 cookie 鉴权的 POST 启用了 CSRF 双提交校验：
    # 登录时种下 nz-csrf cookie，这里要镜像到 X-CSRF-Token 头
    csrf = sess.cookies.get("nz-csrf", "")
    r = sess.post(f"{url}/api/v1/terminal",
                  json={"server_id": server_id},
                  headers={"Authorization": f"Bearer {token}",
                           "X-CSRF-Token": csrf}, timeout=10)
    if r.status_code != 200:
        die(f"创建终端失败 HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    sid = (body.get("data") or {}).get("session_id")
    if not sid:
        die(f"创建终端响应异常: {json.dumps(body)[:300]}")
    return sid


def open_ws(url: str, token: str, session_id: str) -> websocket.WebSocket:
    ws_url = url.replace("http://", "ws://").replace("https://", "wss://")
    ws = websocket.create_connection(
        f"{ws_url}/api/v1/ws/terminal/{session_id}",
        header=[f"Authorization: Bearer {token}"],
        timeout=15,
    )
    # 先发一次 resize，agent 端 pty 才有合理的窗口大小
    ws.send_binary(b"\x01" + json.dumps({"Cols": 220, "Rows": 50}).encode())
    return ws


def recv_loop(ws: websocket.WebSocket, idle_timeout: float, sink) -> None:
    """持续收输出，连续 idle_timeout 秒无数据则返回。"""
    ws.settimeout(idle_timeout)
    while True:
        try:
            frame = ws.recv()
        except websocket.WebSocketTimeoutException:
            return
        except (websocket.WebSocketException, OSError):
            return
        if isinstance(frame, str):
            frame = frame.encode()
        if frame:
            sink(frame)


def cmd_exec(args: argparse.Namespace) -> None:
    url, user, pwd = get_env()
    sess, token = login(url, user, pwd)
    sid = create_terminal(url, sess, token, args.server)
    ws = open_ws(url, token, sid)
    time.sleep(1.0)  # 等 shell 起来、吐出 prompt

    # 双侧哨兵 + 退出码；回显行里哨兵后面跟的是 %s 字面量，不会误命中数字正则
    sentinel = "__NZCLI_DONE__"
    full_cmd = f"{args.command}; printf '\\n{sentinel}%s{sentinel}\\n' $?\n"
    ws.send_binary(b"\x00" + full_cmd.encode())

    pat = re.compile((sentinel + r"(\d+)" + sentinel).encode())
    buf = b""
    exit_code = None
    deadline = time.time() + args.timeout
    ws.settimeout(2)
    while time.time() < deadline:
        try:
            frame = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        except (websocket.WebSocketException, OSError):
            break
        if isinstance(frame, str):
            frame = frame.encode()
        buf += frame
        m = pat.search(buf)
        if m:
            exit_code = int(m.group(1))
            buf = buf[:m.start()]
            break
    ws.close()

    text = buf.decode("utf-8", errors="replace")
    # 去掉 ANSI 控制序列和回显的命令行/提示行，只留真实输出
    text = re.sub(r"\x1b\][^\x07]*\x07|\x1b\[[0-9;?]*[A-Za-z]", "", text)
    lines = text.splitlines()
    out_lines = []
    skipping_echo = True
    for line in lines:
        if skipping_echo:
            if sentinel in line or args.command[:30] in line:
                skipping_echo = False
            continue
        out_lines.append(line)
    sys.stdout.buffer.write("\n".join(out_lines).strip("\r\n").encode("utf-8"))
    sys.stdout.buffer.flush()
    if exit_code is not None:
        print(f"[nezha-cli] exit={exit_code}", file=sys.stderr)
        sys.exit(exit_code)
    print("[nezha-cli] 超时未收到结束标记", file=sys.stderr)
    sys.exit(124)


def cmd_shell(args: argparse.Namespace) -> None:
    url, user, pwd = get_env()
    sess, token = login(url, user, pwd)
    sid = create_terminal(url, sess, token, args.server)
    ws = open_ws(url, token, sid)
    print("[nezha-cli] 已连接，输入命令回车执行，Ctrl+C / exit 退出", file=sys.stderr)

    stop = threading.Event()

    def pump_output() -> None:
        while not stop.is_set():
            try:
                frame = ws.recv()
            except (websocket.WebSocketException, OSError):
                stop.set()
                return
            if isinstance(frame, str):
                frame = frame.encode()
            sys.stdout.buffer.write(frame)
            sys.stdout.buffer.flush()

    t = threading.Thread(target=pump_output, daemon=True)
    t.start()
    try:
        while not stop.is_set():
            line = sys.stdin.readline()
            if not line:
                break
            ws.send_binary(b"\x00" + line.encode())
            if line.strip() == "exit":
                time.sleep(0.5)
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            ws.close()
        except Exception:
            pass


def cmd_servers(args: argparse.Namespace) -> None:
    url, user, pwd = get_env()
    sess, token = login(url, user, pwd)
    r = sess.get(f"{url}/api/v1/server",
                 headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if r.status_code != 200:
        die(f"获取服务器列表失败 HTTP {r.status_code}: {r.text[:200]}")
    for s in (r.json().get("data") or []):
        geo = (s.get("geoip") or {}).get("ip") or {}
        ip = geo.get("ipv4_addr") or geo.get("ipv6_addr") or "?"
        host = s.get("host") or {}
        state = s.get("state") or {}
        up_h = (state.get("uptime") or 0) // 3600
        print(f"id={s['id']:<3} {s.get('name','?'):<20} ip={ip:<16} "
              f"{host.get('platform','?'):<8} uptime={up_h}h")


def main() -> None:
    p = argparse.ArgumentParser(description="Nezha web terminal CLI client")
    sub = p.add_subparsers(dest="action", required=True)

    pe = sub.add_parser("exec", help="执行单条命令并返回输出")
    pe.add_argument("command")
    pe.add_argument("--server", type=int, default=1)
    pe.add_argument("--timeout", type=float, default=60)
    pe.set_defaults(func=cmd_exec)

    ps = sub.add_parser("shell", help="交互式终端（行模式）")
    ps.add_argument("--server", type=int, default=1)
    ps.set_defaults(func=cmd_shell)

    pl = sub.add_parser("servers", help="列出面板中的所有服务器")
    pl.set_defaults(func=cmd_servers)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
