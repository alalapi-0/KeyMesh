# KeyMesh Round 3

## 项目简介
KeyMesh 是一个面向多节点、通过 mTLS 加固的文件同步与共享框架。本仓库在 Round 2 完整通信栈的基础上继续推进 Round 3：新增共享目录扫描、manifest 索引生成与差异对比能力，为后续的增量传输与冲突处理铺平道路。

## 能力范围
- `python -m keymesh init`：初始化项目目录、生成示例配置与提示。
- `python -m keymesh check`：加载并校验 `config.yaml` 的结构、路径与证书占位。
- `python -m keymesh list-shares`：读取配置并列出共享域。
- `python -m keymesh run`：启动 mTLS 服务器、客户端连接器与状态页，可选 `--once-handshake` 验证所有 peer 首次握手。
- `python -m keymesh peers`：通过本地状态页查询当前 peer 连接状态。
- `python -m keymesh manifest`：扫描共享目录并生成 manifest 快照。
- `python -m keymesh diff`：比较本地缓存与指定 peer 的 manifest 差异。
- 证书生成脚本：Linux/macOS 使用 `scripts/gen-certs.sh`，Windows 使用 `scripts/gen-certs.ps1`。

## Round 2 功能概览
- **mTLS 双向认证栈**：同时提供服务端监听与客户端主动连接，严格校验证书链与指纹白名单。
- **应用层握手协议**：交换 `node_id`、协议版本以及可访问的 share 能力列表，向后兼容预留 `features` 字段。
- **连接与心跳管理**：客户端带指数退避重连、心跳发送；服务器校验心跳超时并记录状态。
- **本地只读状态页**：默认监听 `127.0.0.1:52180`，提供 `/health`、`/peers`、`/shares` 三个只读接口。
- **CLI 扩展**：`--status-port`、`--bind-host`、`--once-handshake` 等运行时可调选项，以及 `keymesh peers` 快速查看状态。

## Round 3 功能概览
- **目录索引生成**：异步扫描每个 share，支持 `.keymeshignore` 与隐藏目录过滤，生成稳定排序的 manifest。
- **哈希策略与性能控制**：`indexing` 配置提供 `hash_policy`、`small_threshold_mb`、`sample_mb`、`max_workers` 等参数，平衡准确性与吞吐。
- **manifest 序列化与缓存**：自动写入 `out/manifests/<share>_{timestamp}.json` 及 `<share>_latest.json`，保留历史快照。
- **差异比较工具**：`keymesh diff` 计算新增/修改/删除列表，可导出 JSON 或直接打印 Rich 表格。
- **协议预留**：`keymesh/proto/sync_index.py` 定义 `MSG_MANIFEST` 与 `MSG_MANIFEST_REQ`，为后续传输层对接做准备。

