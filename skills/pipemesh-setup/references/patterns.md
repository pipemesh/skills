# Patterns

Worked shapes, each taken from a public demo that runs on pipemesh.io.
Start from the closest one and adapt; don't paste blindly — every
`paths:`, `produces:` and `consumes:` must match the real repository.

Contents
1. One service: build → staging → production, plus PR checks
2. Container image built and deployed by digest
3. A heavy CI suite as one node of the pipeline
4. Library / CLI release on a tag
5. Scheduled and manual workflows
6. Keep GitHub Actions as the compute (Actions bridge)
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
  stages: [build, staging, production]
  jobs:
    build:
      stage: build
      image: node:22-bookworm
      paths: [src, package.json, package-lock.json, tsconfig.json]
      skip: built                       # a revert or a docs-only commit reuses the stored bundle
      cache: !include .pipemesh/npm-cache.yaml   # the same definition the PR checks use
      script: |
        npm ci --cache .npm --prefer-offline
        npm test
        npm run build
      produces:
        dist: { path: dist, expire: 30d }

    deploy_staging:
      stage: staging
      image: node:22-bookworm
      consumes: [build/dist]            # waits for build; runs only when dist is new to it
      paths: [deploy]                   # …or when the deploy scripts change
      secrets: [STAGING_DEPLOY_TOKEN]
      script: ./deploy/deploy.sh staging dist

    deploy_production:
      stage: production
      image: node:22-bookworm
      needs: [deploy_staging]           # production only takes what staging took
      consumes: [build/dist]
      paths: [deploy]
      secrets: [PRODUCTION_DEPLOY_TOKEN]
      script: ./deploy/deploy.sh production dist

pipemesh:
  pipelines:
    pipeline: !ref pipeline
  workflows:
    checks: { body: !ref checks, on: pull_request }
```

```yaml
# .pipemesh/npm-cache.yaml — one cache definition for every job that runs npm ci
key: npm-${checksum:package-lock.json}
restore_keys: [npm-]
paths: [.npm]
```

```yaml
# .pipemesh/checks.yaml
# yaml-language-server: $schema=https://pipemesh.io/api/meta/pipemesh-body.schema.json
stages: [test]
jobs:
  lint:
    stage: test
    image: node:22-bookworm
    cache: !include .pipemesh/npm-cache.yaml
    script: |
      npm ci --cache .npm --prefer-offline
      npm run lint
  test:
    stage: test
    image: node:22-bookworm
    cache: !include .pipemesh/npm-cache.yaml
    script: |
      npm ci --cache .npm --prefer-offline
      npm test
```

Why it is shaped like this:

- The deploys **consume** the build's output instead of rebuilding: what
  ships is what was tested, and a rollback redeploys the exact files.
- `paths: [deploy]` on the deploys means "the only repository files I
  read are the deploy scripts". Together with `consumes:`, a deploy runs
  when the artifact or its scripts changed, and shows *no changes*
  otherwise. Without `paths:` a deploy reads the whole repository and
  runs on every commit.
- `skip: built` on the build reuses a stored run whose fingerprint
  matches (a revert, a README edit). Deploys keep the pipeline default
  (`skip: unchanged`), which compares with *their own* last success.
- PR checks are a separate workflow body: PRs never deploy, and the
  pipeline only sees the default branch.
- Pull-request runs never save caches; they restore what the pipeline's
  `build` saved, because both include the same cache definition (same
  key and `paths:`).

## 2. Container image built and deployed by digest

Build and push in the script, then record the image as an `oci` entry
with `pipemesh produce` (it verifies the digest in the registry).
Consumers get `registry/repo@sha256:…`. No `image:` on the build job:
Pipemesh's job image has Docker, buildx and the AWS CLI, and hosted
runners start the Docker daemon.

```yaml
pipeline:
  stages: [build, staging, production]
  jobs:
    image:
      stage: build
      paths: [src, package.json, package-lock.json, Dockerfile]
      skip: built
      setup:
        - uses: aws/role@1                       # keyless: the job's OIDC identity assumes the role
          with: { arn: "arn:aws:iam::123456789012:role/shop-image-push", region: eu-west-1 }
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
      stage: staging
      consumes: [image/app]                      # $PIPEMESH_IMAGE_APP = <repo>@sha256:…
      paths: [charts/shop-api, scripts/deploy.sh]
      setup:
        - uses: aws/role@1
          with: { arn: "arn:aws:iam::123456789012:role/shop-deploy-staging", region: eu-west-1 }
      script: ./scripts/deploy.sh staging "$PIPEMESH_IMAGE_APP"
