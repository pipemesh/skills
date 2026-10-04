---
name: pipemesh-setup
description: Set up Pipemesh for a repository. Scans the existing CI/CD configuration (GitHub Actions, GitLab CI and other CI systems, Makefiles, deploy scripts, Dockerfiles, Nx/Turborepo/Bazel monorepos) and writes a pipemesh.yaml with the right pipelines and workflows, each job's job_type (build, deploy, task, workflow, pipeline) and the files it checks out, typed structures (type:) in block-style YAML, build artifacts passed by produces/consumes, caching, secrets and deploy stages; asks clarifying questions when the shape is unclear (pipeline vs workflow, where jobs run, environments); validates the definition; offers to open a pull request; and explains how to add the repository in Pipemesh. Use this whenever the user mentions Pipemesh or pipemesh.yaml, wants to migrate or port their CI/CD to Pipemesh, onboard a repo to Pipemesh, or convert GitHub Actions or GitLab CI workflows into Pipemesh pipelines — and also to review, fix or extend an existing pipemesh.yaml.
---

# Pipemesh setup

Pipemesh (https://pipemesh.io) is a CI/CD control plane. A repository
declares what it runs in one file, `pipemesh.yaml`, at its root:

- a **pipeline** — long-lived; every commit on the repository's branch
  (the default branch it was added with, e.g. `main`) becomes a *revision* that promotes job by job (build → staging →
  production). Each job remembers what it last ran, so the board answers
  "what is deployed right now";
- **workflows** — one-shot runs per trigger: pull requests, tags,
  schedules, pushes, manual runs.

Every job says what it is (`job_type:` — `build`, `deploy`, `task`,
`workflow` or `pipeline`; no `job_type:` means `task`) and what it
reads (`checkout:`). The job type sets the job's defaults: what it
checks out and when it may skip. Every structure that stands on its own
— a root definition, an included file, a component — says its shape
with `type:` (`job`, `workflow`, `pipeline`, `component`), and
Pipemesh checks it against where the structure is used.

Your job: read how this repository builds, tests, releases and deploys
today, and produce a `pipemesh.yaml` (plus `.pipemesh/*.yaml` bodies
when it grows) that does the same work the Pipemesh way — then validate
it, offer a pull request, and tell the user how to turn it on.

Work through the steps in order. The reference files hold the detail;
read each one when its step comes up, not all up front.

| File | Read it when |
| --- | --- |
| `references/decisions.md` | Step 3 — pipeline vs workflows, each job's `job_type:`, and what to ask |
| `references/definition.md` | Step 4 — the grammar (always, before writing YAML) |
| `references/artifacts-and-caching.md` | Step 4 — produces/consumes, images, cache, remote build caches, runtime variables |
| `references/patterns.md` | Step 4 — worked shapes from the public demos |
| `references/migrate-github-actions.md` | Step 4 — the repo has `.github/workflows/` |
| `references/migrate-gitlab-ci.md` | Step 4 — the repo has `.gitlab-ci.yml` |
| `references/migrate-other-ci.md` | Step 4 — any other CI system, or none |
| `references/onboarding.md` | Steps 6–8 — summary, pull request, enabling the repo |
| `scripts/check_definition.py` | Step 5 — validate before showing the result |

## Step 1 — Survey the repository

Find every place work is defined today. Read the files; don't guess
from names.

- CI definitions: `.github/workflows/*.yml|yaml`, `.gitlab-ci.yml` (and
  its `include:`s), `.circleci/config.yml`, `Jenkinsfile`,
  `.buildkite/`, `azure-pipelines.yml`, `bitbucket-pipelines.yml`,
  `.travis.yml`, `cloudbuild.yaml`, `.drone.yml`, `Taskfile.yml`,
  `Makefile`/`justfile` targets the CI calls.
- What the CI calls: scripts under `scripts/`, `deploy/`, `bin/`,
  `ci/`; `package.json` scripts; Gradle/Maven tasks; `Dockerfile`s and
  compose files; Helm charts, Kustomize, Terraform/CDK/Pulumi
  directories; platform configs (`vercel.json`, `fly.toml`,
  `netlify.toml`, `app.yaml`, `serverless.yml`, `wrangler.toml`).
- Shape of the repo: workspace/monorepo markers (`nx.json`,
  `turbo.json`, `pnpm-workspace.yaml`, `lerna.json`, `MODULE.bazel`/
  `WORKSPACE`, `go.work`, Gradle `settings.gradle*` with subprojects,
  Cargo workspaces), lockfiles, toolchain version files (`.nvmrc`,
  `.tool-versions`, `.python-version`, `go.mod`, `.java-version`).
- Repository facts: the remote (`git remote get-url origin`), the
  default branch's name (`git symbolic-ref --short refs/remotes/origin/HEAD`,
  or `gh repo view --json defaultBranchRef`; it's the branch Pipemesh
  will follow and the one OIDC subjects name), and whether a `pipemesh.yaml`
  already exists — if it does, this is an edit: keep job names (they
  carry history; see `was:` in definition.md) and change only what the
  user asked for. A definition from before the current grammar (jobs
  with `kind:`, components with `component: <name>`, standalone bodies
  without `type:`) no longer loads: run the checker on it first; each
  error names the replacement (definition.md → Removed keys).
