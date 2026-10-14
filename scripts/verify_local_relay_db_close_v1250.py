#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
from pathlib import Path

TARGETS = ("local_relay_playlist", "local_relay_segment")
MARKER = "STREAMFORGE_LOCAL_RELAY_DB_CLOSE_V1250"


def _is_access_call(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        return False
    value = stmt.value if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else None
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


def verify(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    funcs = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in TARGETS
    }

    for name in TARGETS:
        fn = funcs.get(name)
        if fn is None:
            errors.append(f"{name}: function missing")
            continue
        access_index = next((i for i, stmt in enumerate(fn.body) if _is_access_call(stmt)), None)
        if access_index is None:
            errors.append(f"{name}: get_local_relay_access(db) assignment missing")
            continue
        if access_index + 1 >= len(fn.body) or not _is_db_close(fn.body[access_index + 1]):
            errors.append(f"{name}: db.close() must immediately follow relay authorization")

    if text.count(MARKER) < 2:
        errors.append(f"marker {MARKER} must appear for both relay handlers")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} PATH_TO_MAIN_PY", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2
    errors = verify(path)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: V1250 local relay DB-close guard verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
