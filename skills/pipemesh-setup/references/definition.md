# The definition: pipemesh.yaml

Condensed from pipemesh.io/docs/pipeline-yaml and /docs/release-flow.
When something here and the live docs disagree, the docs win.

Contents: file layout · types · registration · triggers & inputs ·
bodies · job keys · job types · checkout · skip · executors ·
job_type: workflow and job_type: pipeline · ordering · matrix ·
components · other repositories · renaming jobs · removed keys ·
editor validation

Every example here is block style, and so is every file you write:
one `- item` per line for a list, one `key: value` per line for a map,
never `[a, b]` or `{ a: b }` (an empty `[]` or `{}` is the one
exception). Inline YAML quoted in a sentence or a table below is
shorthand only.

## File layout

`pipemesh.yaml` at the repository root is the single entry point. One
root key is Pipemesh's: `pipemesh:`, which registers workloads. Every
other root key is yours (a body, a job, a map of helpers, an included
file) and must be **reached** from `pipemesh:` through `!ref` —
directly or via other definitions. An unreached key is a load error
(that is how typos are caught).

```yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-definition.schema.json
build_body: !include .pipemesh/build.yaml        # a file brought in whole (it says type: workflow)

pipeline:                                         # your definition: a body
  type: pipeline                                  # it stands alone, so it says its shape
  stages:
    - build
    - staging
  jobs:
    build:
      job_type: workflow                          # runs build_body as one node
      stage: build
      body: !ref build_body
    deploy_staging:
      job_type: deploy                            # checks out nothing by default…
      production: false
      stage: staging
      consumes:
        - build/compile/dist                      # …ships what the build produced
      checkout:
        - deploy                                  # …with the scripts it runs
      script: ./deploy/run.sh staging

pipemesh:
  pipelines:
    pipeline: !ref pipeline
  workflows:
    checks:
      body: !ref build_body
      on: pull_request
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
  Pipemesh reads these files itself; jobs don't need them checked out.

## Types: what shape a structure has

`type:` names a structure's shape: `job`, `workflow`, `pipeline` or
`component`. (What a *job* is — build, deploy, … — is its `job_type:`,
a different key; see Job types.)

- **Required where a structure stands alone**: a root definition in
  `pipemesh.yaml` that is a body or a job, any other `!ref` target used
  as one, every file reached with `!include` that is a body or a job,
  and every component. A standalone structure without `type:` is a load
  error naming its shape.
- **Optional where the position fixes the shape** — a job written
  inline under `jobs:`, a body written inline under `body:`, an inline
  registration entry — and checked when given.
- **Plain data has none**: a cache definition, a list of script lines,
  a map of helper values reached with `!ref` or `!include`.

It is checked where the structure is used:

| used as | must be |
| --- | --- |
| an entry of `jobs:` | `type: job` |
| `pipemesh.pipelines.<name>` (or its `body:`) | `type: pipeline` |
| `pipemesh.workflows.<name>` (or its `body:`) | `type: workflow` |
| `body:` of a `job_type: workflow` job | `type: workflow` |
| `body:` of a `job_type: pipeline` job | `type: pipeline` |
| `uses:` / a `setup:` step's `uses:` | `type: component` |

A mismatch is a load error naming both sides — "job `orders` is
`job_type: workflow`, so its body must be `type: workflow`;
`.pipemesh/service.yaml` is `type: pipeline` — to hand revisions to it,
write `job_type: pipeline`". One `type: workflow` body can back a
workflow registration and any number of `job_type: workflow` jobs; a
`type: pipeline` body backs the pipeline or `job_type: pipeline` jobs.
No body is both.

```yaml
# .pipemesh/lint.yaml — a job kept in its own file
type: job
job_type: build
stage: verify
checkout:
  - src
script: make lint
```

```yaml
# pipemesh.yaml — using it
lint: !include .pipemesh/lint.yaml
pipeline:
  type: pipeline
  stages:
    - verify
  jobs:
    lint: !ref lint
pipemesh:
  pipelines:
    pipeline: !ref pipeline
```

## Registration

```yaml
pipemesh:
  pipelines:
    pipeline: !ref pipeline            # a type: pipeline body (!ref, !include or inline), or body: under the entry
  workflows:
    checks:
      body: !include .pipemesh/ci.yaml # a type: workflow body
      on: pull_request
    release:
      body: !include .pipemesh/release.yaml
      on: tag
      tags:
        - "v*"
    smoke:
      body: !include .pipemesh/smoke.yaml   # no trigger = manual-only
