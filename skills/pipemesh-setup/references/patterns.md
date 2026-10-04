# Patterns

Worked shapes, each taken from a public demo that runs on pipemesh.io.
Start from the closest one and adapt; don't paste blindly — every
`job_type:`, `checkout:`, `produces:` and `consumes:` must match the
real repository. A checkout is enforced: list what the job's commands
read. Every structure that stands alone (the pipeline's root
definition, each `.pipemesh/*.yaml` body, each component) starts with
its `type:`; job fragments shown on their own below sit inline under a
body's `jobs:`, where `type:` is optional. Everything is block style.

Contents
1. One service: build → staging → production, plus PR checks
2. Container image built and deployed by digest
3. A heavy CI suite as one node of the pipeline
4. Library / CLI release on a tag
5. Scheduled and manual workflows
6. Keep GitHub Actions as the compute (`github_actions:`)
7. Monorepo: one pipeline per service (Nx, Turborepo, Bazel, plain paths)
8. A CI image built inside the pipeline
9. Matrix: per-region lanes vs a CI-style matrix
10. Several repositories, one pipeline
11. Jobs on your own machines (macOS signing, GPUs)
12. Tests that need a database (there is no `services:`)

---

## 1. One service: build → staging → production, plus PR checks

The default shape for an application repository.

```yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-definition.schema.json
checks: !include .pipemesh/checks.yaml

pipeline:
  type: pipeline
  stages:
    - build
    - staging
    - production
  jobs:
    build:
      job_type: build                   # skip: built — a revert or a docs-only commit reuses the stored bundle
      stage: build
      image: node:22-bookworm
      checkout:                         # everything npm ci/test/build read
        - src
        - package.json
        - package-lock.json
        - tsconfig.json
      cache: !include .pipemesh/npm-cache.yaml   # the same definition the PR checks use
      script: |
        npm ci --cache .npm --prefer-offline
        npm test
        npm run build
      produces:
        dist:
          path: dist
          expire: 30d

    deploy_staging:
      job_type: deploy                  # skip: unchanged — runs when dist or deploy/ is new to it
      production: false
      stage: staging
      image: node:22-bookworm
      consumes:
        - build/dist                    # waits for build; dist/ lands at its own path
      checkout:
        - deploy                        # the only repository files it reads
      secrets:
        - STAGING_DEPLOY_TOKEN
      script: ./deploy/deploy.sh staging dist

    smoke:
      job_type: task                    # skip: never — it checks the live site every time
      stage: staging
      needs:
        - deploy_staging
      checkout:
        - scripts/smoke.sh
      script: ./scripts/smoke.sh https://staging.example.com

    deploy_production:
      job_type: deploy
      production: true
      stage: production
      image: node:22-bookworm
      needs:
        - smoke                         # production only takes what staging took and passed
      consumes:
        - build/dist
      checkout:
        - deploy
      secrets:
        - PRODUCTION_DEPLOY_TOKEN
      script: ./deploy/deploy.sh production dist

pipemesh:
  pipelines:
    pipeline: !ref pipeline
  workflows:
    checks:
      body: !ref checks
      on: pull_request
```

```yaml
# .pipemesh/npm-cache.yaml — one cache definition for every job that runs npm ci
# (plain data: no type:)
key: npm-${checksum:package-lock.json}
restore_keys:
  - npm-
paths:
  - .npm
```

```yaml
# .pipemesh/checks.yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-body.schema.json
type: workflow
stages:
  - test
jobs:
  lint:
    job_type: build
    stage: test
    image: node:22-bookworm
    checkout:
      - src
      - package.json
      - package-lock.json
      - .eslintrc.json
    cache: !include .pipemesh/npm-cache.yaml
    script: |
      npm ci --cache .npm --prefer-offline
      npm run lint
  test:
    job_type: build
    stage: test
    image: node:22-bookworm
    checkout:
      - src
      - package.json
      - package-lock.json
      - tsconfig.json
    cache: !include .pipemesh/npm-cache.yaml
    script: |
      npm ci --cache .npm --prefer-offline
      npm test
```

Why it is shaped like this:

- The deploys **consume** the build's output instead of rebuilding: what
  ships is what was tested, and a rollback redeploys the exact files.
