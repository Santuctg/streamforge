#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
from pathlib import Path

MARKER = "STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250"
TARGETS = {
    "local_relay_playlist": [
        "    # STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250:",
        "    # Relay authorization is complete. The remaining local relay path uses only",
        "    # already-loaded scalar/relationship state plus filesystem/proc access, so",
        "    # close the request DB session immediately and guarantee pool checkout release.",
    ],
    "local_relay_segment": [
        "    # STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250:",
        "    # Do not keep or reacquire a SQLite connection while serving HLS media.",
    ],
}


def _is_access_call(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        return False
    value = stmt.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "get_local_relay_access"
        and any(isinstance(arg, ast.Name) and arg.id == "db" for arg in value.args)
    )


def _is_db_close(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Attribute)
        and stmt.value.func.attr == "close"
        and isinstance(stmt.value.func.value, ast.Name)
        and stmt.value.func.value.id == "db"
        and not stmt.value.args
        and not stmt.value.keywords
    )


def apply(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)

    funcs = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in TARGETS
    }
    missing = [name for name in TARGETS if name not in funcs]
    if missing:
        raise RuntimeError("missing relay handler(s): " + ", ".join(missing))

    edits: list[tuple[int, list[str]]] = []
    for name, comments in TARGETS.items():
        fn = funcs[name]
        access_index = next((i for i, stmt in enumerate(fn.body) if _is_access_call(stmt)), None)
        if access_index is None:
            raise RuntimeError(f"{name}: get_local_relay_access(db) assignment missing")
        access_stmt = fn.body[access_index]
        function_text = "".join(lines[fn.lineno - 1:fn.end_lineno])
        has_marker = MARKER in function_text
        next_stmt = fn.body[access_index + 1] if access_index + 1 < len(fn.body) else None
        has_close = bool(next_stmt is not None and _is_db_close(next_stmt))

        if has_close and has_marker:
            continue

        insert_at = int(access_stmt.end_lineno or access_stmt.lineno)
        block = [line + "\n" for line in comments]
        if not has_close:
            block.append("    db.close()\n")
        edits.append((insert_at, block))

    if not edits:
        return False

    for insert_at, block in sorted(edits, key=lambda item: item[0], reverse=True):
        lines[insert_at:insert_at] = block

    updated = "".join(lines)
    ast.parse(updated)
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} PATH_TO_MAIN_PY", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2
    try:
        changed = apply(path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("CHANGED" if changed else "ALREADY_FIXED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