```

- Deploy by digest, never by tag: a rebuild with the same digest deploys
  nothing, and a rollback redeploys the digest that revision shipped.
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
ci: !include .pipemesh/ci.yaml

pipeline:
  stages: [verify, staging, production]
  jobs:
    ci:
      stage: verify
      delegate: { type: workflow, params: { body: !ref ci } }   # one run per revision, one verdict
    deploy_staging:
      stage: staging
      consumes: [ci/build/dist]          # <delegating job>/<job inside the workflow>/<key>
      paths: [deploy]
      script: ./deploy/deploy.sh staging
    # …

pipemesh:
  pipelines:
    pipeline: !ref pipeline
  workflows:
    checks: { body: !ref ci, on: pull_request }
```

The board shows `ci` as one card that expands into the workflow's jobs.
Jobs inside a workflow body can't use `skip: unchanged`; use
`skip: built` for the expensive, hermetic ones.

## 4. Library / CLI release on a tag

A release is a record of one event, so it is a workflow.

```yaml
pipemesh:
  workflows:
    checks: { body: !include .pipemesh/checks.yaml, on: pull_request }
    release:
      body: !include .pipemesh/release.yaml
      inputs: { channel: { default: stable } }
      triggers:
        stable: { on: tag, tags: ["v*"] }
        rc:     { on: tag, tags: ["v*-rc*"], inputs: { channel: rc } }
```

```yaml
# .pipemesh/release.yaml
stages: [build, publish]
jobs:
  build:
    stage: build
    image: python:3.12-bookworm
    script: |
      pip install build
      python -m build
    produces:
      wheel: { path: dist }
  publish:
    stage: publish
    image: python:3.12-bookworm
    consumes: [build/wheel]
    secrets: [PYPI_TOKEN]
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
    nightly:  { body: !include .pipemesh/nightly.yaml, on: schedule, cron: "0 3 * * *" }   # UTC, always quoted
    backfill:                                         # no trigger: run from its page or the API
      body: !include .pipemesh/backfill.yaml
      inputs: { since: { default: "7d", description: "How far back" } }
```

Inputs arrive as variables (`$since`).

## 6. Keep GitHub Actions as the compute (Actions bridge)

Pipemesh orchestrates the release; existing Actions workflows do the
work. Good first step for teams with large Actions workflows, or for
steps tied to Actions-only marketplace actions.

```yaml
pipeline:
  stages: [build, staging, production]
  jobs:
    build:
      stage: build
      produces: { dist: file }               # the Actions artifact named "dist" becomes this entry
      delegate:
        type: github_actions
        params: { workflow: build.yml }
    deploy_staging:
      stage: staging
      needs: [build]
      delegate:
        type: github_actions
        params: { workflow: deploy.yml, inputs: { environment: staging } }
    deploy_production:
      stage: production
      needs: [deploy_staging]
      delegate:
        type: github_actions
        params: { workflow: deploy.yml, inputs: { environment: production } }
```

Each Actions workflow must change to be dispatched:

```yaml
on:
  workflow_dispatch:
    inputs:
      pipemesh_sha: { type: string, required: true }     # the revision to act on
      pipemesh_run: { type: string, required: true }     # the Pipemesh job run
      environment:  { type: string, required: true }     # one per definition input (max 8)
run-name: deploy ${{ inputs.environment }} · ${{ inputs.pipemesh_run }}
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { ref: "${{ inputs.pipemesh_sha }}" }     # GitHub reads the file from the branch; check out the revision
      - run: ./deploy.sh ${{ inputs.environment }}
```

- Remove the workflow's old `push:` trigger when Pipemesh takes over, or
  it runs twice.
- Artifacts: `produces: { dist: file }` on the delegating job imports
  the run's Actions artifact named `dist` as an entry; Pipemesh jobs
  that `consumes: [build/dist]` get it unpacked under `dist/`. (The
  older `params.artifacts: [dist]` imports plain artifacts that flow
  along `needs:`.)