- Checking out only `deploy` on the deploys means "the only repository
  files I read are the deploy scripts" — and the workspace holds only
  those plus `dist/`. Together with `consumes:`, a deploy runs when the
  artifact or its scripts changed, and shows *no changes* otherwise.
  A deploy that checked out the whole repository would redeploy on
  every commit.
- The build narrows its checkout to what npm reads, so a README edit
  reuses the stored build. Every root file the tools read must be
  listed — a missing `tsconfig.json` fails the build. When in doubt,
  leave a build at its default `checkout: true` and narrow later.
- `smoke` is a task: it reads a live URL, so it must run every time.
- PR checks are builds too: a pull request that leaves their checkout
  alone reuses the pipeline's pass. PRs never deploy, and the pipeline
  only sees the default branch.
- Pull-request runs never save caches; they restore what the pipeline's
  `build` saved, because both include the same cache definition (same
  key and `paths:`). The lockfile named in the key is checked out
  automatically.
- `checks.yaml` is `type: workflow` because it is registered under
  `pipemesh.workflows`; the root `pipeline` is `type: pipeline`. The
  cache file is plain data and has no `type:`.

## 2. Container image built and deployed by digest

Build and push in the script, then record the image as an `oci` entry
with `pipemesh produce` (it verifies the digest in the registry).
Consumers get `registry/repo@sha256:…`. No `image:` on the build job:
Pipemesh's job image has Docker, buildx and the AWS CLI, and hosted
runners start the Docker daemon.

```yaml
pipeline:
  type: pipeline
  stages:
    - build
    - staging
    - production
  jobs:
    image:
      job_type: build
      stage: build
      # docker build . sends this as the build context: list everything
      # the Dockerfile COPYs, plus .dockerignore
      checkout:
        - src
        - package.json
        - package-lock.json
        - Dockerfile
        - .dockerignore
      setup:
        - uses: aws/role@1                       # keyless: the job's OIDC identity assumes the role
          with:
            arn: arn:aws:iam::123456789012:role/shop-image-push
            region: eu-west-1
      script: |
        repo=123456789012.dkr.ecr.eu-west-1.amazonaws.com/shop-api
        aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin "${repo%%/*}"
        docker build -t "$repo:$CI_COMMIT_SHORT_SHA" .
        docker push "$repo:$CI_COMMIT_SHORT_SHA"
        pushed=$(docker inspect --format '{{index .RepoDigests 0}}' "$repo:$CI_COMMIT_SHORT_SHA")
        pipemesh produce oci app --ref "$repo" --digest "${pushed#*@}"
      produces:
        app: oci

    deploy_staging:
      job_type: deploy
      production: false
      stage: staging
      consumes:
        - image/app                              # $PIPEMESH_IMAGE_APP = <repo>@sha256:…
      checkout:
        - charts/shop-api
        - scripts/deploy.sh
      setup:
        - uses: aws/role@1
          with:
            arn: arn:aws:iam::123456789012:role/shop-deploy-staging
            region: eu-west-1
      script: ./scripts/deploy.sh staging "$PIPEMESH_IMAGE_APP"
```

- Deploy by digest, never by tag: a rebuild with the same digest deploys
  nothing, and a rollback redeploys the digest that revision shipped.
- An image build is a `build`, not a deploy: the image is an output
  that the deploys ship.
- Helm/kubectl aren't in the job image; install them in the script
  (pinned version, `linux-arm64` build) or run the deploy in an image
  that has them plus bash, git, curl and tar.
- The image is **arm64** (hosted runners are Graviton). If the cluster
  runs amd64 nodes, see artifacts-and-caching.md → Images before
  choosing how to build it.
- `publish:` is for CI images Pipemesh keeps (pattern 8), not for images
  your cluster pulls.

## 3. A heavy CI suite as one node of the pipeline

When the default branch should pass the full CI before deploying, and
the same suite runs on pull requests:

```yaml
ci: !include .pipemesh/ci.yaml           # its first line: type: workflow

pipeline:
  type: pipeline
  stages:
    - verify
    - staging
    - production
  jobs:
    ci:
      job_type: workflow                 # one run per revision, one verdict
      stage: verify
      body: !ref ci
    deploy_staging:
      job_type: deploy
      production: false
      stage: staging
      consumes:
        - ci/build/dist                  # <workflow job>/<job inside its body>/<key>
      checkout:
        - deploy
      script: ./deploy/deploy.sh staging
    # …

pipemesh:
  pipelines:
    pipeline: !ref pipeline
  workflows:
    checks:
      body: !ref ci
      on: pull_request
```

The board shows `ci` as one card that expands into the body's jobs.
A `job_type: workflow` job reads what its jobs read: their checkouts
combined. When every job of the body is a build it reuses as a whole
(entries included) on a revision none of them reads; one task in the
body (a commit-message lint, a merge-base check) makes it run every
time. Jobs inside a workflow body can't use `skip: unchanged`, and a
deploy can't live there. The one `type: workflow` body serves both the
`ci` job and the `checks` registration.

## 4. Library / CLI release on a tag

A release is a record of one event, so it is a workflow — and in a
workflow, the publish is a `task` (deploys are pipeline-only).

```yaml
pipemesh:
  workflows:
    checks:
      body: !include .pipemesh/checks.yaml
      on: pull_request
    release:
      body: !include .pipemesh/release.yaml
      inputs:
        channel:
          default: stable
      triggers:
        stable:
          on: tag
          tags:
            - "v*"
        rc:
          on: tag
          tags:
            - "v*-rc*"
          inputs:
            channel: rc
```

```yaml
# .pipemesh/release.yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-body.schema.json
type: workflow
stages:
  - build
  - publish
jobs:
  build:
    job_type: build
    stage: build
    image: python:3.12-bookworm
    checkout:                           # what python -m build packages
      - src
      - pyproject.toml
      - README.md
    script: |
      pip install build
      python -m build
    produces:
      wheel:
        path: dist
  publish:
    job_type: task                      # a release in a workflow: runs once per tag
    stage: publish
    image: python:3.12-bookworm
    consumes:
      - build/wheel                     # dist/ arrives; nothing else is checked out
    secrets:
      - PYPI_TOKEN
    script: |
      pip install twine
      twine upload --non-interactive -u __token__ -p "$PYPI_TOKEN" dist/*
```

`$CI_COMMIT_TAG` holds the tag. Several triggers on one entry share one
history ("every release in one place"); a separate history is a second
entry sharing the body.

## 5. Scheduled and manual workflows

```yaml
pipemesh:
  workflows:
    nightly:
      body: !include .pipemesh/nightly.yaml
      on: schedule
      cron: "0 3 * * *"                               # UTC, always quoted
    backfill:                                         # no trigger: run from its page or the API
      body: !include .pipemesh/backfill.yaml
      inputs:
        since:
          default: 7d
          description: How far back
```

Inputs arrive as variables (`$since`). Their jobs are usually tasks
(they act on the world, so they run every time), each checking out the
scripts it runs. Both bodies are `type: workflow`.

## 6. Keep GitHub Actions as the compute (`github_actions:`)

Pipemesh orchestrates the release; existing Actions workflows do the
work. Good first step for teams with large Actions workflows, or for
steps tied to Actions-only marketplace actions. The executor is where a
job runs, not what it is: a deploy on Actions is still a
`job_type: deploy`.

```yaml
pipeline:
  type: pipeline
  stages:
    - build
    - staging
    - production
  jobs:
    build:
      job_type: build
      stage: build
      produces:
        dist: file                           # the Actions artifact named "dist" becomes this entry
      github_actions: build.yml              # checkout read from build.yml's actions/checkout
    verify:
      job_type: build                        # hosted compute reading what Actions built
      stage: build
      consumes:
        - build/dist
      checkout: false
      script: test -f dist/version.txt
    deploy_staging:
      job_type: deploy
      production: false
      stage: staging
      needs:
        - verify
      github_actions:
        workflow: deploy.yml
        inputs:
          environment: staging
    deploy_production:
      job_type: deploy
      production: true
      stage: production
      needs:
        - deploy_staging
      github_actions:
        workflow: deploy.yml
        inputs:
          environment: production
```

Each Actions workflow must change to be dispatched:

```yaml
on:
  workflow_dispatch:
    inputs:
      pipemesh_sha:                                    # the revision to act on
        type: string
        required: true
      pipemesh_run:                                    # the Pipemesh job run
        type: string
        required: true
      environment:                                     # one per definition input (max 8)
        type: string
        required: true
run-name: deploy ${{ inputs.environment }} · ${{ inputs.pipemesh_run }}
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: "${{ inputs.pipemesh_sha }}"             # GitHub reads the file from the branch; check out the revision
          sparse-checkout: deploy                        # what this workflow reads — and what Pipemesh fingerprints
      - run: ./deploy/deploy.sh ${{ inputs.environment }}
```

- **The job's checkout is read from the workflow file**: an
  `actions/checkout` without `sparse-checkout:` makes it `true` — a
  deploy that runs on every revision. Narrow it in the workflow with
  `sparse-checkout:` (directories, cone mode), or set `checkout:` on the
  Pipemesh job, which overrides the derivation. Pipemesh can't enforce
  an Actions checkout; a narrowed one is a promise the workflow keeps.
  The workflow file itself is always part of the job's inputs.
- Remove the workflow's old `push:` trigger when Pipemesh takes over, or
  it runs twice.
- Artifacts: an entry `dist: file` under the job's `produces:` imports
  the run's Actions artifact named `dist`; Pipemesh jobs that consume
  `build/dist` get it unpacked under `dist/`.
- A dispatched Actions run fetches what the Pipemesh job consumes with
  `- uses: pipemesh/consume@v1` (the job declares `consumes:`; the
  workflow's `permissions:` grant `id-token: write` and
  `contents: read`); entries become `$PIPEMESH_<PATH>` variables in the
  run. `- uses: pipemesh/produce@v1` emits an image or npm package
  entry.
- A `github_actions:` job can't also have `script`, `setup`, `uses`,
  `image`, `tags`, `secrets`, `cache` or `publish` — the Actions run is
  the execution. Secrets stay in GitHub.
- The Pipemesh GitHub App needs **Actions: read and write**; an
  organization that installed it earlier must accept the permission.

## 7. Monorepo: one pipeline per service

Each service gets a child pipeline with its own board; a revision
reaches a service only when that service's inputs changed. The parent
"dispatch" pipeline computes one fingerprint per service and hands the
revision to each child whose fingerprint moved.

```yaml
service: !include .pipemesh/service.yaml      # a type: pipeline body

dispatch:
  type: pipeline
  stages:
    - graph
    - dispatch
  jobs:
    graph:
      job_type: build                          # checks out the whole tree: the build tool reads all of it
      stage: graph
      uses: nx/fingerprint@1                   # or turbo/fingerprint@1, bazel/fingerprint@1
      with:
        image: node:24-bookworm
        extra: .pipemesh/service.yaml deploy   # what the build tool can't see
      produces:
        orders: fingerprints/orders
        payments: fingerprints/payments
    orders:
      job_type: pipeline                       # checks out nothing: its entry is its only input
      stage: dispatch
      consumes:
        - graph/orders                         # hands the revision over only when this changed
      body: !ref service
      variables:
        SERVICE: orders
    payments:
      job_type: pipeline
      stage: dispatch
      consumes:
        - graph/payments
      body: !ref service
      variables:
        SERVICE: payments

pipemesh:
  pipelines:
    pipeline: !ref dispatch
  workflows:
    checks:
      body: !include .pipemesh/checks.yaml
      on: pull_request
```

```yaml
# .pipemesh/service.yaml — instantiated once per service with $SERVICE set
type: pipeline
stages:
  - build
  - staging
  - production
jobs:
  build:
    job_type: build                            # checkout: true — npm ci and Nx read the whole workspace
    stage: build
    image: node:24-bookworm
    secrets:
      - NX_CLOUD_ACCESS_TOKEN                  # remote cache writes from the default branch only
    variables:
      NX_DAEMON: "false"
      NX_CLOUD_DISABLE_METRICS_COLLECTION: "true"
    script: |
      npm ci --no-audit --no-fund
      npx nx run-many -t test build -p @acme/$SERVICE
      mkdir -p dist && cp apps/$SERVICE/dist/$SERVICE.js dist/
    produces:
      bundle:
        path: dist/*.js
  deploy_staging:
    job_type: deploy
    production: false
    stage: staging
    consumes:
      - build/bundle
    checkout:
      - deploy
    script: deploy/deploy.sh $SERVICE staging
  deploy_prod:
    job_type: deploy
    production: true
    stage: production
    needs:
      - deploy_staging
    consumes:
      - build/bundle
    checkout:
      - deploy
    script: deploy/deploy.sh $SERVICE production
```

