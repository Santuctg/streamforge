#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app/main.py"
BASE = ROOT / "app/templates/base.html"
NODES = ROOT / "app/templates/nodes.html"
MARKER = "STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257"


def patch_main() -> None:
    text = MAIN.read_text(encoding="utf-8")
    if MARKER in text:
        return
    anchor = '@app.get("/nodes/{node_id}/update", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.update"))])\n'
    if anchor not in text:
        raise SystemExit("v12.57 main route anchor not found")
    block = r'''# STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257:
# Node updates are managed from one dedicated page. A bulk submission starts
# every selected Node immediately by reusing the established isolated per-Node
# update worker; there is deliberately no global concurrency cap or batch queue.
@app.get("/nodes/updates", response_class=HTMLResponse, dependencies=[Depends(permission_required("nodes.update"))])
def node_updates_page(request: Request, message: str = "", error: str = "", db: Session = Depends(get_db)):
    if not current_admin(request, db):
        return redirect_login()
    remote_nodes = list(db.scalars(
        select(Node).where(Node.node_type == "remote").order_by(Node.name, Node.id)
    ).all())
    with _NODE_UPDATE_LOCK:
        update_jobs = {int(node.id): dict(_NODE_UPDATE_JOBS.get(int(node.id)) or {}) for node in remote_nodes}
    return render(
        request,
        "node_updates.html",
        db,
        nodes=remote_nodes,
        update_jobs=update_jobs,
        target_version=APP_VERSION,
        message=message,
        error=error,
    )


@app.post("/nodes/updates/bulk", dependencies=[Depends(permission_required("nodes.update"))])
def node_updates_bulk(
    request: Request,
    node_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    if not current_admin(request, db):
        return redirect_login()
    selected: list[int] = []
    seen: set[int] = set()
    for node_id in node_ids:
        value = int(node_id)
        if value > 0 and value not in seen:
            seen.add(value)
            selected.append(value)
    if not selected:
        return RedirectResponse("/nodes/updates?error=" + quote_plus("Select at least one remote Node."), status_code=303)

    rows = list(db.scalars(
        select(Node).where(Node.node_type == "remote", Node.id.in_(selected))
    ).all())
    by_id = {int(node.id): node for node in rows}
    panel_url = main_panel_public_base(db, request)
    actor = current_admin(request, db)
    started = 0
    already_running = 0
    missing = 0
    for node_id in selected:
        node = by_id.get(int(node_id))
        if node is None:
            missing += 1
            continue
        if _start_node_update_job(node.id, method="api", panel_url=panel_url):
            started += 1
            log_event(
                "Node bulk API update queued",
                scope="node",
                node_id=node.id,
                actor=actor.username if actor else None,
                details={"target_version": APP_VERSION, "bulk": True},
            )
        else:
            already_running += 1

    parts = [f"Started {started} Node update(s) in parallel"]
    if already_running:
        parts.append(f"{already_running} already running")
    if missing:
        parts.append(f"{missing} unavailable")
    return RedirectResponse("/nodes/updates?message=" + quote_plus(" · ".join(parts)), status_code=303)


'''
    MAIN.write_text(text.replace(anchor, block + anchor, 1), encoding="utf-8")


def patch_base() -> None:
    text = BASE.read_text(encoding="utf-8")
    if 'href="/nodes/updates"' in text:
        return
    text = text.replace(
        "request.url.path.startswith('/nodes') %}active{% endif %}",
        "request.url.path.startswith('/nodes') and not request.url.path.startswith('/nodes/updates') %}active{% endif %}",
        1,
    )
    anchor = '<span class="nav-label">Nodes</span></a>{% endif %}'
    if anchor not in text:
        raise SystemExit("v12.57 Nodes navigation anchor not found")
    link = anchor + '\n      {% if can(\'nodes.update\') %}<a href="/nodes/updates" class="{% if request.url.path.startswith(\'/nodes/updates\') %}active{% endif %}"><span class="nav-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10m0 0 4-4m-4 4-4-4"/><path d="M5 17h14v3H5z"/></svg></span><span class="nav-label">Node Updates</span></a>{% endif %}'
    BASE.write_text(text.replace(anchor, link, 1), encoding="utf-8")


def patch_nodes() -> None:
    text = NODES.read_text(encoding="utf-8")
    old = '{% if can(\'nodes.update\') and node.node_type == \'remote\' %}<a class="button small restart-button" href="/nodes/{{ node.id }}/update">Update</a>{% endif %}\n        '
    if old in text:
        text = text.replace(old, "", 1)
    if '/nodes/{{ node.id }}/update' in text:
        raise SystemExit("v12.57 per-node Update link still present on Nodes page")
    NODES.write_text(text, encoding="utf-8")


patch_main()
patch_base()
patch_nodes()
print("STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257: applied")