- Pipemesh connects to **GitHub** today. If the remote is GitLab,
  Bitbucket or self-hosted, say so early: you can still write the
  definition, but the repository can't be enabled until support lands.

## Step 2 — Inventory what the CI does

Before writing anything, build a table (for yourself; show it to the
user in step 6) with one row per existing job or workflow:

| existing job | trigger | purpose | repository files it reads | outputs it hands on | secrets/vars | where it runs | target env |

Purpose is one of: lint/test, build, package/image, release/publish,
deploy, infra apply, scheduled maintenance, notification. For *files it
reads*, follow the commands, not the job's name: the directories it
builds, the root files its tools read (`package.json`, lockfiles,
`tsconfig.json`, `.nvmrc`, `go.mod`, `gradlew` and `gradle/`, `Makefile`),
the scripts and charts it runs, the files its `cd`, `cat`, `source` and
`-f` arguments name. That column becomes the job's `checkout:`. Note how
artifacts move between jobs (upload/download, `artifacts:`, workspace
persistence), what is cached and keyed on what, which cloud
credentials are used (static keys vs OIDC), approvals before
production, matrices, services (databases), self-hosted runner labels,
concurrency groups. Also note what is *not* in CI but obviously part of
the release (a deploy script the README tells people to run by hand) —
mention it, don't automate it unasked.

## Step 3 — Decide the shape, and ask when in doubt

Read `references/decisions.md`. Map each row of the inventory to the
pipeline, a workflow, or nothing. The usual result for an application:
one `pipeline` (build → staging → production) plus a `checks` workflow
on pull requests, and workflows for tag releases and schedules. For a
library: workflows only (checks + release on tag).

