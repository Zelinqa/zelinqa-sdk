import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";

type Step = {
  name?: string;
  uses?: string;
  run?: string;
  with?: Record<string, string | boolean>;
};

type Workflow = {
  on: { workflow_dispatch: { inputs: { confirm: { required: boolean } } } };
  permissions: Record<string, string>;
  jobs: Record<
    "build-and-test" | "publish",
    {
      if?: string;
      needs?: string;
      "runs-on": string;
      environment?: string;
      permissions?: Record<string, string>;
      steps: Step[];
    }
  >;
};

const filename = "publish-typescript-sdk.yml";
const source = readFileSync(
  new URL(`../../.github/workflows/${filename}`, import.meta.url),
  "utf8",
);
const workflow = parse(source) as Workflow;
const guide = readFileSync(new URL("../../RELEASING.md", import.meta.url), "utf8");
const metadata = JSON.parse(readFileSync(new URL("../../package.json", import.meta.url), "utf8"));

describe("npm trusted publishing configuration", () => {
  it("documents the same identity as the package and publish job", () => {
    const repository = new URL(metadata.repository.url.replace(/^git\+/, ""));
    const [owner, name] = repository.pathname
      .slice(1)
      .replace(/\.git$/, "")
      .split("/");
    expect(repository.hostname).toBe("github.com");
    expect(metadata.name).toBe("@zelinqa/sdk");
    expect(guide).toContain(`| Organization or user | \`${owner}\` |`);
    expect(guide).toContain(`| Repository | \`${name}\` |`);
    expect(guide).toContain(`| Workflow filename | \`${filename}\` |`);
    expect(guide).toContain(`| Environment name | \`${workflow.jobs.publish.environment}\` |`);
    expect(guide).toContain("**Allow npm publish**");
    expect(guide).toContain(
      `gh workflow run ${filename} --repo ${owner}/${name} --ref main -f confirm=publish-zelinqa-sdk`,
    );
  });

  it("keeps the release manual, main-only, and protected by the npm environment", () => {
    expect(workflow.on.workflow_dispatch.inputs.confirm.required).toBe(true);
    expect(workflow.jobs["build-and-test"].if).toBe(
      "github.ref == 'refs/heads/main' && inputs.confirm == 'publish-zelinqa-sdk'",
    );
    expect(workflow.jobs.publish.needs).toBe("build-and-test");
    expect(workflow.jobs.publish.environment).toBe("npm");
    expect(workflow.jobs.publish["runs-on"]).toBe("ubuntu-latest");
  });

  it("grants OIDC only to the publish job without long-lived token injection", () => {
    expect(workflow.permissions).toEqual({ contents: "read" });
    expect(workflow.jobs["build-and-test"].permissions).toBeUndefined();
    expect(workflow.jobs.publish.permissions).toEqual({ contents: "read", "id-token": "write" });
    expect(source).not.toMatch(/secrets\.|NODE_AUTH_TOKEN|NPM_TOKEN/);
    expect(
      workflow.jobs.publish.steps.some((step) => step.uses?.startsWith("actions/checkout@")),
    ).toBe(false);
  });

  it("publishes the tested artifact using an explicit local path and the public registry", () => {
    const steps = workflow.jobs.publish.steps;
    const setup = steps.find((step) => step.uses?.startsWith("actions/setup-node@"));
    expect(setup?.with).toMatchObject({
      "node-version": "24",
      "registry-url": "https://registry.npmjs.org",
      "package-manager-cache": false,
    });
    const download = steps.find((step) => step.uses?.startsWith("actions/download-artifact@"));
    const upload = workflow.jobs["build-and-test"].steps.find((step) =>
      step.uses?.startsWith("actions/upload-artifact@"),
    );
    expect(download?.with?.name).toBe(upload?.with?.name);
    expect(download?.with?.path).toBe("package-artifacts/");
    const publish = steps.find((step) => step.name === "Publish with npm Trusted Publishing");
    expect(publish?.run).toBe("npm publish ./package-artifacts/*.tgz --access public");
  });
});
