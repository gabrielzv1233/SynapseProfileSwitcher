from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_MAGIC_V41 = 0x07564429
_FIXED_ENTRY_HEADER = 60

_TYPE_OBJECT = 0x00
_TYPE_STRING = 0x01
_TYPE_INT32 = 0x02
_TYPE_FLOAT32 = 0x03
_TYPE_POINTER = 0x04
_TYPE_WSTRING = 0x05
_TYPE_COLOR = 0x06
_TYPE_UINT64 = 0x07
_TYPE_END = 0x08
_TYPE_INT64 = 0x0A


@dataclass(frozen=True, slots=True)
class SteamAppMetadata:
    app_id: int
    app_type: str | None
    launch_executables: tuple[str, ...]


def _read_cstring(data: bytes, offset: int, limit: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset, limit)
    if end < 0:
        raise ValueError("unterminated Steam VDF string")
    return data[offset:end].decode("utf-8", errors="replace"), end + 1


def _read_wstring(data: bytes, offset: int, limit: int) -> tuple[str, int]:
    end = offset
    while end + 1 < limit:
        if data[end : end + 2] == b"\x00\x00":
            return data[offset:end].decode("utf-16-le", errors="replace"), end + 2
        end += 2
    raise ValueError("unterminated Steam VDF wide string")


def _string_table(data: bytes, offset: int) -> list[str]:
    if offset + 4 > len(data):
        raise ValueError("invalid Steam string table offset")
    count = struct.unpack_from("<I", data, offset)[0]
    position = offset + 4
    strings: list[str] = []
    for _ in range(count):
        value, position = _read_cstring(data, position, len(data))
        strings.append(value)
    return strings


def _read_object(
    data: bytes,
    offset: int,
    limit: int,
    strings: list[str],
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    position = offset

    while position < limit:
        value_type = data[position]
        position += 1
        if value_type == _TYPE_END:
            return result, position
        if position + 4 > limit:
            raise ValueError("truncated Steam VDF key")

        key_index = struct.unpack_from("<I", data, position)[0]
        position += 4
        if key_index >= len(strings):
            raise ValueError("invalid Steam VDF string-table index")
        key = strings[key_index]

        if value_type == _TYPE_OBJECT:
            value, position = _read_object(data, position, limit, strings)
        elif value_type == _TYPE_STRING:
            value, position = _read_cstring(data, position, limit)
        elif value_type == _TYPE_INT32:
            if position + 4 > limit:
                raise ValueError("truncated Steam VDF int32")
            value = struct.unpack_from("<i", data, position)[0]
            position += 4
        elif value_type == _TYPE_FLOAT32:
            if position + 4 > limit:
                raise ValueError("truncated Steam VDF float32")
            value = struct.unpack_from("<f", data, position)[0]
            position += 4
        elif value_type in (_TYPE_POINTER, _TYPE_COLOR):
            if position + 4 > limit:
                raise ValueError("truncated Steam VDF uint32")
            value = struct.unpack_from("<I", data, position)[0]
            position += 4
        elif value_type == _TYPE_UINT64:
            if position + 8 > limit:
                raise ValueError("truncated Steam VDF uint64")
            value = struct.unpack_from("<Q", data, position)[0]
            position += 8
        elif value_type == _TYPE_INT64:
            if position + 8 > limit:
                raise ValueError("truncated Steam VDF int64")
            value = struct.unpack_from("<q", data, position)[0]
            position += 8
        elif value_type == _TYPE_WSTRING:
            value, position = _read_wstring(data, position, limit)
        else:
            raise ValueError(f"unsupported Steam VDF value type 0x{value_type:02x}")

        result[key] = value

    return result, position


def _launch_executables(appinfo: dict[str, Any]) -> tuple[str, ...]:
    config = appinfo.get("config")
    if not isinstance(config, dict):
        return ()
    launch = config.get("launch")
    if not isinstance(launch, dict):
        return ()

    def sort_key(item: tuple[str, Any]) -> tuple[int, str]:
        key = item[0]
        return (int(key), "") if key.isdigit() else (2**31 - 1, key.casefold())

    result: list[str] = []
    seen: set[str] = set()
    for _, entry in sorted(launch.items(), key=sort_key):
        if not isinstance(entry, dict):
            continue

        entry_config = entry.get("config")
        if isinstance(entry_config, dict):
            os_list = str(entry_config.get("oslist", "")).casefold()
            if os_list and "windows" not in {part.strip() for part in os_list.split(",")}:
                continue

        executable = entry.get("executable")
        if not isinstance(executable, str) or not executable.strip():
            continue
        executable = executable.strip().strip('"')
        folded = executable.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(executable)

    return tuple(result)


def read_app_metadata(path: str | Path, app_ids: set[int]) -> dict[int, SteamAppMetadata]:
    """Read installed Steam app type and Windows launch executable hints.

    Steam's current appinfo cache is binary VDF v41. Unknown/newer formats are
    intentionally rejected so the caller can safely fall back to directory
    heuristics instead of misclassifying an entry.
    """
    if not app_ids:
        return {}

    data = Path(path).read_bytes()
    if len(data) < 16:
        raise ValueError("Steam appinfo.vdf is too small")
    if struct.unpack_from("<I", data, 0)[0] != _MAGIC_V41:
        raise ValueError("unsupported Steam appinfo.vdf format")

    table_offset = struct.unpack_from("<Q", data, 8)[0]
    if table_offset < 16 or table_offset >= len(data):
        raise ValueError("invalid Steam appinfo string-table offset")
    strings = _string_table(data, table_offset)

    result: dict[int, SteamAppMetadata] = {}
    position = 16
    while position + 4 <= table_offset:
        app_id = struct.unpack_from("<I", data, position)[0]
        position += 4
        if app_id == 0:
            break
        if position + 4 > table_offset:
            break

        record_size = struct.unpack_from("<I", data, position)[0]
        position += 4
        record_end = position + record_size
        if record_size < _FIXED_ENTRY_HEADER or record_end > table_offset:
            raise ValueError("invalid Steam appinfo entry size")

        if app_id not in app_ids:
            position = record_end
            continue

        vdf_start = position + _FIXED_ENTRY_HEADER
        try:
            raw, _ = _read_object(data, vdf_start, record_end, strings)
        except ValueError:
            position = record_end
            continue

        appinfo = raw.get("appinfo")
        if not isinstance(appinfo, dict):
            appinfo = raw
        common = appinfo.get("common") if isinstance(appinfo, dict) else None
        app_type: str | None = None
        if isinstance(common, dict):
            raw_type = common.get("type")
            if isinstance(raw_type, str) and raw_type.strip():
                app_type = raw_type.strip().casefold()

        result[app_id] = SteamAppMetadata(
            app_id=app_id,
            app_type=app_type,
            launch_executables=_launch_executables(appinfo if isinstance(appinfo, dict) else {}),
        )
        position = record_end

    return result
