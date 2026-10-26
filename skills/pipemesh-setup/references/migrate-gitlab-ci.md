# Migrating from GitLab CI (.gitlab-ci.yml)

Pipemesh's job keys look familiar to GitLab users (`stage`, `script`,
`needs`, `image`, `variables`, `cache`, `retry`,
`allow_failure`), and jobs run on the `gitlab-runner` agent, so the
core `CI_COMMIT_*` variables keep working. The semantics around them
differ in a few deep ways — read this before translating.

Note: Pipemesh connects to GitHub today. A repository hosted on GitLab
can't be connected yet; a repository that moved to GitHub but kept its
`.gitlab-ci.yml` is the usual case for this reference.

## The big differences

1. **Stages don't order jobs.** In GitLab, a job in `deploy` waits for
   every job in `test`. In Pipemesh, `stage:` only places the job on the
   board; it waits only for its `needs:` and what it `consumes:`. Write
   the edges explicitly: a deploy job lists the test jobs that must pass
   (or consumes the build output, which already depends on them).
2. **One pipeline follows the default branch; everything else is a
   workflow.** GitLab runs one pipeline per push/MR/tag/schedule and
   uses `rules:`/`only:`/`except:` to pick jobs. Pipemesh splits by
   trigger instead: the `pipeline` gets default-branch commits; MR jobs
   become a workflow `on: pull_request`; tag jobs a workflow `on: tag`;
   schedule jobs a workflow `on: schedule`. A job that appears in several
   of them goes into each body (share it with `!ref`).
3. **`rules: changes:` becomes `checkout:` + the job's `job_type:`.**
   The job type decides when the job runs (a `build` reuses, a `deploy`
   skips when its fingerprint matches its last success, a `task` always
   runs); there is no `skip:`. The checkout is what's compared. A job
   that must run every time is a `task`. And the comparison base is the job's own last successful
   run (or any earlier build with the same inputs), not the previous
   commit — a failed build can't be skipped past.
4. **A job checks out only what it lists.** GitLab clones the whole
   tree for every job; Pipemesh gives each job exactly its `checkout:`
   (`true`, `false` or a list), and a file left out isn't in the
   workspace. A `build` defaults to the whole tree; a `deploy` or
   `task` to nothing. List
   what each script reads. History is complete either way, so
   `git diff`/`git merge-base` work.
5. **No anchors, `extends:` or hidden `.jobs`.** Reuse a whole job with
   `!ref` (a job kept on its own says `type: job`); vary it with a
   component (`uses:` + `with:`).
6. **Artifacts are declared outputs.** `artifacts: paths:` +
   `dependencies:` becomes `produces:` on the producer and `consumes:`
   on the consumer (`needs:` orders but passes nothing).

## Key by key

