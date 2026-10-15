#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
main = (ROOT / "app/main.py").read_text(encoding="utf-8")
base = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
nodes = (ROOT / "app/templates/nodes.html").read_text(encoding="utf-8")
bulk_path = ROOT / "app/templates/node_updates.html"
version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

errors = []

def require(cond, message):
    if not cond:
        errors.append(message)

require(version == "12.57", "Main VERSION must be 12.57")
require("STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257" in main, "Main marker missing")
require('href="/nodes/updates"' in base and 'Node Updates' in base, "sidebar Node Updates entry missing")
require("can('nodes.update')" in base, "Node Updates navigation must retain nodes.update permission")
require('/nodes/{{ node.id }}/update' not in nodes, "Nodes cards must not expose per-node Update link")
require('@app.get("/nodes/updates"' in main, "bulk update page route missing")
require('@app.post("/nodes/updates/bulk"' in main, "bulk update POST route missing")
require('node_ids: list[int] = Form' in main or 'node_ids: list[str] = Form' in main, "bulk route must accept repeated node_ids")
require(re.search(r"for\s+node_id\s+in\s+.*node_ids", main) is not None, "bulk route must iterate all selected node ids")
require("_start_node_update_job" in main, "bulk route must reuse existing per-node update engine")
require("ThreadPoolExecutor(max_workers=2" not in main[main.find('STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257'):main.find('STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257') + 12000], "bulk updates must not introduce a 2-node concurrency cap")
require(bulk_path.exists(), "node_updates.html missing")
if bulk_path.exists():
    bulk = bulk_path.read_text(encoding="utf-8")
    require('name="node_ids"' in bulk, "bulk page checkboxes missing")
    require('Update Selected' in bulk, "Update Selected action missing")
    require('data-select-all' in bulk, "Select All control missing")
    require('data-update-row' in bulk, "per-node live update rows missing")
    require('/update/status' in bulk, "per-node status polling endpoint missing")
    require('Advanced' in bulk, "advanced individual update link missing")

if errors:
    raise SystemExit("FAIL: " + "; ".join(errors))
print("PASS: v12.57 Node Bulk Update Center contract verified")
