---
runMethod: inline
shortDescription: Nezha panel terminal - manage remote servers via CLI
triggerPatterns:
  - "nezha"
  - "哪吒"
  - "面板"
whenToUse: |
  Use when the user wants to:
  - List servers in the Nezha panel
  - Execute commands on remote servers managed by Nezha
  - Open an interactive shell to a Nezha-managed server
  - Check server status, uptime, or info from the panel
---

# Nezha CLI Skill

哪吒面板 Web Terminal 的 CLI 客户端。直接通过命令行管理面板中的所有设备。

## 可用命令

### 列出所有服务器
```bash
uv run D:\0cc\.claude\skills\nezha\nezha_cli.py servers
```

### 在指定服务器上执行命令
```bash
uv run D:\0cc\.claude\skills\nezha\nezha_cli.py exec --server <id> "<command>"
```

### 打开交互式终端
```bash
uv run D:\0cc\.claude\skills\nezha\nezha_cli.py shell --server <id>
```

## 配置

凭证从以下来源读取（优先级从高到低）：
1. 环境变量：`NEZHA_URL`, `NEZHA_USER`, `NEZHA_PASS`
2. `config.json` 文件（已包含在 skill 目录中）

## 示例

根据用户需求执行相应的 nezha_cli.py 命令。例如：
- 用户说"列出所有服务器" → 运行 `servers` 命令
- 用户说"在服务器1上执行 df -h" → 运行 `exec --server 1 "df -h"`
- 用户说"连接到服务器2" → 运行 `shell --server 2`

执行命令后，将输出返回给用户。
