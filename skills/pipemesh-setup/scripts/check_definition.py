#!/usr/bin/env python3
"""Static checks for a Pipemesh definition (pipemesh.yaml) before it is committed.

Usage:
    python3 check_definition.py [REPO_ROOT] [--quiet]   # default: the current directory

Reads REPO_ROOT/pipemesh.yaml, resolves `!include` and `!ref` the way the
Pipemesh loader does, and reports what the loader would reject (ERROR) and
what is legal but probably not what you meant (WARN). Then it lists every
job's effective kind, checkout and skip policy, and where each came from
(--quiet leaves that list out). Exit code 1 when there is at least one ERROR.

This mirrors the loader's documented rules; it is not the loader. The
authoritative check happens when the definition lands on the branch
of an enabled repository: a load error shows on the repository's page in
Pipemesh, naming the file and key.

Needs PyYAML. Without it: `pip install pyyaml`, or run with
`uv run --with pyyaml python3 check_definition.py`.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "check_definition.py needs PyYAML: pip install pyyaml "
        "(or: uv run --with pyyaml python3 check_definition.py)\n")
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
CHECKSUM = re.compile(r"\$\{checksum:([^}]+)}")

BODY_KEYS = {"stages", "jobs", "variables", "repos"}

# DESIGN-V72: every job has a kind. It sets the job's checkout: and skip:
# defaults and where the job may appear; absent kind: is a task.
KINDS = ("build", "deploy", "task", "workflow", "pipeline")
SCRIPT_KINDS = {"build", "deploy", "task"}       # run on an executor
CHILD_KINDS = {"workflow", "pipeline"}                          # run a body
KIND_DEFAULTS = {                                               # (checkout, skip)
    "build": (True, "built"),
    "deploy": (False, "unchanged"),
    "task": (False, "never"),
}
CHILD_KIND_KEYS = {"kind", "body", "workload", "variables", "stage", "needs", "consumes",
                   "checkout", "skip", "timeout_seconds", "allow_failure", "matrix", "was"}
JOB_KEYS = {
    "stage", "script", "before_script", "after_script", "needs", "tags",
    "variables", "image", "image_from", "skip", "config", "repo",
    "repos", "services", "secrets", "artifacts", "cache", "publish",
    "allow_failure", "timeout_seconds", "retry", "matrix", "uses", "with",
    "setup", "produces", "consumes", "was",
    "kind", "checkout", "github_actions", "body", "workload",
}
SCRIPT_KEYS = ("script", "setup", "before_script", "after_script")
EXECUTION_KEYS = {"script", "setup", "image", "services", "before_script", "after_script"}
# An Actions run is the whole execution: nothing that only means something
# on a Pipemesh runner sits beside github_actions:.
GHA_EXCLUSIVE = ("script", "before_script", "after_script", "uses", "setup", "tags", "image",
                 "image_from", "services", "secrets", "artifacts", "cache", "publish")
# Keys a component's job: may never declare — they are the job's.
PLACEMENT_KEYS = {"stage", "needs", "tags", "trigger", "timeout_seconds", "retry", "allow_failure",
                  "variables", "artifacts", "cache", "publish", "secrets", "matrix", "when",
                  "produces", "consumes", "image_from", "paths", "skip", "config", "repo", "repos",
                  "kind", "checkout", "github_actions"}
EVENT_KINDS = {"push", "pull_request", "tag", "schedule", "manual"}
FILTER_FOR_KIND = {"branches": "push", "targets": "pull_request", "tags": "tag", "cron": "schedule"}
WORKFLOW_ENTRY_KEYS = {"body", "stages", "jobs", "variables", "inputs", "on",
                       "triggers", "branches", "targets", "tags", "cron", "repos"}
PIPELINE_ENTRY_KEYS = {"body", "stages", "jobs", "variables", "inputs", "repos"}
TRIGGER_KEYS = {"on", "branches", "targets", "tags", "cron", "inputs"}

BODY_KIND_GONE = ("a body's own kind: is gone (DESIGN-V72) — where it is registered (pipelines: or "
                  "workflows:) or the job that runs it (kind: workflow / kind: pipeline) says what it is")

# Keys the grammar removed, with what replaces them.
REMOVED_JOB_KEYS = {
    "paths": "paths: is now checkout: — true (the whole repository), false (nothing) or a list of paths",
    "trigger": "trigger: is gone — kind: workflow or kind: pipeline with body:, or github_actions: on a "
               "build, deploy or task",
    "sources": "sources: is now checkout:",
    "rules": "rules: is gone — checkout: lists the files the job reads, skip: says when it may skip",
}

# Hints for keys people bring from other CI systems.
FOREIGN_KEY_HINTS = {
    "timeout": "write timeout_seconds: (an integer)",
    "when": "there is no when:; a job runs when what it needs approved the revision. "
            "Manual holds are an operator action in Pipemesh (see onboarding.md)",
    "only": "only:/except: have no equivalent: filter with the workflow's trigger "
            "(branches:/targets:/tags:) and the job's checkout:",
    "except": "only:/except: have no equivalent: filter with the workflow's trigger and checkout:",
    "extends": "no extends: — reuse a whole job with !ref, or vary it with a component (uses:/with:)",
    "dependencies": "dependencies: is implicit — entries flow along consumes: edges",
    "environment": "no environment: key — kind: deploy marks a deploy; the job name says where it deploys",
    "interruptible": "not needed — a newer revision supersedes queued ones on its own",
    "resource_group": "not needed — a pipeline job runs one revision at a time",
    "runs-on": "use tags: [<queue>] for your own runners, or nothing for hosted runners",
    "steps": "a job has script: (shell), not steps:",
    "env": "use variables:",
    "if": "no if: — use triggers, checkout: and skip:",
    "coverage": "not supported",
    "parallel": "use matrix:",
    "strategy": "use matrix: (and matrix.as: workflow for a CI-style matrix)",
    "timeout-minutes": "write timeout_seconds:",
    "continue-on-error": "write allow_failure: true",
    "container": "write image:",
    "outputs": "use produces: (files, images, packages) and consumes: on the other side",
    "type": "a job's type is kind: (build, deploy, task, workflow, pipeline)",
}

KIND_HINTS = {
    "test": "a test is a build (it reads the source and is hermetic), or a task when it reads "
            "pull-request context such as a merge-base diff",
    "lint": "a lint is a build", "check": "a check is a build, or a task when it must run every time",
    "release": "a release is a deploy (in a pipeline) or a task (in a workflow)",
    "publish": "a publish is a deploy (in a pipeline) or a task (in a workflow)",
    "package": "packaging is a build (checkout: false when it reads only what it consumes)",
    "transform": "transform is gone: a job that turns what it consumes into something else is a build with checkout: false",
    "delegate": "kind: workflow or kind: pipeline run a body; github_actions: is an executor",
    "github_actions": "github_actions: is an executor key on a build, deploy or task",
    "image": "an image build is a build", "notify": "a notification is a task",
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
WHOLE = ["**"]

# ---------------------------------------------------------------------------
# Reporting


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.rules: list[str] = []

    def error(self, where: str, msg: str):
        line = f"ERROR {where}: {msg}"
        if line not in self.errors:
            self.errors.append(line)

    def warn(self, where: str, msg: str):
        line = f"WARN  {where}: {msg}"
        if line not in self.warnings:
            self.warnings.append(line)


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
        self.own = own_repository_name(self.root)

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

    def exists(self, rel: str) -> bool:
        return bool(rel) and os.path.exists(os.path.join(self.root, rel))

    def read_text(self, rel: str):
        try:
            with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None


def own_repository_name(root):
    """owner/name of the checkout's origin, as Actions names it (for actions/checkout repository:)."""
    try:
        url = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"[:/]([^/:]+/[^/]+?)(\.git)?/?$", url)
    return m.group(1) if m else None


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
        self.stack: list = []
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
            R.error(where, f"!ref cycle: {' -> '.join(p for _, p in self.stack if isinstance(_, str))} -> {path}")
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
# Helpers


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


