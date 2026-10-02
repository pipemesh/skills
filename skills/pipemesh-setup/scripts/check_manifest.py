#!/usr/bin/env python3
"""Static checks for a Pipemesh manifest (pipemesh.yaml) before it is committed.

Usage:
    python3 check_manifest.py [REPO_ROOT]        # default: the current directory

Reads REPO_ROOT/pipemesh.yaml, resolves `!include` and `!ref` the way the
Pipemesh loader does, and reports what the loader would reject (ERROR) and
what is legal but probably not what you meant (WARN). Exit code 1 when there
is at least one ERROR.

This mirrors the loader's documented rules; it is not the loader. The
authoritative check happens when the manifest lands on the default branch
of an enabled repository: a load error shows on the repository's page in
Pipemesh, naming the file and key.

Needs PyYAML. Without it: `pip install pyyaml`, or run with
`uv run --with pyyaml python3 check_manifest.py`.
"""

from __future__ import annotations

import itertools
import os
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "check_manifest.py needs PyYAML: pip install pyyaml "
        "(or: uv run --with pyyaml python3 check_manifest.py)\n")
    sys.exit(2)

# ---------------------------------------------------------------------------
# Grammar facts (from the Pipemesh loader and its JSON schemas)

WORKLOAD_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RESERVED_WORKLOAD_NAMES = {"default", "settings", "jobs", "runs", "events",
                           "workflows", "pipelines", "repository"}
JOB_NAME = re.compile(r"^[a-z0-9_-]+$")
DEFINITION_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
OUTPUT_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
CONFIG_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
CONSUME_PATH = re.compile(r"^[A-Za-z0-9_\-\[\]=,./${}\s]+$")

BODY_KEYS = {"stages", "jobs", "variables", "kind", "repos"}
JOB_KEYS = {
    "stage", "script", "before_script", "after_script", "needs", "tags",
    "variables", "image", "image_from", "paths", "skip", "config", "repo",
    "repos", "services", "secrets", "artifacts", "cache", "publish",
    "allow_failure", "timeout_seconds", "retry", "matrix", "uses", "with",
    "setup", "produces", "consumes", "was", "delegate",
}
EXECUTION_KEYS = {"script", "setup", "image", "services", "before_script", "after_script"}
DELEGATE_EXCLUSIVE = {"script", "uses", "tags", "image", "image_from", "secrets",
                      "artifacts", "cache", "publish", "setup", "services",
                      "before_script", "after_script"}
EVENT_KINDS = {"push", "pull_request", "tag", "schedule", "manual"}
FILTER_FOR_KIND = {"branches": "push", "targets": "pull_request", "tags": "tag", "cron": "schedule"}
WORKFLOW_ENTRY_KEYS = {"body", "stages", "jobs", "variables", "kind", "inputs", "on",
                       "triggers", "branches", "targets", "tags", "cron", "repos"}
PIPELINE_ENTRY_KEYS = {"body", "stages", "jobs", "variables", "kind", "inputs", "repos"}
TRIGGER_KEYS = {"on", "branches", "targets", "tags", "cron", "inputs"}

# Hints for keys people bring from other CI systems.
FOREIGN_KEY_HINTS = {
    "rules": "rules: is gone — paths: lists the files the job reads, skip: says when it may skip",
    "sources": "sources: is now paths:",
    "trigger": "trigger: is now delegate: { type: …, params: … }",
    "timeout": "write timeout_seconds: (an integer)",
    "when": "there is no when:; a job runs when what it needs approved the revision. "
            "Manual holds are an operator action in Pipemesh (see onboarding.md)",
    "only": "only:/except: have no equivalent: filter with the workflow's trigger "
            "(branches:/targets:/tags:) and the job's paths:",
    "except": "only:/except: have no equivalent: filter with the workflow's trigger and paths:",
    "extends": "no extends: — reuse a whole job with !ref, or vary it with a component (uses:/with:)",
    "dependencies": "dependencies: is implicit — artifacts flow along needs:/consumes: edges",
    "environment": "no environment: key — the stage and job name say where it deploys",
    "interruptible": "not needed — a newer revision supersedes queued ones on its own",
    "resource_group": "not needed — a pipeline job runs one revision at a time",
    "runs-on": "use tags: [<queue>] for your own runners, or nothing for hosted runners",
    "steps": "a job has script: (shell), not steps:",
    "env": "use variables:",
    "if": "no if: — use triggers, paths: and skip:",
    "coverage": "not supported",
    "parallel": "use matrix:",
    "strategy": "use matrix: (and matrix.as: workflow for a CI-style matrix)",
    "timeout-minutes": "write timeout_seconds:",
    "continue-on-error": "write allow_failure: true",
    "container": "write image:",
    "outputs": "use produces: (files, images, packages) and consumes: on the other side",
}

# Registry components (ghcr.io/pipemesh/components): name -> (required, optional) params.
REGISTRY_COMPONENTS = {
    "aws/role": ({"arn", "region"}, {"audience"}),
    "bazel/fingerprint": (set(), {"image", "targets", "extra", "out", "bazel"}),
    "bazel/remote-cache": (set(), {"url", "header", "secret_var", "results_url", "bes_backend"}),
    "codecov/upload": (set(), {"token_var"}),
    "docker/build-push": ({"image"}, {"tag", "context", "dockerfile", "registry",
                                      "username_var", "password_var"}),
    "node/pnpm": (set(), {"version"}),
    "nx/fingerprint": ({"image"}, {"install", "with_target", "targets", "extra", "out"}),
    "slack/message": ({"text"}, {"webhook_var"}),
    "turbo/fingerprint": ({"image"}, {"install", "turbo", "task", "deployables", "extra", "out"}),
    "turbo/remote-cache": ({"team"}, {"api", "token_var"}),
    "vercel/turborepo-token": ({"team"}, {"policy_id", "audience"}),
}
REGISTRY_REF = re.compile(r"^([a-z0-9-]+/[a-z0-9-]+)@(\d+)$")
MATRIX_REF = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*}}")
GHA_EXPR = re.compile(r"\$\{\{\s*(github|secrets|env|inputs|steps|needs|vars|runner|job)\.[^}]*}}")
MAX_MATRIX = 256

