# Artifacts, images and caching

Three different mechanisms, often confused:

| Mechanism | Holds | Scope | Use it for |
| --- | --- | --- | --- |
| **Entries** (`produces:` / `consumes:`) | the build's real outputs: files, images, packages — by digest | the revision; named consumers only | anything a later job ships, tests or builds on |
| **Reuse** (`skip: built`, the default of builds and transforms) | a whole job's outputs, keyed by its fingerprint | across revisions and PRs (trusted runs first) | skipping a build whose inputs are unchanged |
| **Cache** (`cache:`) | tool caches (`.npm`, `.gradle`, pip, Bazel repo cache) | one workload, any branch | making a job that does run faster |

Plus **remote build caches** (Nx Cloud, Turborepo, BuildBuddy), which
live outside Pipemesh and are configured per tool.

Contents: entries · consuming · images · reuse · cache · remote build
caches · runtime variables · legacy artifacts

## Entries: produces

```yaml
build:
  kind: build
  stage: build
  checkout: [src, package.json, package-lock.json]
  script: npm ci && npm run build
  produces:
    dist: dist                                  # short form: a path → a file entry
    report: { path: coverage/lcov.info, expire: 7d, when: always }   # long form
    image: oci                                  # a type: emitted at run time (see Images)
    sdk: npm                                    # a type: emitted at run time
```

- A **file entry** is one path in the workspace: a file, a directory,
  or a glob that matches **exactly one** of them (`dist/*.js` must
  match a single file — use the directory otherwise). No leading `/`,
  no `..`.
- `expire:` `Nd`/`Nh`/`Nm`/`never` (default 30d); `when:` `on_success`
  (default) or `always`. Uploads are capped at 500 MB per entry.
- Keys are `[A-Za-z0-9_-]+`. A declared key that the job doesn't
  produce fails the job.
- The entry's digest is its content (a directory hashes its files'
  paths and contents, never timestamps). **Reproducible builds pay
  off here**: same inputs → same digest → consumers skip.
- `type: file` with no path is only valid on a `github_actions:` job
  (the Actions artifact named after the key).
- A `produces:` path is in the workspace, so it's whatever the job
  wrote there — or a checked-in file the job checks out. A path outside
  the checkout fails the job with "does not exist".

## Consuming

```yaml
deploy_staging:
  kind: deploy
  stage: staging
  consumes: [build/dist]          # waits for build; dist/ appears at its own path
  checkout: [deploy]              # the workspace: deploy/ and dist/, nothing else
  script: ./deploy/deploy.sh staging "$PIPEMESH_BUILD_DIST"
```

- `consumes:` names `<job>/<key>`. It is also an ordering edge (the job
  waits for the producer) and a fingerprint input (a new digest makes
  the consumer run; the same digest lets it skip).
- File entries are unpacked **at their original path** in the
  workspace. Each consumed entry is also a variable:
  `PIPEMESH_` + the path upper-cased, non-alphanumerics as `_`:
  `build/dist` → `$PIPEMESH_BUILD_DIST` (the workspace-relative path
  `dist`); `image/app` → `$PIPEMESH_IMAGE_APP` (`registry/repo@sha256:…`);
  `build[region=eu]/dist` → `$PIPEMESH_BUILD_REGION_EU_DIST`.
- Paths through a body: `ci/build/dist` is the `dist` entry of job
  `build` inside the body that the `kind: workflow` job `ci` runs;
  `../build/jar` inside such a body is the parent's `build/jar`.
- **Entries reach only the jobs that name them.** `needs:` orders jobs
  but does not hand entries along: a production deploy that `needs:`
  staging must still `consumes: [build/dist]` itself.
- Two consumed file entries may not overlap in the workspace.
- A job that only consumes (`checkout: false`) gets the entries and no
  repository files: a `transform`, a deploy that ships a bundle, a
  test of an artifact (`kind: build`, `checkout: false`).

The deploy idiom, used by every demo:

```yaml
deploy_production:
  kind: deploy                    # pipelines only; skip: unchanged
  needs: [deploy_staging]         # promotion order
  consumes: [build/dist]          # what it ships, by digest
  checkout: [deploy]              # the only repository files it reads
```

It runs when the artifact or the deploy scripts changed, and otherwise
shows *no changes* — a docs commit or a test-only change deploys
nothing.

## Images

Three cases; pick by who pulls the image.

**Architecture first.** Pipemesh's hosted runners are **Linux arm64**.
Every `image:` needs an arm64 variant (the official language images
have one), and what a job builds — `docker build` and `publish:`
images, native modules `npm ci` compiles or downloads, prebuilt output
such as `vercel build`'s — is arm64 unless the job asks for another
platform. When the deploy target is amd64 (most x86 clusters, many PaaS
runtimes), ask the user which way to go:

