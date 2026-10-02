# The manifest: pipemesh.yaml

Condensed from pipemesh.io/docs/pipeline-yaml and /docs/release-flow.
When something here and the live docs disagree, the docs win.

Contents: file layout · registration · triggers & inputs · bodies ·
job keys · ordering · paths & skip · delegate · matrix · components ·
other repositories · renaming jobs · editor validation

## File layout

`pipemesh.yaml` at the repository root is the single entry point. One
root key is Pipemesh's: `pipemesh:`, which registers workloads. Every
other root key is yours (a body, a job, a map of helpers, an included
file) and must be **reached** from `pipemesh:` through `!ref` —
directly or via other definitions. An unreached key is a load error
(that is how typos are caught).

```yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-manifest.schema.json
build_body: !include .pipemesh/build.yaml        # a file brought in whole

pipeline:                                         # your definition: a body
  stages: [build, staging]
  jobs:
    build: { stage: build, delegate: { type: workflow, params: { body: !ref build_body } } }
    deploy_staging: { stage: staging, consumes: [build/compile/dist], paths: [deploy], script: ./deploy/run.sh staging }

pipemesh:
  pipelines:
    pipeline: !ref pipeline
  workflows:
    checks: { body: !ref build_body, on: pull_request }
```

- `!include <path>`: a `.yaml`/`.yml` file, path from the repository
  root, read at the same commit; used whole (no merging). Included files
  may include others; they may not contain `pipemesh:`.
- `!ref <a.b.c>`: the value at that key path, from the root **of the
  file the tag is written in** (never across files). A whole value only
  — never part of a string, never merged with sibling keys. A `!ref`
  list item whose value is a list splices its items in.
- YAML anchors, aliases and merge keys (`&x`, `*x`, `<<:`) are load
  errors. `!ref` is the one way to reuse a value.
- Larger repos: keep `pipemesh.yaml` short and put bodies in
  `.pipemesh/<name>.yaml` (and components in `.pipemesh/components/`).

## Registration

```yaml
pipemesh:
  pipelines:
    pipeline: !ref pipeline            # a body (!ref / !include / inline) or { body: … }
  workflows:
    checks:  { body: !include .pipemesh/ci.yaml, on: pull_request }
    release: { body: !include .pipemesh/release.yaml, on: tag, tags: ["v*"] }
    smoke:   { body: !include .pipemesh/smoke.yaml }       # no trigger = manual-only
```

- **One pipeline per repository**, named `pipeline` (its URL is
  `/github.com/<org>/<repo>/-/pipeline`). It takes no triggers: every
  commit on the default branch is a revision.
- Any number of workflows. Names match `[a-z0-9][a-z0-9_-]*`, unique
  across both sections; `settings`, `jobs`, `runs`, `events`,
  `workflows`, `pipelines`, `repository`, `default` are reserved, and a
  workflow may not be called `pipeline`.
- A declared workload is live from the moment it is named: URL,
  history, run counter.

## Triggers & inputs (workflows only)