def relative(p: str) -> str:
    t = p.strip()
    while t.startswith("./"):
        t = t[2:]
    while t.endswith("/") and len(t) > 1:
        t = t[:-1]
    return t


def is_glob(p: str) -> bool:
    return any(ch in p for ch in "*?[")


def show_paths(paths) -> str:
    if paths == WHOLE:
        return "true (the whole repository)"
    if not paths:
        return "false (nothing)"
    return "[" + ", ".join(paths) + "]"


def covers(paths, file: str) -> bool:
    """Whether a checkout (a list of paths, ["**"] = all) puts `file` (or part of a directory) in the workspace."""
    if paths == WHOLE:
        return True
    for p in paths:
        q = relative(p)
        if is_glob(q):
            if fnmatch.fnmatchcase(file, q) or fnmatch.fnmatchcase(file, q + "/*"):
                return True
            prefix = q[:min(q.index(ch) for ch in "*?[" if ch in q)].rstrip("/")
            if prefix and (file == prefix or file.startswith(prefix + "/") or prefix.startswith(file + "/")):
                return True
        elif file == q or file.startswith(q + "/") or q.startswith(file + "/"):
            return True
    return False


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


def output_paths(job: dict) -> list:
    """Workspace paths of a job's file entries (for telling produced files from repository files)."""
    out = []
    produces = job.get("produces")
    if isinstance(produces, dict):
        for v in produces.values():
            p = v.get("path") if isinstance(v, dict) else (v if isinstance(v, str) and v not in ("file", "oci", "npm") else None)
            if isinstance(p, str) and "$" not in p:
                base = p.split("*")[0].rstrip("/")
                if base:
                    out.append(relative(base))
    return out


def job_kind(job: dict) -> str:
    k = job.get("kind")
    return k if isinstance(k, str) and k in KINDS else "task"


def check_consume_path(where, raw, body_ctx, want_oci=False):
    jobs = body_ctx["jobs"]
    if not isinstance(raw, str) or not CONSUME_PATH.match(raw):
        R.error(where, f"'{raw}' is not a consume path (<job>/<key>, <job>/<inner job>/<key>, ../<job>/<key>)")
        return
    segs = raw.split("/")
    if segs[0] == "..":
        if not body_ctx["delegated"]:
            R.error(where, f"'{raw}': ../ names the workload that runs this body, and this body is registered on its own")
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
    pkind = job_kind(producer)
    if pkind == "pipeline":
        R.error(where, f"consumes '{raw}': '{name}' is a kind: pipeline job — it hands the revision to a child "
                       "pipeline, which exports nothing")
        return
    if pkind == "workflow":
        if len(segs) < 3:
            R.error(where, f"consumes '{raw}': '{name}' is a kind: workflow job — name a job of its body "
                           f"({name}/<job>/<key>)")
            return
        child = body_ctx["children"].get(name)
        if isinstance(child, dict):
            inner = child.get("jobs") or {}
            iname, _ = parse_selector(segs[1])
            if iname not in inner:
                R.error(where, f"consumes '{raw}': the body '{name}' runs has no job '{iname}'")
            elif len(segs) == 3 and isinstance(inner[iname], dict) and segs[2] not in outputs_of(inner[iname]):
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


def load_local_component(uses, repo: Repo):
    path = os.path.join(repo.root, uses[2:])
    if not os.path.isfile(path):
        return None, f"component file not found: {uses}"
    with open(path, encoding="utf-8") as fh:
        try:
            return yaml.safe_load(fh), None
        except yaml.YAMLError as e:
            return None, f"{uses} is not valid YAML: {e}"


