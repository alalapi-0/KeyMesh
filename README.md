# KeyMesh Round 1 脚手架

## 项目简介
KeyMesh 是一个面向多节点、通过 mTLS 加固的文件同步与共享框架。本仓库处于 Round 1，聚焦在 CLI 脚手架、配置样例与证书生成脚本，帮助后续轮次快速集成安全同步能力。

## 能力范围
- `python -m keymesh init`：初始化项目目录、生成示例配置与提示。
- `python -m keymesh check`：加载并校验 `config.yaml` 的结构、路径与证书占位。
- `python -m keymesh list-shares`：读取配置并列出共享域。
- `python -m keymesh run`：Round 2 预留占位符。
- 证书生成脚本：Linux/macOS 使用 `scripts/gen-certs.sh`，Windows 使用 `scripts/gen-certs.ps1`。

## 快速开始
### Linux/macOS
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m keymesh init
bash scripts/gen-certs.sh host-A
cp config.sample.yaml config.yaml
python -m keymesh check
python -m keymesh list-shares
python -m keymesh run
```

### Windows (PowerShell)
```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m keymesh init
powershell -ExecutionPolicy Bypass -File .\scripts\gen-certs.ps1 -NodeId host-A
Copy-Item config.sample.yaml config.yaml
python -m keymesh check
python -m keymesh list-shares
python -m keymesh run
```

## 平台差异说明
- 证书脚本在 Linux/macOS 使用 Bash，在 Windows 使用 PowerShell；两者均依赖本地安装的 OpenSSL。
- Windows 路径分隔符为 `\\`，脚本与 CLI 会自动归一化路径，但在 `config.yaml` 中推荐使用 POSIX 风格以减少歧义。
- Windows 环境需要通过 `Set-ExecutionPolicy` 或 `-ExecutionPolicy Bypass` 执行脚本，而 Linux/macOS 仅需确保脚本可执行。

## 安全说明
- 所有密钥材料存放于 `keys/`，该目录已在 `.gitignore` 中忽略，禁止提交到仓库。
- 配置模型要求列出允许的共享路径，并通过内建的路径越权检测防止访问根目录之外的文件。
- 证书与 mTLS 握手逻辑将在后续轮次中实现，本轮仅预留配置与检查。

## 后续轮次路线图
- Round 2：实现基础的 `run` 命令、握手与传输流程骨架。
- Round 3：补充增量同步、冲突解决策略与多节点拓扑优化。
- Round 4：完善监控告警、日志聚合与安全策略强化。
- Round 5：提供 GUI/REST 接入层与自动化部署工具。

如需内网穿透或 VPN，请先确保各节点之间网络可达，并在 `config.yaml` 的 `peers[].addr` 字段中填写可访问地址。