Then give each job its `job_type:` (decisions.md → *Choosing each
job's job_type*):

| The job… | `job_type:` | checks out by default | skips by default |
| --- | --- | --- | --- |
| reads the source and is hermetic: compile, test, lint, image build, a build-graph fingerprint | `build` | everything (`true`) | `built`: reuses an earlier run with the same inputs |
| ships what it consumes to an environment — **pipelines only** | `deploy` | nothing | `unchanged`: runs when what it ships changed |
| must run every time: smoke tests, notifications, checks that read the pull request's merge base | `task` (also: no `job_type:`) | nothing | `never` |
| runs a body (a CI suite) as one node and waits for it | `workflow` | its jobs' checkouts | its jobs': `built` if all are builds |
| hands the revision to a child pipeline (one per service) — **pipelines only** | `pipeline` | nothing | `unchanged` |

A deploy in a workflow is a load error: a pull-request preview or a
manual hotfix deploy is a `task` there, and isn't tracked as a
deployment.

Ask the user **before writing** when the answer changes the file and
the repository can't tell you — typically: pipeline vs workflow for
main-branch CI with no deploy; porting jobs to Pipemesh runners vs
keeping GitHub Actions as the compute; the environment promotion order;
what replaces a production approval; per-service pipelines in a
monorepo; and what to do with the old CI. Batch the questions (one
round, up to four), give concrete options, lead with your
recommendation and the evidence. Use the harness's question tool if it
has one (`AskUserQuestion` in Claude Code); otherwise ask in plain text
and wait. Don't ask what you can read from the repo or what has a
conventional default — decide, and list those decisions in the summary.
If no one can answer (a non-interactive run), take the recommended
options and state the assumptions at the top of the summary.

## Step 4 — Write the definition

Read `references/definition.md` first, then the migration reference for
the CI system you found, `references/artifacts-and-caching.md`, and the
closest shape in `references/patterns.md`.

What every file you write looks like — `type:` on each structure that
stands alone, `job_type:` on every job, block style throughout:

```yaml
# .pipemesh/ci.yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-body.schema.json
type: workflow
stages:
  - checks
jobs:
  lint:
    job_type: build
    stage: checks
    checkout:
      - src
      - package.json
    script: make lint
```

```yaml
# pipemesh.yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-definition.schema.json
pipeline:
  type: pipeline
  stages:
    - verify
  jobs:
    ci:
      job_type: workflow                # runs the type: workflow body as one node
      stage: verify
      body: !include .pipemesh/ci.yaml
pipemesh:
  pipelines:
    pipeline: !ref pipeline
  workflows:
    checks:
      body: !include .pipemesh/ci.yaml
      on: pull_request
```

The rules that most often go wrong — they differ from other CI systems:

1. **`stage:` does not order execution.** A job waits only for the jobs
   in its `needs:` and the producers of what it `consumes:`. Translate
   stage order into explicit `needs:` edges.
2. **One branch is watched: the one the repository was added with**
   (its default branch, recorded by name). The pipeline follows it (no
   trigger; one per repository, registered as `pipeline`). Pull
   requests, tags, schedules and manual runs are workflows. Pushes to
   other branches start nothing — ask when the old CI deploys from one.
3. **Every job has a `job_type:`; write it on every job**, even a task.
   The job type sets the defaults in the table above; override one on
   the job when it's wrong for that job (a `job_type: task` with
   `skip: built`, a `job_type: build` whose `checkout:` lists
   `services/orders` and `libs`). Deploys and child pipelines exist
   only in the pipeline; never put `skip: unchanged` in a workflow body.
   `kind:` is the old name and a load error.
4. **A structure that stands alone says its shape with `type:`** —
   `type: pipeline` on the pipeline's root definition, `type: workflow`
   or `type: pipeline` at the top of every `.pipemesh/*.yaml` body,
   `type: job` on a job kept in its own file or root key, and
   `type: component` with `name: <name>` on every component. It must
   agree with where the structure is used: an entry of `jobs:` is a
   `job`; a body under `pipemesh.pipelines` or run by a
   `job_type: pipeline` job is a `pipeline`; one under
   `pipemesh.workflows` or run by a `job_type: workflow` job is a
   `workflow`; what `uses:` names is a `component`. So one body can serve
   the PR workflow and a `job_type: workflow` job, but never both a
   workflow and a pipeline. Written inline (a job under `jobs:`, an
   inline `body:`), `type:` is optional. Plain data — a cache
   definition, a list of script lines — has no `type:`.
5. **Write block style, always.** Every list is one `- item` per line
   and every map one `key: value` per line, as above — never `[a, b]` or
   `{ a: b }` (only an empty `[]` or `{}` stays inline). The references
   sometimes quote YAML inline in a sentence or a table, such as
   `checkout: [deploy]`, for brevity; in a file that is always
   `checkout:` with `- deploy` on the next line. The checker warns on
   flow style.