def check_component_use(where, uses, with_, repo: Repo, setup_step=False):
    if not isinstance(uses, str):
        R.error(where, "uses: takes a component reference")
        return
    m = REGISTRY_REF.match(uses)
    if uses.startswith("./"):
        comp, err = load_local_component(uses, repo)
        if err:
            R.error(where, err)
            return
        if not isinstance(comp, dict) or "component" not in comp or "job" not in comp:
            R.error(where, f"{uses} must declare component: <name> and job:")
            return
        unknown = set(comp) - {"component", "params", "job"}
        if unknown:
            R.error(where, f"{uses}: unknown top-level key(s) {sorted(unknown)} (component/params/job)")
        if isinstance(comp.get("job"), dict):
            for k in sorted(PLACEMENT_KEYS & set(comp["job"])):
                R.error(where, f"{uses}: '{k}' is a placement key and belongs to the job that uses the "
                               "component (kind:, checkout:, stage:, … are the job's), never to the component")
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


def script_text_with_components(job: dict, repo: Repo) -> str:
    """The job's own script plus the scripts of the local components it runs (uses:, setup:)."""
    parts = [text_of(job)]
    refs = [job.get("uses")] + [s.get("uses") for s in as_list(job.get("setup")) if isinstance(s, dict)]
    for u in refs:
        if isinstance(u, str) and u.startswith("./"):
            comp, _ = load_local_component(u, repo)
            if isinstance(comp, dict) and isinstance(comp.get("job"), dict):
                parts.append(text_of(comp["job"]))
    return "\n".join(parts)

# ---------------------------------------------------------------------------
# GitHub Actions: dispatch contract and the derived checkout (DESIGN-V72 §5)


def gha_params(jw, raw):
    if isinstance(raw, str):
        return {"workflow": raw}
    if isinstance(raw, dict):
        for k in raw:
            if k not in ("workflow", "ref", "inputs", "artifacts"):
                R.error(f"{jw}.github_actions", f"unknown key '{k}' — allowed: workflow, ref, inputs, artifacts")
        return raw
    R.error(jw, "github_actions: takes the workflow file (deploy.yml) or { workflow, ref, inputs, artifacts }")
    return None


def check_gha_workflow(where, params, repo: Repo):
    wf = params.get("workflow")
    if not isinstance(wf, str) or not wf.strip():
        R.error(where, "github_actions: names the Actions workflow (its file under .github/workflows, e.g. deploy.yml)")
        return
    inputs = params.get("inputs") or {}
    if not isinstance(inputs, dict):
        R.error(where, "github_actions.inputs must be a mapping")
        inputs = {}
    if len(inputs) > 8:
        R.error(where, f"github_actions.inputs has {len(inputs)} entries — at most 8 (GitHub caps a dispatch at 10, two are Pipemesh's)")
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


def derive_actions_checkout(workflow, repo: Repo):
    """What a dispatched workflow checks out of this repository, read from its file (DESIGN-V72 §5).

    Returns (paths, why): ["**"] for the whole tree, else the sparse patterns
    plus every workflow file read (always part of the fingerprint)."""
    if not isinstance(workflow, str) or not workflow.strip() or workflow.isdigit():
        return WHOLE, "nothing: a numeric workflow id can't be read, so the whole repository"
    first = relative(workflow) if "/" in workflow else ".github/workflows/" + workflow
    whole, why, root_files = False, "", False
    patterns, files, queue = set(), [], [first]
    while queue:
        path = queue.pop(0)
        if path in files:
            continue
        files.append(path)
        text = repo.read_text(path)
        try:
            doc = yaml.safe_load(text) if text is not None else None
        except yaml.YAMLError:
            doc = None
        if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
            whole, why = True, why or f"{path}, which can't be read here, so the whole repository"
            continue
        for job in doc["jobs"].values():
            if not isinstance(job, dict):
                continue
            if isinstance(job.get("uses"), str):
                if job["uses"].startswith("./"):
                    queue.append(relative(job["uses"].split("@", 1)[0]))
                else:
                    whole, why = True, why or f"{path}: it calls a remote reusable workflow, so the whole repository"
                continue
            for st in as_list(job.get("steps")):
                if not isinstance(st, dict) or not isinstance(st.get("uses"), str):
                    continue
                u = st["uses"]
                if u != "actions/checkout" and not u.startswith("actions/checkout@"):
                    continue
                w = st.get("with") if isinstance(st.get("with"), dict) else {}
                r = w.get("repository")
                if r is not None and str(r).strip() and "${{" not in str(r) and \
                        not (repo.own and str(r).strip().lower() == repo.own.lower()):
                    continue  # another repository's checkout
                sparse = w.get("sparse-checkout")
                sparse = "\n".join(str(x) for x in sparse) if isinstance(sparse, list) else ("" if sparse is None else str(sparse))
                if not sparse.strip():
                    whole, why = True, why or f"{path}: actions/checkout without sparse-checkout"
                    continue
                if str(w.get("sparse-checkout-cone-mode")).lower() != "false":
                    root_files = True
                for line in sparse.splitlines():
                    p = relative(line.strip()) if line.strip() else ""
                    p = p.lstrip("/")
                    if p.startswith("!") or p.startswith("#"):
                        whole, why = True, why or f"{path}: sparse-checkout with a negation or a comment, so the whole repository"
                        continue
                    if p:
                        patterns.add(p.rstrip("/"))
    if whole:
        return WHOLE, why
    out = set(patterns) | set(files)
    if root_files:
        out.add("*")
    how = "its sparse-checkout patterns" if patterns else "no checkout step of this repository"
    return sorted(out), f"{files[0]}: {how}, plus the workflow file{'s' if len(files) > 1 else ''}"

# ---------------------------------------------------------------------------
# checkout: (DESIGN-V72 §4) and what the platform adds to it (§8)


