# Choosing the shape: pipeline, workflow, or both — and each job's kind

Pipemesh has two kinds of workload, and most repositories want both.
Picking the wrong one is the most expensive mistake in a migration, so
make this decision deliberately and ask when the evidence is mixed.
Then every job gets a kind (see *Choosing each job's kind* below).

## The two kinds

**Pipeline** — long-lived. Every commit on the repository's default
branch becomes a *revision* that promotes job by job through the stages.
Each job remembers which revision it last ran, so the board answers
"what is in staging right now". A newer revision supersedes older ones
queued behind a slow or failed job. Rollback, promotion holds and DORA
metrics live here. One pipeline per repository, registered as
`pipeline`. A pipeline takes no triggers: the default branch *is* its
trigger.

**Workflow** — one-shot. A trigger (pull request, tag, push, schedule,
manual click, or a pipeline job delegating to it) starts a *run* of the
whole DAG, which settles with one verdict. The run is the record. Any
number of workflows per repository.

## Mapping what the repo does today

| What the existing CI does | Kind | Trigger |
| --- | --- | --- |
| Deploys a service/app to environments on merge to the default branch | pipeline | (default branch) |
| Build + test on every push to the default branch, feeding a deploy | pipeline (the `build` stage) | (default branch) |
| Build + test on the default branch with **no** deploy anywhere | ask — see below | |
| Lint/test/build on pull requests | workflow | `on: pull_request` |
| Lint/test on pushes to any branch (not just PRs) | workflow `on: pull_request` — feature-branch pushes without a PR don't run | |
| Publishes a library, CLI, SDK, chart or image on a tag | workflow | `on: tag`, `tags: ["v*"]` |
| Nightly / scheduled jobs (sweeps, base-image refresh, cleanup) | workflow | `on: schedule`, quoted `cron:` |
| `workflow_dispatch` / "Run pipeline" buttons with inputs | workflow, no triggers (manual-only), `inputs:` | |
| Deploys from a non-default branch (`release/*`, `prod`) | ask — Pipemesh watches one branch per repository, the one it was added with (by name, its default branch). Options: release by tag (`on: tag`), a manual workflow with an input naming what to deploy, or fold that branch's flow into the main pipeline | |
| Preview environments per PR | workflow `on: pull_request`; the preview deploy is a `kind: task` there (a deploy is pipeline-only) | |
| A heavy CI suite that must pass before deploy | a `kind: workflow` job in the pipeline running the suite's body (one node, one verdict) | |

Rules of thumb:

- **Side effects on long-lived environments → pipeline.** Deploys,
  migrations, infrastructure applies. That is where supersede,
  "skip unless something changed", rollback and holds pay off.
- **An immutable record of one event → workflow.** A release of
  version 1.4.2 must be reproducible as "that run", not "whatever the
  pipeline was at".
- **PR checks are always a workflow.** The same body can serve both a
  PR workflow and the pipeline's build stage (`!include` it twice, or
  a `kind: workflow` job with `body:` in the pipeline).
- **A library that publishes on merge to main** (continuous release,
  no tags) is a judgment call: a pipeline gives "what version is
  published right now" and skips publishes when nothing changed; a
  workflow `on: push` gives one record per publish. Ask.

## When to ask (and what)

Ask only when the answer changes the file you write. Batch the
questions (at most four at once — pick the ones that matter most), offer concrete options, and lead with
your recommendation and the evidence for it. Good triggers for a
question:

1. **Default-branch CI with no deploy.** "Your main-branch CI builds and
   tests but deploys nothing. Should it be a pipeline (a live board of
   main, with build reuse) or just PR checks plus a push workflow?"
   Recommend the pipeline when there is any artifact (image, bundle,
   package) that something downstream will eventually ship.
2. **Where the work runs.** When existing GitHub Actions workflows lean
   on marketplace actions that have no shell equivalent, use
   proprietary runners, or the team wants a small first step: offer
   *port to Pipemesh jobs* (hosted runners, full artifact/caching model)
   versus *keep the Actions workflows and let Pipemesh orchestrate them*
   (`github_actions: <file>` on the job, minimal change, Actions stays
   the compute; the job keeps its kind — a deploy on Actions is a
   `kind: deploy`). Mixed is fine: port the build, run the deploy on
   Actions.
3. **Environments and order.** If the deploy targets are ambiguous
   (several environment names, a matrix of regions, conditions on
   branch names), confirm the promotion order: e.g. staging → production,
   or per-region lanes.
4. **Approvals.** If production is gated today (GitHub environment with
   required reviewers, `when: manual`, Jenkins `input`), tell the user
   how Pipemesh holds promotions (see `onboarding.md` → Holding
   production) and confirm that is acceptable before dropping the gate
   from the config.
5. **Monorepo granularity.** Several deployable services in one repo:
   one pipeline with path-filtered jobs, or a dispatch pipeline with a
   child pipeline per service (each with its own board)? Recommend the
   child pipelines when services deploy independently and there are
   more than two or three of them.
6. **Image architecture.** Hosted runners are arm64. When the
   repository builds container images or native binaries that run on
   amd64 machines, ask how to build them — cross-compile, emulate with
   QEMU, deploy arm64, or build elsewhere (artifacts-and-caching.md →
   Images) — instead of silently producing arm64 artifacts.
