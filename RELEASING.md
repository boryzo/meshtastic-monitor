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

1. Update version in `pyproject.toml`.
2. Commit the version bump.
3. Tag and push:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

The `Publish` workflow will build and upload to PyPI automatically.