| GitLab | Pipemesh |
| --- | --- |
| `stages:` | `stages:` (board columns only) |
| `stage:` | `stage:` — plus an explicit `job_type:` on every converted job; a job without one doesn't load (decisions.md → *Choosing each job's job_type*) |
| `script:` / `before_script:` / `after_script:` | same, but the three run as one script under `set -e`: `after_script` is skipped when `script` fails — move cleanup that must always run into a `trap … EXIT` |
| `default: before_script:` | repeat on each job, or put the lines in a component / `!ref` list spliced into `script` |
| `image:` | same — a public image with bash, git, curl, tar (`python:3.12` → `python:3.12-bookworm` is the same thing, explicit) |
| `services:` | not supported (loads, ignored): start the service in the script — patterns.md §12 |
| `variables:` (global/job) | body-level `variables:` / job `variables:` |
| CI/CD variables (masked/protected) | Pipemesh repository **secrets** (named per job in `secrets:`) or **variables** (`NAME: !settings` under the job's `variables:`) |
| `needs:` | `needs:` (jobs of the same body) |
| `dependencies:` | `consumes:` names exactly what the job receives |
| `artifacts: paths:` | a `produces:` entry: `<key>:` with `path: <dir or file>` and `expire: 14d` under it |
| `artifacts: reports: junit` / `coverage_report` | no parsing into counts; point a `reports:` key at the tool's HTML output (`tests: build/reports/tests/test`) so people can read it in the job panel |
| `artifacts: expose_as:` / `artifacts: paths:` kept only to browse | a `reports:` key (a directory with `index.html`, a `.md` file or an image) |
| `artifacts: when: always` for test output | `reports:` uploads whatever the outcome; a `produces:` entry takes `when: always` |
| `cache:key:files:` (a lockfile) | `cache:` with `key: deps-${checksum:<lock>}`, `restore_keys:` listing `deps-`, and `paths:` — PR runs don't save; they restore what default-branch jobs saved with the same paths (see artifacts-and-caching.md) |
| `cache: policy: pull` | `policy: pull` in the job's `cache:` |
| `rules: - if: $CI_PIPELINE_SOURCE == "merge_request_event"` | the job goes in the `on: pull_request` workflow body |
| `rules: - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH` | the job goes in the pipeline body |
| `rules: - if: $CI_COMMIT_TAG` | workflow `on: tag` (its `tags:` filter lists globs such as `"v*"`) |
| `rules: - if: $CI_PIPELINE_SOURCE == "schedule"` | workflow `on: schedule`, `cron:` from the GitLab schedule (UTC) |
| `rules: - changes:` (`path/**`) | `path` in the `checkout:` of a `build` (reuses while `path` is unchanged) or a `deploy` (skips while unchanged) |
| `GIT_STRATEGY: none` | `checkout: false` (the default of every job type but `build`) |
| `GIT_DEPTH:` | nothing: history is always complete |
| `rules: - if: $CI_COMMIT_BRANCH =~ /^release/` (other branches) | not supported: only the branch the repository was added with is watched — ask (tags or a manual workflow) |
| `only:` / `except:` | same mapping as `rules:` |
| `when: manual` | no per-job manual key. Operators hold promotions into a job from the board ("Disable promotions…"); see onboarding.md → Holding production. For manual one-offs, a workflow with no trigger |
| `when: always` / `on_failure` | no equivalent; `allow_failure: true` keeps a red job from blocking |
| `allow_failure:` | same |
| `retry:` | `retry: <n>` |
| `timeout: 1h` | `timeout_seconds: 3600` |
| `tags:` (runner labels) | `runner: <name>` — one runner of the organization's own (`npx pipemesh runners create <name>`); drop it for hosted runners, or name a hosted size (`runner: linux-arm64-medium`) |
| `KUBERNETES_CPU_REQUEST` / `KUBERNETES_MEMORY_*` variables | refused: name a size with `runner:` |
| `services: [docker:dind]`, `DOCKER_HOST` | `dockerd: true`: the job gets its own Docker daemon |
| `parallel: matrix:` | `matrix:` (`as: jobs` for independent lanes, `as: workflow` for one verdict) |
| `environment: name: production` | `job_type: deploy` with `production: true` in the pipeline (a staging environment: `production: false`); the board shows what each job deployed. A review app per MR is a `job_type: task` in the PR workflow |
| `resource_group:` / `interruptible:` | not needed: a pipeline job runs one revision at a time and newer revisions supersede queued ones |
| `extends:` / `!reference` / YAML anchors | `!ref` for whole values; components for variation |
| `include: local:` | `!include .pipemesh/<file>.yaml` (a whole body or job; the file starts with its `type:`) |
| `include: project:` / `template:` / `component:` | no cross-repo includes; copy what's used, or use a Pipemesh registry component |
| `trigger: include:` (child pipeline) | `job_type: workflow` with `body: !include …` of a `type: workflow` file (one node, waits for it), or `job_type: pipeline` with a `type: pipeline` body for a child pipeline per service |
| `trigger: project:` (multi-project) | `repos:` + `repo:` in one pipeline, or a separate repository with its own pipeline |
| `release:` keyword | a script step calling your release tooling (e.g. `gh release create`) |
| `pages:` | a `job_type: deploy` job in the pipeline, `production: true` |
| `coverage:` | no equivalent |
| `id_tokens:` | the same: `id_tokens: { VAULT_ID_TOKEN: { aud: https://vault.example.com } }` (`aud` may be a list). The token carries GitLab's claims (`project_path` and `namespace_path` as that GitLab names them, below its own path when it is served under one, `ref_type`, `ref_protected`, `pipeline_source`, `runner_id`, `runner_environment`) beside Pipemesh's, so the receiving side (a Vault role, a cloud trust) changes its issuer to `https://<instance>/api/oidc` and keeps its claim bindings. `aws/role@1` for AWS |
| `CI_JOB_TOKEN` passed to another service (Vault, an admission check, an internal API) | an `id_tokens:` token for that service's audience: nothing outside Pipemesh can check `CI_JOB_TOKEN`. A service that only calls `GET /api/v4/job` with it keeps working, against the Pipemesh instance's URL |

## Variables

Set: `CI_COMMIT_SHA`, `CI_COMMIT_SHORT_SHA`, `CI_COMMIT_REF_NAME`,
`CI_COMMIT_MESSAGE`, `CI_COMMIT_TAG` (tag runs), `CI_PIPELINE_SOURCE`
(`merge_request_event`, `push` for tags, `schedule`, `web`; unset on
pipeline revisions), `CI_MERGE_REQUEST_IID` and
`CI_MERGE_REQUEST_TARGET_BRANCH_NAME` (PRs), `CI_JOB_NAME`. **Not
set:** `CI_COMMIT_BRANCH`, `CI_DEFAULT_BRANCH`, `CI_PIPELINE_ID`,
`CI_JOB_ID`, `CI_REGISTRY*`, `CI_PROJECT_*` (use
`$PIPEMESH_WORKSPACE` or `$PWD` for the checkout). Scripts that branch
on those need rewriting — usually the condition disappears, because
the job now lives only in the workload where it applies. Full list:
`artifacts-and-caching.md` → Runtime variables.

`CI_JOB_TOKEN` is Pipemesh's job token, not GitLab's: it authenticates
the runner and the job's calls to Pipemesh, answers `GET /api/v4/job`
(GitLab's shape: the job, its pipeline and `project.path_with_namespace`)
and dies with the run. No other GitLab API call takes it.

## Example

```yaml
# .gitlab-ci.yml (before)
stages:
  - test
  - build
  - deploy
.node: &node
  image: node:22
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - .npm
  before_script:
    - npm ci --cache .npm
test:
  <<: *node
  stage: test
  script:
    - npm test
build:
  <<: *node
  stage: build
  script:
    - npm run build
  artifacts:
    paths:
      - dist
deploy_staging:
  stage: deploy
  script:
    - ./deploy.sh staging
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
deploy_production:
  stage: deploy
  script:
    - ./deploy.sh production
  when: manual
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
```

```yaml
# pipemesh.yaml (after)
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-definition.schema.json
node:                                    # plain data: no type:
  cache:
    key: npm-${checksum:package-lock.json}
    restore_keys:
      - npm-
    paths:
      - .npm

checks:
  type: workflow
  stages:
    - test
  jobs:
    test:
      job_type: build
      stage: test
      image: node:22-bookworm
      checkout:
        - src
        - package.json
        - package-lock.json
      cache: !ref node.cache
      script: |
        npm ci --cache .npm --prefer-offline
        npm test

pipeline:
  type: pipeline
  stages:
    - build
    - staging
    - production
  jobs:
    build:
      job_type: build                    # reused while src/ and the lockfile are unchanged
      stage: build
      image: node:22-bookworm
      checkout:
        - src
        - package.json
        - package-lock.json
      cache: !ref node.cache
      script: |
        npm ci --cache .npm --prefer-offline
        npm test
        npm run build
      produces:
        dist:
          path: dist
          expire: 30d
    deploy_staging:
      job_type: deploy                   # no changes unless dist or deploy.sh changed; checks out only deploy.sh
      production: false
      stage: staging
      consumes:
        DIST: build/dist
      checkout:
        - deploy.sh
      script: ./deploy.sh staging
    deploy_production:
      job_type: deploy
      production: true
      stage: production
      needs:
        - deploy_staging
      consumes:
        DIST: build/dist
      checkout:
        - deploy.sh
      script: ./deploy.sh production

pipemesh:
  pipelines:
    pipeline: !ref pipeline
  workflows:
    checks:
      body: !ref checks                  # restores what build saved: same node.cache
      on: pull_request
```

The manual production gate needs a decision from the user (see
onboarding.md → Holding production); say so in the summary rather than
silently dropping it.