## 直连网络前提
KeyMesh 假定节点之间的网络已连通，不提供 NAT 打洞或中继服务。常见做法是：
- 使用 WireGuard、ZeroTier 等 VPN 将各节点加入同一虚拟网段；
- 或在同一局域网内直接互联，确保 `config.yaml` 的 `peers[].addr` 地址可达；
- 在云/容器环境中，需要开放监听端口并保证安全组允许互访。

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
python -m keymesh run --status-port 52180
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
python -m keymesh run --status-port 52180
```

## 快速验收步骤
以下流程可在同一台机器上模拟两个节点（A、B），验证双向握手与心跳，并体验 Round 3 的 manifest/diff 能力：
1. **准备证书与配置**
   - 使用 `scripts/gen-certs.sh host-A`、`scripts/gen-certs.sh host-B` 生成两套证书；
   - `config.yaml` 中将 `node.id` 设置为 `host-A`，`listen_port` 为 `51888`；
   - `peers` 段添加 `host-B`，`addr` 指向 `127.0.0.1:51889`，并将 `host-B` 的证书指纹填入 `cert_fingerprint`；
   - 在另一份配置中镜像设置（`host-B` 监听 51889，指向 `host-A:51888`，指纹互换）。
2. **启动节点 A**
   ```bash
   python -m keymesh run --status-port 52181
   ```
   观察日志：A 正在监听并尝试连接 B，B 未启动时会进入退避重连。
3. **启动节点 B**
   ```bash
   python -m keymesh run --status-port 52182
   ```
   两端日志应显示 TLS 建立成功、HELLO/ACK 交换完成并进入心跳保活。
4. **查看状态页与 CLI**
   ```bash
   curl 127.0.0.1:52181/health
   curl 127.0.0.1:52181/peers
   python -m keymesh peers --port 52181
   ```
   输出中应看到对端 `connected: true`，以及最近一次握手/心跳时间。
5. **验证断连重连**
   - 停止节点 B 进程；
   - 节点 A 日志会提示对端断开并进入退避重连，状态页显示 `connected: false`。
6. **恢复连接**
   - 重新启动节点 B；
   - 节点 A 会自动重连并恢复心跳。

7. **生成 manifest**
   ```bash
   python -m keymesh manifest --share common
   ```
   CLI 表格会显示条目数量、忽略/跳过计数，并生成 `out/manifests/common_latest.json` 与带时间戳的快照。

8. **调整文件并比较差异**
   - 修改 `data/common/b.txt` 内容并删除 `a.txt`；
   - 再次运行 `python -m keymesh manifest --share common`；
   - 执行差异对比：
     ```bash
     python -m keymesh diff --peer local --share common
     ```
     默认输出 Rich 表格；若需 JSON，可追加 `--output diff.json`。

## 握手消息示例
HELLO：
```json
{
  "type": "HELLO",
  "node_id": "host-A",
  "version": "0.2",
  "capabilities": {
    "shares": ["common", "to-B"],
    "features": ["mtls", "heartbeat"]
  }
}
```

ACK：
```json
{
  "type": "ACK",
  "ok": true,
  "reason": null,
  "peer_id": "host-B",
  "capabilities": {
    "shares": ["common"],
    "features": ["mtls", "heartbeat"]
  }
}
```

心跳：
```json
{"type": "HEARTBEAT", "ts": 1699999999}
```

## 状态页示例
`curl 127.0.0.1:52180/peers`：
```json
{
  "peers": [
    {
      "id": "host-B",
      "addr": "127.0.0.1:51889",
      "connected": true,
      "last_error": null,
      "last_hello_ts": 1699999900.123,
      "last_ack_ts": 1699999900.456,
      "last_heartbeat_ts": 1699999920.012,
      "allowed_shares": ["common", "to-B"],
      "fingerprint": "sha256:...",
      "remote_capabilities": {
        "shares": ["common"],
        "features": ["mtls", "heartbeat"]
      }
    }
  ]
}
```

## manifest JSON 样例
`out/manifests/common_latest.json` 示例如下：
```json
{
  "share": "common",
  "generated_at": "2025-10-31T12:00:00Z",
  "entries": [
    {
      "path": "b.txt",
      "size": 42,
      "mtime": 1730340000,
      "hash": "xxh64:8f3b4d..."
    }
  ],
  "policy": {
    "hash": "auto",
    "ignore_count": 1,
    "skipped": 0,
    "small_threshold_mb": 16,
    "sample_mb": 4
  }
}
```

## diff 输出示例
`python -m keymesh diff --peer local --share common`：
```text
┏━━━━━━━━┳━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┓
┃ Share  ┃ Added ┃ Modified ┃ Deleted ┃
┡━━━━━━━━╇━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━┩
│ common │ 0     │ 1        │ 1       │
└────────┴───────┴──────────┴─────────┘
common changes:
  Added:
  Modified: b.txt
  Deleted: a.txt