- **Cross-compile** where the toolchain can (Go with `GOARCH`, a JVM
  jar, a JS bundle): a `FROM --platform=$BUILDPLATFORM` build stage
  compiles for the target, no emulation needed.
- **Emulate**: `docker run --privileged --rm tonistiigi/binfmt --install amd64`,
  then `docker buildx build --platform linux/amd64 --push --metadata-file meta.json …`
  (several times slower for emulated `RUN` steps). For one tag with
  both platforms, `docker buildx create --use` first and pass
  `--platform linux/amd64,linux/arm64`.
- **Deploy arm64** (Graviton nodes) and keep the native build.
- **Build elsewhere**: an amd64 runner of their own (`tags:`), or keep
  the image build in GitHub Actions (`github_actions:` on the build
  job).

`publish:` always builds arm64. Platforms that build remotely
(`flyctl deploy --remote-only`, `vercel deploy` without `--prebuilt`)
are unaffected. Details: pipemesh.io/docs/hosted-runners → *Building
for amd64*.

**1. An image your cluster or platform deploys** (ECR, GHCR, Docker
Hub, GAR): build and push in the script, then declare it as an `oci`
entry with the `pipemesh produce` helper, which verifies the digest in
the registry. Run it with no `image:` — Pipemesh's job image has
Docker, buildx and the AWS CLI, and hosted runners start the Docker
daemon for you.

```yaml
image:
  kind: build
  stage: build
  checkout: [src, package.json, package-lock.json, Dockerfile, .dockerignore]   # all the Dockerfile COPYs
  setup:
    - uses: aws/role@1
      with: { arn: "arn:aws:iam::123456789012:role/shop-image-push", region: eu-west-1 }
  script: |
    repo=123456789012.dkr.ecr.eu-west-1.amazonaws.com/shop-api
    aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin "${repo%%/*}"
    docker build -t "$repo:$CI_COMMIT_SHORT_SHA" .
    docker push "$repo:$CI_COMMIT_SHORT_SHA"
    pushed=$(docker inspect --format '{{index .RepoDigests 0}}' "$repo:$CI_COMMIT_SHORT_SHA")   # <repo>@sha256:…
    pipemesh produce oci app --ref "$repo" --digest "${pushed#*@}"
  produces:
    app: oci

deploy_staging:
  kind: deploy
  stage: staging
  consumes: [image/app]           # $PIPEMESH_IMAGE_APP = <repo>@sha256:…
  checkout: [charts/shop]
  script: helm upgrade --install shop charts/shop --set image="$PIPEMESH_IMAGE_APP" --wait
```

A `docker build` in the script sends its context from the workspace,
so the build job's checkout must hold everything the Dockerfile
`COPY`s — a narrowed checkout that misses a file fails the build
("not found"), which is the point. `checkout: true` is the safe start
for a Dockerfile that copies the whole repository.

For GHCR: `echo "$GHCR_TOKEN" | docker login ghcr.io -u <user> --password-stdin`
with `secrets: [GHCR_TOKEN]` (a token with `write:packages`). Deploy by
digest, never by tag: a rollback then redeploys exactly what that
revision shipped.

**2. A CI/toolchain image your own jobs run in**: let Pipemesh build and
keep it. `publish:` without `repo:` (a `key:` is required) builds the
directory after the script succeeds, stores it in Pipemesh's registry
tagged by the build context's fingerprint (an unchanged directory is
never rebuilt), and later jobs run in it with `image_from:`:

```yaml
build_ci:
  kind: build
  stage: image
  checkout: [ci]                         # publish: contexts are added to the checkout anyway
  script: echo "building the CI image from ci/"
  publish:
    - { context: ci, key: ci_image }     # Dockerfile defaults to ci/Dockerfile; args: → --build-arg
test:
  kind: build
  stage: test
  image_from: build_ci/ci_image
  checkout: [src, Makefile]
  script: make test
```

A pull request that changes `ci/` builds its own copy; other PRs use
the default branch's. Only `image_from` can pull these images.

**3. A public image** (`node:22-bookworm`): just `image:`. Pulls are
anonymous — private registry images can't be used in `image:`; build
them as case 2 instead.

Don't use `publish:` **with** `repo:` on pipemesh.io: `repo:` is a name
inside the Pipemesh instance's own registry (a host like `ghcr.io/…`
fails to load), which only self-hosted instances configure.

npm packages follow case 1: publish in the script, then
`pipemesh produce npm sdk --ref "@acme/sdk@$version" --digest "$(npm view @acme/sdk@$version dist.integrity)"`.
To publish only when the package changed:
`pipemesh produce unchanged sdk --digest "$d" || npm publish` re-emits
the previous entry when the digest matches.

## Reuse: skip: built

