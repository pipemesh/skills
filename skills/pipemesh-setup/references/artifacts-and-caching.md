# Artifacts, images and caching

Three different mechanisms, often confused:

| Mechanism | Holds | Scope | Use it for |
| --- | --- | --- | --- |
| **Entries** (`produces:` / `consumes:`) | the build's real outputs: files and images, by digest | the revision; named consumers only | anything a later job ships, tests or builds on |
| **Reuse** (every `job_type: build`) | a whole job's outputs, keyed by its fingerprint | across revisions and PRs (trusted runs first) | skipping a build whose inputs are unchanged |
| **Cache** (`cache:`) | tool caches (`.npm`, `.gradle`, pip, Bazel repo cache) | one workload, any branch | making a job that does run faster |

Plus **remote build caches** (Nx Cloud, Turborepo, BuildBuddy), which
live outside Pipemesh and are configured per tool.

Contents: entries · expiry and storage · consuming · images · reuse ·
cache · remote build caches · runtime variables

## Entries: produces

```yaml
build:
  job_type: build
  stage: build
  checkout:
    - src
    - package.json
    - package-lock.json
  script: npm ci && npm run build
  produces:
    dist: dist                                  # short form: a path → a file entry
    report:                                     # long form
      path: coverage/lcov.info
      expire: 7d
      when: always
    image: oci                                  # an entry type: the script pushes or records it (see Images)
```

- A **file entry** is one path in the workspace: a file, a directory,
  or a glob that matches **exactly one** of them (`dist/*.js` must
  match a single file — use the directory otherwise). No leading `/`,
  no `..`.
- `expire:` `Nd`/`Nh`/`Nm`/`never`; without it, the organization's
  artifact retention (30 days out of the box; see Expiry below).
  `when:` `on_success` (default) or `always`. Uploads are capped at
  500 MB per entry.
- Keys are `[A-Za-z0-9_-]+`. A declared key that the job doesn't
  produce fails the job.
- The entry's digest is its content (a directory hashes its files'
  paths and contents, never timestamps). **Reproducible builds pay
  off here**: same inputs → same digest → consumers skip.
- An entry of type `file` with no path is only valid on a
  `github_actions:` job (the Actions artifact named after the key). An
  entry's `type:` (`file`, `oci`) is unrelated to a structure's
  `type:` shape. In the short form `file` and `oci` are type names and
  any other string is a path; a path named `oci` takes the long form
  (`{ type: file, path: oci }`). A long form without `type:` is a file
  entry and needs `path:`.
- The long form's keys are `type`, `path`, `expire`, `when`, `build`
  and `verify` (the last two on `oci` entries only; see Images).
- There are only two entry types, `file` and `oci`: `npm` entries were
  removed (a definition with one doesn't load). Publish a package in the
  script, and record its tarball as a file entry if a later job needs it.
- A `produces:` path is in the workspace, so it's whatever the job
  wrote there — or a checked-in file the job checks out. A path outside
  the checkout fails the job with "does not exist".

## Expiry and storage

- **Retention is a setting.** A file entry is kept 30 days after its
  run unless the instance or the organization sets another retention
  (a Pipemesh admin changes it). `expire:` on the entry wins. A changed
  setting applies only to artifacts stored after it.
- **Past its date an artifact is expired**, except the artifacts of the
  job's two newest successful runs, which are kept (the Artifacts tab
  says "kept: one of the job's two latest runs") so a waiting job and a
  rollback still have them. Reports expire by their date alone. Images
  live in their registries and never expire here.
- **What expiry does:** an expired artifact no longer downloads (410);
  a rollback to it isn't offered; a build pass whose outputs
  expired is no match, so the job builds again; a *no changes* skip
  that would carry an expired artifact forward runs instead; and a job
  that consumes it fails before its script:
  "consumes DIST: build/dist expired on 2026-11-18 — re-run build at
  revision 42 to make it again". Re-run the producer at that revision,
  then the consumer.
- **Each organization has an artifact cap and a cache cap** (on
  pipemesh.io, 10 GB each; a self-hosted instance has none unless its
  admin sets them). File entries, reports and Actions artifacts count;
  images and logs don't. At the artifact cap a `produces:`
  upload is refused and the job fails: "artifact storage for this
  organization is full: X of Y in use, and this upload is Z. Expired
  artifacts are deleted every hour; an admin can raise the limit in
  Settings → Storage." A report refused at the cap is only a line in
  the log. A cache save at the cache cap evicts the organization's
  least recently used entries, and at worst is a warning in the log.
  Compute limits hold a job; storage fails it.