```yaml
# .pipemesh/checks.yaml — PR checks: only what the change can affect
type: workflow
stages:
  - test
jobs:
  affected:
    job_type: task                             # reads the merge base, so it runs on every PR revision
    stage: test
    checkout: true                             # the project graph spans the tree; history stays complete
    image: node:24-bookworm
    script: |
      npm ci --no-audit --no-fund
      base=$(git merge-base "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" HEAD)
      NX_DAEMON=false npx nx affected -t test build --base="$base" --head=HEAD
```

The service body is `type: pipeline` because `job_type: pipeline` jobs
run it; writing `job_type: workflow` on `orders` instead is a load error
naming both sides.

Per build tool (full guides: pipemesh.io/docs/nx-monorepo,
/docs/turborepo-monorepo, /docs/bazel-monorepo):

| Tool | Fingerprint component | Remote cache | PR checks |
| --- | --- | --- | --- |
| Nx | `nx/fingerprint@1` (`with:` image, install, with_target, targets, extra) | Nx Cloud: `NX_CLOUD_ACCESS_TOKEN` under `secrets:` on the service build; workspace default access read-only | a `task`: `npx nx affected -t test build --base=$(git merge-base origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME HEAD)` |
| Turborepo | `turbo/fingerprint@1` (`with:` image, install, turbo, deployables, extra) | `setup:` steps `vercel/turborepo-token@1` then `turbo/remote-cache@1` (keyless via OIDC), or `turbo/remote-cache@1` with a token secret for a self-hosted cache | a `task`: `TURBO_SCM_BASE=<merge base> pnpm turbo run build test --affected` |
| Bazel | `bazel/fingerprint@1` (`with:` image, bazel, targets, extra), deployable targets tagged `deployable` | a `setup:` step `bazel/remote-cache@1`, and `BUILDBUDDY_API_KEY` under `secrets:` | a `task`: rdeps query of the diff against the merge base |
| none (plain dirs) | no graph job: each `job_type: pipeline` job's `checkout:` lists the service's directories (`services/orders`, `libs/shared`) instead of consuming a fingerprint | your tool's own | builds with narrowed checkouts (`services/orders`, `libs/shared`, `package.json`, …) |

The deploy-only-on-new-artifact behaviour needs a **reproducible
build** (same inputs → same bytes): esbuild, Bazel deploy jars and
`jar --date=… ` + `javac -g:none` are; most bundlers can be made so.
If the artifact isn't reproducible, deploys run on every build —
dispatch still works, since it relies on the fingerprint.

## 8. A CI image built inside the pipeline