`skip: built` makes a job look its fingerprint up among earlier
successful runs and, on a match, record that run's outputs as its own
(*reused*) instead of running. The fingerprint covers the job's
definition, the files it checks out, consumed digests, parameters and
secret/config versions — so a build with `checkout: true` changes
fingerprint on every commit and reuses only on a revert; a build that
narrows its checkout to what it reads (sources, lockfile, build config)
reuses whenever those are unchanged. Runs on the default branch, tags
and the pipeline reuse only each other; a pull request reuses those
first, then its own earlier runs, never another PR's. Expired artifacts
don't match.

It is the default of `kind: build` and `kind: transform`, in pipelines
and in workflows. Deploys keep `skip: unchanged` (their default; it
exists only in pipelines — in a workflow body it is a load error), and
tasks `skip: never`. A `kind: workflow` job reuses as a whole when
every job of its body is a build.

## Cache

```yaml
cache:
  key: npm-${checksum:package-lock.json}   # exact key
  restore_keys: [npm-]                     # prefixes, newest match wins
  paths: [.npm]                            # workspace-relative
  policy: pull-push                        # default; pull = never save
```

- `${checksum:<file>}` (first 16 hex chars of the file's sha256;
  `none` if missing) is the only interpolation; it may appear several
  times. No `$VARIABLES` in keys. `restore_keys` are literal prefixes.
  Each file a key names is **added to the job's checkout** (and
  fingerprint) automatically, so the key is never computed from a file
  the sparse workspace left out.
- Restore happens before the script; save happens after a successful
  job, only when the exact key missed. Keys are immutable, so the key
  must move whenever the dependencies do: `${checksum:}` hashes one
  file, so add a segment per lockfile or build script that pins
  versions (`gradle-${checksum:settings.gradle.kts}-${checksum:build.gradle.kts}-…`).
- **Paths are inside the workspace.** Point tool caches there:
  `npm ci --cache .npm`, `pnpm config set store-dir .pnpm-store`,
  `export PIP_CACHE_DIR=$PWD/.cache/pip`,
  `export GRADLE_USER_HOME=$PWD/.gradle-home`,
  `export GOMODCACHE=$PWD/.cache/go-mod GOCACHE=$PWD/.cache/go-build`,
  `export CARGO_HOME=$PWD/.cargo`, Maven `-Dmaven.repo.local=.m2/repository`.
  Cache `node_modules` only when installs are slow and the lockfile key
  makes it safe; the package-manager store is usually the better target.
- **Who saves, who restores.** Each workload (the pipeline, each
  workflow, each body a `kind: workflow` or `kind: pipeline` job runs)
  saves into its own namespace, and
  only trusted runs save: **pull-request runs never save**, so code
  under review can't plant entries a deploy would restore. A
  pull-request run restores from its own workload first, then from
  every other workload of the same definition — when the entry was saved
  with the **same cache `paths:`**. An exact key anywhere beats a prefix
  match; the log says `restored <key> (<size>) from <workload>`.
- **So share the cache definition** between the default-branch job and
  the PR job that run the same install: identical `paths:` (and key).
  The simplest way is one file both include —
  `cache: !include .pipemesh/npm-cache.yaml` — or a root definition
  reached with `!ref` when both jobs live in `pipemesh.yaml`. PR checks
  then start warm from what the pipeline saved; no extra trigger needed.
  A PR-only workflow whose cache paths no other workload uses has
  nothing to restore.
- **A cache fills only when its job really runs.** A `skip: built` job
  that is reused saves nothing, so after a key change the cache stays
  empty until a revision actually runs the job.
- Limits: 2 GiB per entry, 10 GiB per workload; entries unused for 14
  days are evicted.

Mapping from other CI: GitHub `actions/cache` / `setup-node cache: npm`
/ `setup-python cache: pip` → `cache:` with the lockfile checksum;
GitLab `cache: key: files: [lock]` → `${checksum:lock}`; CircleCI
`save_cache`/`restore_cache` → `cache:` with `restore_keys`.

## Remote build caches

Build tools with their own content-addressed cache share results across
every job, branch and service — far more than `cache:` can. Rule for
all of them: **the default branch writes; pull requests read at most**,
so code under review can't plant results that a deploy would ship.