def checkout_paths(where, checkout):
    """checkout: -> the model's paths (["**"] for true, [] for false), or None on an error."""
    if isinstance(checkout, bool):
        return WHOLE if checkout else []
    if isinstance(checkout, list):
        if not checkout:
            R.error(where, "checkout: [] checks out nothing; write checkout: false")
            return None
        out = []
        for p in checkout:
            if not isinstance(p, str) or not p.strip():
                R.error(where, f"checkout: lists repository paths, got {p!r}")
                return None
            if p.strip() == "**":
                R.error(where, 'checkout: ["**"] is the whole repository; write checkout: true')
                return None
            if p.strip().startswith("/") or ".." in p.strip().split("/"):
                R.error(where, f"checkout: '{p}' — paths are relative to the repository root (no leading /, no ..)")
            out.append(p.strip())
        return out
    if isinstance(checkout, str):
        R.error(where, f"checkout: is true, false or a list of paths — write checkout: [{checkout}]")
        return None
    R.error(where, "checkout: is true (the whole repository), false (nothing) or a list of paths")
    return None


def platform_reads(job: dict, paths: list):
    """Files the bootstrap itself reads join the checkout: ${checksum:} files of the
    cache key and publish: contexts/Dockerfiles. Mutates paths; returns [(path, why)]."""
    added = []
    if paths == WHOLE:
        return added

    def add(file, why):
        for p in paths:
            q = relative(p)
            if not is_glob(q) and (file == q or file.startswith(q + "/")):
                return
        if file not in paths:
            paths.append(file)
            added.append((file, why))

    cache = job.get("cache")
    if isinstance(cache, dict) and isinstance(cache.get("key"), str):
        for m in CHECKSUM.finditer(cache["key"]):
            add(m.group(1).strip(), "the cache key")
    for p in as_list(job.get("publish")):
        if not isinstance(p, dict):
            continue
        ctx = relative(p["context"]) if isinstance(p.get("context"), str) else ""
        if ctx in ("", "."):
            paths[:] = WHOLE
            added.append((".", "publish: (its context is the repository root)"))
            return added
        add(ctx, "publish:")
        if isinstance(p.get("dockerfile"), str) and p["dockerfile"].strip():
            add(relative(p["dockerfile"]), "publish:")
    return added


def check_mounts(jw, job, repos):
    for alias, spec in (job.get("repos") or {}).items():
        mw = f"{jw}.repos.{alias}"
        if alias != "$self" and alias not in repos:
            R.error(f"{jw}.repos", f"'{alias}' is not declared under the body's repos:")
        if not isinstance(spec, dict):
            R.error(mw, "a mount is { mount: <dir>, checkout: true | [paths] }")
            continue
        for k in spec:
            if k == "paths":
                R.error(mw, "paths: is now checkout: (true or a list)")
            elif k not in ("mount", "checkout"):
                R.error(mw, f"unknown key '{k}' — a mount has mount: and checkout:")
        if "mount" not in spec:
            R.error(mw, "needs mount: (where it goes, relative to the workspace)")
        elif str(spec["mount"]).startswith("/") or ".." in str(spec["mount"]).split("/") or str(spec["mount"]).strip() in ("", "."):
            R.error(mw, "mount can't leave the workspace (no leading /, no .., not '.')")
        co = spec.get("checkout")
        if (co is None and "paths" not in spec) or co is False:
            R.error(mw, "needs checkout: (true or a list): a mount exists to be read")
        else:
            checkout_paths(mw, co)

# ---------------------------------------------------------------------------
# "The script reads a file its checkout leaves out" (the checkout is enforced)

SHELL_SPLIT = re.compile(r"&&|\|\||;|\||\(|\)|`")


def mentioned_repo_paths(text: str, repo: Repo):
    """Repository paths a script names, resolved against the root and every cd target."""
    words, cds = [], [""]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for seg in SHELL_SPLIT.split(line):
            ws = seg.split()
            while ws and ws[0] in ("{", "}", "!", "then", "do", "else", "if", "while", "until", "time", "exec"):
                ws = ws[1:]
            if not ws or ws[0] in ("echo", "printf") or ws[0].startswith("#"):
                continue
            if ws[0] == "cd" and len(ws) > 1:
                d = ws[1].strip("'\"")
                if "$" not in d and not d.startswith(("/", "~", "-")):
                    cds.append(relative(d))
                    words.append(relative(d))
                continue
            for w in ws:
                w = w.strip("'\"").rstrip(";")
                if not w or "$" in w or "=" in w or "://" in w or w.startswith(("-", "/", "~", "<", ">", "2>", "&")):
                    continue
                if "/" not in w and "." not in w.lstrip("."):
                    continue  # a bare word: a command, an argument, prose
                if is_glob(w):
                    head = w[:min(w.index(ch) for ch in "*?[" if ch in w)]
                    if "/" not in head:
                        continue
                    w = head.rsplit("/", 1)[0]
                w = relative(w)
                if w and w not in (".", ".."):
                    words.append(w)
    found = []
    for w in dict.fromkeys(words):
        cands = [c for c in dict.fromkeys(os.path.normpath(os.path.join(b, w)) if b else os.path.normpath(w) for b in cds)
                 if not c.startswith("..") and repo.exists(c)]
        if cands:
            found.append(cands)
    return found


def check_script_reads(jw, job, eff, repo: Repo, produced: list):
    """WARN when a script names a repository path its (enforced) checkout leaves out."""
    if eff["paths"] == WHOLE or eff.get("derived") or job.get("repo") not in (None, "$self") or job.get("repos"):
        return
    text = script_text_with_components(job, repo)
    if not text.strip():
        return
    cache_paths = [relative(str(p)) for p in as_list((job.get("cache") or {}).get("paths"))] if isinstance(job.get("cache"), dict) else []
    outside = []
    for cands in mentioned_repo_paths(text, repo):
        if any(covers(eff["paths"], c) or covers(produced + cache_paths, c) for c in cands):
            continue
        outside.append(cands[0])
    if outside:
        R.warn(jw, f"its script names {', '.join(outside)}, which {'is' if len(outside) == 1 else 'are'} in the "
                   f"repository but not in its checkout ({show_paths(eff['paths'])}). The checkout is enforced: "
                   "the workspace holds only what it lists, so the job fails on a missing file. Add "
                   f"{'it' if len(outside) == 1 else 'them'} to checkout: (or ignore this if the script "
                   "only names the path)")

