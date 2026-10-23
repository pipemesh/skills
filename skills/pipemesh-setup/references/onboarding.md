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
bootstrap stack to deploy by hand (the cloud trust), runners, GitHub App
permissions (details in the "turn it on" steps below).
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
- [ ] Repository connected in Pipemesh (`npx pipemesh repos` lists it; its mesh activates when this file lands on `main`)
- [ ] Secrets created in Pipemesh: `…`
- [ ] Bootstrap stack deployed by hand, with the cloud trust for the job identities (subjects from `npx pipemesh identity` once this is merged): `<the command>`
- [ ] <anything else this definition needs>

The existing CI keeps running until we turn it off; nothing here changes it.
```

## Turning it on (step 8)

Give the user the steps that apply, with this repository's real names.

### 1. Sign in and connect the repository

On a self-hosted instance, read its URL for https://pipemesh.io
everywhere below (sign-in, boards, the issuer `<url>/api/oidc`), and
point the CLI at it first (`npx pipemesh login --api=<url>`; see "Which
Pipemesh" in SKILL.md).

From the terminal (the agent can run these; the person approves in the
browser):

```bash
npx pipemesh login      # shows a code and opens a page; approve it signed in with GitHub or GitLab
npx pipemesh repos      # is this repository listed? then skip connect
npx pipemesh connect    # GitHub: installs the Pipemesh GitHub App; GitLab: opens the page to paste a group token; waits for its repositories
npx pipemesh whoami     # who the CLI is signed in as, and in which organizations
```

`login --no-browser` prints the page without opening it, and
`connect --no-wait` opens the page and returns.

On GitLab, sign in with the GitLab the repository lives on: an instance
may talk to gitlab.com and its own GitLabs side by side, and an account
on one is not an account on another. Connecting a top-level group takes
a group access token (`api` scope, Developer role or higher) pasted by an
Owner of the group; where the instance has several GitLabs, the connect
page asks which one the group is on. A Developer token posts commit
statuses only on branches it may merge into, so tell the user to allow the
token's bot user to merge under the group's Settings → Repository →
Protected branches (or to use a Maintainer token).

Then the group's Owner sets up its **webhook**, the step Pipemesh shows
right after connecting (and the organization's **General** settings
keep): one group hook added in GitLab with a **signing token** (Generate
signing token; push, merge request and member events), pasted back into
Pipemesh. Pipemesh can't add it itself. **On gitlab.com it is required**:
nothing polls a gitlab.com group, so until the hook is set up, pushes
there show up only with **refresh** on the repository, and merging the
pull request does not turn the mesh on by itself. A self-managed group
without one is polled every 5 minutes.

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
anything; `npx pipemesh login` again replaces whichever token it keeps,
a read-only, revoked or expired one included, though a `PIPEMESH_TOKEN`
in the environment still wins). A current CLI keeps it in the
system's keychain (or `~/.config/pipemesh/credentials`) and uses it for
every command, `npx pipemesh@latest api <path>` included: never read it
out.
3. The App is read-mostly: it reads contents, writes check runs, and —
   only for jobs that run on GitHub Actions (`github_actions:`) —
   starts and cancels Actions runs. It can never push code.

### 2. Its mesh

Every repository the connection reaches is a **mesh**: there is nothing
to add or enable. It is **inactive** until a `pipemesh.yaml` lands on its
branch, the default branch by name (e.g. `main`), and **active** from
then on — merging the pull request is what turns it on.
`npx pipemesh@latest mesh` shows its state (in the browser: **Meshes**,
with an **Inactive** tab). The pipeline, push workflows and the jobs'
OIDC subjects follow that branch.

- **Another branch** is set in the repository's settings (**General**),
  or with `npx pipemesh@latest mesh --branch=<b>`. Before the mesh is
  first active it changes freely; afterwards it changes the jobs' OIDC
  subjects (`ref:refs/heads/<branch>`), so Pipemesh shows the old and
  new subjects and asks first (`--yes` on the CLI). Check the branch is
  the one that should deploy before trusting its subjects.
- **Disabling** a mesh (`mesh --disable`, or the switch in General)
  stops everything new from running and keeps its history.
- **For a multi-repository pipeline,** each repository named under
  `repos:` must be reachable by the same organization's connection
  (grant the GitHub App access to it); it needs no definition of its own.

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
- `NAME: !settings` under `variables:` → plain variables.
- A job that declares a name that isn't set fails before its script,
  saying which.

From the terminal, the user runs `npx pipemesh secrets set NAME` and
types the value at its hidden prompt (`--org` for the organization's
level; `--pull-requests` for a read-only credential pull-request runs
may read; `npx pipemesh vars set NAME` for config; `secrets delete NAME`
and `vars delete NAME` remove one). Never ask for a secret
in the chat or put one in a command.

Each edit is versioned: a revision pins the versions current when it
started, and an edit to a value a pipeline job reads starts a new
revision of the repository's pipeline (staging gets the new value
before production). A child pipeline gets it through its parent: the
parent's revision starts, and its `job_type: pipeline` job hands it
over.
List the exact names in your message.

### 4. Cloud access without stored keys (if the definition uses `aws/role@1` or identity tokens)

Pipemesh is an OIDC issuer; trust it once per cloud account. **Don't
compose the values: read them.** Subjects are written with ids (the
GitHub repository's and each job's), so they can't be derived from
names. Once the definition that declares the jobs is on its branch (its
mesh is active):

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

**Put the trust in a bootstrap stack the user deploys by hand.** The
OIDC provider and the roles the jobs assume (their trust and their
permissions) are what a deploy job needs to log in, so the pipeline
can't create or change them: a deploy that adds a role would run as a
role that must already exist, and a trust it breaks locks out the job
that would fix it. Keep them in a small stack of their own, the way
`cdk bootstrap` keeps what CDK deploys with, deployed from the user's
machine with their own credentials, once and again when a job that
needs access is added:

- **AWS**: a CloudFormation or CDK stack (or a Terraform root module),
  e.g. `infra/bootstrap/`, with the IAM OIDC provider for
  `pipemesh.io/api/oidc` (once per account; skip it when one exists),
  each role a job assumes, its trust (the subjects above) and its
  permissions. Take the subjects as a parameter, so adding one is a
  redeploy, not an edit.
- **Google Cloud**: the workload identity pool and provider for the
  issuer, and each service account's `roles/iam.workloadIdentityUser`
  binding for its subjects.
- **Azure**: a federated credential per subject on the app registration
  or managed identity.

Offer to write it, with the one command that deploys it (e.g.
`aws cloudformation deploy --template-file infra/bootstrap/pipemesh.yaml --stack-name pipemesh-bootstrap --capabilities CAPABILITY_NAMED_IAM --parameter-overrides DeploySubject=…`),
and put that command in the pull request's checklist. No job checks it
out or runs it. Order: merge the definition, read the subjects with
`npx pipemesh identity`, deploy the bootstrap stack, then let the
deploy run (or re-run it). Everything else the application needs (its
clusters, buckets, databases) stays in stacks the pipeline deploys.

### 5. If jobs run on GitHub Actions

The App needs **Actions: read and write**. An organization that
installed the App before must accept the permission request on GitHub
(Settings → GitHub Apps → Pipemesh → review request); until then the
`github_actions:` job fails with a message saying so. The workflows must have
the `workflow_dispatch` inputs `pipemesh_sha` and `pipemesh_run` on the
default branch before the first dispatch.

### 6. If jobs name a runner of your own (`runner: <name>`)

The organization's owner creates the runner — in **Settings →
Organization → Runners**, or from the repository (ask first: it changes
the organization):

```bash
npx pipemesh runners create build-farm           # add --pull-requests only for private repositories
npx pipemesh runners auth-token build-farm       # the join command for a machine, its token shown once
```

Each machine joins with `gitlab-runner register --url <Pipemesh>
--token glrt-… --executor <executor>` (gitlab-runner 16 or later); the
web app's **Add a machine** gives the same command. Older agents can
still join with a registration token (`npx pipemesh runners token
build-farm`, `pmr_…`, and `--registration-token`). Jobs naming a runner
never run on hosted compute; they wait for one of its machines, and
their card says why. Pull requests' runs never reach a runner created
without `--pull-requests`. Images used on your runners need bash, git,
curl and tar.

A runner whose machines hold credentials (a cloud role, say) should be
limited to the jobs it's meant for:

- `--repositories=<p,…>`: repository paths without the host (`*` one
  level, `**` any, `acme/payments/**`) or `id:<n>`.
- `--refs=<r,…>`: `default` (the branch each pipeline runs),
  `branch:<glob>`, `tag:<glob>`. A runner with refs never takes
  pull-request jobs.
- A job that names the runner from outside its scope fails at once,
  and its log says what the runner takes.

`npx pipemesh runners update <name>` changes them (`--repositories=`
with nothing lifts the limit; `--description=`, `--paused`, `--resume`,
`--no-pull-requests`). `npx pipemesh runners list` shows the runners,
their scope and machines. `runners apply <file>` creates or updates the
runners a file declares (`--prune` deletes the others),
`runners management-token <name>` makes a token for automation that
manages only the organization's runners (`--expires-in-days=<n>`), and
`runners delete <name>` removes a runner and its machines. Each of
these changes something: ask first.

### 7. Merge and watch

- The PR that adds `pipemesh.yaml` gets no Pipemesh checks: workloads
  exist once they are declared on the repository's branch.
  `npx pipemesh check` (step 5) is the pre-merge validation.
- Merging the PR into that branch starts the first revision. The
  board is at `https://pipemesh.io/github.com/<org>/<repo>` (the
  pipeline at `…/-/pipeline`); from the terminal,
  `npx pipemesh watch --sha=<merge commit>` follows it until it settles
  and `npx pipemesh logs <job>` shows a job's log (a workflow run's:
  `npx pipemesh@latest logs <workflow> --run=<n> [--job=<job>]`, every attempt;
  `npx pipemesh runs <workflow>` lists run numbers). If the definition doesn't load, the
  repository shows the load error, naming the file and key. The first
  revision runs every job — no job has a previous success to compare
  with; later revisions skip what didn't change.
- **A job that fails on a missing file** ("No such file or directory",
  a Dockerfile `COPY` "not found", a tool that can't find its config)
  most likely reads something its `checkout:` leaves out: the checkout
  is enforced. Add the path to the job's `checkout:` (or use `true`)
  and push again. The job's Rules tab on the board shows its effective
  job type, checkout and skip, and where each came from (for a workflow
  or pipeline job, also the artifacts its fingerprint reads and what
  `fingerprint:` narrowed). A workflow or pipeline job takes no
  `checkout:`: fix the body's job that reads the file.
- **A job that consumes an expired artifact** fails before its script:
  "consumes DIST: build/dist expired on <date> — re-run build at
  revision N to make it again". `npx pipemesh@latest artifacts build
  --rev=N` shows the artifact and its date. Running the producer at
  that revision again makes a fresh copy: tell the user, since
  `npx pipemesh rerun` only retries a failed or canceled run. Then
  `npx pipemesh rerun <consumer>` retries the consumer (ask first).
- **A job that fails with "artifact storage for this organization is
  full"** hit the organization's artifact cap: its `produces:` upload
  was refused. `npx pipemesh@latest storage` shows what is in use.
  Expired artifacts are deleted every hour; only a Pipemesh admin can
  raise the cap or shorten retention (Pipemesh admin → Storage, in the
  web app). Caches never fail a job at the cap: older entries are
  evicted, or the save is a warning in the log.
- Tag-triggered workflows fire for tags pushed from now on; existing
  tags are history.
- Pull requests opened after that get check runs named
  `pipemesh/<workflow>/<job>` (e.g. `pipemesh/checks/test`). Make the
  ones that matter required in branch protection; GitHub's merge queue
  works with them unchanged. A PR that breaks the definition gets a
  failing `config` check whose log is the load error.
- Hosted runners are metered per organization: on pipemesh.io 20 vCPU
  at once (10 small jobs) and 2,000 build minutes a month, weighted by
  size (micro ¼×, small 1×, medium 2×, large 4×; a Docker daemon
  doubles it), shown in **Settings → Organization → Compute** and by
  `npx pipemesh@latest compute`. A job over a compute limit is held
  (*queued · held*), not failed; storage is the other way round
  (above). Only a Pipemesh admin changes a limit (Pipemesh admin →
  Compute).

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