7. **What happens to the old CI.** Keep it running side by side for a
   while (safest; recommended), convert Actions workflows to be
   dispatched by Pipemesh, or remove them in the same PR.

Do not ask about things you can read from the repository (language,
package manager, test command, lockfile, Dockerfile location, branch
names) or things with a conventional default (stage names, job names,
cache keys). Make the call and say what you chose in the summary.

When the harness has a structured question tool (Claude Code's
`AskUserQuestion`), use it; otherwise ask in plain text with numbered
options and say which one you would pick. If the user is not available
to answer (a non-interactive run), take the recommended option and list
the assumption at the top of the summary.

## Monorepos and several repositories

- **Single deployable, many packages** (a web app plus internal libs):
  one pipeline; `checkout:` on each job lists the directories it reads
  (and the root files its tools read), so a change to one package
  re-runs only the jobs that read it.
- **Several services, one build tool that knows the graph** (Nx,
  Turborepo, Bazel): a dispatch pipeline. A `graph` job (a `build`
  that checks out the whole tree) uses the build tool's fingerprint
  component (`nx/fingerprint@1`, `turbo/fingerprint@1`,
  `bazel/fingerprint@1`) and produces one entry per service; one
  `kind: pipeline` job per service consumes its entry and runs a shared
  service body with `variables: { SERVICE: <name> }`. It checks out
  nothing, so it hands the revision over only when that entry changed.
  See `patterns.md`.
- **Several services, no build graph** (plain directories): the same
  dispatch shape without the graph job — each `kind: pipeline` job
  lists the service's directories (and shared libraries) in `checkout:`,
  which checks nothing out and only decides when the revision is handed
  over.
- **The service is spread over several repositories** (client library
  in one, consumer in another): one pipeline declaring `repos:`; jobs
  pick their repository with `repo:`. Every declared repository must be
  added to the same Pipemesh organization.

## Choosing each job's kind

The kind says what a job is. It sets two defaults — what the job checks
out and when it may skip — and where it may appear. Decide it from what
the job's commands do, not from its name:

| What the job does | kind | `checkout:` default | `skip:` default | where |
| --- | --- | --- | --- | --- |
| compiles, tests, lints, type-checks, builds an image or bundle, computes build-graph fingerprints — reads the source and is hermetic | `build` | `true` | `built` | pipelines, workflows |
| signs, packages, converts or scans what it consumes, reading no source | `build` with `checkout: false` | `false` | `built` | pipelines, workflows |
| ships to an environment or to users: deploy what it consumes, migrate, apply infrastructure, release a package from the pipeline | `deploy` | `false` | `unchanged` | **pipelines only** |
| anything that must run on every revision: smoke tests against a live URL, notifications, checks that read pull-request context (a merge-base diff, `nx affected`, commit-message lint), a PR preview deploy, a tag release in a workflow | `task` (no `kind:` means this) | `false` | `never` | pipelines, workflows |
| runs a body (a CI suite, the PR checks) as one node and waits for its verdict | `workflow` | its jobs' combined | `built` if all its jobs are builds, else `never` | pipelines, workflows |
| hands the revision to a child pipeline (one per service in a monorepo) | `pipeline` | `false` (it checks nothing out; a list only decides when to hand over) | `unchanged` | **pipelines only** |

What the skip policies mean: `built` reuses the outputs of any earlier
successful run with the same fingerprint (*reused*); `unchanged` skips
when the fingerprint equals the job's own last success (*no changes*,
pipelines only); `never` always runs. The fingerprint covers the job's
definition, the files it checks out, the digests of what it consumes,
its parameters and the versions of the secrets and config it declares.

Judgment calls:

- **Tests are builds** when they depend only on the files they check
  out (unit, integration against services started in the script). A
  test that reads something outside the repository and its consumes —
  a live endpoint, the merge base, the date — is a `task`.
- **A test of an artifact** (it consumes the bundle and checks out
  nothing) is a `build` with `checkout: false`: a bundle it already
  tested reuses that run.
- **Building and pushing an image** that a later deploy ships by
  digest is a `build` (it's an output, like a bundle). **Releasing to
  users** from the pipeline — `npm publish`, a PyPI upload, a
  component or chart release — is a `deploy` (it runs when what it
  ships changed). The same release in a tag workflow is a `task` (a
  workflow has no deploys).
- **A build that reads only part of the tree** keeps `kind: build` and
  narrows `checkout:`. A wrong narrowing fails the job on the missing
  file, which is the point: list every root file it reads.
- **A deploy reads its scripts, not the source**: `checkout: [deploy]`
  (or `[charts/app, scripts/deploy.sh]`) plus `consumes:` for what it
  ships. A deploy that checks out the whole repository redeploys on
  every commit.
- **Override a default only on purpose**, with a comment:
  `kind: task` + `skip: built` (a costly check whose inputs are all
  checked out), `kind: deploy` + `skip: never` (always re-apply).
- **A job that reads nothing** (no checkout, no consumes, no secrets
  or config, no `image_from`, no `repos:`) and still skips runs once
  and then shows *no changes* forever: give it its inputs or
  `skip: never`. The checker warns.