# ---------------------------------------------------------------------------
# Reporting

class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, msg: str):
        self.errors.append(f"ERROR {where}: {msg}")

    def warn(self, where: str, msg: str):
        self.warnings.append(f"WARN  {where}: {msg}")


R = Report()

# ---------------------------------------------------------------------------
# YAML with !include / !ref, no anchors, positions kept

class Tagged:
    def __init__(self, tag: str, value: str, file: str, line: int):
        self.tag, self.value, self.file, self.line = tag, value, file, line

    def __repr__(self):
        return f"{self.tag} {self.value}"


def make_loader(file: str):
    class Loader(yaml.SafeLoader):
        pass

    def tagged(tag):
        def construct(loader, node):
            if not isinstance(node, yaml.ScalarNode):
                R.error(f"{file}:{node.start_mark.line + 1}", f"{tag} takes a scalar path")
                return None
            return Tagged(tag, loader.construct_scalar(node).strip(), file, node.start_mark.line + 1)
        return construct

    def unknown(loader, suffix, node):
        R.error(f"{file}:{node.start_mark.line + 1}",
                f"unknown YAML tag !{suffix} — the tags are !include <path> and !ref <path>")
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_scalar(node)

    Loader.add_constructor("!include", tagged("!include"))
    Loader.add_constructor("!ref", tagged("!ref"))
    Loader.add_multi_constructor("!", unknown)
    return Loader


def check_no_anchors(text: str, file: str):
    try:
        for ev in yaml.parse(text, Loader=yaml.SafeLoader):
            line = ev.start_mark.line + 1
            if isinstance(ev, yaml.AliasEvent):
                R.error(f"{file}:{line}", "YAML aliases (*x) are not supported — define it once and use !ref <path>")
            elif getattr(ev, "anchor", None):
                R.error(f"{file}:{line}", f"YAML anchors (&{ev.anchor}) are not supported — use !ref <path>")
            elif isinstance(ev, yaml.ScalarEvent) and ev.value == "<<" and ev.plain:
                R.error(f"{file}:{line}", "merge keys (<<:) are not supported — nothing overrides; use !ref or a component")
    except yaml.YAMLError:
        pass  # reported by the load


class Repo:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.files: dict[str, object] = {}

    def load(self, rel: str, where: str):
        if rel in self.files:
            return self.files[rel]
        path = os.path.normpath(os.path.join(self.root, rel))
        if not path.startswith(self.root + os.sep):
            R.error(where, f"{rel}: a path outside the repository")
            return None
        if not rel.endswith((".yaml", ".yml")):
            R.error(where, f"{rel}: !include names a .yaml or .yml file")
            return None
        if not os.path.isfile(path):
            R.error(where, f"{rel}: no such file")
            return None
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        check_no_anchors(text, rel)
        try:
            data = yaml.load(text, Loader=make_loader(rel))
        except yaml.YAMLError as e:
            R.error(rel, f"is not valid YAML: {e}")
            return None
        if data is None:
            R.error(where, f"{rel} is empty")
        self.files[rel] = data
        return data


def bool_key_fix(m: dict, where: str, trigger_position: bool) -> dict:
    """YAML 1.1 reads unquoted on/off/yes/no keys as booleans."""
    out = {}
    for k, v in m.items():
        if k is True and trigger_position:
            out["on"] = v
        elif isinstance(k, bool):
            R.error(where, f"a key read as boolean {k} (an unquoted on/off/yes/no) — quote it")
        else:
            out[str(k)] = v
    return out


