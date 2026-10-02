---
name: pipemesh-setup
description: Set up Pipemesh for a repository. Scans the existing CI/CD configuration (GitHub Actions, GitLab CI and other CI systems, Makefiles, deploy scripts, Dockerfiles, Nx/Turborepo/Bazel monorepos) and writes a pipemesh.yaml with the right pipelines and workflows, build artifacts passed by produces/consumes, caching, secrets and deploy stages; asks clarifying questions when the shape is unclear (pipeline vs workflow, where jobs run, environments); validates the manifest; offers to open a pull request; and explains how to add the repository in Pipemesh. Use this whenever the user mentions Pipemesh or pipemesh.yaml, wants to migrate or port their CI/CD to Pipemesh, onboard a repo to Pipemesh, or convert GitHub Actions or GitLab CI workflows into Pipemesh pipelines — and also to review, fix or extend an existing pipemesh.yaml.
---

# Pipemesh setup

Pipemesh (https://pipemesh.io) is a CI/CD control plane. A repository
declares what it runs in one file, `pipemesh.yaml`, at its root:

- a **pipeline** — long-lived; every commit on the default branch
  becomes a *revision* that promotes job by job (build → staging →
  production). Each job remembers what it last ran, so the board answers
  "what is deployed right now";
- **workflows** — one-shot runs per trigger: pull requests, tags,
  schedules, pushes, manual runs.

Your job: read how this repository builds, tests, releases and deploys
today, and produce a `pipemesh.yaml` (plus `.pipemesh/*.yaml` bodies
when it grows) that does the same work the Pipemesh way — then validate
it, offer a pull request, and tell the user how to turn it on.

Work through the steps in order. The reference files hold the detail;
read each one when its step comes up, not all up front.

| File | Read it when |
| --- | --- |
| `references/decisions.md` | Step 3 — choosing pipeline vs workflows, and what to ask |
| `references/manifest.md` | Step 4 — the grammar (always, before writing YAML) |
| `references/artifacts-and-caching.md` | Step 4 — produces/consumes, images, cache, remote build caches, runtime variables |
| `references/patterns.md` | Step 4 — worked shapes from the public demos |
| `references/migrate-github-actions.md` | Step 4 — the repo has `.github/workflows/` |
| `references/migrate-gitlab-ci.md` | Step 4 — the repo has `.gitlab-ci.yml` |
| `references/migrate-other-ci.md` | Step 4 — any other CI system, or none |
| `references/onboarding.md` | Steps 6–8 — summary, pull request, enabling the repo |
| `scripts/check_manifest.py` | Step 5 — validate before showing the result |

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
  default branch (`git symbolic-ref --short refs/remotes/origin/HEAD`,
  falling back to `main`/`master`), and whether a `pipemesh.yaml`
  already exists — if it does, this is an edit: keep job names (they
  carry history; see `was:` in manifest.md) and change only what the
  user asked for.
- Pipemesh connects to **GitHub** today. If the remote is GitLab,
  Bitbucket or self-hosted, say so early: you can still write the
  manifest, but the repository can't be enabled until support lands.

## Step 2 — Inventory what the CI does

Before writing anything, build a table (for yourself; show it to the
user in step 6) with one row per existing job or workflow:

| existing job | trigger | purpose | inputs it reads | outputs it hands on | secrets/vars | where it runs | target env |

Purpose is one of: lint/test, build, package/image, release/publish,
deploy, infra apply, scheduled maintenance, notification. Note how
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

## Step 4 — Write the manifest

Read `references/manifest.md` first, then the migration reference for
the CI system you found, `references/artifacts-and-caching.md`, and the
closest shape in `references/patterns.md`.

The rules that most often go wrong — they differ from other CI systems:

1. **`stage:` does not order execution.** A job waits only for the jobs
   in its `needs:` and the producers of what it `consumes:`. Translate
   stage order into explicit `needs:` edges.
2. **Only the default branch is watched.** The pipeline follows it (no
   trigger; one per repository, registered as `pipeline`). Pull
   requests, tags, schedules and manual runs are workflows. Pushes to
   other branches start nothing — ask when the old CI deploys from one.
3. **Outputs are declared entries, handed only to who names them.** The
   producer declares `produces: { dist: dist }`; each consumer declares
   `consumes: [build/dist]` and finds the files at the same path (and
   the path in `$PIPEMESH_BUILD_DIST`). `needs:` orders but passes
   nothing. Deploys consume what the build produced — never rebuild.
4. **Say what each job reads.** `paths:` absent = the whole repository
   (runs on every commit). Builds: list their inputs and use
   `skip: built`. Deploys: `consumes:` the artifact plus
   `paths: [<deploy scripts/charts>]`, so they run only when what they
   ship changed. Never put `skip: unchanged` in a workflow body.
5. **Images you deploy are built and pushed in the script**, then
   recorded with `pipemesh produce oci <key> --ref <repo> --digest <sha256>`
   and deployed by digest. `publish:` (no `repo:`) is only for CI images
   your own jobs run in via `image_from:`. Hosted runners are **arm64**:
   what they build is arm64 unless cross-built — check the deploy
   target's architecture and ask if it's amd64.
6. **Pull-request runs never save caches; they restore what the
   default-branch jobs saved with the same cache `paths:`.** Key on
   `${checksum:<lockfile>}`, keep paths inside the workspace, and give
   the PR job and the pipeline job that run the same install one shared
   cache definition (`cache: !include .pipemesh/npm-cache.yaml`).
   Remote build caches (Nx Cloud, Turborepo, BuildBuddy) write from the
   default branch only.
7. **Reuse with `!ref`, `!include` and components**, never YAML
   anchors or `extends`. Every root key must be reachable from
   `pipemesh:`.
8. **Secrets are named, not inlined.** List them per job in
   `secrets: [NAME]` (non-secret settings in `config: [NAME]`,
   `UPPER_SNAKE_CASE`); values are set in Pipemesh. Pull-request runs
   don't receive secrets unless a secret allows it. Prefer OIDC to
   stored cloud keys: `setup: [{ uses: aws/role@1, with: { arn, region } }]`.
9. **Images are public and need bash, git, curl and tar** (Debian-based
   tags like `node:22-bookworm`; not Alpine, distroless or most
   `-slim`). No `image:` = Pipemesh's job image (bash, git, curl, tar,
   jq, AWS CLI, Docker + buildx, Node 20, Python 3.9, Java 25) — pin a
   toolchain image when the project needs other versions. `services:` is
   ignored — start databases in the script (patterns.md §12).