```

`pipemesh:` holds `pipelines:` and `workflows:`; the section says what
a registration is, and the body's `type:` must agree with it. Triggers
(`on:`, `cron:`, `tags:`, `targets:`, `inputs:`) sit on the
registration entry, never in a body, so one body serves a registration
and a delegating job alike. `type:` belongs to the body, not to an
entry that has `body:`; an entry written inline (its own `stages:` and
`jobs:`) may carry it.

- **One pipeline per repository**, named `pipeline` (its URL is
  `/github.com/<org>/<repo>/-/pipeline`). It takes no triggers: every
  commit on the repository's branch — the one it was added with, by
  name — is a revision.
- Any number of workflows. Names match `[a-z0-9][a-z0-9_-]*`, unique
  across both sections; `settings`, `jobs`, `runs`, `events`,
  `workflows`, `pipelines`, `repository`, `default` are reserved, and a
  workflow may not be called `pipeline`.
- A declared workload is live from the moment it is named: URL,
  history, run counter.

## Triggers & inputs (workflows only)

| `on:` | filter | meaning |
| --- | --- | --- |
| `push` | — | a new head on the **repository's branch** (runs beside the pipeline's revision) |
| `pull_request` | `targets:` — a list of exact branch names (`main`) | each PR head, and merge-queue groups |
| `tag` | `tags:` — a list of `*`/`?` globs (`"v*"`) | a pushed tag |
| `schedule` | `cron: "0 3 * * *"` | UTC; always quote the cron |
| `manual` | — | explicit manual trigger (no `on:` at all is also manual-only) |

Pushes to other branches never start runs, so a `branches:` filter can
only repeat the repository's branch — any other name never fires. Leave
it out. A tag
trigger fires for tags pushed after it is registered (existing tags are
history). `on:` (one trigger) and `triggers:` (a named map of several)
are mutually exclusive. Several triggers on one entry share one history:

```yaml
release:
  body: !include .pipemesh/release.yaml
  inputs:
    channel:
      default: stable
      description: Release channel
  triggers:
    stable:
      on: tag
      tags:
        - "v*"
    nightly:
      on: schedule
      cron: "0 3 * * *"
      inputs:
        channel: nightly
