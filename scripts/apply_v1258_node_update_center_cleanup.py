#!/usr/bin/env python3
from pathlib import Path
import re
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
main_path = ROOT / "app/main.py"
version_path = ROOT / "VERSION"
node_version_path = ROOT / "node_agent/VERSION"

main = main_path.read_text(encoding="utf-8")
marker = "STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258"

if marker in main:
    version_path.write_text("12.58\n", encoding="utf-8")
    node_version_path.write_text("12.58\n", encoding="utf-8")
    print("ALREADY_FIXED")
    raise SystemExit(0)

if "STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257" not in main:
    raise SystemExit("v12.57 Node Bulk Update Center marker missing")

main = main.replace(
    "# STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257:",
    "# STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258:\n"
    "# Keep normal, bulk, live status and SSH recovery controls inside one Node\n"
    "# Updates center. Legacy single-node GETs redirect here, while the existing\n"
    "# POST/update worker contracts remain compatible.\n"
    "# STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257:",
    1,
)

updates_pattern = re.compile(
    r'@app\.get\("/nodes/updates", response_class=HTMLResponse, dependencies=\[Depends\(permission_required\("nodes\.update"\)\)\]\)\n'
    r'def node_updates_page\(.*?\n(?=@app\.post\("/nodes/updates/bulk")',
    re.S,
)
updates_replacement = '''@app.get("/nodes/updates", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.update"))])
def node_updates_page(
    request: Request,
    node: int = 0,
    message: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    remote_nodes = list(db.scalars(
        select(Node).where(Node.node_type == "remote").order_by(Node.name, Node.id)
    ).all())
    with _NODE_UPDATE_LOCK:
        update_jobs = {int(item.id): dict(_NODE_UPDATE_JOBS.get(int(item.id)) or {}) for item in remote_nodes}
    return render(
        request,
        "node_updates.html",
        db,
        nodes=remote_nodes,
        update_jobs=update_jobs,
        target_version=APP_VERSION,
        open_node_id=max(0, int(node or 0)),
        message=message,
        error=error,
    )


'''
main, count = updates_pattern.subn(updates_replacement, main, count=1)
if count != 1:
    raise SystemExit(f"could not replace node_updates_page: {count}")

legacy_pattern = re.compile(
    r'@app\.get\("/nodes/\{node_id\}/update", response_class=HTMLResponse, dependencies=\[Depends\(permission_required\("nodes\.update"\)\)\]\)\n'
    r'def node_update_page\(.*?\n(?=def node_update_redirect\()',
    re.S,
)
legacy_replacement = '''@app.get("/nodes/{node_id}/update", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.update"))])
def node_update_page(node_id: int, request: Request, error: str = "", message: str = "", db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if not node or node.node_type == "local":
        raise HTTPException(404)
    values: dict[str, str] = {"node": str(int(node.id))}
    if error:
        values["error"] = error
    if message:
        values["message"] = message
    return RedirectResponse(
        f"/nodes/updates?{urlencode(values)}#node-update-{int(node.id)}",
        status_code=303,
    )


'''
main, count = legacy_pattern.subn(legacy_replacement, main, count=1)
if count != 1:
    raise SystemExit(f"could not replace legacy node_update_page: {count}")

redirect_pattern = re.compile(
    r'def node_update_redirect\(node_id: int, \*, error: str = "", message: str = ""\) -> RedirectResponse:\n.*?\n(?=def _set_node_update_job\()',
    re.S,
)
redirect_replacement = '''def node_update_redirect(node_id: int, *, error: str = "", message: str = "") -> RedirectResponse:
    values: dict[str, str] = {"node": str(int(node_id))}
    if error:
        values["error"] = error
    if message:
        values["message"] = message
    return RedirectResponse(
        f"/nodes/updates?{urlencode(values)}#node-update-{int(node_id)}",
        status_code=303,
    )


'''
main, count = redirect_pattern.subn(redirect_replacement, main, count=1)
if count != 1:
    raise SystemExit(f"could not replace node_update_redirect: {count}")

status_return = '    return JSONResponse(job, headers={"Cache-Control": "no-store"})'
if main.count(status_return) != 1:
    raise SystemExit(f"unexpected Node update status return count: {main.count(status_return)}")
main = main.replace(
    status_return,
    '    job["current_version"] = str(node.agent_version or "unknown")\n' + status_return,
    1,
)

main_path.write_text(main, encoding="utf-8")
version_path.write_text("12.58\n", encoding="utf-8")
node_version_path.write_text("12.58\n", encoding="utf-8")
print("FIXED")
