"""Placeholder message definitions for manifest exchange."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

MSG_MANIFEST = "manifest"
MSG_MANIFEST_REQ = "manifest_req"


@dataclass(slots=True)
class ManifestEnvelope:
    """封装单个 manifest 分片的消息结构。"""

    share: str
    chunk_index: int
    chunk_count: int
    manifest: dict[str, Any]
    compression: str | None = None

    def to_bytes(self) -> bytes:
        """序列化为 JSON 字节串。

        Returns:
            UTF-8 编码的 JSON 表示。
        """

        payload = {
            "type": MSG_MANIFEST,
            "share": self.share,
            "chunk_index": self.chunk_index,
            "chunk_count": self.chunk_count,
            "manifest": self.manifest,
            "compression": self.compression,
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_bytes(cls, payload: bytes) -> "ManifestEnvelope":
        """从 JSON 字节串恢复消息对象。

        Args:
            payload: UTF-8 编码的 JSON 字节串。

        Returns:
            ``ManifestEnvelope`` 实例。

        Raises:
            ValueError: 当 ``type`` 字段与 ``MSG_MANIFEST`` 不符时抛出。
        """

        data = json.loads(payload.decode("utf-8"))
        if data.get("type") != MSG_MANIFEST:
            raise ValueError("payload is not a manifest message")
        return cls(
            share=data["share"],
            chunk_index=int(data["chunk_index"]),
            chunk_count=int(data["chunk_count"]),
            manifest=data["manifest"],
            compression=data.get("compression"),
        )


@dataclass(slots=True)
class ManifestRequest:
    """请求指定共享域 manifest 的占位消息。"""

    share: str
    pagination_token: str | None = None

    def to_bytes(self) -> bytes:
        """序列化请求消息。

        Returns:
            UTF-8 编码的 JSON 字节串。
        """

        payload = {
            "type": MSG_MANIFEST_REQ,
            "share": self.share,
            "pagination_token": self.pagination_token,
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_bytes(cls, payload: bytes) -> "ManifestRequest":
        """反序列化请求消息。

        Args:
            payload: UTF-8 JSON 字节串。

        Returns:
            ``ManifestRequest`` 实例。

        Raises:
            ValueError: 当 ``type`` 字段不匹配时抛出。
        """

        data = json.loads(payload.decode("utf-8"))
        if data.get("type") != MSG_MANIFEST_REQ:
            raise ValueError("payload is not a manifest request")
        return cls(share=data["share"], pagination_token=data.get("pagination_token"))