- The organization's **Settings → Storage** shows what is in use
  against each cap, with a warning from 90%, and
  `npx pipemesh@latest storage` prints the same; `npx pipemesh@latest
  artifacts <job>` shows each artifact's expiry. Only a Pipemesh admin
  changes caps, retention or eviction (Pipemesh admin → Storage, in the
  web app); an organization's admins see its settings read-only.

## Consuming

```yaml
deploy_staging:
  job_type: deploy
  production: false
  stage: staging
  consumes:
    DIST: build/dist              # waits for build; dist/ appears at its own path
  checkout:
    - deploy                      # the workspace: deploy/ and dist/, nothing else
  script: ./deploy/deploy.sh staging "$DIST"
```

- `consumes:` is a map, variable name to entry `<job>/<key>`. Consuming
  is also an ordering edge (the job waits for the producer) and a
  fingerprint input (a new digest makes the consumer run; the same
  digest lets it skip).
- File entries are unpacked **at their original path** in the
  workspace. The variable the map names holds the entry's value: for a
  file entry its workspace-relative path (`DIST: build/dist` → `$DIST`
  is `dist`), for an `oci` entry `ref@digest` (`APP_IMAGE:
  image/app` → `registry/repo@sha256:…`). A matrix cell's entry is
  quoted: `BUNDLE: "build[region=eu]/dist"`. Names are `UPPER_SNAKE_CASE`,
  never `PIPEMESH_…` or `CI_…` (Pipemesh's own), and not also set under
  `variables:` or `secrets:`; the same entry cannot be named twice.
- Paths through a body: `ci/build/dist` is the `dist` entry of job
  `build` inside the body that the `job_type: workflow` job `ci` runs;
  `../build/jar` inside such a body is the parent's `build/jar`.
- **Entries reach only the jobs that name them.** `needs:` orders jobs
  but does not hand entries along: a production deploy that `needs:`
  staging must still consume `build/dist` itself.
- Two consumed file entries may not overlap in the workspace.
- A job that only consumes (`checkout: false`) gets the entries and no
  repository files: a build that signs or packages an artifact, a
  deploy that ships a bundle, a test of an artifact (`job_type: build`,
  `checkout: false`).

The deploy idiom, used by every demo:

```yaml
deploy_production:
  job_type: deploy                # pipelines only; runs when what it ships changed
  production: true
  needs:
    - deploy_staging              # promotion order
  consumes:
    DIST: build/dist              # what it ships, by digest
  checkout:
    - deploy                      # the only repository files it reads
```

It runs when the artifact or the deploy scripts changed, and otherwise
shows *no changes* — a docs commit or a test-only change deploys
nothing.

## Images

Four cases; pick by who pulls the image. Nothing outside a Pipemesh
job can pull from Pipemesh's registry (every read needs a credential
Pipemesh mints for one job), so an image a cluster or platform pulls is
always case 1.

**Architecture first.** Pipemesh's hosted runners are **Linux arm64**
by default and **amd64** when the job names a `linux-amd64-*` size. (On
a self-hosted instance, a job without a size runs on the architecture
of the instance's runner; ask its admin.)
Every `image:` needs a variant for the job's architecture (the official
language images have both), and what a job builds — `docker build` and
images Pipemesh builds, native modules `npm ci` compiles or downloads,
prebuilt output such as `vercel build`'s — is its runner's architecture
unless the job asks for another platform. When the deploy target is
amd64 (most x86 clusters, many PaaS runtimes), ask the user which way
to go:

- **Build on amd64**: `runner: linux-amd64-small` (or `-medium`) on the
  job that builds it — native, no emulation; an image Pipemesh builds
  there (`build:`) is amd64.
- **Cross-compile** where the toolchain can (Go with `GOARCH`, a JVM
  jar, a JS bundle): a `FROM --platform=$BUILDPLATFORM` build stage
  compiles for the target, no emulation needed.
- **Emulate** (on a `dockerd: true` job): `docker run --privileged --rm tonistiigi/binfmt --install amd64`,
  then `docker buildx build --platform linux/amd64 --push --metadata-file meta.json …`
  (several times slower for emulated `RUN` steps). For one tag with
  both platforms, `docker buildx create --use` first and pass
  `--platform linux/amd64,linux/arm64`.
