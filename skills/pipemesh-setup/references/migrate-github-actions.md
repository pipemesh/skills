# Migrating from GitHub Actions

Two strategies, and they mix per job. Either way the job gets a
`job_type:` — the executor is where it runs, not what it is.

- **Port** a job: its steps become a Pipemesh job's `script:` on
  Pipemesh runners. You get the full model — `produces:`/`consumes:`
  by digest, `skip: built` reuse, `cache:`, OIDC identity, and an
  enforced `checkout:`.
- **Run it on Actions**: keep the Actions workflow as the compute and
  give the Pipemesh job `github_actions: <workflow file>`, which
  dispatches the workflow and waits for it. Minimal change; Actions
  secrets, environments and marketplace actions keep working; Pipemesh
  orchestrates the promotion and shows the run's jobs as tasks. A
  deploy that runs on Actions is still `job_type: deploy`. See
  patterns.md §6.

Port by default when the steps are shell commands plus the usual setup
actions. Offer running on Actions (ask) when a workflow leans on actions
with no shell equivalent, on GitHub-specific features (environment
approvals the team wants to keep, `macos-*`/`windows-*` runners, larger
runners), or when the user wants the smallest first step.

## Workflows → workloads

| Actions `on:` | Pipemesh |
| --- | --- |
| `push:` with `branches:` `main` (build/deploy) | the **pipeline** (no trigger; it follows the default branch) |
| `push:` with `branches:` `main` (non-deploy side task) | workflow `on: push` (no `branches:` filter) |
| `pull_request` / `pull_request_target` | workflow `on: pull_request` (`targets:` = the `branches:` filter, exact names) |
| `push: tags:` (`"v*"`) / `release: published` | workflow `on: tag`, its `tags:` listing `"v*"` |
| `schedule: - cron:` | workflow `on: schedule`, same cron, quoted (UTC in both) |
| `workflow_dispatch` with `inputs` | workflow with no trigger (manual) and `inputs:` with defaults |
| `push` to other branches | not watched — ask (see decisions.md) |
| `paths:` / `paths-ignore:` on the trigger | each job's `checkout:` (what it reads): a build or deploy runs again only when those files (or what it consumes) change |
| `workflow_run`, `repository_dispatch`, `issue_comment` … | no equivalent; leave in Actions |
| `merge_group` | nothing to do: PR workflows run on merge-queue groups |

A workflow that runs on both `pull_request` and `push: main` (the usual
`ci.yml`) splits in two: its jobs become the PR `checks` workflow, and
the pipeline's build stage runs the same checks — either by running
the same `type: workflow` body as one node (`job_type: workflow` with
`body: !ref checks`) or by folding the tests into the pipeline's build
job.

## Jobs and steps

