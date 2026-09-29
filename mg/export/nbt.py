"""Minimal, dependency-free NBT writer (Java edition, big-endian), plus gzip helpers.

Only what the Minecraft world format actually needs is implemented, but every tag type
used by ``level.dat`` and ``region/*.mca`` chunk payloads is supported.
"""

from __future__ import annotations

import gzip
import io
import struct
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np

# Tag ids
TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class Tag:
    """A named NBT tag."""

    __slots__ = ("id", "name", "value")

    def __init__(self, tag_id: int, name: str, value: Any):
        self.id = tag_id
        self.name = name
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Tag({self.id}, {self.name!r})"


def Byte(name: str, value: Union[int, bool]) -> Tag:
    return Tag(TAG_BYTE, name, int(value))


def Short(name: str, value: int) -> Tag:
    return Tag(TAG_SHORT, name, int(value))


def Int(name: str, value: int) -> Tag:
    return Tag(TAG_INT, name, int(value))


def Long(name: str, value: int) -> Tag:
    return Tag(TAG_LONG, name, int(value))


def Float(name: str, value: float) -> Tag:
    return Tag(TAG_FLOAT, name, float(value))


def Double(name: str, value: float) -> Tag:
    return Tag(TAG_DOUBLE, name, float(value))


def String(name: str, value: str) -> Tag:
    return Tag(TAG_STRING, name, str(value))


def ByteArray(name: str, value) -> Tag:
    return Tag(TAG_BYTE_ARRAY, name, np.asarray(value, dtype=np.int8))


def IntArray(name: str, value) -> Tag:
    return Tag(TAG_INT_ARRAY, name, np.asarray(value, dtype=np.int32))


def LongArray(name: str, value) -> Tag:
    arr = np.asarray(value)
    if arr.dtype == np.uint64:  # reinterpret, never value-cast (would overflow)
        arr = arr.view(np.int64)
    return Tag(TAG_LONG_ARRAY, name, arr.astype(np.int64, copy=False))


def List(name: str, items: Sequence[Tag], item_type: Optional[int] = None) -> Tag:
    items = list(items)
    if item_type is None:
        item_type = items[0].id if items else TAG_END
    return Tag(TAG_LIST, name, (item_type, items))


def Compound(name: str, **kwargs) -> Tag:
    return Tag(TAG_COMPOUND, name, kwargs)


def compound_of(name: str, tags: Iterable[Tag]) -> Tag:
    return Tag(TAG_COMPOUND, name, {t.name: t for t in tags})


# --------------------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------------------


def _write_string(buf: io.BytesIO, text: str) -> None:
    data = text.encode("utf-8")
    buf.write(struct.pack(">H", len(data)))
    buf.write(data)


def _write_payload(buf: io.BytesIO, tag: Tag) -> None:
    v = tag.value
    if tag.id == TAG_BYTE:
        buf.write(struct.pack(">b", int(v)))
    elif tag.id == TAG_SHORT:
        buf.write(struct.pack(">h", int(v)))
    elif tag.id == TAG_INT:
        buf.write(struct.pack(">i", int(v)))
    elif tag.id == TAG_LONG:
        buf.write(struct.pack(">q", int(v)))
    elif tag.id == TAG_FLOAT:
        buf.write(struct.pack(">f", float(v)))
    elif tag.id == TAG_DOUBLE:
        buf.write(struct.pack(">d", float(v)))
    elif tag.id == TAG_BYTE_ARRAY:
        arr = np.asarray(v, dtype=np.int8).tobytes()
        buf.write(struct.pack(">i", len(arr)))
        buf.write(arr)
    elif tag.id == TAG_STRING:
        _write_string(buf, str(v))
    elif tag.id == TAG_LIST:
        item_type, items = v
        buf.write(struct.pack(">b", item_type))
        buf.write(struct.pack(">i", len(items)))
        for item in items:
            _write_payload(buf, item)
    elif tag.id == TAG_COMPOUND:
        for child in v.values():
            buf.write(struct.pack(">b", child.id))
            _write_string(buf, child.name)
            _write_payload(buf, child)
        buf.write(struct.pack(">b", TAG_END))
    elif tag.id == TAG_INT_ARRAY:
        arr = np.ascontiguousarray(np.asarray(v, dtype=np.int32)).astype(">i4")
        buf.write(struct.pack(">i", arr.size))
        buf.write(arr.tobytes())
    elif tag.id == TAG_LONG_ARRAY:
        arr = np.ascontiguousarray(v).view(np.uint64).astype(">u8")
        buf.write(struct.pack(">i", arr.size))
        buf.write(arr.tobytes())
    else:  # pragma: no cover
        raise ValueError(f"unsupported NBT tag id {tag.id}")


