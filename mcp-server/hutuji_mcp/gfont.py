"""奎享 .gfont 单线字库解析（移植自 kx/app/crates/font_gfont 的 Rust 参照实现）。

格式（全部大端、Java DataInput 语义）：
- i32 version；v>=5 时紧跟 i32 加密头长度 + XXTEA-ish 加密的 meta 块，否则 meta 明文内联
- meta：family/languageKind/displayName/sourceName/mark/size/declaredCount，
  v>=9 再带 defaultAdvance/top/bottom/left/right/baseline/capHeight（f32×7）
- i32 previewCount + preview 字形，之后是 ZIP 全量字形库（按 key 命名条目）
- 字形：key（v<=7 为 u16 char，v>7 为 utf）+ v>=9 的 width f32 + 若干 chunk
  （i32 点数 + f32 点列 + i32 verb 数 + verb 字节列；verb 0=move 1=line 2=cubic）

合规备注：字体文件为奎享专有素材，不入库；运行时经 HUTUJI_GFONT_DIR 指向本机目录。
"""

from __future__ import annotations

import io
import struct
import zipfile
from dataclasses import dataclass, field

# 与 Rust 参照一致的固定头密钥（"19890826 19920828" 逐位数字）
_FONT_HEADER_KEY = (1, 9, 8, 9, 0, 8, 2, 6, 1, 9, 9, 2, 0, 8, 2, 8)

_CURVE_STEPS = 24


class GfontError(Exception):
    """gfont 解析/解密失败。"""


def _read_i32(buf: io.BytesIO) -> int:
    data = buf.read(4)
    if len(data) < 4:
        raise GfontError("gfont 截断：读 i32 失败")
    return struct.unpack(">i", data)[0]


def _read_f32(buf: io.BytesIO) -> float:
    data = buf.read(4)
    if len(data) < 4:
        raise GfontError("gfont 截断：读 f32 失败")
    return struct.unpack(">f", data)[0]


def _read_utf(buf: io.BytesIO) -> str:
    data = buf.read(2)
    if len(data) < 2:
        raise GfontError("gfont 截断：读 utf 长度失败")
    (length,) = struct.unpack(">H", data)
    raw = buf.read(length)
    if len(raw) < length:
        raise GfontError("gfont 截断：读 utf 内容失败")
    return raw.decode("utf-8", "replace")


def _skip_utf(buf: io.BytesIO) -> None:
    _read_utf(buf)


def _read_char(buf: io.BytesIO) -> str:
    data = buf.read(2)
    if len(data) < 2:
        raise GfontError("gfont 截断：读 char 失败")
    (value,) = struct.unpack(">H", data)
    return chr(value)


def _pack_java_words(data: bytes | list[int]) -> list[int]:
    """Java byte[]→int[] 大端打包（按 4 字节一组，补 3 个 0 防越界，与 Rust 版对齐）。"""
    expanded = list(data) + [0, 0, 0]
    out = []
    i = 0
    while i < len(data):
        out.append(
            (expanded[i + 3] & 0xFF)
            | ((expanded[i + 2] & 0xFF) << 8)
            | ((expanded[i + 1] & 0xFF) << 16)
            | ((expanded[i] & 0xFF) << 24)
        )
        i += 4
    return out


def _swap_u32(value: int) -> int:
    return (
        ((value & 0xFF000000) >> 24)
        | ((value & 0x00FF0000) >> 8)
        | ((value & 0x0000FF00) << 8)
        | ((value & 0x000000FF) << 24)
    ) & 0xFFFFFFFF


