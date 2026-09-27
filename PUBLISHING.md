# Publishing Zelinqa 1.0.0

Merge and registry publication require maintainer approval.

Release targets: Python `zelinqa` and TypeScript `@zelinqa/sdk`.
Publish only to these registry projects; do not overwrite other distributions.

## Gates

- Reviewed release commit merged by a maintainer; functional CI green on that commit.
- Check the README text and packaged links against the intended registry state.
- Python: `uv sync --locked; uv run pytest; uv run mypy python/src; uv build`.
- TypeScript: `pnpm install --frozen-lockfile; pnpm check; pnpm build; pnpm pack`.
- Clean wheel install and ESM/CommonJS tarball imports tested.
- Real staging runtime + configuration + scopes suites passed with temporary
  keys. Mock tests do not prove server persistence. Verify feedback against storage.
- Check package availability and ownership again immediately before publication.
- No credentials in reports, history, files or stdout.
- Keep all four version files at 1.0.0; do not bump merely to consume old changesets.

## PyPI: new project

Configure a **pending publisher** for `zelinqa` if no project exists:
owner `Zelinqa`, repository `zelinqa-sdk`, workflow
`publish-python-sdk.yml`, environment `pypi`.
A pending publisher does not reserve a name. Do not publish a placeholder.
[Official PyPI instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

Protect the GitHub `pypi` environment with maintainer approval and main-only refs.
After approval, a maintainer runs from this repository:

```bash
gh workflow run publish-python-sdk.yml --ref main -f confirm=publish-zelinqa
```

Verify installation in a clean environment with `uv pip install --python <venv-python> zelinqa==1.0.0`
and `import zelinqa`. Never reuse a published version.

## npm: new package

`@zelinqa/sdk` is NOT the existing `@zelinqa/nbq` package.
Verify organization ownership and configure its own publisher; the old package's
publisher does not transfer. If npm requires a first authenticated publication,
A maintainer must perform that setup interactively with 2FA, using the tested tarball
`zelinqa-sdk-1.0.0.tgz`, before enabling OIDC for subsequent releases.
Do not introduce a permanent token into CI.

Publisher: owner `Zelinqa`, repository `zelinqa-sdk`, workflow
`publish-typescript-sdk.yml`, environment `npm`.
[Official npm instructions](https://docs.npmjs.com/trusted-publishers/).

Once the package publisher is configured and the release gates pass:

```bash
gh workflow run publish-typescript-sdk.yml --ref main -f confirm=publish-zelinqa-sdk
```

Check provenance and clean Node imports in both ESM and CommonJS.

## Release order

1. SDK Python and TypeScript, then verify actual registry installs.
2. MCP dependency switch to the published `zelinqa` SDK, lock and test again.
3. MCP publication, clean `uvx zelinqa-mcp --version` and protocol checks.
4. Public documentation/changelog announcement only after the releases exist.

The repository name is `zelinqa-sdk`. The REST API contract keeps its existing identifiers.
