# Releasing Meshtastic Monitor

## One-time setup (PyPI)

Choose **one** method:

### A) Trusted Publishing (recommended, no secrets)

1. Create the project on PyPI (or test publish once).
2. In PyPI, add a **Trusted Publisher** for this GitHub repo:
   - Owner: `boryzo`
   - Repo: `meshtastic-monitor`
   - Workflow: `publish.yml`
   - Environment: (leave blank unless you use one)

### B) API token (requires GitHub secret)

1. Create a PyPI API token for the project.
2. Add a GitHub Actions secret:
   - Name: `PYPI_API_TOKEN`
   - Value: your PyPI token

## Release steps

### Option A: GitHub workflow (no manual tag)

1. Open Actions → **Release (bump + tag)**.
2. Optional: provide a version (e.g. `0.5.1`).
   - If left empty, it bumps `0.1.X` → `0.1.(X+1)`.
3. Run the workflow.

This creates a commit, tags `vX.Y.Z`, creates a GitHub Release, and triggers `Publish`.

### Option B: Manual tag

1. Update version in `setup.cfg`.
2. Commit the version bump.
3. Tag and push:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

The `Publish` workflow will:

- build + `twine check`
- upload to PyPI (token or Trusted Publishing)
- create a GitHub Release automatically (with attached `dist/*`)
