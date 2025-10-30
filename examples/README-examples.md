# KeyMesh 示例拓扑

本目录将在后续轮次提供更多拓扑案例。当前轮次提供一个最小的三节点示例：

- 节点 A：作为协调者，维护公共共享 `common`。
- 节点 B、C：各自拥有针对自身的私有共享 `to-B`、`to-C`，与公共共享同步。

示例配置片段：

```yaml
peers:
  - id: "host-B"
    addr: "10.8.0.12:51888"
    shares_access:
      - share: "common"
        mode: "rw"
      - share: "to-B"
        mode: "rw"
  - id: "host-C"
    addr: "10.8.0.13:51888"
    shares_access:
      - share: "common"
        mode: "ro"
      - share: "to-C"
        mode: "rw"
```

当前仅提供配置与校验功能，实际握手、加密传输与同步将在 Round 2+ 中实现。
