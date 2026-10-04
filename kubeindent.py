#!/usr/bin/env python3
"""kubeindent: a cross-platform Kubernetes YAML indentation helper.

Requires Python 3.8+. No third-party packages are required.

Examples:
  python kubeindent.py deployment.yaml service.yaml  # repair multiple files in place
  python kubeindent.py ./manifests/*.yaml           # repair matching files (PowerShell too)
  python kubeindent.py ./manifests                  # repair YAML files in this directory
  python kubeindent.py --recursive ./manifests      # include subdirectories
  python kubeindent.py --backup deployment.yaml      # back up, then repair in place
  python kubeindent.py --stdout deployment.yaml      # print one file without writing
  python kubeindent.py --validate ./manifests/*.yaml
"""

import argparse
import glob
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_KEYS = {"apiVersion", "kind", "metadata", "spec", "status", "data", "stringData"}

# These fields have predictable parents in common Kubernetes resources.
PARENTS = {
    # Object metadata
    "name": {"metadata", "containers[]", "initContainers[]", "ephemeralContainers[]",
             "ports[]", "env[]", "volumeMounts[]", "volumes[]", "imagePullSecrets[]",
             "ownerReferences[]", "items[]", "rules[]", "paths[]", "tls[]"},
    "namespace": {"metadata"},
    "labels": {"metadata"},
    "annotations": {"metadata"},
    "ownerReferences": {"metadata"},
    "finalizers": {"metadata"},
    "uid": {"metadata"},
    "resourceVersion": {"metadata"},
    "generation": {"metadata"},
    "creationTimestamp": {"metadata"},
    "managedFields": {"metadata"},

    # Common workload / service spec fields
    "replicas": {"spec"},
    "selector": {"spec"},
    "template": {"spec"},
    "containers": {"spec"},
    "initContainers": {"spec"},
    "ephemeralContainers": {"spec"},
    "volumes": {"spec"},
    "imagePullSecrets": {"spec"},
    "restartPolicy": {"spec"},
    "serviceAccountName": {"spec"},
    "nodeSelector": {"spec"},
    "tolerations": {"spec"},
    "affinity": {"spec"},
    "securityContext": {"spec", "containers[]", "initContainers[]", "ephemeralContainers[]"},
    "terminationGracePeriodSeconds": {"spec"},
    "hostNetwork": {"spec"},
    "dnsPolicy": {"spec"},
    "priorityClassName": {"spec"},
    "runtimeClassName": {"spec"},
    "schedulerName": {"spec"},
    "automountServiceAccountToken": {"spec"},
    "type": {"spec"},
    "clusterIP": {"spec"},
    "clusterIPs": {"spec"},
    "externalIPs": {"spec"},
    "loadBalancerIP": {"spec"},
    "externalName": {"spec"},
    "sessionAffinity": {"spec"},
    "ports": {"spec", "containers[]", "initContainers[]", "ephemeralContainers[]"},
    "accessModes": {"spec"},
    "storageClassName": {"spec"},
    "volumeMode": {"spec"},
    "resources": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "selector": {"spec"},

    # Selector/template structure
    "matchLabels": {"selector"},
    "matchExpressions": {"selector"},
    "metadata": {"template"},
    "spec": {"template"},

    # Container fields
    "image": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "imagePullPolicy": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "command": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "args": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "workingDir": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "stdin": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "tty": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "env": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "envFrom": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "volumeMounts": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "livenessProbe": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "readinessProbe": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "startupProbe": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "lifecycle": {"containers[]", "initContainers[]", "ephemeralContainers[]"},
    "containerPort": {"ports[]"},
    "hostPort": {"ports[]"},
    "protocol": {"ports[]"},
    "mountPath": {"volumeMounts[]"},
    "readOnly": {"volumeMounts[]"},
    "subPath": {"volumeMounts[]"},
    "value": {"env[]"},
    "valueFrom": {"env[]"},
    "secretKeyRef": {"valueFrom"},
    "configMapKeyRef": {"valueFrom"},
    "limits": {"resources"},
    "requests": {"resources"},
    "httpGet": {"livenessProbe", "readinessProbe", "startupProbe"},
    "tcpSocket": {"livenessProbe", "readinessProbe", "startupProbe"},
    "exec": {"livenessProbe", "readinessProbe", "startupProbe"},
    "initialDelaySeconds": {"livenessProbe", "readinessProbe", "startupProbe"},
    "periodSeconds": {"livenessProbe", "readinessProbe", "startupProbe"},
    "timeoutSeconds": {"livenessProbe", "readinessProbe", "startupProbe"},
    "failureThreshold": {"livenessProbe", "readinessProbe", "startupProbe"},
    "successThreshold": {"livenessProbe", "readinessProbe", "startupProbe"},
    "path": {"httpGet"},
    "port": {"httpGet", "tcpSocket"},
    "configMap": {"volumes[]"},
    "secret": {"volumes[]"},
    "emptyDir": {"volumes[]"},
    "hostPath": {"volumes[]"},
    "persistentVolumeClaim": {"volumes[]"},
    "claimName": {"persistentVolumeClaim"},
}

