from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

_EXTINF_ATTRIBUTE_RE = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*(?:"([^"]*)"|([^\s,]+))')


@dataclass(slots=True)
class ImportItem:
    name: str
    input_url: str
    category: str | None = None
    program_id: int | None = None
    logo_url: str | None = None
    video_codec: str | None = None
    video_bitrate: str | None = None
    audio_codec: str | None = None
    audio_bitrate: str | None = None
    output_type: str | None = None
    output_url: str | None = None
    enabled: bool | None = None
    auto_restart: bool | None = None
    extra: dict[str, str] = field(default_factory=dict)


def _clean(value: object) -> str:
    return str(value or "").strip()


def _to_int(value: object) -> int | None:
    text = _clean(value)
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if number > 0 else None


def _to_bool(value: object) -> bool | None:
    text = _clean(value).lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on", "enabled", "enable"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "disable"}:
        return False
    return None


def _fallback_name(url: str, position: int) -> str:
    clean_url = url.split("|", 1)[0].strip()
    parsed = urlsplit(clean_url)
    candidate = unquote(PurePosixPath(parsed.path).name)
    for suffix in (".m3u8", ".m3u", ".ts", ".mpd"):
        if candidate.lower().endswith(suffix):
            candidate = candidate[: -len(suffix)]
            break
    candidate = candidate.replace("-", " ").replace("_", " ").strip()
    return candidate or f"Imported stream {position}"


def _split_extinf(line: str) -> tuple[str, str]:
    quoted = False
    escaped = False
    for index, char in enumerate(line):
        if char == "\\" and not escaped:
            escaped = True
            continue
        if char == '"' and not escaped:
            quoted = not quoted
        elif char == "," and not quoted:
            return line[:index], line[index + 1 :]
        escaped = False
    return line, ""


def parse_m3u(text: str) -> list[ImportItem]:
    items: list[ImportItem] = []
    pending_name = ""
    pending_attrs: dict[str, str] = {}
    pending_program: int | None = None

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            head, display_name = _split_extinf(line)
            pending_name = display_name.strip()
            pending_attrs = {
                match.group(1).lower(): (match.group(2) if match.group(2) is not None else match.group(3) or "").strip()
                for match in _EXTINF_ATTRIBUTE_RE.finditer(head)
            }
            if not pending_name:
                pending_name = pending_attrs.get("tvg-name", "")
            pending_program = _to_int(
                pending_attrs.get("program-id")
                or pending_attrs.get("program_id")
                or pending_attrs.get("service-id")
                or pending_attrs.get("service_id")
            )
            continue
        if line.lower().startswith("#extvlcopt:program="):
            pending_program = _to_int(line.split("=", 1)[1])
            continue
        if line.startswith("#"):
            continue

        position = len(items) + 1
        name = pending_name or pending_attrs.get("tvg-name") or _fallback_name(line, position)
        category = (
            pending_attrs.get("group-title")
            or pending_attrs.get("group_title")
            or pending_attrs.get("category")
            or None
        )
        items.append(
            ImportItem(
                name=name.strip() or _fallback_name(line, position),
                input_url=line,
                category=category.strip() if category else None,
                program_id=pending_program,
                logo_url=(pending_attrs.get("tvg-logo") or pending_attrs.get("logo") or None),
                extra=pending_attrs.copy(),
            )
        )
        pending_name = ""
        pending_attrs = {}
        pending_program = None
    return items


def _row_value(row: dict[str, str], *aliases: str) -> str:
    normalized = {str(key or "").strip().lower().replace("-", "_"): _clean(value) for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(alias.lower().replace("-", "_"), "")
        if value:
            return value
    return ""


def parse_csv(text: str) -> list[ImportItem]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return []

    items: list[ImportItem] = []
    for row_index, row in enumerate(reader, start=2):
        input_url = _row_value(row, "input_url", "url", "stream_url", "source", "input")
        if not input_url:
            continue
        name = _row_value(row, "name", "channel", "title", "tvg_name") or _fallback_name(input_url, row_index - 1)
        items.append(
            ImportItem(
                name=name,
                input_url=input_url,
                category=_row_value(row, "category", "group", "group_title") or None,
                program_id=_to_int(_row_value(row, "program_id", "service_id", "program")),
                logo_url=_row_value(row, "logo_url", "logo", "tvg_logo") or None,
                video_codec=_row_value(row, "video_codec", "vcodec") or None,
                video_bitrate=_row_value(row, "video_bitrate", "vbitrate") or None,
                audio_codec=_row_value(row, "audio_codec", "acodec") or None,
                audio_bitrate=_row_value(row, "audio_bitrate", "abitrate") or None,
                output_type=_row_value(row, "output_type", "output") or None,
                output_url=_row_value(row, "output_url", "destination") or None,
                enabled=_to_bool(_row_value(row, "enabled", "active")),
                auto_restart=_to_bool(_row_value(row, "auto_restart", "autorestart")),
                extra={str(key): _clean(value) for key, value in row.items() if key is not None},
            )
        )
    return items


def detect_and_parse(text: str, filename: str = "") -> tuple[str, list[ImportItem]]:
    content = text.lstrip("\ufeff \t\r\n")
    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        return "CSV", parse_csv(content)
    if lower_name.endswith((".m3u", ".m3u8")):
        return "M3U", parse_m3u(content)

    first_nonempty = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if first_nonempty.upper().startswith(("#EXTM3U", "#EXTINF")):
        return "M3U", parse_m3u(content)

    # CSV files must have a URL-like header. Otherwise treat the content as an
    # M3U/plain URL list, which is more forgiving for operator copy/paste use.
    first_row = first_nonempty.lower().replace("-", "_")
    header_tokens = {token.strip().strip('"\'') for token in re.split(r"[,;\t|]", first_row)}
    known_headers = {"name", "channel", "title", "url", "input", "input_url", "stream_url", "source", "category", "group_title"}
    if len(header_tokens & known_headers) >= 2 and header_tokens & {"url", "input", "input_url", "stream_url", "source"}:
        return "CSV", parse_csv(content)
    return "M3U", parse_m3u(content)