- **Deploy arm64** (Graviton nodes) and keep the native build.
- **Build elsewhere**: a runner of their own (`runner: <name>`), or keep
  the image build in GitHub Actions (`github_actions:` on the build
  job).

An image Pipemesh builds (`build:`) is its runner's architecture. Platforms that build
remotely (`flyctl deploy --remote-only`, `vercel deploy` without
`--prebuilt`) are unaffected. A cache key whose files are native builds
names the architecture when jobs of both share it
(`deps-amd64-${checksum:package-lock.json}`). Details:
pipemesh.io/docs/hosted-runners → *Building for both architectures*.

**1. An image your cluster or platform deploys** (ECR, GHCR, Docker
Hub, GAR): build and push in the script with your own credentials,
then record it as an `oci` entry with the `pipemesh produce` helper
(the job checks the digest in the registry; see The check below). Run
it with no `image:` — Pipemesh's job image has the Docker CLI, buildx
and the AWS CLI — and with `dockerd: true`, which gives the job a
Docker daemon.

```yaml
image:
  job_type: build
  stage: build
  dockerd: true                   # docker build needs a daemon
  checkout:                       # all the Dockerfile COPYs
    - src
    - package.json
    - package-lock.json
    - Dockerfile
    - .dockerignore
  setup:
    - uses: aws/role@1
      with:
        arn: arn:aws:iam::123456789012:role/shop-image-push
        region: eu-west-1
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
  job_type: deploy
  production: false
  stage: staging
  consumes:
    APP_IMAGE: image/app          # $APP_IMAGE = <repo>@sha256:…
  checkout:
    - charts/shop
  script: helm upgrade --install shop charts/shop --set image="$APP_IMAGE" --wait
```

A `docker build` in the script sends its context from the workspace,
so the build job's checkout must hold everything the Dockerfile
`COPY`s — a narrowed checkout that misses a file fails the build
("not found"), which is the point. `checkout: true` is the safe start
for a Dockerfile that copies the whole repository.

For GHCR: `echo "$GHCR_TOKEN" | docker login ghcr.io -u <user> --password-stdin`
with `GHCR_TOKEN` under `secrets:` (a token with `write:packages`). Deploy by
digest, never by tag: a rollback then redeploys exactly what that
revision shipped.