| Tool | Setup in the job | Credentials |
| --- | --- | --- |
| Turborepo + Vercel Remote Cache | `setup: [{ uses: vercel/turborepo-token@1, with: { team: <slug> } }, { uses: turbo/remote-cache@1, with: { team: <slug> } }]` | none stored: OIDC policy on the Vercel team trusting `https://pipemesh.io/api/oidc`, `aud https://vercel.com/<slug>`, `sub` = the build jobs' `ref:refs/heads/<branch>` subjects (never `pull_request`) |
| Turborepo, self-hosted cache | `setup: [{ uses: turbo/remote-cache@1, with: { api: https://cache.example.com, team: <team>, token_var: TURBO_CACHE_TOKEN } }]` | `secrets: [TURBO_CACHE_TOKEN]` on default-branch jobs |
| Turborepo with an existing `TURBO_TOKEN` | `turbo/remote-cache@1` with `team:` and `secrets: [TURBO_TOKEN]` | the token as a secret, not given to PRs |
| Nx Cloud | `secrets: [NX_CLOUD_ACCESS_TOKEN]` (read-write) on default-branch builds; set the workspace's default access to read-only so token-less PR runs read only; `NX_DAEMON=false`, `NX_CLOUD_DISABLE_METRICS_COLLECTION=true` on every Nx job | the token as a secret |
| Bazel + BuildBuddy (or any gRPC cache) | `setup: [{ uses: bazel/remote-cache@1 }]` | `secrets: [BUILDBUDDY_API_KEY]`; optionally a read-only key marked for pull requests, passed as `with: { secret_var: BUILDBUDDY_READONLY_KEY }` |
| Gradle build cache, sccache, ccache | the tool's own remote-cache settings in the script | a secret on default-branch jobs; read-only on PRs |

`turbo/remote-cache@1` and `bazel/remote-cache@1` switch themselves to
read-only on pull-request runs.

## Runtime variables

Every job has: `CI_COMMIT_SHA`, `CI_COMMIT_SHORT_SHA` (8 chars),
`CI_COMMIT_REF_NAME`, `CI_COMMIT_MESSAGE`, `CI_COMMIT_AUTHOR`,
`CI_COMMIT_TIMESTAMP`, `CI_JOB_NAME`, the body's and job's
`variables:`, workflow inputs (under their own names), matrix values
(shell-safe names), and `PIPEMESH_<CONSUME_PATH>` per consumed entry.
`$PIPEMESH_WORKSPACE` is the workspace root (a shell variable — export
it if a child process needs it); it holds the job's checkout and what
it consumes, nothing else. On pipemesh.io every job also has
`PIPEMESH_ID_TOKEN_REQUEST_URL` and `PIPEMESH_ID_TOKEN_REQUEST_TOKEN`
for requesting OIDC identity tokens (what `aws/role@1` uses).

By trigger:

| Run | `CI_PIPELINE_SOURCE` | Extra |
| --- | --- | --- |
| pipeline revision, `on: push` | unset | `CI_COMMIT_REF_NAME` = the repository's branch (e.g. `main`) |
| pull request | `merge_request_event` | `CI_MERGE_REQUEST_IID`, `CI_MERGE_REQUEST_SOURCE_BRANCH_NAME`, `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`; `CI_COMMIT_REF_NAME` = source branch |
| merge queue | `merge_request_event` | `CI_MERGE_REQUEST_EVENT_TYPE=merge_train`, target branch |
| tag | `push` | `CI_COMMIT_TAG`, `CI_COMMIT_REF_NAME` = the tag |
| schedule | `schedule` | — |
| manual | `web` | inputs |

Not set: `CI_COMMIT_BRANCH`, `CI_DEFAULT_BRANCH`, `CI_PIPELINE_ID`,
`CI_JOB_ID`, `CI_COMMIT_BEFORE_SHA`. Don't branch on
`CI_COMMIT_REF_NAME` in pipeline jobs — the pipeline only ever sees the
repository's branch. Pass what the scripts of a body run by a
`kind: workflow`/`kind: pipeline` job need from the parent run (an
input, the tag name) explicitly in that job's `variables:`. The trigger variables
(`CI_PIPELINE_SOURCE`, `CI_COMMIT_TAG`, `CI_MERGE_REQUEST_*`) are set by
Pipemesh only — never set them in `variables:`.

GitHub Actions equivalents: `github.sha` → `$CI_COMMIT_SHA`;
`github.ref_name` → `$CI_COMMIT_REF_NAME` (PRs: source branch; tags:
the tag); `github.base_ref` → `$CI_MERGE_REQUEST_TARGET_BRANCH_NAME`;
`github.event.pull_request.number` → `$CI_MERGE_REQUEST_IID`;
`github.workspace` → `$PIPEMESH_WORKSPACE`; `github.event_name` →
`$CI_PIPELINE_SOURCE` (values above); `secrets.X` → `$X` with `X` in
`secrets:`; `vars.X` → `$X` with `X` in `config:`; `matrix.x` →
`${{ matrix.x }}` (load time) or `$x`.

## Legacy artifacts

`artifacts: { paths: [...], expire: 7d }` still loads; its files flow
along `needs:` edges transitively. It can't be combined with
`produces:` on one job, and it is on its way out — write `produces:` /
`consumes:` in new definitions.
