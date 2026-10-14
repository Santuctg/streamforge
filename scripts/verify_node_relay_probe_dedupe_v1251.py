#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
from pathlib import Path

MARKER = "STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251"


def _funcs(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _slice(lines: list[str], node: ast.AST) -> str:
    lineno = int(getattr(node, "lineno", 1))
    end_lineno = int(getattr(node, "end_lineno", lineno))
    return "".join(lines[lineno - 1:end_lineno])


def verify(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    lines = text.splitlines(keepends=True)
    funcs = _funcs(tree)
    recover = funcs.get("_recover")
    stall = funcs.get("watch_stall")
    runtime_cls = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Runtime"), None)

    if runtime_cls is None:
        errors.append("Runtime class missing")
    else:
        runtime_text = _slice(lines, runtime_cls)
        if "self.relay_health_probe_in_flight = False" not in runtime_text:
            errors.append("Runtime probe-in-flight state missing")

    if recover is None:
        errors.append("_recover function missing")
    else:
        recover_text = _slice(lines, recover)
        required = (
            "current_timer = threading.current_thread()",
            "if rt.restart_timer is not current_timer:",
            "rt.relay_health_probe_in_flight = True",
            "rt.relay_health_probe_in_flight = False",
            "rt.restart_timer = timer",
        )
        for needle in required:
            if needle not in recover_text:
                errors.append(f"_recover missing: {needle}")
        probe_pos = recover_text.find("self.relay_playlist_health(relay_probe_url)")
        clear_pos = recover_text.find("rt.restart_timer = None")
        if probe_pos < 0:
            errors.append("_recover relay health probe missing")
        elif 0 <= clear_pos < probe_pos:
            errors.append("_recover releases restart_timer ownership before relay probe")

    if stall is None:
        errors.append("watch_stall function missing")
    else:
        stall_text = _slice(lines, stall)
        if "if rt.relay_health_probe_in_flight:" not in stall_text:
            errors.append("watch_stall does not dedupe an in-flight relay health probe")
        if "rt.relay_health_probe_in_flight = True" not in stall_text:
            errors.append("watch_stall does not reserve relay health probe ownership")
        if "rt.relay_health_probe_in_flight = False" not in stall_text:
            errors.append("watch_stall does not release relay health probe ownership")

    if text.count(MARKER) < 2:
        errors.append(f"marker {MARKER} must appear in runtime/recovery guard")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} PATH_TO_NODE_APP_PY", file=sys.stderr)
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
    print("PASS: V1251 Node relay health-probe single-owner guard verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