**The check.** After the script, before an image it recorded is kept,
the job checks that `ref@digest` resolves in its registry, with
`docker buildx imagetools inspect` (Pipemesh's job image has it) or
`crane manifest`, using the credentials those see — the `docker login`
above. An image with neither fails it ("the image has neither docker
nor crane"): a JDK image running Jib, say.

**Jib (Gradle or Maven) to your own registry**: no Docker daemon, no
Dockerfile. Jib writes the pushed digest to `build/jib-image.digest`
(Maven: `target/jib-image.digest`). In a JDK image, log in with `crane`
first: Jib reads the login from the same Docker config, and the check
then has `crane`:

```yaml
api_image:
  job_type: build
  stage: build
  image: gradle:8-jdk21                   # a JDK, plus bash, git, curl and tar
  checkout:
    - src
    - build.gradle.kts
    - settings.gradle.kts
    - gradle
    - gradlew
  secrets:
    - REGISTRY_TOKEN
  variables:
    REPO: ghcr.io/acme/shop-api
  script: |
    curl -fsSL https://github.com/google/go-containerregistry/releases/download/v0.22.0/go-containerregistry_Linux_arm64.tar.gz | tar -xz -C /usr/local/bin crane   # on PATH: the check uses it
    echo "$REGISTRY_TOKEN" | crane auth login ghcr.io -u acme-ci --password-stdin
    ./gradlew jib -Djib.to.image="$REPO:$CI_COMMIT_SHORT_SHA"
    pipemesh produce oci api --ref "$REPO" --digest "$(cat build/jib-image.digest)"
  produces:
    api: oci
```

Jib builds `linux/amd64` unless `jib.from.platforms` names others,
whatever the runner's architecture; set it to what the target runs
(and add `linux/arm64` when a Pipemesh job runs in the image).

**`verify:` replaces the check for one key**, when the image was pushed
with other tools or credentials, or when the key needs a stricter check
(a signature):

```yaml
  produces:
    app:
      type: oci
      verify: docker buildx imagetools inspect "$PIPEMESH_REF@$PIPEMESH_DIGEST" > /dev/null && ./cosign verify --key cosign.pub "$PIPEMESH_REF@$PIPEMESH_DIGEST" > /dev/null
```

- It's bash, run in the job after its script, in the workspace, with
  the script's variables and setup (a tool the script downloaded is
  there), once per image recorded under the key. `PIPEMESH_KEY`,
  `PIPEMESH_REF` (without any `@digest`) and `PIPEMESH_DIGEST` name the
  image.
- Exit 0 records the image; anything else fails the job, naming the key
  and the ref. Its exit code is its last command's, so **join several
  checks with `&&`** — on separate lines, only the last would count.
- Only on an `oci` entry the script records (`pipemesh produce`). It's
  a load error with `build:` (Pipemesh checks what it builds), on a file
  entry (hashed at upload) and on a `github_actions:` job
  (`pipemesh/produce` checks what it records). A key pushed to its
  `pipemesh place` (case 3) needs none: the registry's digest is its
  check, and `verify:` doesn't run for it.

**2. A CI/toolchain image built from a Dockerfile, that your own jobs
run in**: let Pipemesh build and keep it. An `oci` entry with `build:` builds the directory after the
script succeeds (so the script can prepare it; a job still needs one), stores it in
Pipemesh's registry tagged by the build context's fingerprint (an
unchanged directory is never rebuilt), and later jobs run in it with
`image_from:`:

```yaml
build_ci:
  job_type: build
  stage: image
  checkout:
    - ci                                 # runs only when ci/ changes (build: adds it anyway)
  script: echo "building the CI image from ci/"
  produces:
    ci_image:
      type: oci                          # build: needs it
      build: ci                          # Dockerfile defaults to ci/Dockerfile
                                         # long form: build: { context, dockerfile, args }
test:
  job_type: build
  stage: test
  image_from: build_ci/ci_image
  checkout:
    - src
    - Makefile
  script: make test
```

A pull request that changes `ci/` builds its own copy; other PRs use
the default branch's. The entry says `type: oci` (`build:` without it
is a load error), needs a hosted runner, and isn't on a
`github_actions:` job. `pipemesh place` and `pipemesh creds` refuse a
`build:` key: Pipemesh pushes it. Later jobs run in it with
`image_from:`, or consume it and pull it (below).

**3. An image your script makes for your own jobs** (Jib, ko,
`docker buildx --push`, an upstream image copied with `crane copy`):
an `oci` key without `build:`, pushed by the script into Pipemesh's
registry at the place `pipemesh place <key>` prints. No registry token
to store and no `pipemesh produce`: after the script succeeds, Pipemesh
records the key from its place, with the digest the registry holds
under the run's tag.

```yaml
api_image:
  job_type: build
  stage: build
  image: gradle:8-jdk21                    # Jib needs a JDK, not a Docker daemon
  checkout:
    - src
    - build.gradle.kts
    - settings.gradle.kts
    - gradle
    - gradlew
  script: |
    pipemesh login                         # Jib, crane, docker and ko now log in by themselves
    ./gradlew jib -Djib.to.image="$(pipemesh place api)"
  produces:
    api: oci

mirror_toolchain:
  job_type: build                          # reuses until the definition changes: bump the tag to copy again
  stage: build
  checkout: false
  secrets:
    - VENDOR_TOKEN
  variables:
    UPSTREAM: registry.vendor.example/toolchain:4.2
  script: |
    curl -fsSL https://github.com/google/go-containerregistry/releases/download/v0.22.0/go-containerregistry_Linux_arm64.tar.gz | tar -xz crane
    echo "$VENDOR_TOKEN" | ./crane auth login registry.vendor.example -u ci --password-stdin
    pipemesh login
    ./crane copy "$UPSTREAM" "$(pipemesh place toolchain)"
  produces:
    toolchain: oci

lint:
  job_type: build
  stage: test
  image_from: mirror_toolchain/toolchain   # runs in the copy, pinned by digest
  checkout:
    - src
  script: toolchain lint src
```

- **`pipemesh place <key>`** prints `<registry>/<place>:<tag>`: the
  key's place in the run's own places (a pull request's run has its
  own, which default-branch runs never read) and the run's own tag.
  Push there; the record after the script reads that tag.
- **`pipemesh login`** writes the `docker-credential-pipemesh` helper
  and adds it to the job's Docker config (`$DOCKER_CONFIG/config.json`,
  by default `~/.docker/config.json`) for the registry's host, so
  docker, buildx, crane, Jib and ko log in by themselves, with a fresh
  credential on every use: push to the job's `oci` keys' places, pull
  the images it consumes. It's never automatic — call it before the
  push — and it leaves alone a config that already names another
  helper for that host.
