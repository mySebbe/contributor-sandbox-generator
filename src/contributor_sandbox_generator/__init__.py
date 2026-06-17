"""Generate contributor sandbox suggestions for common project stacks."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ._version import __version__


@dataclass(frozen=True)
class StackInfo:
    kind: str
    evidence: list[str]


@dataclass(frozen=True)
class SandboxSuggestion:
    stack: StackInfo
    files: dict[str, str]
    notes: list[str]


def detect_stack(root: str | Path) -> StackInfo:
    project_root = Path(root)
    evidence: list[str] = []
    if (project_root / "pyproject.toml").exists():
        evidence.append("pyproject.toml")
    if (project_root / "requirements.txt").exists():
        evidence.append("requirements.txt")
    if evidence:
        return StackInfo("python", evidence)
    if (project_root / "package.json").exists():
        return StackInfo("node", ["package.json"])
    if (project_root / "go.mod").exists():
        return StackInfo("go", ["go.mod"])
    if (project_root / "Cargo.toml").exists():
        return StackInfo("rust", ["Cargo.toml"])
    return StackInfo("generic", [])


def generate_sandbox(root: str | Path) -> SandboxSuggestion:
    stack = detect_stack(root)
    if stack.kind == "python":
        files = _python_files()
        notes = ["Python sandbox uses Python 3.11 and installs editable project dependencies when pyproject.toml is present."]
    elif stack.kind == "node":
        files = _node_files()
        notes = ["Node sandbox uses the current LTS image and runs npm install when package.json is present."]
    elif stack.kind == "go":
        files = _go_files()
        notes = ["Go sandbox uses the official Go image and caches modules in the container volume."]
    elif stack.kind == "rust":
        files = _rust_files()
        notes = ["Rust sandbox uses the official Rust image and caches Cargo registry and target output in container volumes."]
    else:
        files = _generic_files()
        notes = ["Generic sandbox provides a Debian base with common contributor tools."]
    return SandboxSuggestion(stack=stack, files=files, notes=notes)


def validate_sandbox(sandbox: SandboxSuggestion) -> list[str]:
    errors: list[str] = []
    required = [".devcontainer/devcontainer.json", "Dockerfile", "docker-compose.yml"]
    for name in required:
        if name not in sandbox.files:
            errors.append(f"missing generated file: {name}")
    devcontainer = sandbox.files.get(".devcontainer/devcontainer.json", "")
    if "Dockerfile" not in devcontainer:
        errors.append("devcontainer must reference Dockerfile")
    if sandbox.stack.kind == "python" and "python:3.11" not in sandbox.files.get("Dockerfile", ""):
        errors.append("python stack must use a Python 3.11 base image")
    if sandbox.stack.kind == "rust" and "rust:" not in sandbox.files.get("Dockerfile", ""):
        errors.append("rust stack must use an official Rust base image")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suggest or write devcontainer, Dockerfile, and compose sandbox files.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("path", help="Project root to inspect")
    parser.add_argument("--write", action="store_true", help="Write suggested files")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--output", help="Write preview output to a file instead of stdout")
    args = parser.parse_args(argv)

    root = Path(args.path)
    sandbox = generate_sandbox(root)
    validation_errors = validate_sandbox(sandbox)
    if validation_errors:
        _emit(args, {"mode": "invalid", "errors": validation_errors, "sandbox": _sandbox_dict(sandbox)})
        return 2

    if args.write:
        conflicts = [name for name in sandbox.files if (root / name).exists()]
        if conflicts:
            print("error: refusing to overwrite existing files: " + ", ".join(conflicts), file=sys.stderr)
            return 2
        for name, content in sandbox.files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _emit(args, {"mode": "write", "written": sorted(sandbox.files), "sandbox": _sandbox_dict(sandbox)})
        return 0

    _emit(args, {"mode": "preview", "sandbox": _sandbox_dict(sandbox)})
    return 0


def _emit(args: argparse.Namespace, payload: dict[str, object]) -> None:
    if args.json:
        output = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    else:
        sandbox = payload.get("sandbox", {})
        output = _format_text(payload.get("mode", "preview"), sandbox if isinstance(sandbox, dict) else {})
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)


def _sandbox_dict(sandbox: SandboxSuggestion) -> dict[str, object]:
    return asdict(sandbox)


def _format_text(mode: object, sandbox: dict[str, object]) -> str:
    stack = sandbox.get("stack", {})
    files = sandbox.get("files", {})
    notes = sandbox.get("notes", [])
    lines = [f"Mode: {mode}", f"Detected stack: {stack.get('kind', 'unknown') if isinstance(stack, dict) else 'unknown'}", ""]
    lines.append("Suggested files:")
    if isinstance(files, dict):
        for name in sorted(files):
            lines.append(f"  - {name}")
    lines.append("")
    lines.append("Notes:")
    if isinstance(notes, list):
        for note in notes:
            lines.append(f"  - {note}")
    return "\n".join(lines).rstrip() + "\n"


def _python_files() -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": json.dumps(
            {
                "name": "Python Contributor Sandbox",
                "build": {"dockerfile": "../Dockerfile", "context": ".."},
                "workspaceFolder": "/workspace",
                "postCreateCommand": "python -m pip install --upgrade pip && if [ -f pyproject.toml ]; then python -m pip install -e .; elif [ -f requirements.txt ]; then python -m pip install -r requirements.txt; fi",
            },
            indent=2,
        )
        + "\n",
        "Dockerfile": "FROM python:3.11-slim\nWORKDIR /workspace\nRUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*\n",
        "docker-compose.yml": "services:\n  sandbox:\n    build: .\n    volumes:\n      - .:/workspace\n    command: sleep infinity\n",
    }


def _node_files() -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": json.dumps(
            {
                "name": "Node Contributor Sandbox",
                "build": {"dockerfile": "../Dockerfile", "context": ".."},
                "workspaceFolder": "/workspace",
                "postCreateCommand": "npm install",
            },
            indent=2,
        )
        + "\n",
        "Dockerfile": "FROM node:22-bookworm-slim\nWORKDIR /workspace\nRUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*\n",
        "docker-compose.yml": "services:\n  sandbox:\n    build: .\n    volumes:\n      - .:/workspace\n    command: sleep infinity\n",
    }


def _go_files() -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": json.dumps(
            {
                "name": "Go Contributor Sandbox",
                "build": {"dockerfile": "../Dockerfile", "context": ".."},
                "workspaceFolder": "/workspace",
                "postCreateCommand": "go mod download",
            },
            indent=2,
        )
        + "\n",
        "Dockerfile": "FROM golang:1.22-bookworm\nWORKDIR /workspace\nRUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*\n",
        "docker-compose.yml": "services:\n  sandbox:\n    build: .\n    volumes:\n      - .:/workspace\n      - go-mod:/go/pkg/mod\n    command: sleep infinity\nvolumes:\n  go-mod:\n",
    }


def _rust_files() -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": json.dumps(
            {
                "name": "Rust Contributor Sandbox",
                "build": {"dockerfile": "../Dockerfile", "context": ".."},
                "workspaceFolder": "/workspace",
                "postCreateCommand": "cargo fetch",
            },
            indent=2,
        )
        + "\n",
        "Dockerfile": "FROM rust:1-bookworm\nWORKDIR /workspace\nRUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*\n",
        "docker-compose.yml": "services:\n  sandbox:\n    build: .\n    volumes:\n      - .:/workspace\n      - cargo-registry:/usr/local/cargo/registry\n      - cargo-target:/workspace/target\n    command: sleep infinity\nvolumes:\n  cargo-registry:\n  cargo-target:\n",
    }


def _generic_files() -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": json.dumps(
            {
                "name": "Contributor Sandbox",
                "build": {"dockerfile": "../Dockerfile", "context": ".."},
                "workspaceFolder": "/workspace",
            },
            indent=2,
        )
        + "\n",
        "Dockerfile": "FROM debian:bookworm-slim\nWORKDIR /workspace\nRUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl make && rm -rf /var/lib/apt/lists/*\n",
        "docker-compose.yml": "services:\n  sandbox:\n    build: .\n    volumes:\n      - .:/workspace\n    command: sleep infinity\n",
    }


__all__ = ["__version__", "SandboxSuggestion", "StackInfo", "detect_stack", "generate_sandbox", "main", "validate_sandbox"]