```

## Round 4 文件传输引擎

第四轮实现了 KeyMesh 的核心文件传输引擎，具备以下能力：

- **可靠传输**：自定义 FILE_REQ/CHUNK/FILE_END 协议，所有帧基于 mTLS 长连接。每个数据块携带独立 `sha256` 校验，失败自动重试并可断点续传。
- **分块与续传**：默认 4MB 分块，进度记录保存在 `out/.sessions/<peer>__<share>__file.json` 中，未完成文件以 `.part` 后缀保留，恢复后自动覆盖目标文件。
- **速率与并发控制**：可通过 `transfer.rate_limit_mb_s`、`transfer.max_concurrent_per_peer` 限制单 peer 并发和速率，重试退避序列由 `transfer.retry_backoff_sec` 配置。
- **审计日志**：每次传输写入 `logs/transfers/YYYY-MM-DD.log`，格式示例：
  ```text
  [2025-11-01T12:34:56Z] peer=host-B share=common file=a.txt action=send status=success size=4194304 time=2.3s
  ```
- **CLI 工具**：
  - `python -m keymesh send --peer host-B --share common --file data/common/a.txt`
  - `python -m keymesh queue`
  - `python -m keymesh cancel 12`

### 传输协议速览

```
FILE_REQ  ->  {"file":"docs/a.txt","share":"common","size":...}
FILE_META <-  {"status":"ok","resume_offset":1048576}
CHUNK     ->  {"chunk":16,"offset":1048576,"hash":"sha256:..."} + <binary>
CHUNK_ACK <-  {"chunk":16,"status":"ok"}
...
FILE_END  ->  {"hash":"sha256:...","bytes":...}
FILE_END <-   {"status":"ok"}
```

接受方在 `share` 的根目录下创建 `.part` 文件，写入完成后与最终路径原子替换。进度文件记录已完成字节与块编号，重启服务或 CLI 手动发送时会自动续传。

### CLI 队列输出示例

```
$ python -m keymesh queue
┏━━━━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Status  ┃ Task ┃ Peer ┃ Share ┃ File                 ┃ Progress ┃
┡━━━━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ running │ #12  │ B    │ common│ reports/q4.pdf       │ 45.0%    │
│ queued  │ #13  │ C    │ docs  │ handbook.md          │ 0.0%     │
└─────────┴──────┴──────┴───────┴──────────────────────┴──────────┘
```

`python -m keymesh cancel 12` 会写入取消标记，正在运行的任务在下一次调度时自动终止。

### 安全策略

- 传输链路沿用 Round 2 的 mTLS 握手，证书和指纹校验逻辑保持不变。
- 所有写入均通过 `ensure_within` 校验，确保目标文件落在共享目录内，不会越权覆盖。
- 校验失败或异常均记录在审计日志，并保留 `.part` 文件等待人工处理。
- 完成后使用原子重命名覆盖旧文件，避免中途出现半成品。

### 性能建议

- 默认 4MB 分块适用于多数场景，可根据链路质量将 `transfer.chunk_size_mb` 调整至更大值以提升吞吐。
- 小文件建议批量触发 `keymesh send`；大文件断点续传依赖 `.sessions` 记录，请确保该目录持久化。

## Round 3 常见排错
- **无权限路径**：manifest 扫描遇到 `PermissionError` 时会跳过该文件并在表格中统计 `skipped`，请检查共享目录权限或在 `.keymeshignore` 中排除。
- **符号链接未收录**：当前仅索引常规文件，软链接会被忽略；如需同步请转换为硬链接或等待后续版本支持。
- **哈希耗时**：默认对大文件采样 4MB，可在 `config.yaml` 的 `indexing.hash_policy` 设置为 `full` 或调整 `sample_mb` 与 `max_workers`。
- **远端 manifest 缺失**：`keymesh diff` 会提示 `no remote manifest`，请确认已保存对端 manifest 或使用 `--peer local` 对比历史版本。

## 常见错误排查
- **证书或密钥缺失**：`keymesh check --config ...` 会提示缺失路径，请确认 `keys/` 目录下文件齐全。
- **指纹不匹配**：日志出现 `fingerprint mismatch` 时，核对 `config.yaml` 中的 `cert_fingerprint` 是否与实际证书一致，可使用 `openssl x509 -noout -fingerprint -sha256 -in <cert>` 生成。
- **端口不可达**：确认对端监听端口已开放，必要时检查本地防火墙或容器端口映射。
- **VPN 未连接**：若通过 WireGuard/ZeroTier 等组网，确保隧道处于已连接状态，可先互相 `ping` 验证连通性。

## 安全说明
- 所有密钥材料存放于 `keys/`，该目录已在 `.gitignore` 中忽略，禁止提交到仓库。
- 配置模型要求列出允许的共享路径，并通过路径越权检测防止访问根目录之外的文件。
- mTLS 握手会校验证书链并支持指纹白名单；状态页仅绑定本地回环地址，不暴露敏感密钥。

## 后续路线图
- Round 4：接入 manifest 传输与增量块调度，实现端到端文件同步。
- Round 5：完善监控告警、日志聚合与安全策略强化。
- Round 6：提供 GUI/REST 接入层与自动化部署工具。

如需进一步的内网穿透或中继，请优先确保基础 VPN/专线连通，再结合本仓库的直连通信栈进行部署验证。