def _decrypt_block(block: list[int], key: list[int]) -> list[int]:
    """8 字节分组的类 XTEA 解密（16 轮，delta 0x9E3779B9，输出倒序+字节交换，对齐 Rust 版）。"""
    words = _pack_java_words(block)
    keys = _pack_java_words(key)
    n2, n3 = words[0], words[1]
    delta = 0x9E3779B9
    total = (delta << 4) & 0xFFFFFFFFFFFFFFFF
    for _ in range(16):
        n3 = (n3 - ((((n2 << 4) + keys[2]) & 0xFFFFFFFFFFFFFFFF) ^ ((n2 + total) & 0xFFFFFFFFFFFFFFFF) ^ (((n2 >> 5) + keys[3]) & 0xFFFFFFFFFFFFFFFF))) & 0xFFFFFFFF
        n2 = (n2 - ((((n3 << 4) + keys[0]) & 0xFFFFFFFFFFFFFFFF) ^ ((n3 + total) & 0xFFFFFFFFFFFFFFFF) ^ (((n3 >> 5) + keys[1]) & 0xFFFFFFFFFFFFFFFF))) & 0xFFFFFFFF
        total = (total - delta) & 0xFFFFFFFFFFFFFFFF
    tail = struct.pack(">II", _swap_u32(n3), _swap_u32(n2))[::-1]
    return list(tail)


def decode_font_header(ciphertext: bytes) -> bytes:
    """解密 v>=5 的加密 meta 头（CBC 式链式 XOR，首块含 1 字节随机+pad+2 字节校验+7 字节尾）。"""
    if len(ciphertext) < 16 or len(ciphertext) % 8 != 0:
        raise GfontError(f"加密头长度非法：{len(ciphertext)}")

    obj = list(ciphertext)
    key = list(_FONT_HEADER_KEY)
    e = _decrypt_block(obj[:8], key)

    pad_len = e[0] & 7
    total_plain = len(ciphertext) - (1 + pad_len + 2 + 7)
    if total_plain < 0:
        raise GfontError("解密后头长度越界")

    previous_cipher = [0] * 8
    current_cipher = obj[:8]
    offset = 8
    index = pad_len + 1

    skip = 1
    while skip <= 2:
        if index < 8:
            index += 1
            skip += 1
            continue
        if index == 8:
            if offset + 8 > len(obj):
                raise GfontError("加密头在跳填充阶段截断")
            previous_cipher = current_cipher
            current_cipher = obj[offset : offset + 8]
            for i in range(8):
                e[i] ^= obj[offset + i]
            e = _decrypt_block(e, key)
            offset += 8
            index = 0

    plain = bytearray()
    remaining = total_plain
    while remaining > 0:
        if index < 8:
            plain.append((e[index] ^ previous_cipher[index]) & 0xFF)
            index += 1
            remaining -= 1
            continue
        if offset + 8 > len(obj):
            raise GfontError("加密头在取数据阶段截断")
        previous_cipher = current_cipher
        current_cipher = obj[offset : offset + 8]
        for i in range(8):
            e[i] ^= obj[offset + i]
        e = _decrypt_block(e, key)
        offset += 8
        index = 0

    # 尾部 7 字节丢弃，但链式状态无需再用于输出——Rust 版同样只消费不输出
    return bytes(plain)


@dataclass
class FontMeta:
    version: int
    language_kind: int
    family: str
    display_name: str
    source_name: str
    size: int
    declared_count: int
    mark: str
    uuid: str
    default_advance: float = 0.0
    top: float = 0.0
    bottom: float = 0.0
    left: float = 0.0
    right: float = 0.0
    baseline: float = 0.0
    cap_height: float = 0.0


@dataclass
class GlyphPathChunk:
    points: list[float]
    verbs: list[int]


@dataclass
class GlyphData:
    key: str
    width: float
    chunks: list[GlyphPathChunk] = field(default_factory=list)


