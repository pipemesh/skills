# Migrating from other CI systems, or from none

The method is the same everywhere: inventory what runs on which event,
then sort it into the pipeline (default-branch revisions that build and
deploy) and workflows (pull requests, tags, schedules, manual runs).
Each job gets a `kind:` (build, deploy, task; `workflow` and
`pipeline` for nested bodies) and a `checkout:` of the files its
commands read — most CI systems clone the whole tree for every job, and
Pipemesh checks out only what the job lists. Ordering always becomes
explicit `needs:`/`consumes:` edges, artifacts become
`produces:`/`consumes:`, and credentials become named `secrets:` or
OIDC.

## Where the configuration lives

| File | Notes for the translation |
| --- | --- |
| `.circleci/config.yml` | `workflows:` with `requires:` → `needs:`; `filters: branches/tags` → which workload the job lands in; `persist_to_workspace`/`attach_workspace` → `produces:`/`consumes:`; `save_cache`/`restore_cache` → `cache:`; the `checkout` step → the job's `checkout:` (no step = `false`); orbs → shell commands or registry components; `type: approval` jobs → see onboarding.md → Holding production; `docker:` executor image → `image:` (needs bash, git, curl, tar); `path-filtering` orb → `checkout:` on each job |
| `Jenkinsfile` | `stages { stage { steps { sh … } } }` → jobs with `script:`; `parallel` → sibling jobs with the same `needs:`; `when { branch 'main' }` → pipeline; `when { changeRequest() }` → PR workflow; `input` steps → onboarding.md → Holding production; `checkout scm` → the job's `checkout:`; `when { changeset … }` → `checkout:`; `stash`/`unstash`/`archiveArtifacts` → `produces:`/`consumes:`; `credentials()`/`withCredentials` → `secrets:`; `agent { label 'x' }` → `tags: [x]` (own runner queue); shared libraries → read what they run and port the commands |
| `.buildkite/pipeline.yml` | `steps:` with `depends_on` → `needs:`; `wait` steps → `needs:` on everything before; `block`/`input` steps → onboarding.md → Holding production; `artifact_paths` + `buildkite-agent artifact download` → `produces:`/`consumes:`; `agents: queue=x` → `tags: [x]`; plugins → their underlying commands |
| `azure-pipelines.yml` | `stages/jobs/steps` → jobs; `dependsOn` → `needs:`; `trigger:`/`pr:` → pipeline vs PR workflow; `checkout: none` → `checkout: false`; `PublishPipelineArtifact`/`DownloadPipelineArtifact` → `produces:`/`consumes:`; `Cache@2` → `cache:`; `deployment` jobs → `kind: deploy`; environments with approvals → onboarding.md → Holding production |
| `bitbucket-pipelines.yml` | `pipelines: default/branches/pull-requests/tags/custom` maps directly onto pipeline / PR workflow / tag workflow / manual workflow; `artifacts:` → `produces:`; `caches:` → `cache:`; `condition: changesets:` → `checkout:`; `deployment:` steps → `kind: deploy` jobs in the pipeline |
| `.travis.yml` | `script`/`install` → `script:`; `deploy:` providers → their CLI commands in a `kind: deploy` job; `stages` → `needs:` |
| `cloudbuild.yaml` | each step's `name` image + `args` → a job's `image:` + `script:`; `waitFor` → `needs:` |
| `Makefile` / `justfile` / `Taskfile.yml` | no CI of their own: the targets are what the jobs call (`script: make test`) |

## No CI at all

Build the inventory from the repository itself: the README's
build/test/deploy instructions, `package.json` scripts, Make targets,
Dockerfiles, deploy scripts and infrastructure directories. Then:

- Always propose a `checks` workflow on pull requests running the
  project's real lint and test commands (`kind: build` jobs).
- Propose a pipeline only when there is something to ship (a service,
  a site, an image). If no deploy target is visible, ask where it
  deploys rather than inventing one; a pipeline whose last stage builds
  and publishes an artifact is a fine start.
- Use the toolchain versions the repository pins (`.nvmrc`,
  `.python-version`, `go.mod`, `.tool-versions`) to pick images.

## Images for common toolchains

All Debian-based (bash, git, curl and tar included):

| Toolchain | Image |
| --- | --- |
| Node.js | `node:22-bookworm` (match `.nvmrc`/`engines`) |
| Python | `python:3.12-bookworm` |
| Go | `golang:1.23-bookworm` |
| Java/Gradle/Maven | `eclipse-temurin:21-jdk` (Ubuntu-based; add `git` if missing), `gradle:8-jdk21`, `maven:3-eclipse-temurin-21` |
| Rust | `rust:1-bookworm` |
| Ruby | `ruby:3.3-bookworm` |
| .NET | `mcr.microsoft.com/dotnet/sdk:8.0` |
| Terraform / AWS / Docker work | no `image:` — Pipemesh's job image has the AWS and Docker CLIs; install Terraform in the script, or use a Debian-based image that has it |

Pin by digest (`image: node:22-bookworm@sha256:…`) when the repository
already pins its images; otherwise tags are fine to start with.