- A dispatched Actions run fetches what the Pipemesh job consumes with
  `- uses: pipemesh/consume@v1` (the job declares `consumes:`; the
  workflow needs
  `permissions: { id-token: write, contents: read }`); entries become
  `$PIPEMESH_<PATH>` variables in the run. Inside an Actions run, `uses: pipemesh/consume@v1` fetches
  what the Pipemesh job consumes (needs `permissions: id-token: write`),
  and `uses: pipemesh/produce@v1` emits an image or npm package entry.
- A delegating job can't also have `script`, `image`, `tags`,
  `secrets`, `cache` or `publish` — the Actions run is the execution.
  Secrets stay in GitHub.
- The Pipemesh GitHub App needs **Actions: read and write**; an
  organization that installed it earlier must accept the permission.

## 7. Monorepo: one pipeline per service

Each service gets a child pipeline with its own board; a revision
reaches a service only when that service's inputs changed. The parent
"dispatch" pipeline computes one fingerprint per service and hands the
revision to each child whose fingerprint moved.

```yaml
service: !include .pipemesh/service.yaml

dispatch:
  stages: [graph, dispatch]
  jobs:
    graph:
      stage: graph
      uses: nx/fingerprint@1                   # or turbo/fingerprint@1, bazel/fingerprint@1
      with:
        image: node:24-bookworm
        extra: .pipemesh/service.yaml deploy   # what the build tool can't see
      produces:
        orders: fingerprints/orders
        payments: fingerprints/payments
    orders:
      stage: dispatch
      consumes: [graph/orders]
      paths: []                                # the fingerprint is its only input
      delegate:
        type: pipeline
        params: { body: !ref service, variables: { SERVICE: orders } }
    payments:
      stage: dispatch
      consumes: [graph/payments]
      paths: []
      delegate:
        type: pipeline
        params: { body: !ref service, variables: { SERVICE: payments } }

pipemesh:
  pipelines:
    pipeline: !ref dispatch
  workflows:
    checks: { body: !include .pipemesh/checks.yaml, on: pull_request }
```

```yaml
# .pipemesh/service.yaml — instantiated once per service with $SERVICE set
stages: [build, staging, production]
jobs:
  build:
    stage: build
    image: node:24-bookworm
    secrets: [NX_CLOUD_ACCESS_TOKEN]           # remote cache writes from the default branch only
    variables: { NX_DAEMON: "false", NX_CLOUD_DISABLE_METRICS_COLLECTION: "true" }
    script: |
      npm ci --no-audit --no-fund
      npx nx run-many -t test build -p @acme/$SERVICE
      mkdir -p dist && cp apps/$SERVICE/dist/$SERVICE.js dist/
    produces:
      bundle: { path: "dist/*.js" }
  deploy_staging:
    stage: staging
    consumes: [build/bundle]
    paths: [deploy]
    script: deploy/deploy.sh $SERVICE staging
  deploy_prod:
    stage: production
    needs: [deploy_staging]
    consumes: [build/bundle]
    paths: [deploy]
    script: deploy/deploy.sh $SERVICE production
```

Per build tool (full guides: pipemesh.io/docs/nx-monorepo,
/docs/turborepo-monorepo, /docs/bazel-monorepo):

| Tool | Fingerprint component | Remote cache | PR checks |
| --- | --- | --- | --- |
| Nx | `nx/fingerprint@1` (`with: image, install, with_target, targets, extra`) | Nx Cloud: `secrets: [NX_CLOUD_ACCESS_TOKEN]` on the service build; workspace default access read-only | `npx nx affected -t test build --base=$(git merge-base origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME HEAD)` |
| Turborepo | `turbo/fingerprint@1` (`with: image, install, turbo, deployables, extra`) | `setup: [vercel/turborepo-token@1, turbo/remote-cache@1]` (keyless via OIDC), or `turbo/remote-cache@1` with a token secret for a self-hosted cache | `TURBO_SCM_BASE=<merge base> pnpm turbo run build test --affected` |
| Bazel | `bazel/fingerprint@1` (`with: image, bazel, targets, extra`), deployable targets tagged `deployable` | `setup: [bazel/remote-cache@1]` + `secrets: [BUILDBUDDY_API_KEY]` | rdeps query of the diff against the merge base |
| none (plain dirs) | no graph job: each dispatch job lists `paths: [services/orders, libs/shared]` instead of consuming a fingerprint | your tool's own | path-filtered jobs with `skip: built` |