SEQUENCE_KEYS = {
    "containers", "initContainers", "ephemeralContainers", "ports", "env",
    "envFrom", "volumeMounts", "volumes", "imagePullSecrets", "command",
    "args", "items", "tolerations", "ownerReferences", "finalizers",
    "accessModes", "matchExpressions", "rules", "paths", "hosts", "tls",
    "topologySpreadConstraints",
}

ARBITRARY_MAP_KEYS = {"labels", "annotations", "matchLabels", "nodeSelector", "data", "stringData"}
STRUCTURAL_KEYS = {
    "apiVersion", "kind", "metadata", "spec", "status", "selector", "template",
    "containers", "initContainers", "ephemeralContainers", "volumes", "ports",
    "env", "envFrom", "volumeMounts", "replicas", "matchLabels", "matchExpressions",
    "resources", "limits", "requests", "livenessProbe", "readinessProbe", "startupProbe",
    "image", "command", "args", "volumeMounts", "containerPort", "hostPort",
    "protocol", "mountPath", "value", "valueFrom", "type", "clusterIP",
}


def split_key_value(text):
    """Split a simple YAML mapping line at its first unquoted colon."""
    if text.startswith("- "):
        text = text[2:].lstrip()
    single = double = False
    for i, char in enumerate(text):
        if char == "'" and not double:
            single = not single
        elif char == '"' and not single:
            double = not double
        elif char == ":" and not single and not double:
            key = text[:i].strip()
            if key:
                return key, text[i + 1:].strip(), True
    return None, None, False


def next_content(lines, start):
    for line in lines[start + 1:]:
        item = line.strip()
        if item and not item.startswith("#") and item not in ("---", "..."):
            return item
    return ""


def find_parent(stack, allowed):
    for index in range(len(stack) - 1, -1, -1):
        frame = stack[index]
        if frame["key"] in allowed:
            return index
    return None


def fix_indentation(source, width=2):
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    stack = []

    for line_no, raw in enumerate(lines):
        text = raw.strip()

        if not text:
            out.append("")
            continue

        if text.startswith("#"):
            indent = stack[-1]["indent"] + width if stack else 0
            out.append(" " * indent + text)
            continue

        if text in ("---", "..."):
            out.append(text)
            stack.clear()
            continue

        following = next_content(lines, line_no)

        # Sequence item: place it below the nearest sequence field.
        if text.startswith("- "):
            seq_index = next(
                (i for i in range(len(stack) - 1, -1, -1)
                 if stack[i]["kind"] == "seq"),
                None
            )
            if seq_index is None:
                parent_indent = stack[-1]["indent"] if stack else -width
                indent = parent_indent + width
            else:
                stack = stack[:seq_index + 1]
                indent = stack[seq_index]["indent"] + width

            item_text = text[2:].lstrip()
            out.append(" " * indent + "- " + item_text)

            item_key, item_value, is_mapping = split_key_value(item_text)
            parent_key = stack[-1]["key"] if stack else ""
            item_frame_key = (parent_key + "[]") if parent_key else "[]"
            stack.append({"key": item_frame_key, "indent": indent, "kind": "item"})

            if is_mapping and item_value == "":
                stack.append({
                    "key": item_key,
                    "indent": indent + width,
                    "kind": "seq" if item_key in SEQUENCE_KEYS or following.startswith("- ") else "map"
                })
            continue

        key, value, is_mapping = split_key_value(text)
        if not is_mapping:
            # Scalar/block content: keep it beneath the current mapping context.
            indent = stack[-1]["indent"] + width if stack else 0
            out.append(" " * indent + text)
            continue

        # Document roots always start in column zero. "metadata" and "spec"
        # can also occur under a Deployment's template.
        if key in ("apiVersion", "kind", "status", "data", "stringData"):
            stack.clear()
            indent = 0
        elif key in ("metadata", "spec") and not (
            key in PARENTS and find_parent(stack, PARENTS[key]) is not None
        ):
            if key in ("metadata", "spec") and any(f["key"] == "template" for f in stack):
                parent_index = find_parent(stack, PARENTS.get(key, set()))
                if parent_index is not None:
                    stack = stack[:parent_index + 1]
                    indent = stack[parent_index]["indent"] + width
                else:
                    indent = stack[-1]["indent"] + width if stack else 0
            else:
                stack.clear()
                indent = 0
        else:
            # Arbitrary label/data keys belong directly under their map, but
            # structural Kubernetes fields must still follow their known parent.
            if (
                stack
                and stack[-1]["key"] in ARBITRARY_MAP_KEYS
                and key not in STRUCTURAL_KEYS
            ):
                parent_index = len(stack) - 1
            else:
                allowed = PARENTS.get(key)
                parent_index = find_parent(stack, allowed) if allowed else None

            if parent_index is None:
                parent_index = len(stack) - 1 if stack else None

            if parent_index is None:
                indent = 0
            else:
                stack = stack[:parent_index + 1]
                indent = stack[parent_index]["indent"] + width

        out.append(" " * indent + text)

        if value == "":
            kind = "seq" if key in SEQUENCE_KEYS or following.startswith("- ") else "map"
            stack.append({"key": key, "indent": indent, "kind": kind})

    return "\n".join(out).rstrip() + "\n"