6. **`checkout:` is what the job's commands read — and it is
   enforced.** `true` (the whole repository), `false` (nothing) or a
   list of paths from the repository root (a path is itself and
   everything under it; globs cut finer). The job's workspace holds
   exactly that, plus what it consumes: a file the list leaves out is
   not there, and the job fails on it. Write it from the inventory's
   *files it reads* column. A build may keep its default `true` (safe;
   every commit is then new work for it) or narrow it to the
   directories it builds plus the root files its tools read
   (`package.json`, the lockfile, `tsconfig.json`, `.nvmrc`, wrapper
   scripts) — narrow only with a complete list. A deploy lists its
   deploy scripts and charts (`deploy/`) and gets the artifact through
   `consumes:`. A task that only reads git history or the commit checks
   out nothing (history stays complete, so `git log` and
   `git merge-base` work).
   The checkout is also the job's fingerprint: a `built`/`unchanged`
   job runs again only when a file it checks out, or something it
   consumes, changed. Files named by `${checksum:…}` in the cache key
   and `publish:` contexts are added for you. An empty list and a list
   holding only `"**"` are load errors (write `false` / `true`).
7. **Outputs are declared entries, handed only to who names them.** The
   producer declares the entry; each consumer names it and finds the
   files at the same path (and the path in `$PIPEMESH_BUILD_DIST`):

   ```yaml
   build:
     job_type: build
     stage: build
     script: npm run build
     produces:
       dist: dist
   deploy_staging:
     job_type: deploy
     stage: staging
     consumes:
       - build/dist
     script: ./deploy/deploy.sh staging dist
   ```

   `needs:` orders but passes nothing. Deploys consume what the build
   produced — never rebuild.