```

`inputs:` declares names and defaults; triggers supply values; an
undeclared input is a load error; an input without a default needs a
value on every trigger. Values arrive in the run as variables.

## Bodies

A body is `type:` (`workflow` or `pipeline`; required when the body
stands alone), `stages:` (a list, required), `jobs:` (a map, required),
optional `variables:` (body-wide), and for pipelines optional `repos:`.
Nothing else — no `kind:`, no triggers. A `type: workflow` body can
back a workflow entry and `job_type: workflow` jobs at once; a
`type: pipeline` body backs the pipeline or `job_type: pipeline` jobs.

## Job keys

| key | what it does |
| --- | --- |
| `type` | the shape, `job`: required when the job stands alone (its own file, a root key reached with `!ref`, or a job a `!ref` picks out of another definition, like `!ref ci.jobs.lint`), optional inline under `jobs:` |
| `job_type` | what the job is: `build`, `deploy`, `task` (the default), `workflow`, `pipeline` — sets its `checkout:` and `skip:` defaults and where it may appear |
| `production` | **required on a `job_type: deploy`**, refused elsewhere: `true` when the deploy reaches production (what DORA, `pipemesh deployments` and `pipemesh deployed` count), `false` for staging, preview, canary. A matrix's variants share it; it changes nothing about how the job runs |
| `stage` | **required**; must be one of the body's `stages`. Places the job on the board — does **not** order execution |
| `checkout` | the repository files the job's work reads, and all its workspace holds: `true`, `false` or a list of paths. Default from the job type |
| `skip` | `unchanged` \| `built` \| `never` — when it may skip. Default from the job type |
| `script` | shell, as a block scalar (`|`) or a list of lines; runs in `bash` |
| `before_script` / `after_script` | joined with `script` into one shell script under `set -e`: `after_script` does **not** run when `script` fails |
| `uses` / `with` | run a component instead of a script |
| `github_actions` | run the job as a GitHub Actions workflow and wait for it: the file (`deploy.yml`), or a map of `workflow`, `ref`, `inputs`, `artifacts` |
| `setup` | a list of steps, each `uses:` (and `with:`): script-only components prepended to `script` |
| `body` / `workload` | `job_type: workflow` and `job_type: pipeline` only: the body to run (`!ref`, `!include`, inline; its `type:` matches the job type), or an existing workload's alias |
| `image` | a **public** container image with bash, git, curl and tar (private images: build them with `publish:` + `image_from`) |
| `image_from` | run in an image another job built: `<job>/<oci key>` (exclusive with `image`) |
| `services` | loads but is **ignored** by the runners — start services in the script (patterns.md §12) |
| `variables` | job variables (strings); on `job_type: workflow`/`pipeline`, the child's variables |
| `secrets` | names of repository secrets the job receives (it gets no others) |
| `config` | names of repository config variables (`UPPER_SNAKE_CASE`) the job receives |
| `needs` | jobs to wait for (and inherit legacy `artifacts:` from) |
| `consumes` | entries to receive: `<job>/<key>`; also an ordering edge and a fingerprint input |
| `produces` | entries this job outputs (files, images, packages) |
| `publish` | build + push an image from a directory; `key:` makes it an `oci` entry |
| `cache` | `key`, `restore_keys`, `paths`, `policy` — see artifacts-and-caching.md |
| `tags` | runner queue (first tag); untagged = hosted runners |
| `matrix` | load-time expansion |
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
each. Keys Pipemesh **removed**: `kind:` (now `job_type:`), `paths:`,
`delegate:`, `trigger:` (see the end of this file).

## Job types

Every job has a job type; write `job_type:` on every job. Absent
`job_type:` means `task`.

| `job_type:` | `checkout:` default | `skip:` default | allowed in | runs |
| --- | --- | --- | --- | --- |
| `build` | `true` | `built` | pipelines, workflows | an executor |
| `deploy` | `false` | `unchanged` | **pipelines** | an executor |
| `task` (default) | `false` | `never` | pipelines, workflows | an executor |
| `workflow` | its jobs' combined | `built` if all its jobs are builds, else `never` | pipelines, workflows | a `type: workflow` body, waited for |
| `pipeline` | `false` (declared; checks nothing out) | `unchanged` | **pipelines** | a child pipeline (a `type: pipeline` body), handed the revision |

- **`build`** reads the source and is hermetic, so an earlier run with
  the same fingerprint can stand in for it. Tests, lint, image builds
  and build-graph fingerprint jobs (`nx/fingerprint@1`, …) are builds;
  so is a job that signs or packages what it consumes, with
  `checkout: false`.
- **`deploy`** ships what it consumes to an environment and runs when
  that changed since its last success. Pipelines only: the last
  success it compares with, the revision each environment runs,
  rollback and holds exist only in a pipeline. A pull-request preview
  or a manual hotfix deploy is a `task` in a workflow. Every deploy
  says `production: true` or `production: false`.
- **`task`** is anything else; it runs on every revision that reaches
  it (smoke tests, notifications, checks that read pull-request context
  such as a merge-base diff).
- Every default can be overridden on the job (a `job_type: build` whose
  `checkout:` lists `services/orders` and `libs`; a `job_type: task`
  with `skip: built`). The board's Rules tab shows each effective value
  and where it came from: "skip: built (from job_type: build)";
  `npx pipemesh check` lists the effective values.
- `job_type:` belongs to the job, never to a component: the same
  component can be a build in one definition and a task in another.

Decision guide: decisions.md → *Choosing each job's job_type*.

## checkout: what a job reads

```yaml
checkout: true            # the whole repository
```

```yaml
checkout: false           # nothing
```

```yaml
checkout:                 # exactly these
  - services/orders
  - libs/money
  - package.json
```

- A path is itself and everything under it; globs cut finer
  (`apps/*/package.json`, `"**/Dockerfile"`). All paths are anchored at
  the repository root (for a job with `repo:`, that repository's root).
  Quote an entry that starts with `*` (`"**/Dockerfile"`); others,
  `${{ … }}` included, need no quotes in a block list.
- **It is enforced.** On Pipemesh's hosted runners and on your own
  runners the job's workspace holds exactly what it checks out (a
  sparse checkout), plus what it consumes. A file the list leaves out
  isn't there, and a script that reads it fails. A narrowed build must
  list the root files it reads: `package.json`, lockfiles,
  `tsconfig.json`, `.nvmrc`, `gradlew` + `gradle/`, `Makefile`, tool
  pins. A deploy lists the scripts and charts it runs.
