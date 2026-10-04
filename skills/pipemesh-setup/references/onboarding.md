# Summary, pull request, and turning it on

## Summary template (step 6)

Keep it short and concrete. Use this order:

```markdown
**Assumptions** (only if questions went unanswered): …

**Files**
- `pipemesh.yaml` — registers `pipeline` and workflows `checks`, `release`, …
- `.pipemesh/checks.yaml` — …

**How the old CI maps**
| Before | After | Notes |
| --- | --- | --- |
| `ci.yml` › lint, test (PRs) | workflow `checks` › lint, test | builds: reuse when `src/` and the lockfile are unchanged |
| `deploy.yml` › image | pipeline › `image` | a build; records the digest with `pipemesh produce` |
| `deploy.yml` › deploy-staging | pipeline › `deploy_staging` | a deploy: consumes the image, checks out `charts/`; runs only when either changed |

**Decisions** — one line each, with the reason.

**TODOs** — every `# TODO(pipemesh)` left in the files, and why.

**Before the first run** — the secrets/config names to create, the
cloud trust to add, runners, GitHub App permissions (details in the
"turn it on" steps below).
```

## Pull request (step 7)

Ask first; push only on a yes. Then:

```bash
git switch -c pipemesh-setup
git add pipemesh.yaml .pipemesh/          # plus any workflow files you changed with the user's agreement
git commit -m "Add Pipemesh configuration"   # follow the repo's commit style if it has one
git push -u origin pipemesh-setup
gh pr create --title "Add Pipemesh configuration" --body-file <body.md>
```

Without an authenticated `gh`, push and give the user
`https://github.com/<org>/<repo>/compare/<default>...pipemesh-setup`.

PR body template:

```markdown
Adds `pipemesh.yaml` so this repository runs on [Pipemesh](https://pipemesh.io).

## What runs where
- **Pipeline** (every commit on `main`): build → staging → production …
- **Workflows**: `checks` on pull requests, `release` on `v*` tags, …

## Mapping from the current CI
<the table from the summary>

## Before merging
- [ ] Repository enabled in Pipemesh (it waits for this file on `main`)
- [ ] Secrets created in Pipemesh: `…`
- [ ] Cloud trust for the job identities: `…`
- [ ] <anything else this definition needs>

The existing CI keeps running until we turn it off; nothing here changes it.
```

## Turning it on (step 8)

Give the user the steps that apply, with this repository's real names.

### 1. Sign in and connect the repository

From the terminal (the agent can run these; the person approves in the
browser):

```bash
npx pipemesh login      # shows a code and opens a page; approve it signed in with GitHub
npx pipemesh repos      # is this repository listed? then skip connect
npx pipemesh connect    # installs the Pipemesh GitHub App, waits for its repositories
```

Or in the browser:

1. Sign in at **https://pipemesh.io** with GitHub. The account is the
   namespace; there is no separate sign-up.
2. **Connect GitHub repositories** (home page button). It opens the
   Pipemesh GitHub App's installation: choose the account or
   organization that owns this repository and grant it access (all
   repositories, or select this one — and every repository named under
   `repos:` in the definition).

Organization members join on their first sign-in; their GitHub role
decides what they can do (admin/maintain operate pipelines, write can
start manual workflows). The CLI's token acts as the person who
approved it, with the same permissions, for 90 days
(`npx pipemesh login --read-only` gets one that never changes
anything).
3. The App is read-mostly: it reads contents, writes check runs, and —
   only for jobs that run on GitHub Actions (`github_actions:`) —
   starts and cancels Actions runs. It can never push code.

### 2. Enable the repository

`npx pipemesh enable` inside the checkout (or `npx pipemesh enable
<org>/<repo>`), or in the browser: **Repositories** → enable
`<org>/<repo>`. Enabling records the
repository's branch **by name** — its default branch at that moment
(e.g. `main`). The pipeline, push workflows and the jobs' OIDC subjects
all follow that branch. A later rename or a new default branch is not
followed: changing it means removing the repository and adding it again,
which starts a fresh history — so check the default branch is the one
that should deploy before enabling. A repository without
`pipemesh.yaml` on that branch is enabled in a waiting state and
activates when the file lands — so enable it before merging the PR.
For a multi-repository pipeline, also add each repository named under
`repos:` to the same organization (they need no definition of their own).

### 3. Secrets and config

Open the repository in Pipemesh and use its **⚙ Settings** link
(maintainers see it), or the organization's settings (the gear on the
organization's catalog) for values shared by several repositories — the
most specific level wins on a name clash. Create every name the
definition lists (names are `UPPER_SNAKE_CASE`):

- `secrets:` names → secrets (masked in logs). By default a secret is
  available to every kind of run **except pull requests**; a PR job
  that declares it runs without it. Tick **pull request** only for
  read-only credentials (e.g. a read-only cache key).
- `config:` names → plain configuration variables.
- A job that declares a name that isn't set fails before its script,
  saying which.

Each edit is versioned: a revision pins the versions current when it
started, and an edit to a value a pipeline job reads starts a new
revision of its own (staging gets the new value before production).
List the exact names in your message.

### 4. Cloud access without stored keys (if the definition uses `aws/role@1` or identity tokens)

Pipemesh is an OIDC issuer; trust it once per cloud account:

- **Issuer / provider URL:** `https://pipemesh.io/api/oidc`
- **Audience:** `sts.amazonaws.com` for AWS (or what the party documents)
- **Subject:** one per job allowed in:
  `pipeline:<workload alias>:<context>:job:<job>`

Compute the subjects for the user. The workload alias is the
workload's page path without the leading `/`, with `/-/` and every `/`
after it turned into `:`:

| Job | Page | Subject (repository added on `main`) |
| --- | --- | --- |
| `deploy_staging` in the repo's pipeline | `/github.com/acme/shop/-/pipeline` | `pipeline:github.com/acme/shop:pipeline:ref:refs/heads/main:job:deploy_staging` |
| `build` in child pipeline `orders` | `/github.com/acme/shop/-/pipeline/orders` | `pipeline:github.com/acme/shop:pipeline:orders:ref:refs/heads/main:job:build` |
| `compile` inside the body the pipeline's `build` job (`job_type: workflow`) runs | `/github.com/acme/shop/-/pipeline/build` | `pipeline:github.com/acme/shop:pipeline:build:ref:refs/heads/main:job:compile` |
| `publish` in workflow `release`, on a tag | `/github.com/acme/shop/-/release` | `pipeline:github.com/acme/shop:release:ref:refs/tags/v1.2.3:job:publish` (trust with a `StringLike` on `…:release:ref:refs/tags/v*:job:publish`) |

Contexts: `ref:refs/heads/<branch>` (pipeline revisions and push,
schedule and manual runs — `<branch>` is the branch the repository was
added with), `ref:refs/tags/<tag>`, `pull_request`. Use the real branch
name from step 1 of the survey; a trust pinned to `main` refuses jobs
from any other branch. **Never trust `pull_request`
for anything that can write** — anyone who can open a PR runs code
there. Wildcards over the context let PRs in; wildcards over the job
name after a pinned context don't.

AWS example trust policy (the IAM OIDC provider for
`pipemesh.io/api/oidc` created once per account):

```json
{
  "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::<account>:oidc-provider/pipemesh.io/api/oidc" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "pipemesh.io/api/oidc:aud": "sts.amazonaws.com",
      "pipemesh.io/api/oidc:sub": "pipeline:github.com/acme/shop:pipeline:ref:refs/heads/main:job:deploy_staging"
    }
  }
}
```

When the old CI used GitHub's OIDC (`role-to-assume`), the roles can
stay — add a statement trusting Pipemesh's issuer and subjects next to
GitHub's, and remove GitHub's once the old workflow is retired.

### 5. If jobs run on GitHub Actions

The App needs **Actions: read and write**. An organization that
installed the App before must accept the permission request on GitHub
(Settings → GitHub Apps → Pipemesh → review request); until then the
`github_actions:` job fails with a message saying so. The workflows must have
the `workflow_dispatch` inputs `pipemesh_sha` and `pipemesh_run` on the
default branch before the first dispatch.

### 6. If jobs use `tags:` (your own runners)

**My account → Runners** → create a registration token for the queue
(the job's first tag), then on the machine:

```bash
gitlab-runner register --non-interactive --url https://pipemesh.io \
  --registration-token <pmr_… token> --executor shell --description <name>
```

Tagged jobs never run on hosted compute; they wait for a runner on
their queue. Images used on your runners need bash, git, curl and tar.

### 7. Merge and watch

- The PR that adds `pipemesh.yaml` gets no Pipemesh checks: workloads
  exist once they are declared on the repository's branch.
  `npx pipemesh check` (step 5) is the pre-merge validation.
- Merging the PR into that branch starts the first revision. The
  board is at `https://pipemesh.io/github.com/<org>/<repo>` (the
  pipeline at `…/-/pipeline`). If the definition doesn't load, the
  repository shows the load error, naming the file and key. The first
  revision runs every job — no job has a previous success to compare
  with; later revisions skip what didn't change.
- **A job that fails on a missing file** ("No such file or directory",
  a Dockerfile `COPY` "not found", a tool that can't find its config)
  most likely reads something its `checkout:` leaves out: the checkout
  is enforced. Add the path to the job's `checkout:` (or use `true`)
  and push again. The job's Rules tab on the board shows its effective
  job type, checkout and skip, and where each came from.
- Tag-triggered workflows fire for tags pushed from now on; existing
  tags are history.
- Pull requests opened after that get check runs named
  `pipemesh/<workflow>/<job>` (e.g. `pipemesh/checks/test`). Make the
  ones that matter required in branch protection; GitHub's merge queue
  works with them unchanged. A PR that breaks the definition gets a
  failing `config` check whose log is the load error.
- Hosted runners are metered per organization (concurrent jobs and
  build minutes per month, shown under **My account → Compute**); a job
  over the limit waits, it doesn't fail.

### 8. Retire the old CI

Recommend running both for a few merges, then disabling the old
workflows (or removing them in a follow-up PR) once the Pipemesh board
shows the same results. Two systems deploying the same environment at
once is the one combination to avoid: if the old CI deploys, disable its
deploy jobs before the Pipemesh pipeline's first production deploy.

### Holding production

There is no per-job approval key in the definition. What Pipemesh offers:

- **Disable promotions** on a job from its drawer on the board (a
  maintainer, with a reason): revisions queue in front of it until
  someone re-enables, then the newest flows. Good for "hold production
  during an incident / a freeze".
- **Rollback** re-deploys an earlier revision's artifacts and gates the
  job automatically until promotions are re-enabled.
- If every production deploy must be approved by a person, keep that
  approval where it already lives: run the production deploy on a
  GitHub Actions workflow whose `environment:` has required reviewers
  (`job_type: deploy` with `github_actions: deploy.yml` and a generous
  `timeout_seconds`); Pipemesh waits for the run, which waits for the
  approval.

Tell the user which option the definition uses, and don't drop an
existing approval silently.

### Editor support (optional)

The schema modeline at the top of each file gives validation in any
editor using yaml-language-server; in VS Code add `!include scalar`
and `!ref scalar` to the `yaml.customTags` setting.