def _parse_meta(version: int, buf: io.BytesIO) -> FontMeta:
    family = _read_utf(buf)
    language_kind = _read_i32(buf)
    display_name = _read_utf(buf)
    source_name = _read_utf(buf)
    mark = _read_utf(buf)
    size = _read_i32(buf)
    declared_count = _read_i32(buf)

    uuid = ""
    if version >= 2:
        uuid = _read_utf(buf)
    if version >= 4:
        _skip_utf(buf)
    if version >= 7:
        uuid = _read_utf(buf)

    kwargs: dict[str, float] = {}
    if version >= 9:
        kwargs = {
            "default_advance": _read_f32(buf),
            "top": _read_f32(buf),
            "bottom": _read_f32(buf),
            "left": _read_f32(buf),
            "right": _read_f32(buf),
            "baseline": _read_f32(buf),
            "cap_height": _read_f32(buf),
        }

    return FontMeta(
        version=version,
        language_kind=language_kind,
        family=family,
        display_name=display_name,
        source_name=source_name,
        size=size,
        declared_count=declared_count,
        mark=mark,
        uuid=uuid,
        **kwargs,
    )


def _parse_glyph(version: int, buf: io.BytesIO, data: bytes, preview_only: bool) -> GlyphData:
    key = _read_char(buf) if version <= 7 else _read_utf(buf)
    width = _read_f32(buf) if version >= 9 else 0.0
    chunks: list[GlyphPathChunk] = []

    while buf.tell() < len(data):
        point_count_raw = buf.read(4)
        if len(point_count_raw) < 4:
            break
        (point_count,) = struct.unpack(">i", point_count_raw)
        if not 0 <= point_count < 10_000:
            raise GfontError(f"字形点数非法：{point_count}")
        points = [_read_f32(buf) for _ in range(point_count)]

        verb_count = _read_i32(buf)
        if not 0 <= verb_count < 10_000:
            raise GfontError(f"字形 verb 数非法：{verb_count}")
        verbs_raw = buf.read(verb_count)
        if len(verbs_raw) < verb_count:
            raise GfontError("gfont 截断：读 verbs 失败")
        verbs = list(verbs_raw)

        if verbs:
            chunks.append(GlyphPathChunk(points=points, verbs=verbs))

        if preview_only:
            break

    return GlyphData(key=key, width=width, chunks=chunks)


def _normalized_glyph_entry(key: str, version: int) -> str:
    if version < 3:
        return key
    return "_".join(key)


class GfontFile:
    """已解析的 .gfont：meta + preview 字形 + ZIP 全量字形库（惰性按 key 取）。"""

    def __init__(self, meta: FontMeta, preview: dict[str, GlyphData], zip_blob: bytes):
        self.meta = meta
        self.preview = preview
        self._zip_blob = zip_blob

    @classmethod
    def parse(cls, data: bytes) -> "GfontFile":
        buf = io.BytesIO(data)
        version = _read_i32(buf)
        if version > 9:
            raise GfontError(f"不支持的 gfont 版本：{version}")

        # v>=5 一般是加密头；但实测「方正兰亭超细黑体」v6 仍是明文 meta
        # （i32 之后直接是 utf family）——encrypted_len 非法时按明文回退
        use_plain_header = version < 5
        if version >= 5:
            probe = io.BytesIO(data)
            probe.seek(4)
            encrypted_len = _read_i32(probe)
            end = 8 + encrypted_len
            if encrypted_len < 16 or encrypted_len % 8 != 0 or end > len(data):
                use_plain_header = True

        if use_plain_header:
            header_bytes = data[4:]
        else:
            buf.seek(8 + encrypted_len)
            header_bytes = decode_font_header(data[8 : 8 + encrypted_len])

        header_buf = io.BytesIO(header_bytes)
        meta = _parse_meta(version, header_buf)
        if use_plain_header:
            buf.seek(4 + header_buf.tell())

        # preview 计数宽度两种：加密头字体用 i32（陈继世6/如果梦实测），
        # 明文头的方正兰亭用 u16——i32 读出天文数字时回退高 16 位为计数、低 16 位即首字形 key
        preview_raw = _read_i32(buf)
        if preview_raw > 10_000:
            preview_count = preview_raw >> 16
            buf.seek(buf.tell() - 2)
        else:
            preview_count = max(0, preview_raw)
        preview: dict[str, GlyphData] = {}
        for _ in range(preview_count):
            glyph = _parse_glyph(version, buf, data, preview_only=True)
            preview[glyph.key] = glyph

        zip_blob = data[buf.tell() :]
        return cls(meta, preview, zip_blob)

    def has_glyph(self, key: str) -> bool:
        if key in self.preview:
            return True
        try:
            return self.load_glyph(key) is not None
        except GfontError:
            return False

    def missing_chars(self, text: str) -> list[str]:
        return [ch for ch in text if not self.has_glyph(ch)]

    def load_glyph(self, key: str) -> GlyphData | None:
        # Windows 端字体（v5-v7 实测）条目名是十进制码位字符串（如「永」→"27704"）；
        # Rust 参照的 "_".join 命名对应 Mac 端另一变体——按序探测三种命名
        candidates = [str(ord(key[0]))] if len(key) == 1 else []
        candidates.append(key)
        candidates.append(_normalized_glyph_entry(key, self.meta.version))
        raw: bytes | None = None
        try:
            with zipfile.ZipFile(io.BytesIO(self._zip_blob)) as archive:
                for entry in candidates:
                    try:
                        raw = archive.read(entry)
                        break
                    except KeyError:
                        continue
        except zipfile.BadZipFile as exc:
            raise GfontError(f"字形 ZIP 库损坏：{exc}") from exc
        if raw is None:
            return None
        buf = io.BytesIO(raw)
        return _parse_glyph(self.meta.version, buf, raw, preview_only=False)


