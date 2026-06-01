# Publishing cloud-vfs to PyPI

## One-time setup

1. Create a project on [PyPI](https://pypi.org/) named `cloud-vfs` (or claim the name if available).
2. Configure **trusted publishing** (recommended) on PyPI — open [cloud-vfs publishing settings](https://pypi.org/manage/project/cloud-vfs/settings/publishing/) and add a **GitHub** publisher with **exactly**:
   - **Owner**: `sahasrarjn`
   - **Repository name**: `cloud-vfs`
   - **Workflow name**: `publish.yml` (filename only, not the full path)
   - **Environment name**: `pypi` (must match the workflow `environment: pypi`)
3. In GitHub repo **Settings → Environments**, create environment `pypi` (no secrets needed for trusted publishing).

Alternative: store a PyPI API token as the repo secret `PYPI_API_TOKEN`, add `password: ${{ secrets.PYPI_API_TOKEN }}` to the publish step, and remove `id-token: write` (API tokens disable OIDC trusted publishing).

### Troubleshooting `invalid-publisher`

If the publish workflow fails with:

```text
invalid-publisher: valid token, but no corresponding publisher
```

PyPI received a valid GitHub OIDC token but has **no trusted publisher** matching the workflow claims. Fix step 2 above — the owner, repository, workflow filename, and environment name must match exactly.

After adding the publisher on PyPI, re-run the failed workflow from **Actions → Publish to PyPI → Re-run jobs**, or trigger **Run workflow** manually (`workflow_dispatch`).

## Release checklist

1. Bump version in **both**:
   - `cloud_vfs/__init__.py` (`__version__`)
   - `pyproject.toml` (`[project].version`)
2. Update `CHANGELOG.md`.
3. Commit, tag, and push:

```bash
git tag v0.5.0
git push origin v0.5.0
```

4. On GitHub: **Releases → Draft a new release** → choose tag `v0.5.0` → **Publish release**.

The `publish.yml` workflow runs on `release: published` and uploads the wheel + sdist.

## Local test build

```bash
pip install build
python -m build
twine check dist/*
```

Install locally:

```bash
pip install dist/cloud_vfs-*.whl
cloud-vfs doctor
```

## First manual upload (optional)

If you need to publish before trusted publishing is wired:

```bash
python -m build
twine upload dist/*
```

Use a PyPI API token with `twine` or `uv publish`.
