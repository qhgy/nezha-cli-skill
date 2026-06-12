# nezha-cli 便携包

哪吒面板 Web Terminal 的 CLI 客户端。不开浏览器，直接管理面板里挂的所有设备。

## 一次性准备（新电脑）

只需要 [uv](https://docs.astral.sh/uv/)（Python 依赖自动装）：

```powershell
# Windows
winget install astral-sh.uv
# 或
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
```

凭证已写在 `config.json`（面板地址/账号/密码），脚本自动读取，无需任何配置。
也可用环境变量 `NEZHA_URL` / `NEZHA_USER` / `NEZHA_PASS` 覆盖。

## 用法

```powershell
# 列出面板里所有设备（拿 server id）
uv run nezha_cli.py servers

# 在指定设备上执行单条命令（默认 --server 1）
uv run nezha_cli.py exec --server 1 "hostname && uptime"
uv run nezha_cli.py exec --server 2 "df -h / && free -m"

# 复杂命令照常引号包起来
uv run nezha_cli.py exec --server 1 "cd /root/lof-monitor/app && journalctl -u lof-monitor -n 20 --no-pager"

# 交互式终端（行模式，exit 或 Ctrl+C 退出）
uv run nezha_cli.py shell --server 1
```

`exec` 会带回远程退出码（`exit=N` 打到 stderr，进程退出码同步），适合脚本化。

## 原理

走面板自己的 API，与网页终端完全同源：

1. `POST /api/v1/login` → JWT（同时种下 nz-csrf cookie）
2. `POST /api/v1/terminal`（带 X-CSRF-Token）→ session_id，面板向 agent 下发终端任务
3. `WS /api/v1/ws/terminal/<id>` → 双向流。发送帧首字节 `0x00`=输入，`0x01`=resize JSON `{"Cols","Rows"}`；接收帧为 pty 原始输出

## 已知事项

- 连到的环境 = 网页终端的环境。如果 agent 跑在容器里，看到的就是容器（如 server 1 是 Docker 容器 `1111b9d8593c`）。
- 国内面板 IP 直连，脚本已设 `trust_env=False` 忽略系统代理。
- `exec` 用哨兵 `__NZCLI_DONE__` 判定命令结束，超时（默认 60s，`--timeout` 可调）返回码 124。
- 凭证在 `config.json` 里是明文，**不要把这个目录提交到任何仓库或公开分享**。