# ---------------------------------------------------------------------------
# Body and job checks


def check_body(where: str, body, kind: str, repo: Repo, delegated: bool, depth: int = 0):
    """Checks a body used as `kind` ('pipeline' or 'workflow'); returns what its jobs read,
    combined (for a kind: workflow job that runs it), or None."""
    if not isinstance(body, dict):
        R.error(where, "a body is a mapping with stages: and jobs:")
        return None
    body = bool_key_fix(body, where, False)
    for k in body:
        if k == "kind":
            R.error(where, BODY_KIND_GONE)
        elif k == "live":
            R.error(where, "live: is gone — a body registered under pipelines: (or run by kind: pipeline) is a pipeline")
        elif k not in BODY_KEYS:
            R.error(where, f"unknown key '{k}' — a body has stages, jobs, variables and repos")
    stages = body.get("stages")
    if not isinstance(stages, list) or not stages:
        R.error(where, "stages: is required (a list)")
        stages = []
    stages = [str(s) for s in stages]
    jobs_raw = body.get("jobs")
    if not isinstance(jobs_raw, dict) or not jobs_raw:
        R.error(where, "defines no jobs")
        return None
    repos = body.get("repos") or {}
    for alias, url in (repos.items() if isinstance(repos, dict) else []):
        if not isinstance(url, str):
            R.error(f"{where}.repos.{alias}", "must be a repository path (github.com/org/repo); a declared repository "
                    "is followed on the branch it was added with")
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
    for name, job in jobs.items():
        if job_kind(job) in CHILD_KINDS and isinstance(job.get("body"), dict):
            ctx["children"][name] = job["body"]
    produced = [p for j in jobs.values() for p in output_paths(j)]

    summary = {"paths": set(), "whole": False, "all_built": True, "first_unbuilt": None,
               "other_repos": False, "config": set(), "secrets": set(), "handed_up": []}
    edges: dict[str, set[str]] = {}
    rule_lines = []
    for name, job in jobs.items():
        jw = f"{where}.jobs.{name}"
        edges[name] = set()
        eff = check_job(jw, name, job, kind, repo, ctx, repos, stages, depth)
        rule_lines.append((name, eff))

        # What a kind: workflow job running this body reads (DESIGN-V72 §6).
        if job.get("repo") is not None or job.get("repos"):
            summary["other_repos"] = True
        if eff["skip_model"] != "built":
            summary["all_built"] = False
            summary["first_unbuilt"] = summary["first_unbuilt"] or (name, eff)
        summary["config"].update(str(x) for x in as_list(eff["config"]))
        summary["secrets"].update(str(x) for x in as_list(eff["secrets"]))
        if eff.get("derived") or eff["paths"] == WHOLE or any("${{" in p for p in eff["paths"]):
            summary["whole"] = True
        else:
            summary["paths"].update(eff["paths"])
        for c in as_list(job.get("consumes")) + as_list(job.get("image_from")):
            if isinstance(c, str) and c.startswith("../"):
                summary["handed_up"].append(c[3:])

        if job.get("image_from"):
            edges[name].add(parse_selector(str(job["image_from"]).split("/")[0])[0])
        for n in as_list(job.get("needs")):
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
        if eff["kind"] in SCRIPT_KINDS and not eff.get("actions"):
            check_script_reads(jw, job, eff, repo, produced + output_paths(job))

    # The effective rules, for the report (nested bodies print under their job).
    for name, eff in rule_lines:
        R.rules[eff.pop("_rules_at")] = rule_line(name, eff, depth)

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
    summary["paths"] = WHOLE if summary["whole"] else sorted(summary["paths"])
    return summary