class Resolver:
    """Resolves !include and !ref into plain values; tracks root-key reachability."""

    def __init__(self, repo: Repo):
        self.repo = repo
        self.stack: list[str] = []
        self.root_refs: dict[str, set[str]] = {}  # pipemesh.yaml root key -> root keys it refs
        self.current_root_key: str | None = None

    def ref(self, t: Tagged, file_root):
        path = t.value
        where = f"{t.file}:{t.line}"
        if not re.match(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$", path):
            R.error(where, f"!ref '{path}' is not a path of keys (letters, digits, _ and -, joined by dots)")
            return None
        if t.file == "pipemesh.yaml" and self.current_root_key is not None:
            self.root_refs.setdefault(self.current_root_key, set()).add(path.split(".")[0])
        key = (t.file, path)
        if key in self.stack:
            R.error(where, f"!ref cycle: {' -> '.join(p for _, p in self.stack)} -> {path}")
            return None
        node = file_root
        for seg in path.split("."):
            if isinstance(node, Tagged):
                node = self.resolve(node, file_root)
            if not isinstance(node, dict) or seg not in {str(k) for k in node}:
                R.error(where, f"!ref {path}: no key '{seg}'")
                return None
            node = {str(k): v for k, v in node.items()}[seg]
        self.stack.append(key)
        try:
            return self.resolve(node, file_root)
        finally:
            self.stack.pop()

    def include(self, t: Tagged):
        where = f"{t.file}:{t.line}"
        if t.value in [s for s in self.stack if isinstance(s, str)]:
            R.error(where, f"!include cycle through {t.value}")
            return None
        data = self.repo.load(t.value, where)
        if data is None:
            return None
        if isinstance(data, dict) and "pipemesh" in data:
            R.error(where, f"{t.value}: only pipemesh.yaml registers workloads — an included file may not hold pipemesh:")
        self.stack.append(t.value)
        saved = self.current_root_key
        self.current_root_key = None
        try:
            return self.resolve(data, data)
        finally:
            self.current_root_key = saved
            self.stack.pop()

    def resolve(self, node, file_root):
        if isinstance(node, Tagged):
            if node.tag == "!include":
                return self.include(node)
            return self.ref(node, file_root)
        if isinstance(node, dict):
            return {k: self.resolve(v, file_root) for k, v in node.items()}
        if isinstance(node, list):
            out = []
            for item in node:
                spliced = isinstance(item, Tagged) and item.tag == "!ref"
                value = self.resolve(item, file_root)
                if spliced and isinstance(value, list):
                    out.extend(value)
                else:
                    out.append(value)
            return out
        return node

# ---------------------------------------------------------------------------
# Body and job checks

def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def text_of(job: dict) -> str:
    parts = []
    for k in ("script", "before_script", "after_script"):
        v = job.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.extend(str(x) for x in v)
    return "\n".join(parts)


def matrix_axes(job: dict) -> dict:
    m = job.get("matrix")
    if not isinstance(m, dict):
        return {}
    return {str(k): v for k, v in m.items() if k != "as"}


def parse_selector(ref: str):
    """'test[stream=aws, arch=arm64]' -> ('test', {'stream': 'aws', 'arch': 'arm64'})"""
    m = re.match(r"^([^\[\]]+)(\[(.*)\])?$", ref.strip())
    if not m:
        return ref, None
    name, sel = m.group(1), m.group(3)
    if sel is None:
        return name, None
    pairs = {}
    for part in sel.split(","):
        if "=" not in part:
            return name, {"__bad__": part}
        k, v = part.split("=", 1)
        pairs[k.strip()] = v.strip()
    return name, pairs


def check_selector(where, ref_name, sel, jobs):
    target = jobs.get(ref_name)
    if target is None:
        return
    axes = matrix_axes(target)
    mas = (target.get("matrix") or {}).get("as", "jobs") if isinstance(target.get("matrix"), dict) else None
    if sel is None:
        return
    if "__bad__" in sel:
        R.error(where, f"selector '{sel['__bad__']}': selectors are always key=value, e.g. {ref_name}[stream=aws]")
        return
    if not axes:
        R.error(where, f"'{ref_name}' has no matrix, so it takes no selector")
        return
    if mas == "workflow":
        R.error(where, f"'{ref_name}' is a matrix as: workflow — depend on the node itself: needs: [{ref_name}]")
        return
    for k, v in sel.items():
        if k not in axes:
            R.error(where, f"selector key '{k}' is not a matrix variable of '{ref_name}' (declared: {', '.join(axes)})")
        elif "${{" not in v and v not in [str(x) for x in as_list(axes[k])]:
            R.error(where, f"selector {k}={v} matches no variant of '{ref_name}' (declared: {axes[k]})")


def outputs_of(job: dict) -> dict:
    """key -> type ('file', 'oci', 'npm') of what a job produces."""
    out = {}
    produces = job.get("produces")
    if isinstance(produces, dict):
        for k, v in produces.items():
            t = "file"
            if isinstance(v, str) and v in ("file", "oci", "npm"):
                t = v
            elif isinstance(v, dict) and v.get("type") in ("file", "oci", "npm"):
                t = v["type"]
            out[str(k)] = t
    for p in as_list(job.get("publish")):
        if isinstance(p, dict) and p.get("key"):
            out[str(p["key"])] = "oci"
    return out


def check_consume_path(where, raw, body_ctx, want_oci=False):
    jobs = body_ctx["jobs"]
    if not isinstance(raw, str) or not CONSUME_PATH.match(raw):
        R.error(where, f"'{raw}' is not a consume path (<job>/<key>, <job>/<inner job>/<key>, ../<job>/<key>)")
        return
    segs = raw.split("/")
    if segs[0] == "..":
        if not body_ctx["delegated"]:
            R.error(where, f"'{raw}': ../ names the workload that delegated this one, and this body is not delegated")
        return
    if len(segs) < 2:
        R.error(where, f"'{raw}' names a job but no key — write <job>/<key>")
        return
    name, sel = parse_selector(segs[0])
    if "${{" in name:
        return
    if name not in jobs:
        R.error(where, f"consumes '{raw}': no job '{name}' in this body")
        return
    check_selector(where, name, sel, jobs)
    producer = jobs[name]
    delegate = producer.get("delegate") if isinstance(producer.get("delegate"), dict) else None
    if delegate and delegate.get("type") == "pipeline":
        R.error(where, f"consumes '{raw}': '{name}' dispatches a pipeline, and a dispatched pipeline exports nothing")
        return
    if delegate and delegate.get("type") == "workflow":
        if len(segs) < 3:
            R.error(where, f"consumes '{raw}': '{name}' delegates a workflow — name a job of its body ({name}/<job>/<key>)")
            return
        child = body_ctx["children"].get(name)
        if isinstance(child, dict):
            inner = child.get("jobs") or {}
            iname, _ = parse_selector(segs[1])
            if iname not in inner:
                R.error(where, f"consumes '{raw}': the workflow '{name}' delegates to has no job '{iname}'")
            elif len(segs) == 3 and segs[2] not in outputs_of(inner[iname]):
                R.error(where, f"consumes '{raw}': {name}/{iname} does not produce '{segs[2]}'")
        return
    if len(segs) == 2:
        outs = outputs_of(producer)
        if segs[1] not in outs:
            if outs:
                R.error(where, f"consumes '{raw}': '{name}' does not produce '{segs[1]}' (it produces: {', '.join(outs)})")
            else:
                R.error(where, f"consumes '{raw}': '{name}' produces nothing — declare produces: on it")
        elif want_oci and outs[segs[1]] != "oci":
            R.error(where, f"image_from '{raw}' is a {outs[segs[1]]} entry; it must name an oci entry (an image)")


def check_component_use(where, uses, with_, repo: Repo, setup_step=False):
    if not isinstance(uses, str):
        R.error(where, "uses: takes a component reference")
        return
    m = REGISTRY_REF.match(uses)
    if uses.startswith("./"):
        path = os.path.join(repo.root, uses[2:])
        if not os.path.isfile(path):
            R.error(where, f"component file not found: {uses}")
            return
        with open(path, encoding="utf-8") as fh:
            try:
                comp = yaml.safe_load(fh)
            except yaml.YAMLError as e:
                R.error(where, f"{uses} is not valid YAML: {e}")
                return
        if not isinstance(comp, dict) or "component" not in comp or "job" not in comp:
            R.error(where, f"{uses} must declare component: <name> and job:")
            return
        unknown = set(comp) - {"component", "params", "job"}
        if unknown:
            R.error(where, f"{uses}: unknown top-level key(s) {sorted(unknown)} (component/params/job)")
        params = comp.get("params") or {}
        required = {k for k, v in params.items() if isinstance(v, dict) and v.get("required")}
        optional = set(params) - required
        if setup_step and isinstance(comp.get("job"), dict) and set(comp["job"]) - {"script"}:
            R.error(where, f"{uses} is used in setup: and must be a script-only fragment")
    elif m:
        name = m.group(1)
        if name not in REGISTRY_COMPONENTS:
            R.warn(where, f"{uses}: not a registry component this checker knows "
                          f"({', '.join(sorted(REGISTRY_COMPONENTS))}) — check the name")
            return
        required, optional = REGISTRY_COMPONENTS[name]
    else:
        R.error(where, f"uses: '{uses}' is neither a same-repo path (./.pipemesh/components/<name>.yaml) "
                       "nor a registry reference (<stream>/<name>@<major>)")
        return
    given = set((with_ or {}).keys()) if isinstance(with_, dict) else set()
    for p in sorted(required - given):
        R.error(where, f"{uses}: required parameter '{p}' was not passed in with:")
    for p in sorted(given - required - optional):
        R.error(where, f"{uses}: unknown parameter '{p}' (declared: {', '.join(sorted(required | optional)) or 'none'})")


def check_gha_workflow(where, params, repo: Repo):
    wf = params.get("workflow")
    if not isinstance(wf, str) or not wf.strip():
        R.error(where, "delegate github_actions: params.workflow names the Actions workflow (e.g. deploy.yml)")
        return
    inputs = params.get("inputs") or {}
    if not isinstance(inputs, dict):
        R.error(where, "params.inputs must be a mapping")
        inputs = {}
    if len(inputs) > 8:
        R.error(where, f"params.inputs has {len(inputs)} entries — at most 8 (GitHub caps a dispatch at 10, two are Pipemesh's)")
    for k, v in inputs.items():
        if k in ("pipemesh_sha", "pipemesh_run"):
            R.error(where, f"input '{k}' is reserved — Pipemesh always sends it")
        if isinstance(v, (dict, list)):
            R.error(where, f"input '{k}' must be a scalar (GitHub passes inputs as strings)")
    if wf.isdigit():
        return
    path = os.path.join(repo.root, ".github", "workflows", wf)
    if not os.path.isfile(path):
        R.warn(where, f".github/workflows/{wf} does not exist in this checkout")
        return
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except yaml.YAMLError:
        R.warn(where, f".github/workflows/{wf} is not valid YAML")
        return
    on = doc.get("on", doc.get(True))
    dispatch = None
    if isinstance(on, dict):
        dispatch = on.get("workflow_dispatch", "missing")
    elif isinstance(on, list):
        dispatch = {} if "workflow_dispatch" in on else "missing"
    elif on == "workflow_dispatch":
        dispatch = {}
    if dispatch == "missing" or dispatch is None and on != "workflow_dispatch":
        R.error(where, f".github/workflows/{wf} must declare on: workflow_dispatch with inputs pipemesh_sha and pipemesh_run")
        return
    declared = set(((dispatch or {}).get("inputs") or {}).keys()) if isinstance(dispatch, dict) else set()
    for need in ["pipemesh_sha", "pipemesh_run", *inputs.keys()]:
        if need not in declared:
            R.error(where, f".github/workflows/{wf}: workflow_dispatch does not declare input '{need}'")
    if "pipemesh_run" not in str(doc.get("run-name", "")):
        R.warn(where, f".github/workflows/{wf}: put ${{{{ inputs.pipemesh_run }}}} in run-name: so Pipemesh matches the run exactly")


def check_body(where: str, body, kind: str, repo: Repo, delegated: bool):
    if not isinstance(body, dict):
        R.error(where, "a body is a mapping with stages: and jobs:")
        return
    body = bool_key_fix(body, where, False)
    for k in body:
        if k not in BODY_KEYS:
            R.error(where, f"unknown key '{k}' — a body has stages, jobs, variables, repos (and kind)")
    if body.get("kind") not in (None, kind):
        R.error(where, f"body declares kind '{body.get('kind')}' but is used as a {kind}")
    stages = body.get("stages")
    if not isinstance(stages, list) or not stages:
        R.error(where, "stages: is required (a list)")
        stages = []
    stages = [str(s) for s in stages]
    jobs_raw = body.get("jobs")
    if not isinstance(jobs_raw, dict) or not jobs_raw:
        R.error(where, "defines no jobs")
        return
    repos = body.get("repos") or {}
    for alias, url in (repos.items() if isinstance(repos, dict) else []):
        if not isinstance(url, str):
            R.error(f"{where}.repos.{alias}", "must be a repository path (github.com/org/repo); a declared repository "
                    "is followed on its default branch")
    if repos and kind != "pipeline":
        R.warn(where, "repos: is a pipeline's (every revision pins a version of each declared repository)")
    jobs = {}
    for name, job in jobs_raw.items():
        name = str(name)
        if name.startswith("."):
            R.error(f"{where}.jobs.{name}", "job names must not start with '.' (use components for reuse, not hidden jobs)")
        elif not JOB_NAME.match(name):
            R.error(f"{where}.jobs.{name}", "job names are lowercase letters, digits, _ and -")
        if not isinstance(job, dict):
            R.error(f"{where}.jobs.{name}", "a job is a mapping")
            continue
        jobs[name] = bool_key_fix(job, f"{where}.jobs.{name}", False)

    ctx = {"jobs": jobs, "delegated": delegated, "children": {}}
    # Delegated children first, so consume paths into them can be checked.
    for name, job in jobs.items():
        d = job.get("delegate")
        if isinstance(d, dict) and isinstance(d.get("params"), dict) and isinstance(d["params"].get("body"), dict):
            ctx["children"][name] = d["params"]["body"]

    edges: dict[str, set[str]] = {}
    for name, job in jobs.items():
        jw = f"{where}.jobs.{name}"
        edges[name] = set()
        for k in job:
            if k not in JOB_KEYS:
                hint = FOREIGN_KEY_HINTS.get(k)
                R.error(jw, f"unknown key '{k}'" + (f" — {hint}" if hint else
                        f" — allowed: {', '.join(sorted(JOB_KEYS))}"))
        stage = job.get("stage")
        if stage is None:
            R.error(jw, "has no stage")
        elif str(stage) not in stages:
            R.error(jw, f"references unknown stage '{stage}' (stages: {stages})")

        skip = job.get("skip")
        if skip is not None and skip not in ("unchanged", "built", "never"):
            R.error(jw, f"skip: is unchanged, built or never — got '{skip}'")
        if skip == "unchanged" and kind == "workflow":
            R.error(jw, "skip: unchanged is a pipeline's policy — a workflow run has no earlier run to compare with; "
                        "use skip: built to reuse a stored run, or leave skip: out")
        if kind == "workflow" and "paths" in job and skip != "built" and "delegate" not in job:
            R.warn(jw, "paths: in a workflow only matters with skip: built (a workflow job runs every time otherwise)")

        delegate = job.get("delegate")
        if delegate is not None:
            check_delegate(jw, name, job, delegate, kind, repo)
        uses = job.get("uses")
        if uses is not None:
            for k in sorted(EXECUTION_KEYS & set(job)):
                R.error(jw, f"'{k}' belongs to the component when uses: instantiates one — pass a param instead")
            check_component_use(jw, uses, job.get("with"), repo)
        elif "with" in job:
            R.error(jw, "with: without uses:")
        for i, step in enumerate(as_list(job.get("setup"))):
            if not isinstance(step, dict) or "uses" not in step or set(step) - {"uses", "with"}:
                R.error(f"{jw}.setup[{i}]", "each setup step is { uses:, with: }")
                continue
            check_component_use(f"{jw}.setup[{i}]", step["uses"], step.get("with"), repo, setup_step=True)
        if delegate is None and uses is None and not job.get("script"):
            R.error(jw, "has no script: — every job that doesn't delegate or use a component needs one "
                        "(a publish-only job can use script: echo …)")
        if job.get("services"):
            R.warn(jw, "services: loads but runners ignore it — start the service in the script "
                       "(docker run on the default job image, or install it in the image)")
        if job.get("after_script"):
            R.warn(jw, "after_script runs in the same shell after script and is skipped when script fails — "
                       "use trap … EXIT for cleanup that must always run")

        if job.get("image") and job.get("image_from"):
            R.error(jw, "image and image_from are exclusive")
        img = str(job.get("image") or "")
        if "alpine" in img or img.startswith("busybox"):
            R.warn(jw, f"image '{img}': hosted runners need bash, git, curl and tar in the image — Alpine/busybox images lack them")
        if job.get("image_from"):
            check_consume_path(jw + ".image_from", job["image_from"], ctx, want_oci=True)
            edges[name].add(parse_selector(str(job["image_from"]).split("/")[0])[0])

        for i, n in enumerate(as_list(job.get("needs"))):
            if isinstance(n, dict):
                ref, sel = n.get("job"), n.get("matrix")
                sel = {str(k): str(v) for k, v in sel.items()} if isinstance(sel, dict) else None
            else:
                ref, sel = parse_selector(str(n))
            if ref not in jobs:
                R.error(f"{jw}.needs", f"no job '{ref}' in this body")
                continue
            check_selector(f"{jw}.needs", ref, sel, jobs)
            edges[name].add(ref)
        for c in as_list(job.get("consumes")):
            check_consume_path(f"{jw}.consumes", c, ctx)
            first = str(c).split("/")[0]
            if first != "..":
                edges[name].add(parse_selector(first)[0])

        produces = job.get("produces")
        if produces is not None:
            if not isinstance(produces, dict):
                R.error(jw, "produces: maps keys to a type (file, oci, npm), a path, or { type, path, expire, when }")
            else:
                for k, v in produces.items():
                    if not OUTPUT_KEY.match(str(k)):
                        R.error(f"{jw}.produces", f"key '{k}' must be letters, digits, _ and -")
                    if isinstance(v, dict):
                        for kk in v:
                            if kk not in ("type", "path", "expire", "when"):
                                R.error(f"{jw}.produces.{k}", f"unknown key '{kk}' — allowed: type, path, expire, when")
                        if v.get("type") not in (None, "file", "oci", "npm"):
                            R.error(f"{jw}.produces.{k}", "type is file, oci or npm")
                        if v.get("when") not in (None, "on_success", "always"):
                            R.error(f"{jw}.produces.{k}", "when is on_success or always")
                    elif not isinstance(v, str):
                        R.error(f"{jw}.produces.{k}", "a type (file, oci, npm), a path, or the long form")
                    gha = isinstance(delegate, dict) and delegate.get("type") == "github_actions"
                    vtype = v if isinstance(v, str) and v in ("file", "oci", "npm") else (v.get("type") if isinstance(v, dict) else None)
                    vpath = v.get("path") if isinstance(v, dict) else (None if vtype else v)
                    if (vtype in (None, "file")) and not vpath and not gha:
                        R.error(f"{jw}.produces.{k}", "a file entry needs a path (dist: dist, or { path: dist })")
                    if vtype in ("oci", "npm") and not gha:
                        keyed = any(isinstance(p, dict) and p.get("key") == k for p in as_list(job.get("publish")))
                        if not keyed and "pipemesh produce" not in text_of(job) and not job.get("uses"):
                            R.warn(f"{jw}.produces.{k}", f"an {vtype} entry is emitted at run time — add "
                                   f"`pipemesh produce {vtype} {k} --ref <ref> --digest <digest>` to the script")
            if isinstance(delegate, dict) and delegate.get("type") in ("workflow", "pipeline"):
                R.error(jw, "a delegating job produces nothing itself — consumers name the delegated body's "
                            f"entries by path ({name}/<job>/<key>)")
            if job.get("artifacts") is not None:
                R.error(jw, "produces replaces artifacts — declare the files as a file entry")
        for i, p in enumerate(as_list(job.get("publish"))):
            if not isinstance(p, dict):
                R.error(f"{jw}.publish[{i}]", "publish entries are mappings { repo?, context, dockerfile?, args?, key? }")
                continue
            for kk in p:
                if kk not in ("repo", "dockerfile", "context", "args", "key"):
                    R.error(f"{jw}.publish[{i}]", f"unknown key '{kk}' — allowed: repo, dockerfile, context, args, key")
            if p.get("key") and not OUTPUT_KEY.match(str(p["key"])):
                R.error(f"{jw}.publish[{i}]", "key must be letters, digits, _ and -")
            repo_ = p.get("repo")
            if repo_ is None:
                if not p.get("key"):
                    R.error(f"{jw}.publish[{i}]", "an image Pipemesh keeps (no repo:) needs key: — consumers use it with image_from")
            else:
                first = str(repo_).split("/")[0]
                if "://" in str(repo_) or str(repo_).startswith("/") or "." in first or ":" in first or first == "localhost":
                    R.error(f"{jw}.publish[{i}]", f"repo: '{repo_}' names a registry host — repo: is a path inside the "
                            "Pipemesh instance's own registry. To push to your registry, build and push in the script "
                            "and emit the image with `pipemesh produce oci <key> --ref <repo> --digest <sha256>`")
                elif ":" in str(repo_):
                    R.error(f"{jw}.publish[{i}]", "repo: takes no tag")
                else:
                    R.warn(f"{jw}.publish[{i}]", "publish: with repo: pushes to the Pipemesh instance's registry (self-hosted "
                           "instances). On pipemesh.io, push to your registry in the script and emit `pipemesh produce oci`")
        cache = job.get("cache")
        if cache is not None:
            if not isinstance(cache, dict) or "key" not in cache or "paths" not in cache:
                R.error(f"{jw}.cache", "cache needs key: and paths:")
            else:
                for kk in cache:
                    if kk not in ("key", "restore_keys", "paths", "policy"):
                        R.error(f"{jw}.cache", f"unknown key '{kk}' — allowed: key, restore_keys, paths, policy")
                if cache.get("policy") not in (None, "pull-push", "pull"):
                    R.error(f"{jw}.cache", "policy is pull-push or pull")
                if not as_list(cache.get("paths")):
                    R.error(f"{jw}.cache", "paths must be non-empty")
                for p in as_list(cache.get("paths")):
                    if str(p).startswith("/") or str(p).startswith("~"):
                        R.error(f"{jw}.cache", f"path '{p}' must be relative to the workspace (point the tool's cache dir inside it)")
        artifacts = job.get("artifacts")
        if artifacts is not None:
            R.warn(jw, "artifacts: is the old form — prefer produces: { <key>: <path> } so consumers can name it")
        for v in (job.get("variables") or {}):
            if v in ("CI_PIPELINE_SOURCE", "CI_COMMIT_TAG", "CI_MERGE_REQUEST_EVENT_TYPE") or str(v).startswith("CI_MERGE_REQUEST_"):
                R.error(f"{jw}.variables", f"{v} is set by the trigger, never by configuration")
        for s in as_list(job.get("config")):
            if not CONFIG_NAME.match(str(s)):
                R.error(f"{jw}.config", f"'{s}' — config names are UPPER_SNAKE_CASE")
        for s in as_list(job.get("secrets")):
            if not CONFIG_NAME.match(str(s)):
                R.error(f"{jw}.secrets", f"'{s}' — secret names are UPPER_SNAKE_CASE")
        for k in ("timeout_seconds", "retry"):
            v = job.get(k)
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                R.error(jw, f"{k} must be a non-negative integer")
        if job.get("repo") is not None and job["repo"] != "$self" and job["repo"] not in repos:
            R.error(jw, f"repo: '{job['repo']}' is not declared under the body's repos: ({', '.join(repos) or 'none'})")
        for alias, spec in (job.get("repos") or {}).items():
            if alias != "$self" and alias not in repos:
                R.error(f"{jw}.repos", f"'{alias}' is not declared under the body's repos:")
            if not isinstance(spec, dict) or "mount" not in spec:
                R.error(f"{jw}.repos.{alias}", "needs mount: (where it goes, relative to the workspace)")
            elif str(spec["mount"]).startswith("/") or ".." in str(spec["mount"]).split("/"):
                R.error(f"{jw}.repos.{alias}", "mount can't leave the workspace (no leading /, no ..)")
        if job.get("was") is not None and job.get("matrix") is not None:
            R.error(jw, "was: is not allowed on a matrix job")

        # Matrix
        mx = job.get("matrix")
        txt = text_of(job) + "\n" + str(job.get("needs", "")) + str(job.get("consumes", "")) + str(job.get("with", "")) + str(job.get("setup", ""))
        if mx is not None:
            if not isinstance(mx, dict):
                R.error(f"{jw}.matrix", "variable: [values] (plus optional as: jobs | workflow)")
            else:
                if mx.get("as") not in (None, "jobs", "workflow"):
                    R.error(f"{jw}.matrix", "as: is jobs or workflow")
                axes = matrix_axes(job)
                total = 1
                for k, vals in axes.items():
                    if not isinstance(vals, list) or not vals:
                        R.error(f"{jw}.matrix.{k}", "needs a non-empty list of values")
                        continue
                    total *= len(vals)
                    for v in vals:
                        if any(ch in str(v) for ch in "[],=") or not str(v).strip():
                            R.error(f"{jw}.matrix.{k}", f"value '{v}' must not be blank or contain [ ] , =")
                if total > MAX_MATRIX:
                    R.error(f"{jw}.matrix", f"expands to {total} variants — the cap is {MAX_MATRIX}")
                for ref in set(MATRIX_REF.findall(txt)):
                    if ref not in axes:
                        R.error(jw, f"uses ${{{{ matrix.{ref} }}}} but the matrix declares: {', '.join(axes)}")
        elif MATRIX_REF.search(txt):
            R.error(jw, "uses ${{ matrix.* }} but declares no matrix")
        for expr in set(m.group(0) for m in GHA_EXPR.finditer(text_of(job))):
            R.warn(jw, f"'{expr}' is a GitHub Actions expression — it is not interpolated here; use a variable "
                       "($CI_COMMIT_SHA, $CI_COMMIT_REF_NAME, a declared secret's name, …)")

    # needs/consumes cycles
    seen, onstack = set(), []

    def visit(n):
        if n in onstack:
            cyc = onstack[onstack.index(n):] + [n]
            R.error(where, f"needs/consumes form a cycle: {' -> '.join(cyc)}")
            return
        if n in seen:
            return
        seen.add(n)
        onstack.append(n)
        for m in edges.get(n, ()):
            if m in edges:
                visit(m)
        onstack.pop()

    for n in edges:
        visit(n)


def check_delegate(jw, name, job, delegate, kind, repo):
    if not isinstance(delegate, dict) or "type" not in delegate:
        R.error(jw, "delegate takes { type: workflow | pipeline | github_actions, params: … }")
        return
    for k in delegate:
        if k not in ("type", "params"):
            R.error(f"{jw}.delegate", f"unknown key '{k}' — delegate takes type and params")
    t = delegate.get("type")
    params = delegate.get("params") or {}
    if not isinstance(params, dict):
        R.error(f"{jw}.delegate", "params must be a mapping")
        return
    for k in sorted(DELEGATE_EXCLUSIVE & set(job)):
        R.error(jw, f"'{k}' can't sit beside delegate: — the delegated run is the whole execution")
    if t in ("workflow", "pipeline"):
        if t == "pipeline" and kind != "pipeline":
            R.error(f"{jw}.delegate", "type: pipeline — only a pipeline dispatches, and this body is a workflow's")
        for k in params:
            if k not in ("body", "workload", "variables"):
                R.error(f"{jw}.delegate.params", f"unknown key '{k}' — allowed: body, workload, variables")
        if ("body" in params) == ("workload" in params):
            R.error(f"{jw}.delegate.params", "needs exactly one of body (!ref, !include or inline) or workload")
        if "variables" in params and not isinstance(params["variables"], dict):
            R.error(f"{jw}.delegate.params", "variables must be a mapping")
        if "body" in params:
            check_body(f"{jw}.delegate.params.body", params["body"], t, repo, delegated=True)
    elif t == "github_actions":
        for k in params:
            if k not in ("workflow", "ref", "inputs", "artifacts"):
                R.error(f"{jw}.delegate.params", f"unknown key '{k}' — allowed: workflow, ref, inputs, artifacts")
        check_gha_workflow(f"{jw}.delegate", params, repo)
    else:
        R.error(f"{jw}.delegate", f"unknown type '{t}' — one of workflow, pipeline, github_actions")

# ---------------------------------------------------------------------------
# Registration

def cache_path_sets(body):
    """(job name, sorted cache paths) for every cached job of a body and its delegated bodies."""
    out = []
    if not isinstance(body, dict) or not isinstance(body.get("jobs"), dict):
        return out
    for name, j in body["jobs"].items():
        if not isinstance(j, dict):
            continue
        c = j.get("cache")
        if isinstance(c, dict) and c.get("paths"):
            out.append((name, tuple(sorted(str(x) for x in as_list(c["paths"])))))
        d = j.get("delegate")
        if isinstance(d, dict) and isinstance(d.get("params"), dict):
            out.extend(cache_path_sets(d["params"].get("body")))
    return out


def pr_cache_warnings(registrations):
    """A pull-request run never saves; it restores what another workload saved with the same paths."""
    for where, body, kinds in registrations:
        if kinds != {"pull_request"}:
            continue
        others = {paths for w2, b2, _ in registrations if w2 != where for _, paths in cache_path_sets(b2)}
        for job, paths in cache_path_sets(body):
            if paths not in others:
                R.warn(f"{where}.jobs.{job}", f"cache paths {list(paths)}: pull-request runs never save, and no "
                       "other workload in this manifest saves a cache with these paths, so this job always starts "
                       "cold — share the cache definition with the default-branch job that runs the same install")


def check_inputs(where, entry):
    declared = entry.get("inputs") or {}
    if not isinstance(declared, dict):
        R.error(where, "inputs: maps names to a default (or { default, description })")
        return {}
    out = {}
    for k, v in declared.items():
        out[str(k)] = v.get("default") if isinstance(v, dict) else v
    return out


def check_trigger(where, trig, declared):
    trig = bool_key_fix(trig, where, True) if isinstance(trig, dict) else trig
    if not isinstance(trig, dict):
        R.error(where, "a trigger is a mapping with on:")
        return
    for k in trig:
        if k not in TRIGGER_KEYS:
            R.error(where, f"unknown key '{k}' — allowed: {', '.join(sorted(TRIGGER_KEYS))}")
    on = trig.get("on")
    if on is None:
        R.error(where, "missing on: <event kind>")
        return
    if on not in EVENT_KINDS:
        R.error(where, f"unknown event kind '{on}' — one of {', '.join(sorted(EVENT_KINDS))}")
        return
    for f, kind in FILTER_FOR_KIND.items():
        if f in trig and on != kind:
            R.error(where, f"{f}: filters on: {kind} only")
    if on == "push" and "branches" in trig:
        R.warn(where, "on: push only ever sees the default branch, so branches: adds nothing — leave it out")
    for t in as_list(trig.get("targets")):
        if any(ch in str(t) for ch in "*?["):
            R.warn(where, f"targets: '{t}' — target branches are exact names, not globs")
    if on == "schedule":
        if "cron" not in trig:
            R.error(where, "on: schedule needs cron: (quoted)")
        elif not isinstance(trig["cron"], str):
            R.error(where, "cron: must be a quoted string, e.g. cron: \"0 3 * * *\"")
    given = trig.get("inputs") or {}
    for k in given:
        if k not in declared:
            R.error(where, f"undeclared input '{k}' — declared: {', '.join(declared) or 'none'}")
    for k, default in declared.items():
        if default is None and k not in given:
            R.error(where, f"required input '{k}' has no default and no value")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    repo = Repo(root)
    manifest_path = os.path.join(repo.root, "pipemesh.yaml")
    if not os.path.isfile(manifest_path):
        print(f"no pipemesh.yaml at {repo.root}")
        return 1
    raw = repo.load("pipemesh.yaml", "pipemesh.yaml")
    if raw is None:
        return finish()
    if not isinstance(raw, dict):
        R.error("pipemesh.yaml", "must be a mapping")
        return finish()
    raw = bool_key_fix(raw, "pipemesh.yaml", False)
    for k in raw:
        if k != "pipemesh" and not DEFINITION_NAME.match(k):
            R.error("pipemesh.yaml", f"definition name '{k}' must be letters, digits, _ and - (it is a path segment for !ref)")
    if "pipemesh" not in raw:
        R.error("pipemesh.yaml", "registers its workloads under the pipemesh: key — "
                "pipemesh: { pipelines: { pipeline: !ref <definition> }, workflows: { <name>: { body: …, on: … } } }")
        return finish()

    res = Resolver(repo)
    resolved = {}
    for k, v in raw.items():
        res.current_root_key = k
        resolved[k] = res.resolve(v, raw)
    res.current_root_key = None

    # Every root key must be reached from pipemesh:
    reached, queue = set(), list(res.root_refs.get("pipemesh", ()))
    while queue:
        n = queue.pop()
        if n not in reached:
            reached.add(n)
            queue.extend(res.root_refs.get(n, ()))
    for k in raw:
        if k != "pipemesh" and k not in reached:
            R.error("pipemesh.yaml", f"'{k}' is defined but never reached from pipemesh: (through !ref) — a typo, or a definition nothing uses")

    pm = resolved["pipemesh"]
    if not isinstance(pm, dict):
        R.error("pipemesh", "must be a mapping holding pipelines: and/or workflows:")
        return finish()
    for k in pm:
        if k not in ("pipelines", "workflows"):
            R.error("pipemesh", f"unknown key '{k}' — pipemesh: holds pipelines: and workflows:")
    pipelines = pm.get("pipelines") or {}
    workflows = pm.get("workflows") or {}
    if not pipelines and not workflows:
        R.error("pipemesh", "registers no workloads — add pipelines: or workflows:")
    if not isinstance(pipelines, dict) or not isinstance(workflows, dict):
        R.error("pipemesh", "pipelines: and workflows: are mappings of name -> entry")
        return finish()
    if len(pipelines) > 1:
        R.error("pipemesh.pipelines", "supports one entry for now")
    names = set()
    registrations = []
    for section, entries in (("pipelines", pipelines), ("workflows", workflows)):
        for name, entry in entries.items():
            name = str(name)
            w = f"pipemesh.{section}.{name}"
            if not WORKLOAD_NAME.match(name):
                R.error(w, "workload names match [a-z0-9][a-z0-9_-]*")
            if name in RESERVED_WORKLOAD_NAMES:
                R.error(w, f"'{name}' is reserved")
            if section == "workflows" and name == "pipeline":
                R.error(w, "a workflow may not be named pipeline — that is the pipeline's slot")
            if section == "pipelines" and name != "pipeline":
                R.warn(w, "name the repository's pipeline 'pipeline' (its URL is then /<repo>/-/pipeline)")
            if name in names:
                R.error(w, "workload names are unique across pipelines: and workflows:")
            names.add(name)
            kind = "pipeline" if section == "pipelines" else "workflow"
            if isinstance(entry, dict):
                entry = bool_key_fix(entry, w, True)
            if kind == "pipeline":
                registrations.append((w, entry.get("body") if isinstance(entry, dict) and "body" in entry else entry, {"pipeline"}))
            if isinstance(entry, dict) and "body" in entry:
                allowed = PIPELINE_ENTRY_KEYS if kind == "pipeline" else WORKFLOW_ENTRY_KEYS
                for k in entry:
                    if k not in allowed:
                        extra = " — pipelines take no triggers: every commit on the default branch is a revision" \
                            if kind == "pipeline" and k in ("on", "triggers", "branches", "cron", "tags", "targets") else ""
                        R.error(w, f"unknown key '{k}'{extra}")
                if "file" in entry:
                    R.error(w, "file: was removed — write body: !include <path>")
                body = entry["body"]
                if not isinstance(body, dict):
                    R.error(w, "body: takes !ref <definition>, !include <path>, or inline stages:/jobs:")
                    continue
                if "repos" in entry:
                    body = dict(body, repos=entry["repos"])
                check_body(w, body, kind, repo, delegated=False)
            elif isinstance(entry, dict) and ("jobs" in entry or "stages" in entry):
                body = {k: v for k, v in entry.items() if k in BODY_KEYS}
                rest = {k: v for k, v in entry.items() if k not in BODY_KEYS}
                rest["body"] = body
                for k in rest:
                    if k not in (WORKFLOW_ENTRY_KEYS if kind == "workflow" else PIPELINE_ENTRY_KEYS):
                        R.error(w, f"unknown key '{k}'")
                check_body(w, body, kind, repo, delegated=False)
                entry = rest
            elif isinstance(entry, dict) and kind == "pipeline":
                check_body(w, entry, kind, repo, delegated=False)
                continue
            elif isinstance(entry, dict):
                R.error(w, "needs a body — body: !ref <definition>, body: !include <path>, or inline stages/jobs")
                continue
            else:
                R.error(w, "takes a body (!ref, !include, inline) or { body: … }")
                continue
            if kind == "workflow":
                kinds = set()
                if isinstance(entry.get("triggers"), dict):
                    kinds = {t.get("on", t.get(True)) for t in entry["triggers"].values() if isinstance(t, dict)}
                elif "on" in entry:
                    kinds = {entry["on"]}
                registrations.append((w, entry.get("body"), kinds))
                declared = check_inputs(w, entry)
                if "on" in entry and "triggers" in entry:
                    R.error(w, "on: and triggers: are mutually exclusive")
                elif "on" in entry:
                    check_trigger(w, {k: entry[k] for k in TRIGGER_KEYS if k in entry and k != "inputs"}, declared)
                elif "triggers" in entry:
                    trigs = entry["triggers"]
                    if not isinstance(trigs, dict):
                        R.error(w, "triggers: is a named map of { on: … }")
                    else:
                        for tname, trig in trigs.items():
                            check_trigger(f"{w}.triggers.{tname}", trig, declared)
                else:
                    for k in ("branches", "targets", "tags", "cron"):
                        if k in entry:
                            R.error(w, f"{k}: without on:")
    pr_cache_warnings(registrations)
    return finish()


def finish():
    for line in R.errors + R.warnings:
        print(line)
    print(f"\n{len(R.errors)} error(s), {len(R.warnings)} warning(s)")
    return 1 if R.errors else 0


if __name__ == "__main__":
    sys.exit(main())