10. **GitHub `${{ … }}` expressions are not interpolated.** Use the
    runtime variables (`$CI_COMMIT_SHA`, `$CI_COMMIT_REF_NAME`, …);
    Pipemesh's own `${{ matrix.x }}` and `${{ params.x }}` are load-time.
    Job names are lowercase `[a-z0-9_-]`; quote crons; the default job
    timeout is one hour (`timeout_seconds:` for longer).

Don't invent what you can't see. Keep the project's own commands and
scripts (the deploy script is the source of truth for *how* to deploy).
When a step has no faithful translation (an unusual marketplace action,
a vendor plugin), either delegate that work to the existing GitHub
Actions workflow or leave a precise `# TODO(pipemesh): …` comment in the
script and list it in the summary — never a silent approximation.

Prefer a small `pipemesh.yaml` that registers workloads and `!include`s
bodies from `.pipemesh/` once there is more than one body or the file
passes ~150 lines. Put the schema modeline at the top of each file.
Comment the non-obvious decisions in the YAML itself (why a deploy has
`paths: [deploy]`, why a build has `skip: built`), briefly.

## Step 5 — Validate

Run the bundled checker from the repository root:

```bash
python3 <this skill's directory>/scripts/check_manifest.py .
```

It resolves `!include`/`!ref` like the loader, and reports what the
loader would reject (ERROR) and likely mistakes (WARN). Fix every
error; fix or consciously accept each warning. If PyYAML is missing,
`uv run --with pyyaml python3 …` or `pip install pyyaml`; if Python
isn't available, check by hand against the rules in step 4 and
manifest.md. The checker mirrors the loader's documented rules; the
authoritative check happens when the file reaches the default branch of
an enabled repository, where Pipemesh reports any load error with the
file and key.

Then re-read the result against the inventory: every row is covered or
deliberately dropped; every deploy consumes what it ships; nothing
deploys from a pull request.

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
config this manifest names, set up cloud trust for the exact job
identities it uses, and what to watch after merging. Follow
`references/onboarding.md`, and tailor it: list the real secret names,
compute the real OIDC subjects for this repository's jobs, and skip
sections that don't apply.