def _cubic_point(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    mt = 1.0 - t
    return mt * mt * mt * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t * t * t * p3


def glyph_to_polylines(
    glyph: GlyphData, offset_x: float = 0.0, offset_y: float = 0.0
) -> list[list[tuple[float, float]]]:
    """verb 序列 → 多段线；cubic 按 24 步细分（与 Rust 参照同参）。"""
    paths: list[list[tuple[float, float]]] = []

    for chunk in glyph.chunks:
        current: list[tuple[float, float]] = []
        point_index = 0
        cursor = (0.0, 0.0)

        for verb in chunk.verbs:
            if verb == 0:  # moveTo：收前一段、起新段
                if current:
                    paths.append(current)
                    current = []
                if point_index + 1 >= len(chunk.points):
                    break
                cursor = (
                    chunk.points[point_index] + offset_x,
                    chunk.points[point_index + 1] + offset_y,
                )
                current.append(cursor)
                point_index += 2
            elif verb == 1:  # lineTo
                if point_index + 1 >= len(chunk.points):
                    break
                cursor = (
                    chunk.points[point_index] + offset_x,
                    chunk.points[point_index + 1] + offset_y,
                )
                current.append(cursor)
                point_index += 2
            elif verb == 2:  # cubicTo：取 6 个坐标，24 步细分
                if point_index + 5 >= len(chunk.points):
                    break
                c1 = (
                    chunk.points[point_index] + offset_x,
                    chunk.points[point_index + 1] + offset_y,
                )
                c2 = (
                    chunk.points[point_index + 2] + offset_x,
                    chunk.points[point_index + 3] + offset_y,
                )
                end = (
                    chunk.points[point_index + 4] + offset_x,
                    chunk.points[point_index + 5] + offset_y,
                )
                for step in range(1, _CURVE_STEPS + 1):
                    t = step / _CURVE_STEPS
                    current.append(
                        (
                            _cubic_point(cursor[0], c1[0], c2[0], end[0], t),
                            _cubic_point(cursor[1], c1[1], c2[1], end[1], t),
                        )
                    )
                cursor = end
                point_index += 6
            # 其余 verb（quad 等）按 Rust 参照忽略

        if current:
            paths.append(current)

    return paths


def glyph_advance(glyph: GlyphData) -> float:
    """步进宽：v>=9 用 width 字段，否则取笔画最大 X，兜底 10（与 Rust 参照一致）。"""
    if glyph.width > 0.0:
        return glyph.width
    max_x = 0.0
    for chunk in glyph.chunks:
        for i in range(0, len(chunk.points) - 1, 2):
            if chunk.points[i] > max_x:
                max_x = chunk.points[i]
    return max_x if max_x > 0.0 else 10.0