def check_job(jw, name, job, body_kind, repo: Repo, ctx, repos, stages, depth):
    """Checks one job; returns its effective kind, checkout and skip, with where each came from."""
    raw_kind = job.get("kind")
    eff = {"kind": "task", "kind_src": "no kind:", "paths": [], "co_src": "", "added": [],
           "skip": "never", "skip_model": "never", "skip_src": "", "secrets": job.get("secrets"),
           "config": job.get("config"), "consumes": list(as_list(job.get("consumes")))}
    if raw_kind is not None:
        if raw_kind in KINDS:
            eff["kind"], eff["kind_src"] = raw_kind, ""
        else:
            hint = KIND_HINTS.get(str(raw_kind))
            R.error(jw, f"kind: '{raw_kind}' — kind: is one of {', '.join(KINDS)}" + (f" ({hint})" if hint else ""))
            eff["kind"] = "task"
    kind = eff["kind"]
    R.rules.append(None)                        # this job's slot in the report
    eff["_rules_at"] = len(R.rules) - 1

    # Keys
    eff["broken"] = any(k in job for k in ("delegate", *REMOVED_JOB_KEYS))
    for k in job:
        if k == "delegate":
            d = job["delegate"]
            t = d.get("type") if isinstance(d, dict) else None
            if t in CHILD_KINDS:
                R.error(jw, f"delegate: {{ type: {t} }} is now kind: {t} with body: (or workload:) and "
                            "variables: on the job")
            elif t == "github_actions":
                R.error(jw, "delegate: { type: github_actions } is now github_actions: on a build, "
                            "deploy or task (github_actions: deploy.yml, or { workflow, ref, inputs, artifacts })")
            else:
                R.error(jw, "delegate: is gone — kind: workflow or kind: pipeline with body:, or github_actions: "
                            "on a build, deploy or task")
        elif k in REMOVED_JOB_KEYS:
            R.error(jw, REMOVED_JOB_KEYS[k])
        elif k not in JOB_KEYS:
            hint = FOREIGN_KEY_HINTS.get(k)
            R.error(jw, f"unknown key '{k}'" + (f" — {hint}" if hint else
                    f" — allowed: {', '.join(sorted(JOB_KEYS))}"))
    stage = job.get("stage")
    if stage is None:
        R.error(jw, "has no stage")
    elif str(stage) not in stages:
        R.error(jw, f"references unknown stage '{stage}' (stages: {stages})")

    # Where the kind may appear
    in_workflow = body_kind == "workflow"
    if in_workflow and kind == "deploy":
        R.error(jw, "kind: deploy belongs to a pipeline: a deploy's last success, rollback and holds exist "
                    "only there; a one-off deploy in a workflow (a preview, a manual hotfix) is a task")
    if in_workflow and kind == "pipeline":
        R.error(jw, "kind: pipeline belongs to a pipeline: only a pipeline hands its revisions to a child pipeline")

    skip = job.get("skip")
    if skip is not None and skip not in ("unchanged", "built", "never"):
        R.error(jw, f"skip: is unchanged, built or never — got '{skip}'")
        skip = None
    if skip == "unchanged" and in_workflow:
        R.error(jw, "skip: unchanged is a pipeline's policy — a workflow run has no earlier run to compare with; "
                    "use skip: built to reuse a stored run, or skip: never")

    if kind in SCRIPT_KINDS:
        check_script_job(jw, name, job, kind, skip, eff, repo, ctx)
    else:
        check_child_job(jw, name, job, kind, skip, eff, repo, depth, in_workflow)

    check_common(jw, name, job, kind, eff, repo, ctx, repos)

    # §7: a job that reads nothing runs once, then skips (or reuses) on every revision.
    if eff["broken"]:
        return eff
    if eff["skip"] != "never" and not eff["paths"] and not eff.get("derived") and not eff["consumes"] \
            and not job.get("image_from") and not job.get("repos") and not as_list(eff["secrets"]) \
            and not as_list(eff["config"]):
        R.warn(jw, f"reads nothing, so it runs once and then {'skips' if eff['skip'] == 'unchanged' else 'reuses that run'} "
                   f"every revision (skip: {eff['skip']}): say what it reads (checkout:, consumes:), or skip: never")
    # A deploy (or a hand-over) that reads the whole tree runs on every revision.
    if kind == "deploy" and eff["paths"] == WHOLE and eff["skip"] == "unchanged":
        R.warn(jw, f"a deploy that checks out the whole repository ({eff['co_src']}) runs on every revision; "
                   "a deploy usually reads what it consumes plus its deploy scripts — narrow the checkout if it reads less")
    if kind == "pipeline" and eff["paths"] == WHOLE and eff["skip"] == "unchanged":
        R.warn(jw, "checkout: true hands every revision to the child pipeline; declare what selects it "
                   "(consumes: [graph/<service>], or checkout: [services/<service>, libs/…])")
    return eff


def check_script_job(jw, name, job, kind, skip, eff, repo: Repo, ctx):
    for k in ("body", "workload"):
        if k in job:
            R.error(jw, f"{k}: belongs to kind: workflow or kind: pipeline (this job is a {kind})")
    actions = "github_actions" in job
    params = gha_params(jw, job["github_actions"]) if actions else None
    script_key = "script" if "script" in job else next((k for k in SCRIPT_KEYS if k in job and "uses" not in job), None)
    script = script_key is not None
    uses = "uses" in job
    executors = [n for n, on in ((f"{script_key}:", script), ("uses:", uses), ("github_actions:", actions)) if on]
    if len(executors) > 1:
        R.error(jw, f"names {' and '.join(executors)} — a {kind} runs on exactly one executor: script: (Pipemesh "
                    "runners), uses: (a component) or github_actions: (an Actions run)")
    elif not executors and not eff["broken"]:
        R.error(jw, f"a {kind} runs on an executor: script:, uses: (a component) or github_actions: (an Actions run)"
                    " — a publish-only job can use script: echo …")
    elif script and not job.get("script") and not uses:
        R.error(jw, "has no script: — setup:/before_script:/after_script: run around a script:")
    eff["actions"] = actions

    # checkout
    default_co, default_skip = KIND_DEFAULTS[kind]
    if "checkout" in job:
        paths = checkout_paths(jw, job["checkout"])
        eff["paths"], eff["co_src"] = (paths if paths is not None else []), "declared"
        eff["broken"] = eff["broken"] or paths is None
    elif actions:
        paths, why = derive_actions_checkout((params or {}).get("workflow"), repo)
        eff["paths"], eff["co_src"], eff["derived"] = paths, f"read from {why}", True
    else:
        eff["paths"], eff["co_src"] = (list(WHOLE) if default_co else []), f"from kind: {kind}"
    if not actions and eff["paths"] != WHOLE:
        eff["paths"] = list(eff["paths"])
        eff["added"] = platform_reads(job, eff["paths"])

    # skip
    if skip is not None:
        eff["skip"], eff["skip_src"] = skip, "declared"
    else:
        eff["skip"], eff["skip_src"] = default_skip, f"from kind: {kind}"
    eff["skip_model"] = eff["skip"]

    if actions:
        if params is not None:
            check_gha_workflow(f"{jw}.github_actions", params, repo)
        for k in GHA_EXCLUSIVE:
            if k in job and k not in ("script", "before_script", "after_script", "uses", "setup"):
                R.error(jw, f"'{k}' can't sit beside github_actions: — the Actions run is the whole execution "
                            "(secrets stay in GitHub)")
    uses_v = job.get("uses")
    if uses_v is not None:
        for k in sorted(EXECUTION_KEYS & set(job) - {"script"}):
            R.error(jw, f"'{k}' belongs to the component when uses: instantiates one — pass a param instead")
        check_component_use(jw, uses_v, job.get("with"), repo)
    elif "with" in job:
        R.error(jw, "with: without uses:")
    for i, step in enumerate(as_list(job.get("setup"))):
        if not isinstance(step, dict) or "uses" not in step or set(step) - {"uses", "with"}:
            R.error(f"{jw}.setup[{i}]", "each setup step is { uses:, with: }")
            continue
        check_component_use(f"{jw}.setup[{i}]", step["uses"], step.get("with"), repo, setup_step=True)


