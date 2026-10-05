# Summary, pull request, and turning it on

## Summary template (step 6)

Keep it short and concrete. Use this order:

```markdown
**Preview** — <the link from npx pipemesh preview>: the board it draws, and what it changes

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

Preview of the board: <the link from npx pipemesh preview> (open until <date>)

## Mapping from the current CI
<the table from the summary>

## Before merging
- [ ] Repository enabled in Pipemesh (it waits for this file on `main`)
- [ ] Secrets created in Pipemesh: `…`
- [ ] Cloud trust for the job identities (subjects from `npx pipemesh identity` once this is merged): `…`
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

From the terminal, the user runs `npx pipemesh secrets set NAME` and
types the value at its hidden prompt (`--org` for the organization's
level; `npx pipemesh vars set NAME` for config). Never ask for a secret
in the chat or put one in a command.

Each edit is versioned: a revision pins the versions current when it
started, and an edit to a value a pipeline job reads starts a new
revision of its own (staging gets the new value before production).
List the exact names in your message.

### 4. Cloud access without stored keys (if the definition uses `aws/role@1` or identity tokens)

Pipemesh is an OIDC issuer; trust it once per cloud account. **Don't
compose the values: read them.** Subjects are written with ids (the
GitHub repository's and each job's), so they can't be derived from
names. Once the repository is enabled and the definition that declares
the jobs is on its branch:

```bash
npx pipemesh identity                 # the repository you're in
npx pipemesh identity <org>/<repo>    # another one
npx pipemesh identity --json          # the same, to read in a script
```

(pipemesh CLI 0.6.0 or later.) It prints the issuer and each job's
subject on the repository's branch. It also prints one line for every
job of the repository, for parties that match patterns. The
repository's **Settings → Job identity** page in Pipemesh shows the same
values.

- **Issuer / provider URL:** the `Issuer` line (`https://pipemesh.io/api/oidc`)
- **Audience:** `sts.amazonaws.com` for AWS (or what the party documents)
- **Subject:** the line of each job allowed in, e.g.
  `repo:1084047182:job:k2m9x4pq:ref:refs/heads/main`. Trust only the
  jobs that need the access. Use the "every job" line
  (`repo:<id>:job:*:ref:refs/heads/main`) only where the party matches
  patterns (AWS `StringLike`, Google Cloud conditions), and only for
  read-only access such as a cache.

What changes a subject, and what doesn't:
- **No change:** renaming the repository or its organization, renaming
  a job with `was:`, and transferring the repository to another owner.
  Trusts follow the repository.
- **A new subject:** a new job (or a job renamed without `was:`). Run
  `npx pipemesh identity` again once the definition with it is on the
  branch, and add it to the trust.

A job's subject exists once the definition that declares it is on the
repository's branch. So set up the trust after merging. If the job ran
first and failed to assume the role, re-run it (`npx pipemesh rerun
<job>`).

Contexts: the subjects listed are for the repository's branch. A
pull request's run of the same job gets `…:pull_request`. **Never trust
`pull_request` for anything that can write**: anyone who can open a PR
runs code there. A job that runs on tags gets `…:ref:refs/tags/<tag>`:
take its listed subject and replace `ref:refs/heads/<branch>` with
`ref:refs/tags/v*` in a `StringLike`.

AWS example trust policy (the IAM OIDC provider for
`pipemesh.io/api/oidc` created once per account), with the subject read
from `npx pipemesh identity`:

```json
{
  "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::<account>:oidc-provider/pipemesh.io/api/oidc" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "pipemesh.io/api/oidc:aud": "sts.amazonaws.com",
      "pipemesh.io/api/oidc:sub": "<the deploy_staging line from npx pipemesh identity>"
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
  pipeline at `…/-/pipeline`); from the terminal,
  `npx pipemesh watch --sha=<merge commit>` follows it until it settles
  and `npx pipemesh logs <job>` shows a job's log. If the definition doesn't load, the
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
