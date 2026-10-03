# Choosing the shape: pipeline, workflow, or both

Pipemesh has two kinds of workload, and most repositories want both.
Picking the wrong one is the most expensive mistake in a migration, so
make this decision deliberately and ask when the evidence is mixed.

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
| Preview environments per PR | workflow `on: pull_request` | |
| A heavy CI suite that must pass before deploy | pipeline job that `delegate`s to a workflow body (one node, one verdict) | |

Rules of thumb:

- **Side effects on long-lived environments → pipeline.** Deploys,
  migrations, infrastructure applies. That is where supersede,
  "skip unless something changed", rollback and holds pay off.
- **An immutable record of one event → workflow.** A release of
  version 1.4.2 must be reproducible as "that run", not "whatever the
  pipeline was at".
- **PR checks are always a workflow.** The same body can serve both a
  PR workflow and the pipeline's build stage (`!include` it twice, or
  `delegate: { type: workflow }` from the pipeline).
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
   (`delegate: { type: github_actions }`, minimal change, Actions stays
   the compute). Mixed is fine: port the build, delegate the deploy.
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
  one pipeline; `paths:` on each job to the directories it reads.
- **Several services, one build tool that knows the graph** (Nx,
  Turborepo, Bazel): a dispatch pipeline. A `graph` job uses the build
  tool's fingerprint component (`nx/fingerprint@1`,
  `turbo/fingerprint@1`, `bazel/fingerprint@1`) and produces one entry
  per service; one dispatch job per service consumes its entry, reads
  no files (`paths: []`), and delegates `type: pipeline` to a shared
  service body with `variables: { SERVICE: <name> }`. See `patterns.md`.
- **Several services, no build graph** (plain directories): the same
  dispatch shape without the graph job — each dispatch job lists the
  service's directories (and shared libraries) in `paths:`.
- **The service is spread over several repositories** (client library
  in one, consumer in another): one pipeline declaring `repos:`; jobs
  pick their repository with `repo:`. Every declared repository must be
  added to the same Pipemesh organization.