def check_child_job(jw, name, job, kind, skip, eff, repo: Repo, depth, in_workflow):
    for k in job:
        if k in JOB_KEYS and k not in CHILD_KIND_KEYS and k != "produces":
            R.error(jw, f"kind: {kind} takes body: or workload:, variables:, stage:, needs:, consumes:, skip:, "
                        f"timeout_seconds:, allow_failure:, matrix:{', checkout:' if kind == 'pipeline' else ''} "
                        f"and was:; not {k}: (a body's jobs run the work)")
    if ("body" in job) == ("workload" in job):
        R.error(jw, f"kind: {kind} needs exactly one of body: (!ref, !include or inline stages:/jobs:) or "
                    "workload: (an existing workload's alias)")
    if "variables" in job and not isinstance(job["variables"], dict):
        R.error(jw, "variables: must be a mapping (the child's variables)")
    if "workload" in job and (not isinstance(job["workload"], str) or not job["workload"].strip()):
        R.error(jw, "workload: takes a workload alias")
    child = None
    if "body" in job:
        if isinstance(job["body"], dict):
            start = len(R.rules)
            child = check_body(f"{jw}.body", job["body"], kind, repo, delegated=True, depth=depth + 1)
            key = (kind, jw.rsplit(".jobs.", 1)[0], repr(job["body"]))
            if key in SEEN_BODIES:   # the same body again (one per service): say so instead of repeating it
                del R.rules[start:]
                R.rules.append("  " * (depth + 3) + f"(the same body as {SEEN_BODIES[key]})")
            else:
                SEEN_BODIES[key] = name
        else:
            R.error(jw, "body: takes a body — !ref, !include or inline stages:/jobs: — not a name or a path")
    if child:
        eff["consumes"] = list(dict.fromkeys(eff["consumes"] + child["handed_up"]))

    if kind == "workflow":
        if "checkout" in job:
            R.error(jw, "checkout: on kind: workflow — the workflow's jobs say what they read")
        if skip is not None and skip != "never":
            R.error(jw, "kind: workflow skips by its jobs' rules: built when every job is a build, never otherwise; "
                        "skip: never is the only override")
        if child:
            eff["paths"], eff["co_src"] = child["paths"], "its jobs' checkouts combined"
            eff["secrets"] = sorted(set(as_list(eff["secrets"])) | child["secrets"])
            eff["config"] = sorted(set(as_list(eff["config"])) | child["config"])
        elif "workload" in job:
            eff["paths"], eff["co_src"] = list(WHOLE), "an existing workload: its jobs aren't known here"
        else:
            eff["paths"], eff["co_src"] = list(WHOLE), "its body didn't load"
        if skip == "never":
            eff["skip_model"], eff["skip_src"] = "never", "declared"
        elif not child:
            eff["skip_model"], eff["skip_src"] = "never", ("an existing workload runs every time"
                                                           if "workload" in job else "its body didn't load")
        elif child["other_repos"]:
            eff["skip_model"], eff["skip_src"] = "never", "its body works in another repository"
        elif child["all_built"]:
            eff["skip_model"], eff["skip_src"] = "built", "its jobs': every one has skip: built"
        else:
            n, e = child["first_unbuilt"]
            eff["skip_model"], eff["skip_src"] = "never", f"its jobs': {n} is a {e['kind']} with skip: {e['skip_model']}"
        eff["skip"] = eff["skip_model"]
        if in_workflow and eff["skip"] != "never":
            eff["skip"], eff["skip_src"] = "never", "a nested workflow job runs every time inside a workflow run"
    else:  # pipeline
        if "checkout" in job:
            paths = checkout_paths(jw, job["checkout"])
            eff["paths"], eff["co_src"] = (paths if paths is not None else []), "declared — it checks nothing out"
        else:
            eff["paths"], eff["co_src"] = [], "from kind: pipeline"
        if skip is not None and skip not in ("unchanged", "never"):
            R.error(jw, "kind: pipeline takes skip: unchanged or never")
        eff["skip"] = skip if skip in ("unchanged", "never") else "unchanged"
        eff["skip_src"] = "declared" if skip in ("unchanged", "never") else "from kind: pipeline"
        eff["skip_model"] = eff["skip"]


def check_common(jw, name, job, kind, eff, repo: Repo, ctx, repos):
    actions = eff.get("actions")
    if job.get("image_from"):
        check_consume_path(jw + ".image_from", job["image_from"], ctx, want_oci=True)
    produces = job.get("produces")
    if produces is not None:
        if kind in CHILD_KINDS:
            R.error(jw, f"a kind: {kind} job produces nothing itself — consumers name the entries of its body's "
                        f"jobs by path ({name}/<job>/<key>)")
        elif not isinstance(produces, dict):
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
                vtype = v if isinstance(v, str) and v in ("file", "oci", "npm") else (v.get("type") if isinstance(v, dict) else None)
                vpath = v.get("path") if isinstance(v, dict) else (None if vtype else v)
                if (vtype in (None, "file")) and not vpath and not actions:
                    R.error(f"{jw}.produces.{k}", "a file entry needs a path (dist: dist, or { path: dist }) — "
                                                  "only a github_actions: job takes `file` alone (the run's artifact)")
                if vtype in ("oci", "npm") and not actions:
                    keyed = any(isinstance(p, dict) and p.get("key") == k for p in as_list(job.get("publish")))
                    if not keyed and "pipemesh produce" not in text_of(job) and not job.get("uses"):
                        R.warn(f"{jw}.produces.{k}", f"an {vtype} entry is emitted at run time — add "
                               f"`pipemesh produce {vtype} {k} --ref <ref> --digest <digest>` to the script")
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
    if job.get("artifacts") is not None and produces is None:
        R.warn(jw, "artifacts: is the old form — prefer produces: { <key>: <path> } so consumers can name it")
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
    for v in (job.get("variables") or {}) if isinstance(job.get("variables"), dict) else {}:
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
    if job.get("repos") is not None:
        if not isinstance(job["repos"], dict):
            R.error(f"{jw}.repos", "maps an alias (or $self) to { mount: <dir>, checkout: true | [paths] }")
        else:
            check_mounts(jw, job, repos)
    if job.get("was") is not None and job.get("matrix") is not None:
        R.error(jw, "was: is not allowed on a matrix job")

    # Matrix
    mx = job.get("matrix")
    txt = "\n".join([text_of(job), str(job.get("needs", "")), str(job.get("consumes", "")),
                     str(job.get("with", "")), str(job.get("setup", "")), str(job.get("checkout", "")),
                     str(job.get("variables", "")), str(job.get("github_actions", ""))])
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