- **History is always complete**: the fetch is never shallow, so
  `git log`, `git diff` between commits and `git merge-base` work even
  with `checkout: false`.
- **It is the job's fingerprint input**: a job with `skip: built` or
  `unchanged` runs again only when a file it checks out changed (or a
  consumed digest, its definition, a secret or config version). A
  `true` checkout makes every commit new work for the job.
- **Added for you** (to the workspace and the fingerprint): each file
  named by `${checksum:<file>}` in the job's cache key, and each
  `publish:` context and Dockerfile (a context of `.` makes it `true`).
- One spelling per meaning: an empty list and a list holding only
  `"**"` are load errors — write `false` / `true`.
- A `github_actions:` job's checkout is read from its workflow file
  (see Executors); `checkout:` on the job overrides that.
- `job_type: workflow` takes no `checkout:` (its jobs say what they
  read); on `job_type: pipeline` it checks nothing out and only decides
  when the revision is handed over.

## skip: when a job runs

A job's **fingerprint** hashes its definition, parameters, the digests
of what it consumes, the files it checks out, and the versions of its
secrets/config. The skip policy says what to do with it:

- `skip: unchanged` — **deploys and child pipelines.** Skips when the
  fingerprint equals its own last *successful* run's (shown as
  *no changes*). After a failure it runs regardless until it succeeds.
  **Load error in a workflow body** (a workflow run has no earlier run
  to compare with).
- `skip: built` — **builds.** Reuses the outputs of any
  earlier successful run with the same fingerprint (shown as
  *reused*). PRs reuse trusted (default-branch/tag/pipeline) runs and
  their own, never another PR's.
- `skip: never` — **tasks.** Always runs.

A job that reads nothing (no checkout, no consumes, no `image_from`,
no `repos:`, no secrets or config) and still skips has a fingerprint of
its definition alone: it runs once and then shows *no changes* on every
revision. It loads, with a warning; give it its inputs or `skip: never`.

## Executors

A `build`, `deploy` or `task` names exactly one executor:

- **`script:`** (with `setup:`, `image:`, `image_from:`, `services:`,
  `tags:`, `cache:`, `publish:`, `secrets:`): Pipemesh's hosted
  runners, or your own (`tags:`).
- **`uses:`**: a component, which brings the execution keys.
- **`github_actions:`**: an Actions workflow, dispatched and waited for.
  The executor is where the job runs, not what it is: a deploy that
  runs on Actions is a `job_type: deploy`.

```yaml
deploy_prod:
  job_type: deploy
  production: true
  stage: production
  needs:
    - smoke
  consumes:
    - build/dist                           # fetched in the run with pipemesh/consume@v1
  github_actions:                          # or just: github_actions: deploy.yml
    workflow: deploy.yml
    inputs:
      environment: production
```

- `github_actions:` takes `workflow` (the file under
  `.github/workflows`, or its numeric id), `ref`, `inputs` (≤ 8;
  `pipemesh_sha` and `pipemesh_run` are reserved) and `artifacts`. The
  job takes `consumes:` and `produces:` (an entry `dist: file` under
  `produces:` imports the run's Actions artifact named `dist`), and none of
  `script`, `setup`, `uses`, `image`, `image_from`, `services`, `tags`,
  `secrets`, `cache`, `publish`, `artifacts` — secrets stay in GitHub.
- **Its checkout is read from the workflow file** at each revision,
  over every job of it and of each local reusable workflow it calls:
  `actions/checkout` with `sparse-checkout:` → those directories (plus
  the root files, in cone mode); without it → `true`; no checkout of
  this repository → nothing; a remote reusable workflow, a numeric id,
  or a `sparse-checkout:` with a negation → `true`. The workflow files
  are always part of the fingerprint, so editing the workflow re-runs
  the job. `checkout:` on the job overrides the derivation. Pipemesh
  can't enforce an Actions run's checkout: a narrowed one is a promise
  the workflow keeps.
- The workflow must accept `workflow_dispatch` with inputs
  `pipemesh_sha` and `pipemesh_run` (patterns.md §6).

## job_type: workflow and job_type: pipeline

```yaml
ci:                                      # run a body as one node; the job waits for its verdict
  job_type: workflow
  stage: verify
  body: !include .pipemesh/ci.yaml       # a type: workflow body; or !ref <definition>, or inline

orders:                                  # hand the revision to a child pipeline (pipelines only)
  job_type: pipeline
  stage: dispatch
  body: !ref service                     # a type: pipeline body
  variables:                             # the child's variables
    SERVICE: orders
  consumes:
    - graph/orders                       # what decides the hand-over
```

- Both take `body:` (the child's body), `variables:`, `stage:`, `needs:`, `consumes:`, `skip:`,
  `timeout_seconds:`, `allow_failure:`, `matrix:` and `was:` — none of
  the executor keys, and no `produces:`: consumers name the body's
  entries by path (`ci/build/dist`).
- **`job_type: workflow`** combines its jobs' inputs (the union of their
  checkouts, the config and secrets they read, what they consume from
  up here) and takes their skip: `built` when every job is a build, so
  a workflow of builds reuses as a whole, entries included; `never` as
  soon as one job runs every time. `skip: never` is its only override;
  `checkout:` on it is a load error. A body that works in another
  repository runs every time. Inside a
  workflow run, a nested `job_type: workflow` job runs every time.
- **`job_type: pipeline`** passes the revision on only when its declared
  inputs changed: the graph job's entry it consumes, or the service's
  files listed in `checkout:` (which checks nothing out). Default:
  nothing — so declare one. `skip:` is `unchanged` (default) or `never`.
  It settles as *Dispatched*; the child promotes the revision on its
  own board.
- Inside a body, a consume of `../build/jar` names the parent's `build`
  job's `jar` entry (the `job_type: workflow`/`pipeline` job consumes it
  too).
