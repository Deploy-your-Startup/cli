"""Migrate shared workflow calls to one local, versioned entry point.

The callers retain their triggers, job names, dependencies, inputs and secrets.
Only the entry point contains an external deployment revision.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import click
import yaml

from cli.project_version import WORKFLOW

OPERATIONS = {
    "build-and-deploy-service.yml": "build",
    "build-and-deploy-service-parallel.yml": "build",
    "deploy.yml": "deploy",
    "deploy-infrastructure.yml": "infrastructure",
    "deploy-pages.yml": "pages",
    "container-e2e.yml": "e2e",
}
PERMISSIONS = {
    "contents": "read",
    "actions": "read",
    "pull-requests": "write",
    "packages": "write",
    "id-token": "write",
}


class WorkflowDumper(yaml.SafeDumper):
    """Use double quotes for quoted scalars, matching generated-project Prettier."""

    def choose_scalar_style(self):
        style = super().choose_scalar_style()
        return '"' if style == "'" else style


def include_version_changes(content: str) -> str:
    """Include the one version file in each caller's existing event path filters."""
    # Copier's raw delimiters occur only around otherwise plain caller YAML.
    # Mask them as comments without shifting node offsets in the original text.
    masked = re.sub(
        r"\{% (?:end)?raw %\}", lambda m: "#" + " " * (len(m[0]) - 1), content
    )
    edits = []
    for paths in _event_paths(yaml.compose(masked)):
        if any(item.value == WORKFLOW for item in paths.value):
            continue
        mark = paths.start_mark
        if mark is None:
            raise click.ClickException(
                "Cannot locate the workflow path filter; no files changed."
            )
        position = mark.index
        if paths.flow_style:
            separator = "," if content[position + 1 : position + 2] == "\n" else ", "
            edits.append(
                (
                    position + 1,
                    json.dumps(WORKFLOW) + (separator if paths.value else ""),
                )
            )
        else:
            edits.append((position, "- '" + WORKFLOW + "'\n" + " " * mark.column))
    for position, addition in sorted(edits, reverse=True):
        content = content[:position] + addition + content[position:]
    return content


def _event_paths(document: yaml.Node | None) -> list[yaml.SequenceNode]:
    if not isinstance(document, yaml.MappingNode):
        return []
    found = []
    for key, events in document.value:
        if key.value != "on" or not isinstance(events, yaml.MappingNode):
            continue
        for _event, settings in events.value:
            if not isinstance(settings, yaml.MappingNode):
                continue
            found.extend(
                paths
                for setting, paths in settings.value
                if setting.value == "paths" and isinstance(paths, yaml.SequenceNode)
            )
    return found


def entry_workflow(schema: dict, repository: str, router: str, revision: str) -> str:
    """Generate the native GitHub entry from the released router's public schema."""
    call = schema.get("on", schema.get(True))["workflow_call"]
    job = {
        "uses": f"{repository}/.github/workflows/{router}@{revision}",
        "with": {name: "${{ inputs." + name + " }}" for name in call["inputs"]},
        "secrets": {
            name: "${{ secrets." + name + " }}" for name in call.get("secrets", {})
        },
    }
    return yaml.dump(
        {
            "name": "Shared deployment version",
            "on": {"workflow_call": call},
            "jobs": {"runtime": job},
        },
        sort_keys=False,
        width=1000,
        Dumper=WorkflowDumper,
    )


def _permissions(block: str, indent: str, pages: bool) -> str:
    required = {"contents": "read", "deployments": "write"} if pages else PERMISSIONS
    match = re.search(r"^" + indent + r"permissions:([^\n]*)\n", block, re.MULTILINE)
    lines = "".join(
        indent + "  " + key + ": " + value + "\n" for key, value in required.items()
    )
    if not match:
        return block + indent + "permissions:\n" + lines
    if match[1].strip() not in {"", "{}"}:
        if match[1].strip() == "write-all":
            return block
        raise click.ClickException(
            "Review the job's scalar permissions before migrating shared workflows."
        )
    start, end = match.end(), match.end()
    for line in block[start:].splitlines(keepends=True):
        if line.strip() and not line.startswith(indent + " "):
            break
        end += len(line)
    existing = yaml.safe_load(block[match.start() : end]) or {}
    scopes = existing.get("permissions") or {}
    for key, value in required.items():
        if scopes.get(key) != "write":
            scopes[key] = value
    lines = "".join(
        indent + "  " + key + ": " + value + "\n" for key, value in scopes.items()
    )
    return block[: match.start()] + indent + "permissions:\n" + lines + block[end:]