| Actions | Pipemesh |
| --- | --- |
| `jobs.<id>` | a job with a `job_type:` (decisions.md → *Choosing each job's job_type*); ids become lowercase `[a-z0-9_-]` (`deploy-staging` → `deploy_staging` or keep the hyphen) |
| `needs:` | `needs:` — plus `consumes:` where an output is passed |
| `runs-on: ubuntu-*` | nothing (hosted runners); pick `image:` for the toolchain |
| `runs-on:` self-hosted labels / `macos-*` / `windows-*` | `tags:` (the first is your runners' queue) and a registered runner — or run the job on Actions (`github_actions:`) |
| `container: image` | `image:` |
| `services:` | not supported (ignored): start it in the script — patterns.md §12 |
| `steps: - run:` | lines of `script:` (one shell, `set -e`; `working-directory:` → `cd`; `shell: python` → `python - <<'EOF'`) |
| `env:` (workflow/job/step) | `variables:` (body or job); step-level env → `export` in the script |
| `if:` | usually disappears: the job goes in the workload where it applies. Branch/event conditions → which workload; path conditions → `checkout:`; `if: failure()` steps → `trap` |
| `strategy.matrix` | `matrix:`; `as: workflow` when variants pass/fail together (CI), `as: jobs` for independent lanes; `include:`/`exclude:` → reshape or separate jobs; `fail-fast` has no equivalent |
| `continue-on-error: true` | `allow_failure: true` |
| `timeout-minutes: 30` | `timeout_seconds: 1800` (default 3600) |
| `concurrency:` | not needed: one revision at a time per pipeline job, newer ones supersede |
| `environment: production` | `job_type: deploy` in the pipeline (the job name says where); approval rules → onboarding.md → Holding production. A PR preview environment is a `job_type: task` in the PR workflow |
| `outputs:` / `$GITHUB_OUTPUT` | a file entry (`meta: out/meta.env` under `produces:`, consumer `source "$PIPEMESH_BUILD_META"`), or an `oci` entry for an image ref |
| `permissions: id-token: write` | not needed: every Pipemesh job can request identity tokens |
| reusable workflows (`uses: ./.github/workflows/x.yml`) | a `type: workflow` body in `.pipemesh/` run by a `job_type: workflow` job or reached with `!ref`/`!include`, or a component for parameterized jobs |
| composite actions in the repo | a `type: component` in `.pipemesh/components/` (`setup:` for script fragments) |

## Common actions

| Action | Pipemesh |
| --- | --- |
| `actions/checkout` | the job's `checkout:` — a job starts in a checkout of exactly what it lists. A plain `actions/checkout` is `checkout: true` (a build's default); its `sparse-checkout:` directories become the list; a job with no checkout step is `checkout: false` (a task's or deploy's default). History is always complete, so `fetch-depth: 0` needs nothing and `git merge-base` works on PR runs against `origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME` |
| `actions/setup-node` / `setup-python` / `setup-go` / `setup-java` / `ruby/setup-ruby` | `image:` with that version (`node:22-bookworm`, `python:3.12-bookworm`, `golang:1.23-bookworm`, `gradle:8-jdk21`, `ruby:3.3-bookworm`); read `.nvmrc`/`node-version-file` for the version |
| `cache: npm` / `pip` / `gradle` on a setup action, `actions/cache` | `cache:` keyed on the lockfile checksum, paths inside the workspace, one definition shared by the PR and default-branch jobs (artifacts-and-caching.md → Cache) |
| `pnpm/action-setup` | `corepack enable` in the script (or a `setup:` step `uses: node/pnpm@1`) |
| `actions/upload-artifact` → `actions/download-artifact` | `produces:` on the producer → `consumes:` on the consumer |
| `aws-actions/configure-aws-credentials` with `role-to-assume` | a `setup:` step `uses: aws/role@1` `with:` `arn` and `region` (below); the role's trust policy gains Pipemesh's issuer and subjects (onboarding.md §4) |
| `aws-actions/configure-aws-credentials` with access keys | prefer `aws/role@1`; otherwise `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` under `secrets:` |
| `aws-actions/amazon-ecr-login` | `aws ecr get-login-password … \| docker login …` in the script |
| `docker/login-action` | `docker login` in the script with a secret |
| `docker/setup-buildx-action`, `setup-qemu-action` | nothing on the default job image (buildx included) |
| `docker/build-push-action` | `docker build` + `docker push` in the script, then `pipemesh produce oci <key> --ref <repo> --digest <sha256>` (artifacts-and-caching.md → Images). Hosted runners are arm64: GitHub's `ubuntu-latest` built amd64 — check the target |
| `azure/setup-helm`, `azure/setup-kubectl` | install the pinned binary in the script, or use an image that has it |
| `google-github-actions/auth` (WIF) | a token from `$PIPEMESH_ID_TOKEN_REQUEST_URL` with the pool's audience, exchanged with `gcloud iam workload-identity-pools create-cred-config` — or a key in `secrets:` |
| `hashicorp/setup-terraform` | install Terraform in the script or use a Debian-based image with it |
| `slackapi/slack-github-action` (webhook) | a `setup:` step `uses: slack/message@1` `with:` `text:`, and `SLACK_WEBHOOK` under `secrets:` — or a `curl` |
| `codecov/codecov-action` | a `setup:` step `uses: codecov/upload@1`, and `CODECOV_TOKEN` under `secrets:` |
| `dorny/paths-filter` + `if:` | `checkout:` on each job (and a `job_type: pipeline` job per service in monorepos — patterns.md §7) |
| `superfly/flyctl-actions` | `curl -L https://fly.io/install.sh \| sh` then `~/.fly/bin/flyctl deploy …`, with `FLY_API_TOKEN` under `secrets:` |
| `vercel deploy` / `amondnet/vercel-action` | `npx vercel deploy --prebuilt --prod --token "$VERCEL_TOKEN"` with secrets `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` |
| `softprops/action-gh-release` | `gh release create` with a `GH_TOKEN` secret (the Pipemesh App can't write to the repository) |
| anything else | read its `action.yml`: port the commands it runs, or run the job on Actions (`github_actions:`); never guess silently — leave `# TODO(pipemesh): …` |

The `role-to-assume` translation, in full:

```yaml
deploy_staging:
  job_type: deploy
  stage: staging
  setup:
    - uses: aws/role@1
      with:
        arn: arn:aws:iam::123456789012:role/deploy-staging
        region: eu-west-1
  checkout:
    - deploy
  script: ./deploy/deploy.sh staging
```

## Expressions

`${{ … }}` is not interpolated by Pipemesh (except its own
`${{ matrix.x }}` and component `${{ params.x }}`). Replace:

| Actions | Pipemesh |
| --- | --- |
| `${{ github.sha }}` | `$CI_COMMIT_SHA` |
| `${{ github.ref_name }}` | `$CI_COMMIT_REF_NAME` (PR: source branch; tag run: the tag) |
| `${{ github.head_ref }}` / `github.base_ref` | `$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME` / `$CI_MERGE_REQUEST_TARGET_BRANCH_NAME` |
| `${{ github.event.pull_request.number }}` | `$CI_MERGE_REQUEST_IID` |
| `${{ github.workspace }}` | `$PIPEMESH_WORKSPACE` (or `$PWD` at the start of the script) |
| `${{ github.repository }}` | write it literally (`acme/shop`) |
| `${{ github.run_id }}` / `run_number` | no equivalent; use `$CI_COMMIT_SHORT_SHA` for unique names |
| `${{ secrets.X }}` | `$X`, with `X` listed in the job's `secrets:` |
| `${{ vars.X }}` | `$X`, with `X` listed in the job's `config:` |
| `${{ env.X }}` | `$X` |
| `${{ inputs.x }}` | `$x` (workflow inputs keep their names) |
| `${{ matrix.x }}` | `${{ matrix.x }}` (same syntax, load time) |
| `${{ needs.job.outputs.x }}` | consume an entry from `job` (see `outputs:` above) |
| `${{ steps.id.outputs.x }}` | a shell variable in the same script |

`GITHUB_TOKEN` doesn't exist in Pipemesh jobs. Jobs that call the
GitHub API (`gh`, release uploads, comments) need a token stored as a
secret — ask the user whether that job should stay in Actions instead.

## Secrets

List every `secrets.X` the ported jobs use; each becomes a Pipemesh
secret named `X` (names are `UPPER_SNAKE_CASE`; rename if needed) set
in the repository's Settings in Pipemesh, and declared on exactly the
jobs that use it. Environment-scoped secrets (staging vs production
values of one name) become two names (`STAGING_DATABASE_URL`,
`PRODUCTION_DATABASE_URL`). Values never go in the definition. Cloud
credentials that were static keys are a good moment to move to OIDC —
suggest it, don't force it.

## Jobs that run on Actions: what changes in `.github/workflows`

Only if the user chose to keep a workflow on Actions:

- add `workflow_dispatch` with the inputs `pipemesh_sha` and
  `pipemesh_run` (plus one per definition input, ≤ 8);
- check out `${{ inputs.pipemesh_sha }}` in every job;
- put `${{ inputs.pipemesh_run }}` in `run-name:`;
- remove the `push`/`pull_request` triggers that Pipemesh now owns, so
  the workflow doesn't run twice;
- **the Pipemesh job's checkout is read from this file**:
  `actions/checkout` without `sparse-checkout:` makes it `true` (the
  job runs on every revision — wrong for a deploy); with
  `sparse-checkout:` (directories, cone mode) it is those directories
  plus the root files; no checkout of this repository makes it
  nothing. The workflow files are always part of the job's inputs.
  Narrow the deploy workflows' checkout to what they read, or set
  `checkout:` on the Pipemesh job (it overrides the derivation);
- artifacts the Pipemesh side needs are uploaded with
  `actions/upload-artifact` under the entry's key, and declared on the
  Pipemesh job as `<key>: file` under `produces:`;
- entries the run needs from Pipemesh (an image digest, a bundle) come
  from `- uses: pipemesh/consume@v1`, with `permissions:` granting
  `id-token: write` and `contents: read`, after the Pipemesh job
  declares them in `consumes:`.

`npx pipemesh check` sends the workflow file along with the definition
and prints the checkout Pipemesh reads from it. Check the inputs the
job passes against the workflow's `workflow_dispatch:` inputs yourself.
