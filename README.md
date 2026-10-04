# Pipemesh agent skills

Skills that teach coding agents to work with [Pipemesh](https://pipemesh.io),
the CI/CD control plane. They follow the open
[Agent Skills](https://agentskills.io) format, so they work in Claude Code
and in any other agent that reads `SKILL.md` skills.

## pipemesh-setup

Sets up Pipemesh for a repository. Ask your agent something like
*"set up Pipemesh for this repo"* and it will:

1. **Survey** the CI/CD you have today — GitHub Actions, GitLab CI and
   other CI systems, Makefiles, deploy scripts, Dockerfiles, Helm charts,
   and monorepo tools (Nx, Turborepo, Bazel).
2. **Ask** when the right shape isn't clear from the code: a long-lived
   pipeline or one-shot workflows, porting jobs to Pipemesh runners or
   keeping GitHub Actions as the compute, the environment order, what
   replaces a production approval.
3. **Write** `pipemesh.yaml` (and `.pipemesh/*.yaml` bodies): a pipeline
   that promotes each commit through build → staging → production,
   workflows for pull requests, tags and schedules, every job's kind
   (`build`, `deploy`, `task`, `workflow`, `pipeline`) and
   the files it checks out (`checkout:`, read from what its commands
   use), build outputs handed to deploys by digest (`produces:` /
   `consumes:`), build reuse, caching, remote build caches, secrets by
   name, and keyless cloud access (OIDC).
4. **Validate** the result with a bundled checker that resolves
   `!include` / `!ref`, reports what the Pipemesh loader would reject,
   warns about jobs that read nothing or whose scripts name files their
   checkout leaves out, and lists each job's effective kind, checkout
   and skip policy.
5. **Offer a pull request** with the files and a mapping from the old CI.
6. **Explain how to turn it on**: connecting the GitHub App, enabling the
   repository, the secrets to create, the exact OIDC subjects to trust.

It also reviews or extends an existing `pipemesh.yaml`.

## Install

**Claude Code**, as a plugin:

```
/plugin marketplace add pipemesh/skills
/plugin install pipemesh@pipemesh
```

The skill then triggers on its own when you mention Pipemesh, or run it
directly with `/pipemesh:pipemesh-setup`.

**Any agent** with Agent Skills support (Codex, Cursor, Gemini CLI,
GitHub Copilot, OpenCode, Amp, …), with the
[`skills`](https://github.com/vercel-labs/skills) installer:

```bash
npx skills add pipemesh/skills
```

**By hand**: copy `skills/pipemesh-setup` into your agent's skills
directory — `~/.claude/skills/` (Claude Code, all projects),
`.claude/skills/` (Claude Code, this project), or `.agents/skills/`
(most other agents).

## Requirements

- `git`. The GitHub CLI (`gh`) to open the pull request (otherwise the
  skill gives you the compare URL).
- Python 3 with PyYAML for the checker (`pip install pyyaml`, or
  `uv run --with pyyaml`). Without it the agent checks the definition by
  hand against the same rules.
- Pipemesh connects to GitHub today; GitLab and self-hosted repositories
  are coming soon.

## Layout

```
skills/pipemesh-setup/
├── SKILL.md                         the workflow the agent follows
├── references/
│   ├── decisions.md                 pipeline vs workflow, job kinds, and what to ask
│   ├── definition.md                the pipemesh.yaml grammar (kinds, checkout, executors)
│   ├── artifacts-and-caching.md     produces/consumes, images, cache, remote caches, variables
│   ├── patterns.md                  worked shapes from the public demos
│   ├── migrate-github-actions.md
│   ├── migrate-gitlab-ci.md
│   ├── migrate-other-ci.md
│   └── onboarding.md                summary, pull request, enabling the repository
└── scripts/
    └── check_definition.py          static checks before committing
```

The references are condensed from the [Pipemesh docs](https://pipemesh.io/docs/getting-started)
and the public demos ([demo-showcase](https://github.com/pipemesh/demo-showcase),
[demo-actions](https://github.com/pipemesh/demo-actions),
[demo-matrix](https://github.com/pipemesh/demo-matrix),
[demo-nx](https://github.com/pipemesh/demo-nx),
[demo-turborepo](https://github.com/pipemesh/demo-turborepo),
[demo-bazel](https://github.com/pipemesh/demo-bazel),
[demo-multirepo-pipeline](https://github.com/pipemesh/demo-multirepo-pipeline)).
When they disagree, the docs win — please open an issue.