def _job(block: str, repository: str, pages: bool) -> tuple[str, int]:
    pattern = re.compile(
        r"^(?P<indent> +)uses:\s*"
        + re.escape(repository)
        + r"/\.github/workflows/(?P<name>[\w.-]+)@[^\s#]+[^\n]*\n",
        re.MULTILINE,
    )
    match = pattern.search(block)
    if not match:
        return block, 0
    operation = OPERATIONS.get(match["name"])
    if operation is None or (operation in {"pages", "e2e"}) != pages:
        raise click.ClickException(
            f"Unsupported shared workflow {match['name']}; migrate it explicitly."
        )
    indent = match["indent"]
    block = (
        block[: match.start()]
        + indent
        + "uses: ./"
        + WORKFLOW
        + "\n"
        + block[match.end() :]
    )
    inputs = re.search(r"^" + indent + r"with:\s*\n", block, re.MULTILINE)
    if inputs:
        block = (
            block[: inputs.end()]
            + indent
            + "  operation: "
            + operation
            + "\n"
            + block[inputs.end() :]
        )
    else:
        line = indent + "uses: ./" + WORKFLOW + "\n"
        block = block.replace(
            line,
            line + indent + "with:\n" + indent + "  operation: " + operation + "\n",
            1,
        )
    return _permissions(block, indent, pages), 1


def consolidated_workflows(
    project: Path, repository: str, router: str, revision: str, schema: dict
) -> tuple[dict[Path, str], int]:
    """Prepare all edits before writing; unsupported calls leave the project intact."""
    updates = {}
    count = 0
    entry = project / WORKFLOW
    marker = re.compile(r"uses:\s*" + re.escape(repository) + r"/\.github/")
    for path in sorted(entry.parent.glob("*.y*ml")):
        if path == entry:
            continue
        content = path.read_text()
        # Reusable workflow jobs have exactly two-space job keys in the shipped
        # templates. Split on those keys without serializing owner YAML/comments.
        blocks = re.split(r"(?=^  [\w-]+:\s*\n)", content, flags=re.MULTILINE)
        rewritten = []
        for block in blocks:
            text, moved = _job(block, repository, router == "project-pages.yml")
            rewritten.append(text)
            count += moved
        updated = "".join(rewritten)
        if marker.search(updated):
            raise click.ClickException(
                f"{path} contains an unsupported shared action or workflow; no files changed."
            )
        if updated != content:
            updated = include_version_changes(updated)
            yaml.safe_load(updated)
            updates[path] = updated
    if not entry.exists():
        updates[entry] = entry_workflow(schema, repository, router, revision)
    return updates, count


def legacy_workflows(project: Path, repository: str, revision: str) -> dict[Path, str]:
    """Restore direct calls when explicitly rolling back to an older release."""
    updates = {}
    names = {value: key for key, value in OPERATIONS.items() if "parallel" not in key}
    for path in sorted((project / WORKFLOW).parent.glob("*.y*ml")):
        if path == project / WORKFLOW:
            continue
        blocks = re.split(r"(?=^  [\w-]+:\s*\n)", path.read_text(), flags=re.MULTILINE)
        rewritten = []
        for original in blocks:
            block = original
            if "uses: ./" + WORKFLOW in block:
                operation = re.search(
                    r"^ +operation: ([\w-]+)\s*\n", block, re.MULTILINE
                )
                if not operation or operation[1] not in names:
                    raise click.ClickException(
                        f"Cannot roll back the operation in {path}; no files changed."
                    )
                block = block[: operation.start()] + block[operation.end() :]
                block = block.replace(
                    "uses: ./" + WORKFLOW,
                    f"uses: {repository}/.github/workflows/{names[operation[1]]}@{revision}",
                )
            rewritten.append(block)
        updated = "".join(rewritten)
        if updated != path.read_text():
            updates[path] = updated
    return updates
