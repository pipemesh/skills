# Migrating from GitHub Actions

Two strategies, and they mix per job:

- **Port** a job: its steps become a Pipemesh job's `script:` on
  Pipemesh runners. You get the full model — `produces:`/`consumes:`
  by digest, `skip: built` reuse, `cache:`, OIDC identity.
- **Delegate** a job: keep the Actions workflow as the compute and let
  a pipeline job dispatch it (`delegate: { type: github_actions }`).
  Minimal change; Actions secrets, environments and marketplace actions
  keep working; Pipemesh orchestrates the promotion and shows the run's
  jobs as tasks. See patterns.md §6.

Port by default when the steps are shell commands plus the usual setup
actions. Offer delegation (ask) when a workflow leans on actions with no
shell equivalent, on GitHub-specific features (environment approvals
the team wants to keep, `macos-*`/`windows-*` runners, larger runners),
or when the user wants the smallest first step.

## Workflows → workloads

| Actions `on:` | Pipemesh |
| --- | --- |
| `push: branches: [main]` (build/deploy) | the **pipeline** (no trigger; it follows the default branch) |
| `push: branches: [main]` (non-deploy side task) | workflow `on: push` (no `branches:` filter) |
| `pull_request` / `pull_request_target` | workflow `on: pull_request` (`targets:` = `branches:` filter, exact names) |
| `push: tags: ["v*"]` / `release: published` | workflow `on: tag`, `tags: ["v*"]` |
| `schedule: - cron:` | workflow `on: schedule`, same cron, quoted (UTC in both) |
| `workflow_dispatch` with `inputs` | workflow with no trigger (manual) and `inputs:` with defaults |
| `push` to other branches | not watched — ask (see decisions.md) |
| `paths:` / `paths-ignore:` on the trigger | job-level `paths:` (what each job reads) |
| `workflow_run`, `repository_dispatch`, `issue_comment` … | no equivalent; leave in Actions |
| `merge_group` | nothing to do: PR workflows run on merge-queue groups |

A workflow that runs on both `pull_request` and `push: main` (the usual
`ci.yml`) splits in two: its jobs become the PR `checks` workflow, and
the pipeline's build stage runs the same checks — either by including
the same body (`delegate: { type: workflow, params: { body: !ref checks } }`)
or by folding the tests into the pipeline's build job.

## Jobs and steps

| Actions | Pipemesh |
| --- | --- |
| `jobs.<id>` | a job; ids become lowercase `[a-z0-9_-]` (`deploy-staging` → `deploy_staging` or keep the hyphen) |
| `needs:` | `needs:` — plus `consumes:` where an output is passed |
| `runs-on: ubuntu-*` | nothing (hosted runners); pick `image:` for the toolchain |
| `runs-on: [self-hosted, gpu]` / `macos-*` / `windows-*` | `tags: [<queue>]` and a registered runner — or delegate the job to Actions |
| `container: image` | `image:` |
| `services:` | not supported (ignored): start it in the script — patterns.md §12 |
| `steps: - run:` | lines of `script:` (one shell, `set -e`; `working-directory:` → `cd`; `shell: python` → `python - <<'EOF'`) |
| `env:` (workflow/job/step) | `variables:` (body or job); step-level env → `export` in the script |
| `if:` | usually disappears: the job goes in the workload where it applies. Branch/event conditions → which workload; path conditions → `paths:`; `if: failure()` steps → `trap` |
| `strategy.matrix` | `matrix:`; `as: workflow` when variants pass/fail together (CI), `as: jobs` for independent lanes; `include:`/`exclude:` → reshape or separate jobs; `fail-fast` has no equivalent |
| `continue-on-error: true` | `allow_failure: true` |
| `timeout-minutes: 30` | `timeout_seconds: 1800` (default 3600) |
| `concurrency:` | not needed: one revision at a time per pipeline job, newer ones supersede |
| `environment: production` | the job name/stage; approval rules → onboarding.md → Holding production |
| `outputs:` / `$GITHUB_OUTPUT` | a file entry (`produces: { meta: out/meta.env }`, consumer `source "$PIPEMESH_BUILD_META"`), or an `oci` entry for an image ref |
| `permissions: id-token: write` | not needed: every Pipemesh job can request identity tokens |
| reusable workflows (`uses: ./.github/workflows/x.yml`) | a body in `.pipemesh/` reached with `!ref`/`!include`, or a component for parameterized jobs |
| composite actions in the repo | a component in `.pipemesh/components/` (`setup:` for script fragments) |

## Common actions