8. **A job runs on one executor**: `script:` (Pipemesh's runners),
   `uses:` (a component) or `github_actions:` (an existing Actions
   workflow, dispatched and waited for). The executor is where the job
   runs, not what it is: a deploy on Actions is still
   `job_type: deploy`. An Actions job's checkout is read from its
   workflow file (`actions/checkout` = `true`, its `sparse-checkout:` =
   those directories, no checkout step = nothing); Pipemesh can't
   enforce it. A suite that runs as one node is `job_type: workflow`
   with `body:` (a `type: workflow` body); one pipeline per service is
   `job_type: pipeline` with `body:` (a `type: pipeline` body) and
   `variables:`, handing the revision over only when what it
   `consumes:` (the service's fingerprint) or `checkout:` lists changed.
9. **Images you deploy are built and pushed in the script**, then
   recorded with `pipemesh produce oci <key> --ref <repo> --digest <sha256>`
   and deployed by digest. `publish:` (no `repo:`) is only for CI images
   your own jobs run in via `image_from:`. Hosted runners are **arm64**:
   what they build is arm64 unless cross-built — check the deploy
   target's architecture and ask if it's amd64.
10. **Pull-request runs never save caches; they restore what the
   default-branch jobs saved with the same cache `paths:`.** Key on
   `${checksum:<lockfile>}`, keep paths inside the workspace, and give
   the PR job and the pipeline job that run the same install one shared
   cache definition (`cache: !include .pipemesh/npm-cache.yaml`, a
   plain-data file with no `type:`). Remote build caches (Nx Cloud,
   Turborepo, BuildBuddy) write from the default branch only.
11. **Reuse with `!ref`, `!include` and components**, never YAML
   anchors or `extends`. Every root key must be reachable from
   `pipemesh:`, and every body, job or component reached that way
   carries its `type:` (rule 4).
12. **Secrets are named, not inlined.** List them per job under
   `secrets:` (non-secret settings under `config:`, `UPPER_SNAKE_CASE`);
   values are set in Pipemesh. Pull-request runs don't receive secrets
   unless a secret allows it. Prefer OIDC to stored cloud keys:

   ```yaml
   setup:
     - uses: aws/role@1
       with:
         arn: arn:aws:iam::123456789012:role/deploy-staging
         region: eu-west-1
   ```
13. **Images are public and need bash, git, curl and tar** (Debian-based
   tags like `node:22-bookworm`; not Alpine, distroless or most
   `-slim`). No `image:` = Pipemesh's job image (bash, git, curl, tar,
   jq, AWS CLI, Docker + buildx, Node 20, Python 3.9, Java 25) — pin a
   toolchain image when the project needs other versions. `services:` is
   ignored — start databases in the script (patterns.md §12).
14. **GitHub `${{ … }}` expressions are not interpolated.** Use the
    runtime variables (`$CI_COMMIT_SHA`, `$CI_COMMIT_REF_NAME`, …);
    Pipemesh's own `${{ matrix.x }}` and `${{ params.x }}` are load-time.
    Job names are lowercase `[a-z0-9_-]`; quote crons; the default job
    timeout is one hour (`timeout_seconds:` for longer).

Don't invent what you can't see. Keep the project's own commands and
scripts (the deploy script is the source of truth for *how* to deploy).
When a step has no faithful translation (an unusual marketplace action,
a vendor plugin), either run that job on the existing GitHub Actions
workflow (`github_actions:`) or leave a precise `# TODO(pipemesh): …`
comment in the script and list it in the summary — never a silent
approximation.

Prefer a small `pipemesh.yaml` that registers workloads and `!include`s
bodies from `.pipemesh/` once there is more than one body or the file
passes ~150 lines; each included body starts with its `type:`. Put the
schema modeline at the top of each file. Comment the non-obvious
decisions in the YAML itself (why a deploy checks out only `deploy/`,
why a build narrows its checkout or a test is a task), briefly.

## Step 5 — Validate

Run the bundled checker from the repository root:

```bash
python3 <this skill's directory>/scripts/check_definition.py .
```

It resolves `!include`/`!ref` like the loader, checks every `type:`
against where the structure is used (and that every standalone one has
one), reports what the loader would reject (ERROR) and likely mistakes
(WARN, including flow-style collections), and then lists every job's
effective `job_type`, checkout and skip policy with where each came
from ("skip: built (from job_type: build)"). Fix every error; fix every
flow-style warning by rewriting the lines in block style; fix or
consciously accept each other warning — in particular *reads nothing* (the
job runs once and then skips every revision: give it a checkout or a
consume, or `skip: never`) and *its script names …, which is not in
its checkout* (the enforced checkout would fail the job). Read the
effective list against the inventory: each build checks out what it
compiles, each deploy what it runs, each task what it needs.

If PyYAML is missing, `uv run --with pyyaml python3 …` or
`pip install pyyaml`; if Python isn't available, check by hand against
the rules in step 4 and definition.md. The checker mirrors the loader's documented rules; the
authoritative check happens when the file reaches the branch of an
enabled repository, where Pipemesh reports any load error with the
file and key.

Then re-read the result against the inventory: every row is covered or
deliberately dropped; every job has a `job_type:`; every standalone
body, job and component has its `type:`; every deploy is a
`job_type: deploy` in the pipeline and consumes what it ships; nothing
deploys from a pull request; the files are block style throughout.

## Step 6 — Summarize

Tell the user, concisely (see the template in `references/onboarding.md`):
the files written, a mapping of old jobs → new jobs, the decisions and
assumptions you made, any TODOs, and what Pipemesh will need before the
first run (secrets and config by name, cloud trust, runners, GitHub App
permissions).

## Step 7 — Offer a pull request

Ask whether to open a pull request with the result; don't push without
a yes. On yes: create a branch (e.g. `pipemesh-setup`), commit only the
files you wrote or changed with a message in the repository's own
style, push, and open the PR (`gh pr create` when the GitHub CLI is
authenticated; otherwise give the compare URL). Use the PR body
template in `references/onboarding.md`. Leave existing CI files alone
unless the user chose to change them in step 3.

## Step 8 — Explain how to turn it on

Walk the user through adding the repository in Pipemesh — sign in,
connect the GitHub App, enable the repository, add the secrets and
config this definition names, set up cloud trust for the exact job
identities it uses, and what to watch after merging. Follow
`references/onboarding.md`, and tailor it: list the real secret names,
compute the real OIDC subjects for this repository's jobs, and skip
sections that don't apply.
