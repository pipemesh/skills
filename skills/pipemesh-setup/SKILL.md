---
name: pipemesh-setup
description: Set up Pipemesh for a repository. Scans the existing CI/CD configuration (GitHub Actions, GitLab CI and other CI systems, Makefiles, deploy scripts, Dockerfiles, Nx/Turborepo/Bazel monorepos) and writes a pipemesh.yaml with the right pipelines and workflows, each job's job_type (build, deploy, task, workflow, pipeline) and the files it checks out, typed structures (type:) in block-style YAML, build artifacts passed by produces/consumes, caching, secrets and deploy stages; asks clarifying questions when the shape is unclear (pipeline vs workflow, where jobs run, environments); validates the definition and shows the user the board it will draw (npx pipemesh preview); offers to open a pull request; and explains how to turn it on in Pipemesh. Use this whenever the user mentions Pipemesh or pipemesh.yaml, wants to migrate or port their CI/CD to Pipemesh, onboard a repo to Pipemesh, or convert GitHub Actions or GitLab CI workflows into Pipemesh pipelines — and also to review, fix or extend an existing pipemesh.yaml, or to answer how a Pipemesh repository is doing, why a job failed, what went to production, whether a change was deployed, or how much artifact storage and how many build minutes its organization has used.
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
`workflow` or `pipeline`; a job without one is a load error) and what
it reads (`checkout:`). The job type decides when the job runs and
what it checks out by default. Every structure that stands on its own
— a root definition, an included file, a component — says its shape
with `type:` (`job`, `workflow`, `pipeline`, `component`), and
Pipemesh checks it against where the structure is used.

Your job: read how this repository builds, tests, releases and deploys
today, and produce a `pipemesh.yaml` (plus `.pipemesh/*.yaml` bodies
when it grows) that does the same work the Pipemesh way — then validate
it, offer a pull request, and tell the user how to turn it on.

Work through the steps in order. The reference files hold the detail;
read each one when its step comes up, not all up front.

**Before Step 1, check this skill is current** (it is version 0.25.0;
the version below changes with it):

```bash
npx pipemesh@latest version --skill=0.25.0
```

If it says a newer pipemesh-setup skill is out, tell the user, with the
update line it prints, and ask whether to update first or go on with
this one. Pipemesh's grammar moves; an old skill writes what an old
Pipemesh accepted. If the command can't reach Pipemesh, go on.

**Which Pipemesh.** Every `npx pipemesh` command talks to
https://pipemesh.io unless something names another instance, such as
one the user's company runs. If the user mentions their own instance,
or the repository lives on a self-managed GitLab, ask for its URL and
point the CLI at it once (pipemesh 0.10.0 or newer):

```bash
npx pipemesh login --api=https://pipemesh.acme.internal   # signs in and makes it the default
npx pipemesh instance https://pipemesh.acme.internal      # instead, where sign-in is off
npx pipemesh instance                                    # which one commands use, and why
```

When the user also uses pipemesh.io, `npx pipemesh instance <url> --here`
points only the repository they're in at their instance;
`npx pipemesh instance --reset` (with `--here`, for this repository)
forgets the choice.

`npx pipemesh@latest instance` also says whether the instance's public
view is on, and whether only Pipemesh admins add organizations (then
the user asks one to add theirs).

Behind a TLS-inspecting proxy (Zscaler and the like), or with an
instance whose certificate comes from the company's own CA, Node needs
that CA: `export NODE_EXTRA_CA_CERTS=/path/to/ca-bundle.pem` before
any `npx pipemesh` command; the CLI says so when it meets a certificate
it doesn't trust. Below, "Pipemesh" and every pipemesh.io URL (sign-in,
boards, the OIDC issuer `<url>/api/oidc`, schemas `<url>/api/meta/…`)
mean that instance.

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
  without `type:`) no longer loads: run `npx pipemesh check` on it
  first (step 5); each error names the replacement (definition.md →
  Removed keys).