def rule_line(name, eff, depth):
    pad = "  " * (depth + 2)
    kind = eff["kind"] + (" (no kind:)" if eff["kind_src"] else "")
    paths = eff["paths"]
    co = "true" if paths == WHOLE else "false" if not paths else "[" + ", ".join(paths) + "]"
    added = "".join(f"; {p} added by {why}" for p, why in eff.get("added") or [])
    return (f"{pad}{name} — kind: {kind} · checkout: {co} ({eff['co_src']}{added}) · "
            f"skip: {eff['skip']} ({eff['skip_src']})")

# ---------------------------------------------------------------------------
# Registration


def cache_path_sets(body):
    """(job name, sorted cache paths) for every cached job of a body and the bodies its jobs run."""
    out = []
    if not isinstance(body, dict) or not isinstance(body.get("jobs"), dict):
        return out
    for name, j in body["jobs"].items():
        if not isinstance(j, dict):
            continue
        c = j.get("cache")
        if isinstance(c, dict) and c.get("paths"):
            out.append((name, tuple(sorted(str(x) for x in as_list(c["paths"])))))
        if isinstance(j.get("body"), dict):
            out.extend(cache_path_sets(j["body"]))
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
                       "other workload in this definition saves a cache with these paths, so this job always starts "
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
        names = [str(b) for b in as_list(trig.get("branches"))]
        if REPO_BRANCH and REPO_BRANCH not in names:
            R.error(where, f"branches: {names} never matches: push runs come only from the branch the repository is "
                           f"added with ({REPO_BRANCH}, the checkout's default branch) — leave branches: out")
        else:
            R.warn(where, "push runs come only from the branch the repository was added with, so branches: can "
                          "only repeat it — leave it out")
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


REPO_BRANCH = None
SEEN_BODIES: dict = {}


def default_branch(root):
    """The checkout's default branch name (what Pipemesh records when the repository is added), if git knows it."""
    # origin/HEAD only: the checked-out branch may be a feature branch.
    try:
        out = subprocess.run(["git", "-C", root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if out and out != "HEAD":
        return out.split("/", 1)[1] if out.startswith("origin/") else out
    return None


def main():
    global REPO_BRANCH
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    quiet = "--quiet" in sys.argv[1:] or "-q" in sys.argv[1:]
    root = args[0] if args else "."
    repo = Repo(root)
    REPO_BRANCH = default_branch(repo.root)
    definition_path = os.path.join(repo.root, "pipemesh.yaml")
    if not os.path.isfile(definition_path):
        print(f"no pipemesh.yaml at {repo.root}")
        return 1
    raw = repo.load("pipemesh.yaml", "pipemesh.yaml")
    if raw is None:
        return finish(quiet)
    if not isinstance(raw, dict):
        R.error("pipemesh.yaml", "must be a mapping")
        return finish(quiet)
    raw = bool_key_fix(raw, "pipemesh.yaml", False)
    for k in raw:
        if k != "pipemesh" and not DEFINITION_NAME.match(k):
            R.error("pipemesh.yaml", f"definition name '{k}' must be letters, digits, _ and - (it is a path segment for !ref)")
    if "pipemesh" not in raw:
        R.error("pipemesh.yaml", "registers its workloads under the pipemesh: key — "
                "pipemesh: { pipelines: { pipeline: !ref <definition> }, workflows: { <name>: { body: …, on: … } } }")
        return finish(quiet)

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
        return finish(quiet)
    for k in pm:
        if k not in ("pipelines", "workflows"):
            R.error("pipemesh", f"unknown key '{k}' — pipemesh: holds pipelines: and workflows:")
    pipelines = pm.get("pipelines") or {}
    workflows = pm.get("workflows") or {}
    if not pipelines and not workflows:
        R.error("pipemesh", "registers no workloads — add pipelines: or workflows:")
    if not isinstance(pipelines, dict) or not isinstance(workflows, dict):
        R.error("pipemesh", "pipelines: and workflows: are mappings of name -> entry")
        return finish(quiet)
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
            R.rules.append(f"{section[:-1]} {name}")
            if kind == "pipeline":
                registrations.append((w, entry.get("body") if isinstance(entry, dict) and "body" in entry else entry, {"pipeline"}))
            if isinstance(entry, dict) and "body" in entry:
                allowed = PIPELINE_ENTRY_KEYS if kind == "pipeline" else WORKFLOW_ENTRY_KEYS
                for k in entry:
                    if k == "kind":
                        R.error(w, BODY_KIND_GONE)
                    elif k not in allowed:
                        extra = " — pipelines take no triggers: every commit on the repository's branch is a revision" \
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
                body = {k: v for k, v in entry.items() if k in BODY_KEYS or k == "kind"}
                rest = {k: v for k, v in entry.items() if k not in BODY_KEYS and k != "kind"}
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
    return finish(quiet)


def finish(quiet=False):
    for line in R.errors + R.warnings:
        print(line)
    rules = [r for r in R.rules if r]
    if rules and not quiet:
        print("\nEffective rules (what each job is, what it checks out, when it may skip; "
              "a job without kind: is a task):")
        for line in rules:
            print(("  " + line) if not line.startswith(" ") else line)
    print(f"\n{len(R.errors)} error(s), {len(R.warnings)} warning(s)")
    return 1 if R.errors else 0


if __name__ == "__main__":
    sys.exit(main())
