# 📦 Publishing releases

How a StreamForge release is built, tested and published — for maintainers.
Read [Installation & upgrades](INSTALLATION.md) and
[Architecture & security](ARCHITECTURE.md) first.

---

## ⚙️ How publishing works

`.github/workflows/publish-release.yml` runs on every push to `main` that
touches `VERSION`, `CHANGELOG.md`, `release-notes/**` or the workflow itself.
It reads `VERSION`, creates the tag `v<VERSION>` and a GitHub Release whose
body is `release-notes/v<VERSION>.md`, and uploads
`streamforge-<VERSION>.zip` built with `git archive` from that tag. If the
release already exists it only re-uploads the ZIP.

| Change touches | Result on merge to `main` |
| --- | --- |
| `VERSION`, `CHANGELOG.md`, `release-notes/**`, `publish-release.yml` | Tag, GitHub Release and ZIP are published |
| Anything else (code, docs, workflows) | Nothing is published |

- **Merging a PR that bumps `VERSION` publishes a release.** Merge only when
  the release is ready.
- Documentation-only or code-only changes that do not touch those paths do
  not publish anything.

---

## 🧱 Installed sources are patched in place

Existing servers are never re-imaged from the repository. Each updater
changes a bounded set of installed files, most often by applying a patcher
(`scripts/apply_*.py`) to a **staged copy of the installed** `app/main.py`.
Therefore:

1. Any change to `app/main.py` must also be shipped as an idempotent patcher
   (today: `scripts/apply_main_hotfixes.py`; add a new numbered fix there),
   and the repository `app/main.py` must equal "previous release `main.py` +
   patcher" byte for byte. Fresh installs use the repository file; existing
   servers get the patcher; both must end up identical.
2. Patchers locate exact anchor text and refuse (non-zero exit) when an
   anchor is missing or ambiguous; they skip fixes whose marker is already
   present.
3. Templates and other files that are copied wholesale are listed explicitly
   in the updater and copied atomically.

---

## ✅ Checklist for a new release `X.YY`

1. **Code change** with its patcher fix (see above) and a focused test.
2. **Versions**: set `VERSION` and `node_agent/VERSION` to `X.YY`; Main and
   the bundled Node Agent versions stay aligned.
3. **Updater** `scripts/force_update_vXYY.sh`, modelled on the previous one:
   - a unique marker comment (`STREAMFORGE_VXYY_..._UPDATER`);
   - require the package version and accept installed `<previous>|X.YY`;
   - verify the Node runtime payload is byte-identical (except `VERSION`)
     unless the release changes the Node Agent;
   - stage and patch, verify markers and Python syntax, back up to
     `/var/backups/streamforge/vXYY-<timestamp>`, install atomically, set
     both `VERSION` files, restart only the services the change needs
     (normally `streamforge` and `streamforge-public`), check they are
     active, and roll back on any failure;
   - honour `STREAMFORGE_ROOT`, `STREAMFORGE_BACKUP_ROOT`,
     `STREAMFORGE_HOTFIX_TEST_MODE=1` and a
     `STREAMFORGE_VXYY_TEST_FAIL_AFTER_WRITE=1` hook for tests.
4. Point `scripts/update.sh` at the new updater.
5. **Updater test** `scripts/test_vXYY_updater.py`: upgrade from the previous
   release, idempotent reinstall, controlled rollback, version gate refusal,
   Node parity.
6. **Staging guard**: add a `vXYY` branch with the updater's required markers
   to `scripts/verify_release_updater_staging.py` (otherwise it falls through
   to the full-tree cleanup checks and fails).
7. **Carry historical checks forward**:
   - add `X.YY` to the version allow-lists in
     `.github/workflows/v1257-node-bulk-update-center.yml` and
     `.github/workflows/v1262-password-navigation.yml`;
   - in the previous release's workflow, run its updater test, `update.sh`
     check and version check only when `VERSION` equals that release;
   - move `scripts/verify_panel_credential_vault_v1263.py` to `X.YY` and add a
     contract check for the new change.
8. **Workflow** `.github/workflows/vXYY-*.yml` running the new tests, the
   staging guard, `bash -n` on the updater, `git diff --check`, and the
   version/notes checks. Install `httpx` when a test uses Starlette's
   `TestClient` (it is not in `requirements.txt`).
9. **Release notes** `release-notes/vX.YY.md`: what changed, what is
   untouched, required installed version, and the exact line
   `streamforge-update --force /root/streamforge-X.YY.zip` (checked by CI).
10. Open a PR, wait for **all** checks, then merge; confirm the
    *Publish StreamForge Release* run succeeded, download the ZIP, and run the
    new updater test from inside the extracted ZIP.

---

## 🧪 Running checks locally

```bash
python3 -m pip install -r requirements.txt httpx
python3 scripts/test_vXYY_updater.py
python3 scripts/verify_release_updater_staging.py
python3 scripts/verify_panel_credential_vault_v1263.py
bash -n scripts/force_update_vXYY.sh scripts/update.sh
git diff --check origin/main...HEAD
```

Older `scripts/test_v12*_updater.py` scripts are tied to their own release
version and fail on newer `VERSION` values; CI only runs them on their own
version.

> ⚠️ Some workflows contain steps guarded by
> `if: github.event_name == 'push' && github.ref == ...` that commit and push
> (for example in `v1253-main-waiting-retry.yml`). Never run those steps
> locally.