| `on:` | filter | meaning |
| --- | --- | --- |
| `push` | — | a new head on the **default branch** (runs beside the pipeline's revision) |
| `pull_request` | `targets: [main]` (exact branch names) | each PR head, and merge-queue groups |
| `tag` | `tags: ["v*"]` (`*` and `?` globs) | a pushed tag |
| `schedule` | `cron: "0 3 * * *"` | UTC; always quote the cron |
| `manual` | — | explicit manual trigger (no `on:` at all is also manual-only) |

Pushes to other branches never start runs, and a `branches:` filter
is an exact match that can silently never fire — leave it out. A tag
trigger fires for tags pushed after it is registered (existing tags are
history). `on:` (one trigger) and `triggers:` (a named map of several)
are mutually exclusive. Several triggers on one entry share one history:

```yaml
release:
  body: !include .pipemesh/release.yaml
  inputs: { channel: { default: stable, description: "Release channel" } }
  triggers:
    stable:  { on: tag, tags: ["v*"] }
    nightly: { on: schedule, cron: "0 3 * * *", inputs: { channel: nightly } }
```

`inputs:` declares names and defaults; triggers supply values; an
undeclared input is a load error; an input without a default needs a
value on every trigger. Values arrive in the run as variables.

## Bodies

A body is `stages:` (a list, required), `jobs:` (a map, required),
optional `variables:` (body-wide), and for pipelines optional `repos:`.
Nothing else. The position types it: the same body can back a workflow
entry, a `delegate: { type: workflow }`, or the pipeline.

## Job keys

| key | what it does |
| --- | --- |
| `stage` | **required**; must be one of the body's `stages`. Places the job on the board — does **not** order execution |
| `script` | shell, as a block scalar (`|`) or a list of lines; runs in `bash` |
| `before_script` / `after_script` | joined with `script` into one shell script under `set -e`: `after_script` does **not** run when `script` fails |
| `image` | a **public** container image with bash, git, curl and tar (private images: build them with `publish:` + `image_from`) |
| `image_from` | run in an image another job built: `<job>/<oci key>` (exclusive with `image`) |
| `services` | loads but is **ignored** by the runners — start services in the script (patterns.md §12) |
| `variables` | job variables (strings) |
| `secrets` | names of repository secrets the job receives (it gets no others) |
| `config` | names of repository config variables (`UPPER_SNAKE_CASE`) the job receives |
| `needs` | jobs to wait for (and inherit artifacts from) |
| `consumes` | entries to receive: `<job>/<key>`; also an ordering edge |
| `produces` | entries this job outputs (files, images, packages) |
| `publish` | build + push an image from a directory; `key:` makes it an `oci` entry |
| `cache` | `{ key, restore_keys, paths, policy }` — see artifacts-and-caching.md |
| `paths` | repository files the job reads (fingerprint input) |
| `skip` | `unchanged` \| `built` \| `never` — when it may skip |
| `tags` | runner queue (first tag); untagged = hosted runners |
| `matrix` | load-time expansion |
| `uses` / `with` | instantiate a component |
| `setup` | list of `{ uses, with }` script fragments prepended to `script` |
| `delegate` | hand the work to a workflow, a child pipeline or GitHub Actions |
| `timeout_seconds` | integer; default 3600 on Pipemesh runners — raise it for long builds and deploys |
| `retry` | re-attempts a failed job in a workflow run |
| `allow_failure` | `true`: a failure doesn't block what follows (the job publishes nothing) |
| `repo`, `repos` | work in / mount another declared repository |
| `was` | take over a renamed job's history |

Unknown keys are load errors (closed schema). Job names are lowercase
letters, digits, `_` and `-`. Keys from other CI systems that **do not
exist**: `when`, `only`, `except`, `rules`, `extends`, `dependencies`,
`environment`, `if`, `runs-on`, `steps`, `env`, `strategy`,
`continue-on-error` — see the migration references for what replaces
each.

## Ordering

**A job waits only for what it names**: the jobs in `needs:` and the
producers of what it `consumes:`. A job that names nothing starts with
the revision, whatever its stage. When translating stage-ordered CI
(GitLab, Jenkins stages, CircleCI workflows), add the `needs:` edges
explicitly. Cycles are load errors.

Entries (`produces:`) reach only the jobs that `consumes:` them by
name — `needs:` orders but hands nothing over. A production deploy
that needs staging still consumes the build's output itself.

## paths & skip: when a job runs

A job's **fingerprint** hashes its definition, parameters, the digests
of what it consumes, the files under its `paths:`, and the versions of
its secrets/config. It runs when the fingerprint is new to it.

- `paths:` absent → the job reads the whole repository: every commit
  is new work. `paths: [src, package.json]` → only those (a path means
  itself and everything under it; globs cut finer). `paths: []` → no
  files; only what it consumes.
- `skip: unchanged` — **pipeline default**. Skips when the fingerprint
  equals its own last *successful* run's (shown as *no changes*). After
  a failure it runs regardless until it succeeds. Right for deploys and
  anything with side effects. **Load error in a workflow body.**
- `skip: built` — reuses outputs of any earlier successful run with the
  same fingerprint (shown as *reused*). Right for builds, images,
  hermetic tests. PRs reuse trusted (default-branch/tag/pipeline) runs
  and their own, never another PR's.
- `skip: never` — always runs. **Workflow default.** Right for smoke
  tests, notifications.

In a workflow, `paths:` only matters together with `skip: built`.

## delegate

```yaml
build:                                   # run a workflow body; the job waits for its verdict
  stage: build
  delegate: { type: workflow, params: { body: !ref builds, variables: { TARGET: web } } }

orders:                                  # dispatch the revision to a child pipeline (pipelines only)
  stage: dispatch
  delegate: { type: pipeline, params: { body: !ref service, variables: { SERVICE: orders } } }

deploy_staging:                          # dispatch a GitHub Actions workflow and wait
  stage: staging
  delegate:
    type: github_actions
    params: { workflow: deploy.yml, ref: main, inputs: { environment: staging }, artifacts: [dist] }
```

- `workflow` and `github_actions` start a run and wait; the job's status
  is the run's verdict and its outputs are the run's. `pipeline` hands
  the revision over and settles as *Dispatched*.
- `type: pipeline` inside a workflow body is a load error.
- A delegating job can't also have `script`, `uses`, `setup`, `image`,
  `image_from`, `tags`, `secrets`, `artifacts`, `cache` or `publish`.
  A `workflow`/`pipeline` delegating job produces nothing itself
  (consumers name `<job>/<inner job>/<key>`); a `github_actions` job may
  declare `produces:` for the run's artifacts.
- Inside a delegated body, `consumes: [../build/jar]` names the parent's
  `build` job's `jar` entry.
- The child lives under the parent: `…/repo/-/pipeline/<job>`.

## matrix

```yaml
test:
  stage: test
  matrix:
    stream: [aws, docker]
    arch: [amd64, arm64]            # 2×2 = 4 jobs: test[stream=aws,arch=amd64], …
  script: ./test.sh ${{ matrix.stream }} --arch ${{ matrix.arch }}
```

- `as: jobs` (default): sibling jobs, each with its own cursor — one
  broken lane doesn't block the others. `needs: [test]` = all variants;
  `test[stream=aws]` = a slice; `test[stream=aws, arch=arm64]` = one.
  Structured form: `{ job: test, matrix: { stream: aws } }`.
- `as: workflow`: one node over a generated child workflow, one verdict
  (the CI-matrix shape); reference only the node.
- `${{ matrix.x }}` interpolates at load; shell-safe names are also
  variables (`$stream`). Values may not contain `[ ] , =`. Cap: 256.
- Lane-to-lane: `needs: ["test[stream=${{ matrix.stream }}]"]`.

## Components

```yaml
deploy_production:
  stage: production
  uses: ./.pipemesh/components/eks-deploy.yaml      # or a registry component: aws/role@1
  with: { cluster: prod, replicas: 4 }
```

```yaml
# .pipemesh/components/eks-deploy.yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-component.schema.json
component: eks-deploy
params:
  cluster:  { type: string, required: true }
  replicas: { type: int, default: 2 }               # types: string, int, bool, list
job:
  image: ghcr.io/acme/deploy-tools@sha256:…   # helm + kubectl, and bash, git, curl, tar
  script: |
    helm upgrade app charts/app --kube-context ${{ params.cluster }} --set replicas=${{ params.replicas }}
```

- Placement keys (`stage`, `needs`, `paths`, `skip`, `tags`, timeouts,
  `consumes`, `produces`, `secrets`) stay on the job; execution keys
  (`script`, `setup`, `image`, `services`) belong to the component —
  declaring one beside `uses:` is a load error.
- No inheritance or overriding. To vary behaviour, add a param.
- `setup:` steps must be script-only components; they run before
  `script`.

Registry components (`uses: <stream>/<name>@<major>`, resolved from
`ghcr.io/pipemesh/components`, pinned by digest):

| component | params (required **bold**) | what it does |
| --- | --- | --- |
| `aws/role@1` | **arn**, **region**, audience | OIDC → assumes an AWS role; every later AWS call uses it (setup step) |
| `vercel/turborepo-token@1` | **team**, policy_id, audience | OIDC → Vercel → exports `TURBO_TOKEN` (setup step) |
| `turbo/remote-cache@1` | **team**, api, token_var | points turbo at a remote cache; read-only on PRs (setup step) |
| `turbo/fingerprint@1` | **image**, install, turbo, task, deployables, extra, out | one fingerprint per Turborepo package (job) |
| `nx/fingerprint@1` | **image**, install, with_target, targets, extra, out | one fingerprint per Nx project (job) |
| `bazel/fingerprint@1` | image, targets, extra, out, bazel | one fingerprint per deployable Bazel package (job) |
| `bazel/remote-cache@1` | url, header, secret_var, results_url, bes_backend | Bazel remote cache (BuildBuddy default); PRs never upload (setup step) |
| `docker/build-push@1` | **image**, tag, context, dockerfile, registry, username_var, password_var | docker login + build + push one tag (prefer `publish:`) |
| `node/pnpm@1` | version | corepack-provisioned pnpm (setup step) |
| `codecov/upload@1` | token_var | Codecov CLI upload (setup step) |
| `slack/message@1` | **text**, webhook_var | post to a Slack incoming webhook (setup step) |

## Other repositories

```yaml
pipeline:
  repos:
    infra: github.com/acme/infra           # followed on its default branch
  stages: [deploy]
  jobs:
    apply:
      stage: deploy
      repo: infra                           # checked out in place of the pipeline's repo
      paths: [envs/staging, modules]
      repos:
        $self: { paths: [config], mount: app }   # mount the pipeline's own repo at ./app
      script: cd envs/staging && terraform apply -auto-approve
```

Values under `repos:` are repository paths (strings). Each revision
pins a commit of every declared repository; a push to any of them
starts a revision. Declared repositories must be added to the
same Pipemesh organization. Secrets, config and commit statuses stay the
pipeline repository's.

## Renaming jobs

A pipeline job keeps history under its name. When a rename is part of
the change, keep history with `was:`:

```yaml
deploy_prod:
  was: deploy_production     # takes over its cursor and runs, once
```

When editing an existing manifest, prefer keeping job names.

## Editor validation

Add the modeline at the top of each file — manifest:
`# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-manifest.schema.json`;
`.pipemesh/*.yaml` bodies: `…/pipemesh-body.schema.json`; components:
`…/pipemesh-component.schema.json`. In VS Code settings:
`"yaml.customTags": ["!include scalar", "!ref scalar"]`.