| Action | Pipemesh |
| --- | --- |
| `actions/checkout` | nothing — jobs start in a checkout of the revision. `fetch-depth: 0` / `git merge-base` work on PR runs against `origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME` |
| `actions/setup-node` / `setup-python` / `setup-go` / `setup-java` / `ruby/setup-ruby` | `image:` with that version (`node:22-bookworm`, `python:3.12-bookworm`, `golang:1.23-bookworm`, `gradle:8-jdk21`, `ruby:3.3-bookworm`); read `.nvmrc`/`node-version-file` for the version |
| `cache: npm` / `pip` / `gradle` on a setup action, `actions/cache` | `cache:` keyed on the lockfile checksum, paths inside the workspace, one definition shared by the PR and default-branch jobs (artifacts-and-caching.md → Cache) |
| `pnpm/action-setup` | `corepack enable` in the script (or `setup: [{ uses: node/pnpm@1 }]`) |
| `actions/upload-artifact` → `actions/download-artifact` | `produces:` on the producer → `consumes:` on the consumer |
| `aws-actions/configure-aws-credentials` with `role-to-assume` | `setup: [{ uses: aws/role@1, with: { arn: …, region: … } }]`; the role's trust policy gains Pipemesh's issuer and subjects (onboarding.md §4) |
| `aws-actions/configure-aws-credentials` with access keys | prefer `aws/role@1`; otherwise `secrets: [AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY]` |
| `aws-actions/amazon-ecr-login` | `aws ecr get-login-password … \| docker login …` in the script |
| `docker/login-action` | `docker login` in the script with a secret |
| `docker/setup-buildx-action`, `setup-qemu-action` | nothing on the default job image (buildx included) |
| `docker/build-push-action` | `docker build` + `docker push` in the script, then `pipemesh produce oci <key> --ref <repo> --digest <sha256>` (artifacts-and-caching.md → Images). Hosted runners are arm64: GitHub's `ubuntu-latest` built amd64 — check the target |
| `azure/setup-helm`, `azure/setup-kubectl` | install the pinned binary in the script, or use an image that has it |
| `google-github-actions/auth` (WIF) | a token from `$PIPEMESH_ID_TOKEN_REQUEST_URL` with the pool's audience, exchanged with `gcloud iam workload-identity-pools create-cred-config` — or a key in `secrets:` |
| `hashicorp/setup-terraform` | install Terraform in the script or use a Debian-based image with it |
| `slackapi/slack-github-action` (webhook) | `setup: [{ uses: slack/message@1, with: { text: … } }]` with `secrets: [SLACK_WEBHOOK]`, or a `curl` |
| `codecov/codecov-action` | `setup: [{ uses: codecov/upload@1 }]` with `secrets: [CODECOV_TOKEN]` |
| `dorny/paths-filter` + `if:` | `paths:` on each job (and dispatch per service in monorepos — patterns.md §7) |
| `superfly/flyctl-actions` | `curl -L https://fly.io/install.sh \| sh` then `~/.fly/bin/flyctl deploy …` with `secrets: [FLY_API_TOKEN]` |
| `vercel deploy` / `amondnet/vercel-action` | `npx vercel deploy --prebuilt --prod --token "$VERCEL_TOKEN"` with secrets `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` |
| `softprops/action-gh-release` | `gh release create` with a `GH_TOKEN` secret (the Pipemesh App can't write to the repository) |
| anything else | read its `action.yml`: port the commands it runs, or delegate the job to Actions; never guess silently — leave `# TODO(pipemesh): …` |

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

## Delegated workflows: what changes in `.github/workflows`

Only if the user chose delegation for a workflow:

- add `workflow_dispatch` with the inputs `pipemesh_sha` and
  `pipemesh_run` (plus one per definition input, ≤ 8);
- check out `${{ inputs.pipemesh_sha }}` in every job;
- put `${{ inputs.pipemesh_run }}` in `run-name:`;
- remove the `push`/`pull_request` triggers that Pipemesh now owns, so
  the workflow doesn't run twice;
- artifacts the Pipemesh side needs are uploaded with
  `actions/upload-artifact` under the entry's key, and declared as
  `produces: { <key>: file }` on the delegating job;
- entries the run needs from Pipemesh (an image digest, a bundle) come
  from `- uses: pipemesh/consume@v1` with
  `permissions: { id-token: write, contents: read }`, after the
  delegating job declares them in `consumes:`.

The checker (scripts/check_definition.py) verifies the dispatch inputs
when the workflow file is in the checkout.