The deploy-only-on-new-artifact behaviour needs a **reproducible
build** (same inputs → same bytes): esbuild, Bazel deploy jars and
`jar --date=… ` + `javac -g:none` are; most bundlers can be made so.
If the artifact isn't reproducible, deploys run on every build —
dispatch still works, since it relies on the fingerprint.

## 8. A CI image built inside the pipeline

A toolchain image (Bazel, a pinned Node with cached deps) built from a
directory, kept by Pipemesh, and used by later jobs by digest:

```yaml
build_ci:
  stage: image
  paths: [ci]
  skip: built                                # same ci/ content → same image, no rebuild
  script: echo "building the CI image"
  publish:
    - { context: ci, key: ci_image }         # no repo: Pipemesh keeps it, no registry token
graph:
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
  stage: ship
  matrix:
    region: [us, eu]
  needs:
    - "regional_test[region=${{ matrix.region }}]"   # each region ships what its lane tested
  consumes: [bundle/dist]
  paths: []
  script: ./deploy.sh ${{ matrix.region }}

# CI matrix: variants pass or fail as one node.
suite:
  stage: test
  matrix:
    node: ["20", "22"]
    os_image: [bookworm]
    as: workflow
  image: node:${{ matrix.node }}-${{ matrix.os_image }}
  script: npm ci && npm test
```

Selectors are always `key=value`; quote bracketed names inside flow
lists. The cross product is capped at 256. GitHub `include:`/`exclude:`
have no equivalent: list the variants as separate jobs or reshape the
axes.

## 10. Several repositories, one pipeline

```yaml
pipeline:
  repos:
    orders: github.com/acme/orders
    billing: github.com/acme/billing
  stages: [build, staging]
  jobs:
    orders_client:
      stage: build
      repo: orders                       # works in the orders repository
      paths: [client]
      skip: built
      script: ./gradlew :client:jar && cp client/build/libs/client.jar out/orders-client.jar
      produces: { jar: out/orders-client.jar }
    billing:
      stage: build
      repo: billing
      paths: [src]
      skip: built
      consumes: [orders_client/jar]      # $PIPEMESH_ORDERS_CLIENT_JAR is the jar's path
      script: ./gradlew build -PordersClient="$PIPEMESH_ORDERS_CLIENT_JAR"
      produces: { jar: build/libs/billing.jar }
    deploy_billing_staging:
      stage: staging
      consumes: [billing/jar]
      paths: []
      script: ./deploy.sh billing staging
```

Every declared repository must be added to the same Pipemesh
organization (it needs no `pipemesh.yaml` of its own); each revision
pins a commit of each, and a push to any of them starts a revision.

## 11. Jobs on your own machines

```yaml
sign_app:
  stage: release
  tags: [macos]                 # the first tag is the queue; runs only on runners registered to it
  consumes: [build/app]
  secrets: [APPLE_ID_PASSWORD]
  script: ./scripts/sign-and-notarize.sh
```

Untagged jobs run on Pipemesh's hosted runners. A tagged job never runs
on hosted compute — it waits for a runner on its queue (the open-source
`gitlab-runner` agent registered with a token from **My account →
Runners**).

## 12. Tests that need a database (there is no `services:`)

`services:` loads but is ignored by the runners. Start the dependency
in the script instead. With no `image:`, the job image has Docker and
hosted runners start its daemon, so the GitHub/GitLab `services:` block
becomes two `docker run`s:

```yaml
test:
  stage: test
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
  stage: test
  image: node:22-bookworm
  script: |
    apt-get update -qq && apt-get install -y -qq postgresql >/dev/null
    pg_ctlcluster "$(ls /etc/postgresql)" main start
    su postgres -c "psql -qc \"ALTER USER postgres PASSWORD 'postgres'\""
    export DATABASE_URL=postgres://postgres:postgres@localhost:5432/postgres
    npm ci && npm test
```
