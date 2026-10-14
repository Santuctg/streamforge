#!/usr/bin/env python3
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
version = (root / "VERSION").read_text(encoding="utf-8").strip()
compact = version.replace(".", "")
updater = root / "scripts" / f"force_update_v{compact}.sh"

errors = []
cleanup_markers = [
    "find \"$STAGE/scripts\" -maxdepth 1 -type f -name 'force_update_v*.sh'",
    "! -name 'force_update_v1236.sh' -delete",
]


def has_full_cleanup(text: str) -> bool:
    return all(marker in text for marker in cleanup_markers)


if not updater.is_file():
    errors.append(f"missing current updater: {updater.name}")
else:
    text = updater.read_text(encoding="utf-8")

    # STREAMFORGE_RELEASE_SOURCE_NO_PRESTAGE_BYTECODE_V1252:
    # The release source is copied into one or more nested staging trees before
    # the inherited package-hygiene guard runs. Running py_compile against the
    # source tree before that copy creates __pycache__ and makes a clean GitHub
    # ZIP fail its own cache/metadata validation.
    stage_copy_pos = text.find('cp -a "$SOURCE_DIR/." "$STAGE/"')
    source_pycompile = re.search(r'python3\s+-m\s+py_compile\s+"?\$SOURCE_DIR/', text)
    if source_pycompile and (stage_copy_pos < 0 or source_pycompile.start() < stage_copy_pos):
        errors.append(
            f"{updater.name} runs py_compile in SOURCE_DIR before staging and can create __pycache__"
        )

    # STREAMFORGE_RELEASE_BOUNDED_MAIN_HOTFIX_V1253:
    # A deliberately Main-only hotfix does not stage the whole release tree, so
    # stale force-updater cleanup is not applicable. It must explicitly declare
    # that scope, avoid a full SOURCE_DIR staging copy, preserve the Node version,
    # and use non-writing syntax validation.
    bounded_main_hotfix = "STREAMFORGE_MAIN_ONLY_HOTFIX_V1253" in text
    if bounded_main_hotfix:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} declares Main-only scope but copies the full release tree")
        if "STREAMFORGE_V1253_NO_SOURCE_BYTECODE" not in text:
            errors.append(f"{updater.name} Main-only scope lacks no-bytecode verification marker")
        if 'NODE_TARGET_VERSION="12.52"' not in text:
            errors.append(f"{updater.name} Main-only scope does not pin the unchanged Node Agent version")
        if "Remote Node Agent is unchanged" not in text:
            errors.append(f"{updater.name} Main-only scope does not explicitly preserve Remote Node Agent")
    elif not has_full_cleanup(text):
        # STREAMFORGE_RELEASE_STAGING_DELEGATED_CLEANUP_V1251:
        # A thin current-version updater may delegate the full deployment to a
        # proven force-update bridge. In that case the staged bridge itself must
        # own the wildcard cleanup before the inherited full updater runs.
        bridge_match = re.search(
            r'BASE_WRAPPER="\$SOURCE_DIR/scripts/(force_update_v\d+\.sh)"',
            text,
        )
        if not bridge_match:
            for marker in cleanup_markers:
                if marker not in text:
                    errors.append(f"{updater.name} missing staging cleanup marker: {marker}")
        else:
            bridge_name = bridge_match.group(1)
            bridge = root / "scripts" / bridge_name
            bridge_text = bridge.read_text(encoding="utf-8") if bridge.is_file() else ""
            if not bridge_text:
                errors.append(f"{updater.name} delegates staging cleanup to missing {bridge_name}")
            elif not has_full_cleanup(bridge_text):
                errors.append(f"delegated updater {bridge_name} lacks wildcard staging cleanup")
            if f'bash "$STAGE/scripts/{bridge_name}" "$@"' not in text:
                errors.append(f"{updater.name} does not execute staged delegated updater {bridge_name}")

    if re.search(r'rm -f \"\$STAGE/scripts/force_update_v\d+\.sh\"', text):
        errors.append(f"{updater.name} uses single-file cleanup and can leave stale force updaters")

if errors:
    raise SystemExit("\n".join(f"FAIL: {e}" for e in errors))
print("Force-updater staging regression guard: PASS")