A toolchain image (Bazel, a pinned Node with cached deps) built from a
directory, kept by Pipemesh, and used by later jobs by digest (two
jobs, inline under a body's `jobs:`):

```yaml
build_ci:
  job_type: build                            # same ci/ content → same image, no rebuild
  stage: image
  checkout:
    - ci                                     # the publish: context is added anyway
  script: echo "building the CI image"
  publish:
    - context: ci                            # no repo: Pipemesh keeps it, no registry token
      key: ci_image
graph:
  job_type: build
  stage: graph
  image_from: build_ci/ci_image              # runs in exactly that image, pinned by digest
  script: make fingerprints
```

A pull request that changes `ci/` builds its own; otherwise PR jobs run
in the default branch's image. The image still needs bash, git, curl
and tar.

## 9. Matrix: per-region lanes vs a CI-style matrix

```yaml
# Lanes: each variant is its own job with its own promotion cursor.
deploy:
  job_type: deploy                                   # checks out nothing but its script: ships the bundle
  production: true
  stage: ship
  matrix:
    region:
      - us
      - eu
  needs:
    - regional_test[region=${{ matrix.region }}]     # each region ships what its lane tested
  consumes:
    - bundle/dist
  checkout:
    - deploy.sh
  script: ./deploy.sh ${{ matrix.region }}

# CI matrix: variants pass or fail as one node.
suite:
  job_type: build
  stage: test
  matrix:
    node:
      - "20"
      - "22"
    os_image:
      - bookworm
    as: workflow
  image: node:${{ matrix.node }}-${{ matrix.os_image }}
  checkout:
    - src
    - test
    - package.json
    - package-lock.json
  script: npm ci && npm test
```

Selectors are always `key=value`; in a block list they need no quotes.
The cross product is capped at 256. GitHub `include:`/`exclude:` have
no equivalent: list the variants as separate jobs or reshape the axes.
`${{ matrix.x }}` works in `checkout:` too (a `checkout:` item
`streams/${{ matrix.stream }}`), so each lane re-runs only when its own
files change.

## 10. Several repositories, one pipeline

```yaml
pipeline:
  type: pipeline
  repos:
    orders: github.com/acme/orders
    billing: github.com/acme/billing
  stages:
    - build
    - staging
  jobs:
    orders_client:
      job_type: build
      stage: build
      repo: orders                       # works in the orders repository: checkout is orders' paths
      checkout:
        - client
        - gradlew
        - gradle
        - settings.gradle.kts
      script: ./gradlew :client:jar && mkdir -p out && cp client/build/libs/client.jar out/orders-client.jar
      produces:
        jar: out/orders-client.jar
    billing:
      job_type: build
      stage: build
      repo: billing
      checkout:
        - src
        - gradlew
        - gradle
        - settings.gradle.kts
        - build.gradle.kts
      consumes:
        - orders_client/jar              # $PIPEMESH_ORDERS_CLIENT_JAR is the jar's path
      script: ./gradlew build -PordersClient="$PIPEMESH_ORDERS_CLIENT_JAR"
      produces:
        jar: build/libs/billing.jar
    deploy_billing_staging:
      job_type: deploy                   # checks out nothing: ships the jar
      production: false
      stage: staging
      consumes:
        - billing/jar
      script: java -jar "$PIPEMESH_BILLING_JAR" --deploy staging
```

Every declared repository must be added to the same Pipemesh
organization (it needs no `pipemesh.yaml` of its own); each revision
pins a commit of each, and a push to any of them starts a revision.

## 11. Jobs on your own machines

```yaml
sign_app:
  job_type: build               # signs the consumed app; checks out only its script
  stage: release
  tags:
    - macos                     # the first tag is the queue; runs only on runners registered to it
  consumes:
    - build/app
  checkout:
    - scripts/sign-and-notarize.sh
  secrets:
    - APPLE_ID_PASSWORD
  script: ./scripts/sign-and-notarize.sh
  produces:
    signed: out/App.zip
```

Untagged jobs run on Pipemesh's hosted runners. A tagged job never runs
on hosted compute — it waits for a runner on its queue (the open-source
`gitlab-runner` agent registered with a token from **My account →
Runners**). The checkout is enforced there too.

## 12. Tests that need a database (there is no `services:`)

`services:` loads but is ignored by the runners. Start the dependency
in the script instead. With no `image:`, the job image has Docker and
hosted runners start its daemon, so the GitHub/GitLab `services:` block
becomes two `docker run`s:

```yaml
test:
  job_type: build
  stage: test
  checkout:
    - src
    - test
    - package.json
    - package-lock.json
  script: |
    docker run -d --name pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
    until docker exec pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
    docker run --rm --network host -v "$PWD:/src" -w /src \
      -e DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres \
      node:22-bookworm sh -c 'npm ci && npm test'
```

Or stay in the toolchain image and install the service from the
distribution (slower, no Docker needed):

```yaml
test:
  job_type: build
  stage: test
  image: node:22-bookworm
  checkout:
    - src
    - test
    - package.json
    - package-lock.json
  script: |
    apt-get update -qq && apt-get install -y -qq postgresql >/dev/null
    pg_ctlcluster "$(ls /etc/postgresql)" main start
    su postgres -c "psql -qc \"ALTER USER postgres PASSWORD 'postgres'\""
    export DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres
    npm ci && npm test
```

A test that starts its own database from pinned images is still
hermetic, so it stays a `build` and reuses when its checkout is
unchanged.