- The child lives under the parent: `…/repo/-/pipeline/<job>`.

## Ordering

**A job waits only for what it names**: the jobs in `needs:` and the
producers of what it `consumes:`. A job that names nothing starts with
the revision, whatever its stage. When translating stage-ordered CI
(GitLab, Jenkins stages, CircleCI workflows), add the `needs:` edges
explicitly. Cycles are load errors.

Entries (`produces:`) reach only the jobs that `consumes:` them by
name — `needs:` orders but hands nothing over. A production deploy
that needs staging still consumes the build's output itself.

## matrix

```yaml
test:
  job_type: build
  stage: test
  matrix:
    stream:
      - aws
      - docker
    arch:                           # 2×2 = 4 jobs: test[stream=aws,arch=amd64], …
      - amd64
      - arm64
  checkout:
    - streams/${{ matrix.stream }}
  script: ./test.sh ${{ matrix.stream }} --arch ${{ matrix.arch }}
```

- `as: jobs` (default): sibling jobs, each with its own cursor — one
  broken lane doesn't block the others. Under `needs:`, `test` = all
  variants; `test[stream=aws]` = a slice; `test[stream=aws, arch=arm64]`
  = one. The structured form names the job and the slice:

  ```yaml
  needs:
    - job: test
      matrix:
        stream: aws
  ```
- `as: workflow`: one node over a generated child workflow, one verdict
  (the CI-matrix shape); reference only the node.
- `${{ matrix.x }}` interpolates at load (also in `checkout:`);
  shell-safe names are also variables (`$stream`). Values may not
  contain `[ ] , =`. Cap: 256.
- Lane-to-lane: a `needs:` item `test[stream=${{ matrix.stream }}]`.
  In a block list a selector needs no quotes; quote an item that starts
  with a YAML indicator (`*`, `&`, `!`, `[`, `{`).

## Components

```yaml
deploy_production:
  job_type: deploy
  production: true
  stage: production
  consumes:
    - image/app                                     # $PIPEMESH_IMAGE_APP = <repo>@sha256:…
  checkout:
    - charts/app                                    # what the component's script reads
  uses: ./.pipemesh/components/eks-deploy.yaml      # a type: component file, or a registry component: aws/role@1
  with:
    cluster: prod
    replicas: 4
```

```yaml
# .pipemesh/components/eks-deploy.yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-component.schema.json
type: component
name: eks-deploy
params:
  cluster:
    type: string                                    # a param's type: string, int, bool, list
    required: true
  replicas:
    type: int
    default: 2
job:
  image: ghcr.io/acme/deploy-tools@sha256:…   # helm + kubectl, and bash, git, curl, tar
  script: |
    helm upgrade app charts/app --kube-context ${{ params.cluster }} \
      --set image="$PIPEMESH_IMAGE_APP" --set replicas=${{ params.replicas }}
```

