# Releasing Fence

The package is `gitfence` on PyPI. The import package and the main command are `fence`.
Releases are published by `.github/workflows/publish.yml` through PyPI Trusted
Publishing. No API token is involved.

## One-time setup

1. On PyPI, add a trusted publisher for `gitfence`: owner `aryanpol73`, repository `fence`,
   workflow `publish.yml`, environment `pypi`. Before the first upload, this is a
   "pending publisher". It does **not** reserve the name: anyone can register the
   project first, so publish soon after adding it. (Done for `gitfence` at 0.1.0.)
2. On GitHub, create the environment `pypi` (Settings > Environments). Adding a required
   reviewer is recommended, so every publish waits for a human.

## Each release, in this order

1. **Merge.** The release commit is on `main`, with `__version__` in `fence/__init__.py`
   and the `CHANGELOG.md` heading updated.
2. **CI green.** Every `ci` job passes on that exact `main` commit (tests on Windows,
   macOS and Linux, plus the package jobs).
3. **Tag.** Tag that commit and push only the tag. The tag must equal `v` plus
   `__version__`, or the build job fails.
   ```bash
   git tag -a v0.1.0 -m "Fence 0.1.0" <commit>
   git push origin v0.1.0
   ```
4. **Publish.** The tag starts `publish`: build, `twine check --strict`, then upload from
   the `pypi` environment (approve it if it has a reviewer).
5. **Verify.** In a fresh virtual environment:
   ```bash
   pip install gitfence==0.1.0
   fence version
   gitfence version
   ```
   Check https://pypi.org/project/gitfence/ shows the README and the right version.

A published version can be yanked but never replaced. Fix forward with a new version.
