# Releasing

Releases are published from `main` by a maintainer, through the GitHub Actions
workflows and trusted publishing (OIDC). No registry token is stored in this
repository.

1. Open a release pull request: bump the version in every package manifest
   (the CI verifies that they match), date the entry in `CHANGELOG.md`, update
   the READMEs if the public API changed.
2. Merge it once CI is green on the pull request and on `main`.
3. Python: run the `Publish Python SDK to PyPI` workflow on `main` with the
   confirmation `publish-zelinqa`, then approve the `pypi` environment.
4. TypeScript: run the `Publish TypeScript SDK to npm` workflow on `main` with
   the confirmation `publish-zelinqa-sdk`, then approve the `npm` environment.
5. Verify a clean install of both packages at the new version, in ESM and
   CommonJS for TypeScript.

A published version number is never reused. Security fixes follow
[SECURITY.md](SECURITY.md).