- **`pipemesh creds <key> --username`** / **`--password`** print a
  login for one key's place, for a tool that takes one (each call makes
  a new password, valid 15 minutes and masked in the log):
  `crane auth login "$(pipemesh place api | cut -d/ -f1)" -u "$(pipemesh creds api --username)" -p "$(pipemesh creds api --password)"`.
  No environment variable ever holds a registry credential, and nothing
  goes under `secrets:` for Pipemesh's registry.
- A declared key the script neither pushed to its place nor recorded
  fails the job, as any missing entry does.
- `pipemesh` is a shell function in the job's script and the `bash` it
  starts; the image needs bash, git, curl and tar, nothing else. Its
  `pipemesh login` logs the job in to Pipemesh's registry; it isn't the
  npm CLI's `npx pipemesh login`, which signs a person in to the instance
  from their terminal.
- Architecture: `crane copy` copies every platform the upstream has;
  Jib builds `linux/amd64` unless `jib.from.platforms` says otherwise,
  and a job on the default arm64 runner that runs in the image
  (`image_from:`) needs `linux/arm64`. The `crane` tarball is
  `…_Linux_arm64` on arm64 runners, `…_Linux_x86_64` on
  `linux-amd64-*`.
- Where the registry lives doesn't change any of this: on a
  self-hosted instance it is Pipemesh's own (Zot, at the instance's
  host) unless its admin opted into ECR; on pipemesh.io it is ECR.

**Pulling an image from Pipemesh's registry in a later job**: consume
it by name (`$NAME` is `ref@digest`) and call `pipemesh login`, or use
`pipemesh creds NAME --username` / `--password` for a pull-only login.
An image anywhere else the job pulls with its own credentials.

```yaml
contract_tests:
  job_type: build
  stage: test
  dockerd: true
  consumes:
    API_IMAGE: api_image/api
  checkout:
    - tests/contract
  script: |
    pipemesh login
    docker run -d -p 8080:8080 "$API_IMAGE"
    ./tests/contract/run.sh http://localhost:8080
```

**4. A public image** (`node:22-bookworm`): just `image:`. Pulls are
anonymous — a private registry's image can't be used in `image:`;
copy it into Pipemesh's registry (case 3) or build it (case 2), and run
in it with `image_from:`.

Packages (npm, PyPI, …) have no entry type: publish them in the
script, and record the tarball as a file entry when a later job needs it.

## Reuse: what a build does

A `job_type: build` looks its fingerprint up among earlier
successful runs and, on a match, record that run's outputs as its own
(*reused*) instead of running. The fingerprint covers the job's
definition, the files it checks out, consumed digests, parameters and
secret/config versions — so a build with `checkout: true` changes
fingerprint on every commit and reuses only on a revert; a build that
narrows its checkout to what it reads (sources, lockfile, build config)
reuses whenever those are unchanged. Runs on the default branch, tags
and the pipeline reuse only each other; a pull request reuses those
first, then its own earlier runs, never another PR's. A run whose
outputs have expired doesn't match: the job builds again.
`reuse: trusted` on a build reuses only passes a trusted run made.

Every build reuses, in pipelines and in workflows; `skip:` is
deprecated and ignored (`npx pipemesh check` warns), so never write it. A deploy skips when its fingerprint matches its last
success (*no changes*; pipelines only), and a task runs every time. A
`job_type: workflow` job always runs; the builds in its body reuse,
and hand up the reused pass's artifacts.

## Cache

```yaml
cache:
  key: npm-${checksum:package-lock.json}   # exact key
  restore_keys:                            # prefixes, newest match wins
    - npm-
  paths:                                   # workspace-relative
    - .npm
  policy: pull-push                        # default; pull = never save
```

A cache definition is plain data: kept in its own file
(`.pipemesh/npm-cache.yaml`) or a root key, it has no `type:`.

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
  workflow, each body a `job_type: workflow` or `job_type: pipeline` job runs)
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
- **A cache fills only when its job really runs.** A build
  that is reused saves nothing, so after a key change the cache stays
  empty until a revision actually runs the job.