- Pipemesh connects to **GitHub** and **GitLab**: gitlab.com groups on
  Premium or Ultimate (they need group access tokens), and on a
  self-hosted Pipemesh any self-managed GitLab its admin added, beside
  gitlab.com (each has its own sign-in button, "Continue with
  git.acme.io"). An account on one GitLab is not the same account on
  another: sign in with the GitLab the repository lives on. If the
  remote is Bitbucket or another host, say so early: you can still write
  the definition, but Pipemesh can't run it there.

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
production, matrices, services (databases), Docker in jobs (→ `dockerd: true`),
memory-hungry jobs (→ a bigger `runner:`), self-hosted runner labels,
concurrency groups, and output people read (test results, coverage,
screenshots: → `reports:` on the job that writes it). Also note what
is *not* in CI but obviously part of the release (a deploy script the
README tells people to run by hand) — mention it, don't automate it
unasked.

## Step 3 — Decide the shape, and ask when in doubt

Read `references/decisions.md`. Map each row of the inventory to the
pipeline, a workflow, or nothing. The usual result for an application:
one `pipeline` (build → staging → production) plus a `checks` workflow
on pull requests, and workflows for tag releases and schedules. For a
library: workflows only (checks + release on tag).

Then give each job its `job_type:` (decisions.md → *Choosing each
job's job_type*):

| The job… | `job_type:` | when it runs | `checkout:` |
| --- | --- | --- | --- |
| reads the source and is hermetic: compile, test, lint, image build, a build-graph fingerprint | `build` | reuses an earlier pass with the same fingerprint (*reused*); runs when none matches | `true` by default |
| ships what it consumes to an environment — **pipelines only** | `deploy` | skips when its fingerprint matches its last success (*no changes*); after a failed or cancelled run, runs in full until one succeeds | `false` by default |
| must run every time: smoke tests, notifications, checks that read the pull request's merge base | `task` | every time | `false` by default |
| runs a body (a CI suite) as one node and waits for it | `workflow` | every time; the builds in its body reuse what they can | not allowed |
| hands the revision to a child pipeline (one per service) — **pipelines only** | `pipeline` | without `fingerprint:`, every revision; with it, when something it names changes | not allowed (`fingerprint.checkout` names files) |

Nothing defaults: a job without `job_type:` is a load error naming the
first untyped job. `skip:` is deprecated and ignored: a job that still
writes it loads, `npx pipemesh check` warns, and the job type decides.
Never write it — pick the job type instead. A job that must run every
time is a `task`.

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
   in its `needs:` and the producers of what it `consumes:` (a workflow
   or pipeline job: of what its body reaches with `../`, and a pipeline
   job of its `fingerprint.artifacts` too). Translate stage order into
   explicit `needs:` edges.
2. **One branch is watched: the one the repository was added with**
   (its default branch, recorded by name). The pipeline follows it (no
   trigger; one per repository, registered as `pipeline`). Pull
   requests, tags, schedules and manual runs are workflows. Pushes to
   other branches start nothing — ask when the old CI deploys from one.
3. **Every job has a `job_type:`; write it on every job**, even a task
   (without one the definition doesn't load). The job type decides when
   the job runs (the table above); `skip:` is deprecated and ignored, so
   never write it.
   Override the checkout on the job when the default is wrong for it (a
   `job_type: build` whose `checkout:` lists `services/orders` and
   `libs`). A build that must run every time is really a `task`.
   Deploys and child pipelines exist only in the pipeline. `kind:` is
   the old name and a load error.
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
   `checkout:` with `- deploy` on the next line.
6. **`checkout:` is what the job's commands read — and it is
   enforced.** `true` (the whole repository), `false` (nothing) or a
   list of paths from the repository root (a path is itself and
   everything under it; globs cut finer). The job's workspace holds
   exactly that, plus what it consumes: a file the list leaves out is
   not there, and the job fails on it. Write it from the inventory's
   *files it reads* column, **as coarse as it can be**: `true`, a few
   whole directories, or the whole repository minus what the job
   doesn't read, `["**", "!docs", "!**/*.md"]` (an entry starting with
   `!` excludes; git reads the list: the most specific path decides, the
   last entry breaks a tie). Never a long
   list of loose root files: it goes stale when a file is added, and the
   job fails on it. A build keeps `true` unless it plainly reads part of
   the tree (one service of a monorepo: its directory, the shared
   libraries, the root config). A deploy lists its deploy directory
   (`deploy/`, `charts/app`) and gets the artifact through `consumes:`.
   A task that only reads git history or the commit checks out nothing
   (history stays complete, so `git log` and `git merge-base` work).
   The checkout is also the job's fingerprint: a build or deploy runs
   again only when a file it checks out, or something it consumes,
   changed (a task runs every time). Files named by `${checksum:…}` in the cache key
   and the directories of images Pipemesh builds (`build:`) are added for you. An empty list, a list
   holding only `"**"`, and a list of exclusions alone are load errors
   (write `false` / `true` / start with `"**"`).
7. **Outputs are declared entries, handed only to who names them.** The
   producer declares the entry; each consumer names it and finds the
   files at the same path (and that path in the variable it names, `$DIST`):

   ```yaml
   build:
     job_type: build
     stage: build
     script: npm run build
     produces:
       dist: dist
   deploy_staging:
     job_type: deploy
     production: false
     stage: staging
     consumes:
       DIST: build/dist
     script: ./deploy/deploy.sh staging "$DIST"
   ```

   `needs:` orders but passes nothing. Deploys consume what the build
   produced — never rebuild. Every deploy says whether it reaches
   production, `production: true` or `production: false` (a load error
   without it): the production deploys are what DORA metrics,
   `pipemesh deployments` and `pipemesh deployed` count. Staging, preview
   and canary deploys are `false`; ask when the environment isn't clear.
   What a job writes for people to read (test results as a site, a
   coverage summary) is a `reports:` key on that job, shown in its
   Reports tab; no job consumes a report.
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
   `variables:`. Without `fingerprint:` it hands every revision to its
   child; with `fingerprint:` it holds a revision until something it
   names changes — the service's own fingerprint artifact
   (`fingerprint.artifacts`) or directories (`fingerprint.checkout`).
9. **Images you deploy are built and pushed in the script**, then
   recorded with `pipemesh produce oci <key> --ref <repo> --digest <sha256>`
   and deployed by digest. An `oci` entry with `build:` is only for CI
   images your own jobs run in via `image_from:`. Hosted runners are **arm64**
   unless the job names a `linux-amd64-*` size: what a job builds is its
   runner's architecture — check the deploy target's architecture and
   ask if it's amd64. On a self-hosted instance a job without a size
   runs on the architecture of the instance's runner (its admin knows),
   and its default image is `ghcr.io/pipemesh/runner`.
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
   `secrets:`; a non-secret setting is a variable the job declares with
   `NAME: !settings` under `variables:` (`UPPER_SNAKE_CASE`). Values are
   set in Pipemesh, and a name has one source: never `!settings` and a
   value in the definition both. Pull-request runs don't receive secrets
   unless a secret allows it. Prefer OIDC to stored cloud keys; the
   OIDC provider and the roles jobs assume go in a bootstrap stack the
   user deploys by hand, never in the pipeline that needs them to log in
   (onboarding.md §4):

   ```yaml
   setup:
     - uses: aws/role@1
       with:
         arn: arn:aws:iam::123456789012:role/deploy-staging
         region: eu-west-1
   ```
13. **Images are public and need bash, git, curl and tar** (Debian-based
   tags like `node:22-bookworm`; not Alpine, distroless or most
   `-slim`). No `image:` = Pipemesh's job image: Ubuntu 24.04 like
   GitHub's runners, with git, curl, wget, jq, make, zip, Python 3.12,
   Node 24, the AWS CLI, the Docker CLI + buildx and `sudo` (no Java) —
   pin a toolchain image when the project needs other versions. A job
   that must not follow image updates (a deploy that changes the
   platform it runs on) can take its image from a Settings variable:
   `DEPLOY_IMAGE: !settings` under `variables:` and
   `image: !var DEPLOY_IMAGE`, the variable holding a full reference
   pinned by digest (definition.md → Tags). `services:` is
   ignored — start databases in the script (patterns.md §12).
14. **GitHub `${{ … }}` expressions are not interpolated.** Use the
    runtime variables (`$CI_COMMIT_SHA`, `$CI_COMMIT_REF_NAME`, …);
    Pipemesh's own `${{ matrix.x }}` and `${{ params.x }}` are load-time.
    For release numbers, a pipeline job gets `$PIPEMESH_REVISION` and a
    workflow job `$PIPEMESH_RUN_NUMBER`; both only grow
    (`:r$PIPEMESH_REVISION`). Write them where a release is applied (a
    tag), never into what a build produces: a reused build keeps what it
    built then.
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

From the repository root:

```bash
npx pipemesh check
```

It sends `pipemesh.yaml` and only the files it names — what it
includes, its local components, the GitHub Actions workflows its jobs
run — to Pipemesh, which loads them with the loader the repository's
pipeline will meet, and stores nothing. It lists the files before it
sends them (`npx pipemesh check --dry-run` lists them and sends
nothing); tell the user that the check sends them to Pipemesh (the
instance `npx pipemesh instance` names, pipemesh.io unless set).

It prints the load error, if any, then warnings, then every job's
effective `job_type`, checkout and when it skips (`skip:`, from its
type) (`-q` leaves the job
list out), and exits 1 on an error. The loader stops at the first
error, so fix it and run again until the definition loads. Fix or consciously accept each warning — in
particular *reads nothing* (a build or deploy with no input runs once and then
skips every revision: list what it reads in `checkout:`, or make it a
`job_type: task`; a `job_type: pipeline` job whose `fingerprint:`
names nothing: name what it watches in `fingerprint:`, or leave
`fingerprint:` out to hand every revision over). Read the
job list against the inventory: each build checks out what it
compiles, each deploy what it runs, each task what it needs.

The check does not flag these; look for them yourself:

- flow-style collections (`[a, b]`, `{ a: b }`): rewrite them in
  block style;
- a script that names a repository file its `checkout:` leaves out:
  the enforced checkout fails the job;
- a deploy that checks out the whole repository: it redeploys on every
  commit.

It needs Node 20 or newer and network access. Without them, check by
hand against the rules in step 4 and definition.md. The same loader
runs again when the file reaches its mesh's branch,
where Pipemesh reports any load error with the file and key.

Then re-read the result against the inventory: every row is covered or
deliberately dropped; every job has a `job_type:`; every standalone
body, job and component has its `type:`; every deploy is a
`job_type: deploy` in the pipeline and consumes what it ships; test and
coverage output the old CI kept is a `reports:` key on the job that
writes it; nothing deploys from a pull request; the files are block
style throughout.

## Step 6 — Summarize

**Show it first.** `npx pipemesh preview` sends what `check` sends and
returns a link: the board this definition will draw and, when the
repository is already on Pipemesh, what it changes — jobs added,
removed or changed, and how (job type, checkout, needs, and whether what a
job runs changed, so it runs again). It needs sign-in
(`npx pipemesh login`; the user approves the code in the browser).
Anyone with the link can open it for 7 days, and it shows the job
names and the structure: say so before you make one for a private
repository. Put the link at the top of the summary and in the PR body.
While you and the user iterate on the definition, run
`npx pipemesh preview --watch` in the background instead: each save
updates the same link, so they watch one page while you edit
(`--no-browser` prints the link without opening it).

Then tell the user, concisely (see the template in `references/onboarding.md`):
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

Walk the user through turning it on in Pipemesh — sign in, connect the
GitHub App (every repository it reaches is a mesh, inactive until its
`pipemesh.yaml` lands; there is nothing to add or enable), add the secrets and
config this definition names, set up cloud trust for the exact job
identities it uses, and what to watch after merging. Follow
`references/onboarding.md`, and tailor it: list the real secret names,
name the jobs that need cloud access, and skip sections that don't
apply. Never compose an OIDC subject: they're written with ids. Once
the definition is merged and its mesh is active, read them with
`npx pipemesh identity` (or offer to), and give the user the issuer and
the exact subject lines of those jobs for their trust policies.

Offer to do the first part from the terminal: `npx pipemesh login`
(show the user the code and page it prints; they approve it in the
browser — never approve it for them; `--no-browser` only prints the
page), `npx pipemesh repos` to see
whether the repository is connected, and `npx pipemesh connect` if it
isn't (the user installs the GitHub App in the page it opens;
`--no-wait` returns at once). Merging the pull request is what turns it
on: `npx pipemesh@latest mesh` shows the mesh go from inactive to
active. Every command takes `--json`.
`npx pipemesh whoami` says who the CLI is signed in as. Running
`npx pipemesh login` again replaces the token it keeps, including a
read-only, revoked or expired one; a `PIPEMESH_TOKEN` in the
environment still wins over it, and the CLI says so.
`npx pipemesh logout` revokes the token and forgets it; when revoking
fails, it still forgets it and says where to revoke it.

## After it's on: following it, and what went out

Once its mesh is active, answer questions about it with the CLI
(add `--json` to read the answer; public repositories need no sign-in).
The commands written `npx pipemesh@latest …` below are new: an older
CLI says it doesn't know them.

| Question | Command |
| --- | --- |
| What would my edit change? | edit, `npx pipemesh check`, then `npx pipemesh preview` (`--watch` while iterating) — give the user the link before committing |
| How is it doing? | `npx pipemesh status` — each job at the latest revision, what each deploy has deployed |
| Did my merge go through? | `npx pipemesh watch --sha=<merge commit>` (or `--rev=<n>`; `--timeout=<minutes>`, default 60) — waits for the commit's revision and follows it; exit 0 settled, 1 failed, 3 waiting for a person (an approval or a hold), 4 timeout |
| What ran in a workflow? | `npx pipemesh runs [workflow]` — the latest runs and their numbers (`--limit=<n>`) |
| Why did a job fail? | `npx pipemesh logs <job>` (`--rev=<n>` for an older run, `--follow` while it runs); a workflow run's job: `npx pipemesh@latest logs <workflow> --run=<n> [--job=<job>]` — every attempt, oldest first; `--job` may be left out when the workflow has one job; `npx pipemesh runs <workflow>` lists run numbers. A hosted job whose machine was reclaimed runs again, up to three attempts (a deploy never retries on its own); `npx pipemesh@latest logs <job>` and the board's job panel say why Pipemesh failed an earlier attempt ("the job's node was lost") |
| What did a job make, and until when? | `npx pipemesh@latest artifacts <job> [--rev=<n>]` (a pipeline job) or `npx pipemesh@latest artifacts <workflow> --run=<n> [--job=<job>]` — what it produced and consumed, each with its expiry ("expires 2026-11-18", "kept: one of the job's two latest runs", "never expires", "expired 2026-10-03"), and its reports with their state |
| Is storage full? | `npx pipemesh@latest storage [--org=<path>]` — the organization (named: `--org=`, else `PIPEMESH_DEFAULT_ORG`, else `npx pipemesh org default <path>`)'s artifact and cache storage against its caps, artifact retention, cache eviction and the per-pipeline cache limit; it warns from 90% |
| Are we out of build minutes? | `npx pipemesh@latest compute [--org=<path>]` — the organization's build minutes this month against its allowance, concurrent builds and the vCPU cap, minutes by runner |
| What do I put in a cloud trust? | `npx pipemesh identity` — the issuer and each job's subject (never compose one) |
| Anything no command answers | `npx pipemesh@latest api [method] <path>` (`/api/…`; `--data=<json>`, `--data=@file` or `--data=@-` for a body) — the CLI makes the call signed in as the user and prints the answer (JSON indented, tokens masked). Only GET without asking: ask the user before any other method |
| What went to production today? | `npx pipemesh deployments --since=today` (`--all` across the user's repositories) |
| Was the fix deployed? | `npx pipemesh deployed '#<PR>'` (or a commit or branch) — per production deploy: deployed (since when), unchanged (nothing it deploys changed, so production already matches), deploying, waiting for approval, failed, rolled back, or not yet; exit 0 when every one has it |

"Production" is the deploys that say `production: true`. A commit counts as deployed
once a production deploy ran a revision that contains it: a push of
several commits makes one revision, at its head.

**Never read, print or copy the CLI's token.** A current CLI keeps it
in the system's keychain (or `~/.config/pipemesh/credentials`), never
in `config.json`; an older one kept it in `~/.config/pipemesh/config.json`.
Every command above, `pipemesh api` included, uses it for you. Don't
open those files, don't call the API with curl, and don't ask the user
for a token or for `PIPEMESH_TOKEN`; if a call needs signing in, ask the
user to run `npx pipemesh login`. The CLI prints only a token's last 4
characters (`pmk_…a1b2`).

**Limits are a Pipemesh admin's.** Storage caps, artifact retention,
cache eviction and compute limits change only in the web app, by a
Pipemesh admin (**Pipemesh admin → Storage** / **Compute**); an
organization's admins see its **Settings → Storage** read-only. The CLI
only reads them. When one is in the way:

- A job failed with "artifact storage for this organization is full":
  run `npx pipemesh@latest storage --org=<the repository's organization path>`,
  show the user the numbers, and
  suggest asking a Pipemesh admin to raise the cap or shorten
  retention. Expired artifacts are deleted every hour.
- A job is *queued · held* over a compute limit: `npx pipemesh@latest
  compute --org=<path>` shows which; it starts once its size fits or the month rolls
  over, or a Pipemesh admin raises the limit.
- A consumer failed with "consumes DIST: build/dist expired on <date> —
  re-run build at revision N to make it again":
  `npx pipemesh@latest artifacts build --rev=N` shows it. Running
  `build` at revision N again stores a fresh copy; tell the user, since
  `npx pipemesh rerun` only retries a run that failed or was canceled
  and can't do it. Then re-run the consumer (`npx pipemesh rerun <job>`
  retries its failed run; ask first).

Acting on it changes something, so ask the user first, every time
(the server refuses what they may not do, and says why):

| To | Command |
| --- | --- |
| Start a manual workflow | `npx pipemesh run <workflow>` |
| Approve or reject a waiting deploy | `npx pipemesh approve <job>` / `reject <job>` (`--rev=<n>` when several wait) |
| Stop new revisions reaching a job | `npx pipemesh hold <job> --reason="…"`; `release <job>` lets them in |
| Run a failed job again | `npx pipemesh rerun <job>`; a workflow run's failed jobs: `rerun <workflow> --run=<n>` (`--job=<job>` for one) |
| Cancel | `npx pipemesh cancel <job>` (or `<workflow> --run=<n>`); add `--yes` only once the user said so |
| Roll back | `npx pipemesh rollback <job> [--to=<n>]`: it runs the last good revision again and holds the job; add `--yes` only once the user said so (a revision whose artifacts expired isn't offered) |
| Change a storage or compute limit, retention or cache eviction | not the CLI: a Pipemesh admin, in the web app (above) |
| Report a Pipemesh problem | when a Pipemesh command or page fails in a way the definition doesn't explain (a 5xx, a hang, a load error that contradicts the docs), offer to report it; on a yes, `npx pipemesh report "<what happened, and what you expected>"` — it sends the CLI's last API calls (no bodies or credentials) and versions; give the user the report id it prints |
| Secrets and variables | `npx pipemesh secrets` / `vars` list them (secret values never come back); the user sets one with `npx pipemesh secrets set NAME` and types the value at its hidden prompt — never ask for a secret in the chat or put it in a command; `--pull-requests` lets pull-request runs read it (read-only credentials only); `npx pipemesh vars set NAME`; `secrets delete NAME` / `vars delete NAME`; `--org=<path>` (`github.com/acme`) for the organization's own level |
