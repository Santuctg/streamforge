from __future__ import annotations

import json
import re
from typing import Any, Iterable

UNCATEGORIZED_KEY = "uncategorized"


def channel_category_key(channel: Any) -> str:
    category = getattr(channel, "category", None)
    category_id = getattr(category, "id", None) if category is not None else None
    return f"category:{int(category_id)}" if category_id is not None else UNCATEGORIZED_KEY


def channel_category_name(channel: Any) -> str:
    category = getattr(channel, "category", None)
    name = str(getattr(category, "name", "") or "").strip() if category is not None else ""
    return name or "Uncategorized"


def _default_channel_sort(channel: Any, category_position: dict[str, int] | None = None) -> tuple[int, int, int, str, int]:
    category_position = category_position or {}
    try:
        sort_order = int(getattr(channel, "sort_order", 100000) or 100000)
    except (TypeError, ValueError):
        sort_order = 100000
    return (
        category_position.get(channel_category_key(channel), 10**9),
        sort_order,
        _default_category_sort(channel)[1],
        str(getattr(channel, "name", "") or "").lower(),
        int(getattr(channel, "id", 0) or 0),
    )


def _default_category_sort(channel: Any) -> tuple[int, int, str]:
    category = getattr(channel, "category", None)
    if category is None:
        return (1, 10**9, "uncategorized")
    try:
        sort_order = int(getattr(category, "sort_order", 100000) or 100000)
    except (TypeError, ValueError):
        sort_order = 100000
    return (0, sort_order, channel_category_name(channel).lower())


def parse_channel_order(raw: str | None, allowed_ids: set[int], default_ids: list[int] | None = None) -> list[int]:
    values: list[int] = []
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = [part for part in re.split(r"[,\s]+", str(raw)) if part]
        if isinstance(parsed, list):
            for item in parsed:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value in allowed_ids and value not in values:
                    values.append(value)
    if default_ids is None:
        values.extend(sorted(allowed_ids - set(values)))
    else:
        remainder = [int(item) for item in default_ids if int(item) in allowed_ids and int(item) not in values]
        used = set(values) | set(remainder)
        remainder.extend(sorted(allowed_ids - used))
        values.extend(remainder)
    return values


def default_category_order(channels: Iterable[Any]) -> list[str]:
    representative: dict[str, Any] = {}
    for channel in channels:
        representative.setdefault(channel_category_key(channel), channel)
    return [
        key
        for key, _channel in sorted(
            representative.items(),
            key=lambda item: (_default_category_sort(item[1]), item[0]),
        )
    ]


def parse_category_order(raw: str | None, channels: Iterable[Any]) -> list[str]:
    channel_list = list(channels)
    defaults = default_category_order(channel_list)
    allowed = set(defaults)
    values: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = [part for part in re.split(r"[,\s]+", str(raw)) if part]
        if isinstance(parsed, list):
            for item in parsed:
                value = str(item or "").strip().lower()
                if value in allowed and value not in values:
                    values.append(value)
    values.extend(key for key in defaults if key not in values)
    return values


def category_position_map(channels: Iterable[Any], raw_category_order: str | None) -> dict[str, int]:
    channel_list = list(channels)
    return {key: index for index, key in enumerate(parse_category_order(raw_category_order, channel_list))}


def ordered_channels(
    channels: Iterable[Any],
    raw_channel_order: str | None,
    raw_category_order: str | None = None,
) -> list[Any]:
    channel_list = list(channels)
    by_id = {int(channel.id): channel for channel in channel_list}
    category_position = category_position_map(channel_list, raw_category_order)
    default_ids = [
        int(channel.id)
        for channel in sorted(by_id.values(), key=lambda channel: _default_channel_sort(channel, category_position))
    ]
    order = parse_channel_order(raw_channel_order, set(by_id), default_ids)
    channel_position = {channel_id: index for index, channel_id in enumerate(order)}
    return sorted(
        by_id.values(),
        key=lambda channel: (
            category_position.get(channel_category_key(channel), 10**9),
            channel_position.get(int(channel.id), 10**9),
            _default_channel_sort(channel, category_position)[1],
            str(getattr(channel, "name", "") or "").lower(),
            int(channel.id),
        ),
    )