def validate_with_kubectl(text, source_path):
    kubectl = shutil.which("kubectl")
    if not kubectl:
        return False, "kubectl not found in PATH"

    # Feed stdin so validation doesn't depend on a temporary file path.
    result = subprocess.run(
        [kubectl, "apply", "--dry-run=client", "-f", "-"],
        input=text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    message = (result.stdout or result.stderr).strip()
    return result.returncode == 0, message or ("validation passed" if result.returncode == 0 else "validation failed")


def expand_paths(inputs, recursive=False):
    """Expand explicit paths, wildcard patterns, and directories into files."""
    found = []
    seen = set()

    for item in inputs:
        # Expand wildcard patterns ourselves because PowerShell may pass them
        # to Python without expanding them.
        if glob.has_magic(item):
            matches = sorted(glob.glob(item, recursive=recursive))
            candidates = [Path(match) for match in matches]
        else:
            candidates = [Path(item)]

        for candidate in candidates:
            if candidate.is_dir():
                pattern = "**/*" if recursive else "*"
                candidates_in_dir = sorted(candidate.glob(pattern))
                candidates_in_dir = [
                    child for child in candidates_in_dir
                    if child.is_file() and child.suffix.lower() in (".yaml", ".yml")
                ]
            elif candidate.is_file():
                candidates_in_dir = [candidate]
            else:
                continue

            for path in candidates_in_dir:
                try:
                    identity = str(path.resolve())
                except OSError:
                    identity = str(path.absolute())
                if identity not in seen:
                    seen.add(identity)
                    found.append(path)

    return found


def process_file(path, args):
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"kubeindent: cannot read {path}: {exc}", file=sys.stderr)
        return 1, False

    fixed = fix_indentation(original, args.indent)
    normalized_original = original.replace("\r\n", "\n").replace("\r", "\n")
    changed = fixed != normalized_original

    if args.check:
        print(f"kubeindent: {'needs indentation repair' if changed else 'no changes detected'}: {path}")
        return 0, changed

    if args.validate:
        ok, message = validate_with_kubectl(fixed, path)
        print(
            f"kubeindent: {'validation passed' if ok else 'validation failed'}: {path}: {message}",
            file=sys.stderr,
        )
        if not ok:
            return 3, changed

    if args.stdout:
        sys.stdout.write(fixed)
        return 0, changed

    if changed and args.backup:
        backup = Path(str(path) + ".bak")
        try:
            backup.write_text(original, encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"kubeindent: cannot create backup for {path}: {exc}", file=sys.stderr)
            return 1, changed
        print(f"kubeindent: backup created: {backup}", file=sys.stderr)

    if changed:
        try:
            path.write_text(fixed, encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"kubeindent: cannot write {path}: {exc}", file=sys.stderr)
            return 1, changed
        print(f"kubeindent: repaired indentation: {path}", file=sys.stderr)
    else:
        print(f"kubeindent: no changes detected: {path}", file=sys.stderr)

    return 0, changed


def main():
    parser = argparse.ArgumentParser(
        description="Repair common Kubernetes YAML indentation mistakes in one or more files."
    )
    parser.add_argument("files", nargs="+", help="YAML file(s), wildcard pattern(s), or directory(s)")
    parser.add_argument("--stdout", action="store_true", help="print repaired YAML instead of modifying a file (single file only)")
    parser.add_argument("--backup", action="store_true", help="save each changed original as FILE.bak before writing")
    parser.add_argument("--validate", action="store_true", help="validate each repaired result with kubectl --dry-run=client")
    parser.add_argument("--indent", type=int, choices=(2, 4), default=2, help="indent width (default: 2)")
    parser.add_argument("--check", action="store_true", help="report whether each file needs repair, without writing")
    parser.add_argument("--recursive", action="store_true", help="when a directory is supplied, include YAML files in subdirectories")
    args = parser.parse_args()

    paths = expand_paths(args.files, recursive=args.recursive)
    if not paths:
        print("kubeindent: no files found; check the paths, wildcard patterns, or directory", file=sys.stderr)
        return 1

    if args.stdout and len(paths) != 1:
        print("kubeindent: --stdout can be used with exactly one file", file=sys.stderr)
        return 1

    exit_code = 0
    any_changed = False
    for path in paths:
        code, changed = process_file(path, args)
        any_changed = any_changed or changed
        if code != 0:
            # Keep processing remaining files, but remember the first failure.
            if exit_code == 0:
                exit_code = code

    if args.check and exit_code == 0 and any_changed:
        return 4
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
