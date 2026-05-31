# Publishing cloud-vfs to PyPI

## One-time setup

1. Create a project on [PyPI](https://pypi.org/) named `cloud-vfs` (or claim the name if available).
2. Configure **trusted publishing** (recommended) on PyPI:
   - Owner: your GitHub user/org
   - Repository: `sahasrarjn/cloud-vfs`
   - Workflow: `.github/workflows/publish.yml`
   - Environment name: `pypi` (matches the workflow `environment: pypi`)
3. In GitHub repo **Settings → Environments**, create environment `pypi` (no secrets needed for trusted publishing).

Alternative: store `PYPI_API_TOKEN` as a repo secret and remove `id-token: write` / trusted publishing (not configured in the default workflow).

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

## Until `pip install cloud-vfs` works on PyPI

As of the last publish prep check, **the project is not on PyPI** (`https://pypi.org/pypi/cloud-vfs/` returns 404). Use:

```bash
pip install "git+https://github.com/sahasrarjn/cloud-vfs.git"
```

(or a release tag: `pip install "git+https://github.com/sahasrarjn/cloud-vfs.git@v0.5.0"`).

**Before the first GitHub release publish:** align `pyproject.toml` `version` and `cloud_vfs/__init__.py` `__version__` (they must match). Create the GitHub **environment** named `pypi` (repo currently has no environments). On PyPI, register the project and configure trusted publishing for `sahasrarjn/cloud-vfs` / `.github/workflows/publish.yml` / environment `pypi`.

**First release after setup:** tag the aligned version (e.g. `v0.5.5`), push the tag, then **Releases → Publish release** on that tag. The workflow has never run (`publish.yml` has no completed runs until a release is published).

Local artifacts were verified with `python -m build` and `twine check dist/*` (passes for wheel + sdist).