def write_nbt(root: Tag, *, gzipped: bool = True, compression_level: int = 6) -> bytes:
    """Serialise a root compound tag (with header) to bytes."""
    buf = io.BytesIO()
    buf.write(struct.pack(">b", root.id))
    _write_string(buf, root.name)
    _write_payload(buf, root)
    raw = buf.getvalue()
    if not gzipped:
        return raw
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", compresslevel=compression_level, mtime=0) as gz:
        gz.write(raw)
    return out.getvalue()


def write_nbt_uncompressed(root: Tag) -> bytes:
    return write_nbt(root, gzipped=False)


# --------------------------------------------------------------------------------------
# Reading (enough to round-trip and verify our own output)
# --------------------------------------------------------------------------------------


def _read_string(buf: io.BytesIO) -> str:
    (length,) = struct.unpack(">H", buf.read(2))
    return buf.read(length).decode("utf-8")


def _read_payload(buf: io.BytesIO, tag_id: int):
    if tag_id == TAG_BYTE:
        return struct.unpack(">b", buf.read(1))[0]
    if tag_id == TAG_SHORT:
        return struct.unpack(">h", buf.read(2))[0]
    if tag_id == TAG_INT:
        return struct.unpack(">i", buf.read(4))[0]
    if tag_id == TAG_LONG:
        return struct.unpack(">q", buf.read(8))[0]
    if tag_id == TAG_FLOAT:
        return struct.unpack(">f", buf.read(4))[0]
    if tag_id == TAG_DOUBLE:
        return struct.unpack(">d", buf.read(8))[0]
    if tag_id == TAG_BYTE_ARRAY:
        (n,) = struct.unpack(">i", buf.read(4))
        return np.frombuffer(buf.read(n), dtype=np.int8)
    if tag_id == TAG_STRING:
        return _read_string(buf)
    if tag_id == TAG_LIST:
        (item_type,) = struct.unpack(">b", buf.read(1))
        (n,) = struct.unpack(">i", buf.read(4))
        return [_read_payload(buf, item_type) for _ in range(n)]
    if tag_id == TAG_COMPOUND:
        out: Dict[str, Any] = {}
        while True:
            (child_id,) = struct.unpack(">b", buf.read(1))
            if child_id == TAG_END:
                break
            name = _read_string(buf)
            out[name] = (child_id, _read_payload(buf, child_id))
        return out
    if tag_id == TAG_INT_ARRAY:
        (n,) = struct.unpack(">i", buf.read(4))
        return np.frombuffer(buf.read(n * 4), dtype=">i4").astype(np.int32)
    if tag_id == TAG_LONG_ARRAY:
        (n,) = struct.unpack(">i", buf.read(4))
        return np.frombuffer(buf.read(n * 8), dtype=">i8").astype(np.int64)
    raise ValueError(f"unsupported tag id {tag_id}")


def read_nbt(data: bytes) -> Dict[str, Any]:
    """Parse NBT bytes (gzip or raw) into ``{name: (tag_id, value)}``."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    buf = io.BytesIO(data)
    (root_id,) = struct.unpack(">b", buf.read(1))
    name = _read_string(buf)
    value = _read_payload(buf, root_id)
    return {"name": name, "id": root_id, "value": value}