- A component starts with `type: component` and `name: <name>`; the
  rest is `params:` and `job:`. (A param's `type:` is its value type,
  unrelated to the component's shape.)
- Placement keys (`type`, `job_type`, `checkout`, `stage`, `needs`,
  `skip`, `tags`, timeouts, `consumes`, `produces`, `secrets`, `cache`,
  `publish`) stay on the job; execution keys (`script`, `setup`,
  `image`, `services`) belong to the component — declaring one beside
  `uses:` is a load error. The job's `checkout:` must cover what the
  component's script reads.
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
| `turbo/fingerprint@1` | **image**, install, turbo, task, deployables, extra, out | one fingerprint per Turborepo package (a `build` job; checkout `true`) |
| `nx/fingerprint@1` | **image**, install, with_target, targets, extra, out | one fingerprint per Nx project (a `build` job; checkout `true`) |
| `bazel/fingerprint@1` | image, targets, extra, out, bazel | one fingerprint per deployable Bazel package (a `build` job; checkout `true`) |
| `bazel/remote-cache@1` | url, header, secret_var, results_url, bes_backend | Bazel remote cache (BuildBuddy default); PRs never upload (setup step) |
| `docker/build-push@1` | **image**, tag, context, dockerfile, registry, username_var, password_var | docker login + build + push one tag (prefer `publish:`) |
| `node/pnpm@1` | version | corepack-provisioned pnpm (setup step) |
| `codecov/upload@1` | token_var | Codecov CLI upload (setup step) |
| `slack/message@1` | **text**, webhook_var | post to a Slack incoming webhook (setup step) |

## Other repositories

```yaml
pipeline:
  type: pipeline
  repos:
    infra: github.com/acme/infra           # followed on the branch it was added with
  stages:
    - deploy
  jobs:
    apply:
      job_type: deploy
      production: false
      stage: deploy
      repo: infra                           # works in infra: its checkout is infra's paths
      checkout:
        - envs/staging
        - modules
      repos:
        $self:                              # the pipeline's own repository at ./app, only config/
          mount: app
          checkout:
            - config
      script: cd envs/staging && terraform apply -auto-approve
```

Values under the body's `repos:` are repository paths (strings). Each
revision pins a commit of every declared repository; a push to any of
them starts a revision. Declared repositories must be added to the
same Pipemesh organization. A mount (`repos:` on a job) needs
`mount:` and `checkout:` (`true` or a list — a mount exists to be
read). Secrets, config and commit statuses stay the pipeline
repository's.

## Renaming jobs

A pipeline job keeps history under its name. When a rename is part of
the change, keep history with `was:`:

```yaml
deploy_prod:
  job_type: deploy
  production: true
  was: deploy_production     # takes over its cursor and runs, once
```

When editing an existing definition, prefer keeping job names.

## Removed keys

Pipemesh rejects the older grammar with a message naming the
replacement:

| Old | Now |
| --- | --- |
| `kind:` on a job | `job_type:`, same values and defaults (`job_type:` and `kind:` together is an error too) |
| `component: <name>` at the top of a component | `type: component` and `name: <name>` |
| a standalone body, job or component without `type:` | the load error names its shape: add `type: workflow`, `pipeline`, `job` or `component` |
| `kind: pipeline` / `kind: workflow` at the top of a body | `type: pipeline` / `type: workflow`, matching where the body is used |
| `paths:` on a job | `checkout:` (an empty `paths:` → `checkout: false`; no `paths:` → `checkout: true`, which only a build defaults to) |
| `delegate:` with `type: workflow` or `type: pipeline` and `params:` (`body`, `variables`) | `job_type: workflow` / `job_type: pipeline` with `body:`, `variables:` (and, for a pipeline, what decides the hand-over: `consumes:` or `checkout:`) on the job |
| `delegate:` with `type: github_actions` and `params:` (`workflow`, `inputs`) | `github_actions:` (`workflow`, `inputs`) on a build, deploy or task |
| `trigger:` | as `delegate:` |
| `paths:` in a mount under a job's `repos:` | `checkout:` |
| a job without a job type reading the whole repository | it is a `task` that checks out nothing: give it its `job_type:` and checkout |

## Editor validation

Add the modeline at the top of each file — definition:
`# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-definition.schema.json`;
`.pipemesh/*.yaml` bodies: `…/pipemesh-body.schema.json`;
components: `…/pipemesh-component.schema.json`. In VS Code settings, add
`!include scalar` and `!ref scalar` to `yaml.customTags`.