- Limits: 2 GiB per entry. Out of the box an entry unused for 14 days
  is evicted, and a pipeline's entries together stay under 10 GB (the
  least recently used go first); both are defaults a Pipemesh admin can
  change for the instance or one organization ("only when space is
  needed" turns age eviction off). The organization's cache cap also
  evicts its least recently used entries.

Mapping from other CI: GitHub `actions/cache` / `setup-node cache: npm`
/ `setup-python cache: pip` → `cache:` with the lockfile checksum;
GitLab `cache:key:files:` (the lockfile) → `${checksum:<lockfile>}`; CircleCI
`save_cache`/`restore_cache` → `cache:` with `restore_keys`.

## Remote build caches

Build tools with their own content-addressed cache share results across
every job, branch and service — far more than `cache:` can. Rule for
all of them: **the default branch writes; pull requests read at most**,
so code under review can't plant results that a deploy would ship.

| Tool | Setup in the job | Credentials |
| --- | --- | --- |
| Turborepo + Vercel Remote Cache | two `setup:` steps, `vercel/turborepo-token@1` then `turbo/remote-cache@1`, each `with:` `team: <slug>` (below) | none stored: OIDC policy on the Vercel team trusting `https://pipemesh.io/api/oidc`, `aud https://vercel.com/<slug>`, `sub` = the build jobs' `ref:refs/heads/<branch>` subjects (never `pull_request`) |
| Turborepo, self-hosted cache | a `setup:` step `turbo/remote-cache@1` `with:` `api: https://cache.example.com`, `team: <team>`, `token_var: TURBO_CACHE_TOKEN` | `TURBO_CACHE_TOKEN` under `secrets:` on default-branch jobs |
| Turborepo with an existing `TURBO_TOKEN` | `turbo/remote-cache@1` with `team:`, and `TURBO_TOKEN` under `secrets:` | the token as a secret, not given to PRs |
| Nx Cloud | `NX_CLOUD_ACCESS_TOKEN` (read-write) under `secrets:` on default-branch builds; set the workspace's default access to read-only so token-less PR runs read only; `NX_DAEMON=false`, `NX_CLOUD_DISABLE_METRICS_COLLECTION=true` on every Nx job | the token as a secret |
| Bazel + BuildBuddy (or any gRPC cache) | a `setup:` step `bazel/remote-cache@1` | `BUILDBUDDY_API_KEY` under `secrets:`; optionally a read-only key marked for pull requests, passed `with:` `secret_var: BUILDBUDDY_READONLY_KEY` |
| Gradle build cache, sccache, ccache | the tool's own remote-cache settings in the script | a secret on default-branch jobs; read-only on PRs |

The Turborepo + Vercel setup, in full:

```yaml
setup:
  - uses: vercel/turborepo-token@1           # OIDC → Vercel → TURBO_TOKEN
    with:
      team: acme
  - uses: turbo/remote-cache@1               # points turbo at it; read-only on PRs
    with:
      team: acme
```

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
it consumes, nothing else. Every job has `PIPEMESH_JOB_TOKEN`, its own
credential for Pipemesh. On pipemesh.io it also has
`PIPEMESH_ID_TOKEN_REQUEST_URL`: `POST` there with
`Authorization: Bearer $PIPEMESH_JOB_TOKEN` and `?audience=<aud>` for an
OIDC identity token. Prefer declaring tokens with `id_tokens:` (each
arrives under its own name, as `aws/role@1` declares its own).

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
`job_type: workflow`/`job_type: pipeline` job need from the parent run (an
input, the tag name) explicitly in that job's `variables:`. The trigger variables
(`CI_PIPELINE_SOURCE`, `CI_COMMIT_TAG`, `CI_MERGE_REQUEST_*`) are set by
Pipemesh only — never set them in `variables:`.

GitHub Actions equivalents: `github.sha` → `$CI_COMMIT_SHA`;
`github.ref_name` → `$CI_COMMIT_REF_NAME` (PRs: source branch; tags:
the tag); `github.base_ref` → `$CI_MERGE_REQUEST_TARGET_BRANCH_NAME`;
`github.event.pull_request.number` → `$CI_MERGE_REQUEST_IID`;
`github.workspace` → `$PIPEMESH_WORKSPACE`; `github.event_name` →
`$CI_PIPELINE_SOURCE` (values above); `secrets.X` → `$X` with `X` in
`secrets:`; `vars.X` → `$X` with `X: !settings` under `variables:`; `matrix.x` →
`${{ matrix.x }}` (load time) or `$x`.
