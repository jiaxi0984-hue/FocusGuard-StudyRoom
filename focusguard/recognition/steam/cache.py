"""有长度和深度限制的 Steam 本地 KeyValues 读取器；不读取账号与凭据。

格式依据：xPaw/SteamAppInfo 的 appinfo 格式说明、ValvePython/vdf 的类型定义。
自行实现，只读取识别所需的安装和启动配置。未知格式一律拒绝。
"""
import hashlib
import re
import struct


def text_vdf(text):
    tokens = []
    pattern = re.compile(r'\s+|//[^\n]*|"((?:\\.|[^"\\])*)"|([{}])')
    position = 0
    while position < len(text):
        match = pattern.match(text, position)
        if not match:
            raise ValueError("安装清单含有不支持的内容")
        position = match.end()
        if match.group(1) is not None:
            value = re.sub(r'\\([\\"])', lambda m: m[1], match.group(1))
            tokens.append(("string", value))
        elif match.group(2):
            tokens.append((match[2], match[2]))
        if len(tokens) > 100000:
            raise ValueError("安装清单过大")
    offset = 0
    def parse(depth=0):
        nonlocal offset
        if depth > 32:
            raise ValueError("安装清单嵌套过深")
        out = {}
        while offset < len(tokens):
            kind, key = tokens[offset]
            offset += 1
            if kind == "}":
                if not depth:
                    raise ValueError("安装清单多余结束符")
                return out
            if kind != "string" or offset >= len(tokens) or key.casefold() in out:
                raise ValueError("安装清单键无效或重复")
            kind, value = tokens[offset]
            offset += 1
            if kind == "{":
                value = parse(depth + 1)
            elif kind != "string":
                raise ValueError("安装清单值无效")
            out[key.casefold()] = value
        if depth:
            raise ValueError("安装清单不完整")
        return out
    return parse()


class Reader:
    def __init__(self, data, strings=None):
        self.data, self.pos, self.strings = data, 0, strings
        self.nodes = 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError("Steam 缓存不完整")
        out = self.data[self.pos:self.pos+n]
        self.pos += n
        return out

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def string(self):
        end = self.data.find(b"\0", self.pos, min(len(self.data), self.pos + 65536))
        if end < 0:
            raise ValueError("Steam 缓存字符串不完整")
        out = self.data[self.pos:end].decode("utf-8", errors="strict")
        self.pos = end + 1
        return out

    def kv(self, depth=0):
        if depth > 48:
            raise ValueError("Steam 缓存嵌套过深")
        result = {}
        while True:
            kind = self.take(1)[0]
            if kind == 8:
                return result
            self.nodes += 1
            if self.nodes > 200000:
                raise ValueError("Steam 缓存节点过多")
            if self.strings is None:
                key = self.string()
            else:
                index = self.u32()
                if index >= len(self.strings):
                    raise ValueError("Steam 缓存字符串索引无效")
                key = self.strings[index]
            key = key.casefold()
            if key in result:
                raise ValueError("Steam 缓存重复键")
            if kind == 0:
                value = self.kv(depth + 1)
            elif kind == 1:
                value = self.string()
            elif kind in (2, 4, 6):
                value = struct.unpack("<i", self.take(4))[0]
            elif kind == 3:
                value = struct.unpack("<f", self.take(4))[0]
            elif kind in (7, 10):
                value = struct.unpack("<Q" if kind == 7 else "<q", self.take(8))[0]
            else:
                raise ValueError(f"Steam 缓存未知字段类型 {kind}")
            result[key] = value


def appinfo(data, wanted):
    """支持 v39/40/41；按 AppID 跳过无关记录，不导出其他应用资料。"""
    reader = Reader(data)
    magic, universe = reader.u32(), reader.u32()
    if magic not in (0x07564427, 0x07564428, 0x07564429) or universe != 1:
        raise ValueError("Steam appinfo 缓存版本或区域不受支持")
    strings, boundary = None, len(data)
    if magic == 0x07564429:
        boundary = struct.unpack("<Q", reader.take(8))[0]
        if not 16 <= boundary <= len(data) - 4:
            raise ValueError("Steam 字符串表位置无效")
        table = Reader(data[boundary:])
        count = table.u32()
        if count > 500000:
            raise ValueError("Steam 字符串表过大")
        strings = [table.string() for _ in range(count)]
    found = {}
    while reader.pos < boundary:
        appid = reader.u32()
        if not appid:
            return found
        size = reader.u32()
        end = reader.pos + size
        fixed = 60 if magic >= 0x07564428 else 40
        if size < fixed or end > boundary or size > 16 * 1024 * 1024:
            raise ValueError("Steam 应用记录长度无效")
        if appid in wanted:
            if appid in found:
                raise ValueError("Steam 缓存重复应用标识")
            header = reader.take(fixed)
            payload = reader.take(size - fixed)
            if magic >= 0x07564428 and hashlib.sha1(payload).digest() != header[40:60]:
                raise ValueError("Steam 缓存应用校验失败")
            values = Reader(payload, strings)
            parsed = values.kv()
            if values.pos != len(payload):
                raise ValueError("Steam 应用记录有多余数据")
            found[appid] = parsed.get("appinfo", parsed)
        reader.pos = end
    raise ValueError("Steam 缓存缺少结束标记")
