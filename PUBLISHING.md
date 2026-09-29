# Publishing the Zelinqa SDKs

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
- Keep all four version files at the release version. Bump them by hand; do not run
  `changeset version` merely to consume old changesets.

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

Verify installation in a clean environment with `uv pip install --python <venv-python> zelinqa==<version>`
and `import zelinqa`. Never reuse a published version.

## npm: configure the existing package before publishing

`@zelinqa/sdk` already exists. An earlier interactive publication does not
authorize GitHub Actions to publish subsequent releases. The package needs its
own trusted publisher on npm; approving the GitHub `npm` environment is a
separate gate and does not create this connection.

A package owner must complete these steps on npmjs.com:

1. Open [@zelinqa/sdk](https://www.npmjs.com/package/@zelinqa/sdk), then
   **Settings**. Complete the security-key check if prompted.
2. Under **Trusted Publisher**, choose **GitHub Actions**.
3. Enter these exact, case-sensitive values:

   | Field | Value |
   | --- | --- |
   | Organization or user | `Zelinqa` |
   | Repository | `zelinqa-sdk` |
   | Workflow filename | `publish-typescript-sdk.yml` |
   | Environment name | `npm` |

   The workflow field takes only the filename, not `.github/workflows/` or
   the workflow's display name. The organization is the GitHub owner, not
   the lowercase npm scope.
4. Under **Allowed actions**, enable **Allow npm publish**. This workflow
   calls `npm publish`, not `npm stage publish`.
5. Select **Set up new trusted publisher connection** and verify that the
   saved connection shows the values above. If a different connection exists,
   have the owner replace the incorrect connection; do not relax the environment
   restriction or disable two-factor authentication.
6. Keep the GitHub `npm` environment restricted to `main` with maintainer approval.

No npm token is required in repository secrets, workflow variables, or `.npmrc`.
[Official npm instructions](https://docs.npmjs.com/trusted-publishers/).

### Workflow prerequisites and retry

The publish job uses a GitHub-hosted runner, Node 24, `contents: read`, and
`id-token: write`. Trusted publishing requires Node 22.14.0 or later and npm
11.5.1 or later; verify the versions printed by the setup step. Do not add an
unnecessary `npm@latest` upgrade when the bundled CLI already meets this minimum.

The build job creates the tarball without publishing permissions. The approved
publish job downloads that artifact and uses an explicit local path
(`./package-artifacts/*.tgz`). Do not replace this with a source-directory publish
or introduce a token-based fallback. The `repository.url` in `package.json` must
continue to identify `Zelinqa/zelinqa-sdk` for provenance.

Once the npm connection is saved, the release changes are merged, and the gates
pass, dispatch a **new run on main**, then approve its `npm` environment:

```bash
gh workflow run publish-typescript-sdk.yml --repo Zelinqa/zelinqa-sdk --ref main -f confirm=publish-zelinqa-sdk
```

Check provenance and clean Node imports in both ESM and CommonJS.

### Troubleshooting an authentication failure

An `E404` on the registry `PUT` can mean that the workflow is not authorized,
even when the package exists and packing succeeded. First inspect the package's
trusted publisher and its allowed action, then compare all four fields above.
Do not delete or recreate the package to resolve an authorization error.

`actions/setup-node` creates a temporary `NPM_CONFIG_USERCONFIG` when given a
registry URL. Its reference to `NODE_AUTH_TOKEN` is not proof that a real token
is configured. Do not print npmrc contents or credential environment variables
while diagnosing failures. Check the workflow for token injection or registry
overrides instead. `npm whoami` and a publish dry run do **not** validate OIDC
authorization: the real exchange occurs during publication.

## Release order

1. SDK Python and TypeScript, then verify actual registry installs.
2. MCP dependency switch to the published `zelinqa` SDK, lock and test again.
3. MCP publication, clean `uvx zelinqa-mcp --version` and protocol checks.
4. Public documentation/changelog announcement only after the releases exist.

The repository name is `zelinqa-sdk`. The REST API contract keeps its existing identifiers.
