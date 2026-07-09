"""Generate contributor sandbox suggestions for common project stacks."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from ._version import __version__

BuildContext = Literal["minimal", "source"]

_MAX_IMAGE_REFERENCE_LENGTH = 512
_MAX_LINUX_ID = 2_147_483_647
_BUILD_CONTEXTS = frozenset({"minimal", "source"})
_DEFAULT_IMAGES = {
    "python": "python:3.11-slim",
    "node": "node:22-bookworm-slim",
    "go": "golang:1.22-bookworm",
    "rust": "rust:1-bookworm",
    "generic": "debian:bookworm-slim",
}

_PATH_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_DOMAIN_COMPONENT = r"(?:[a-z0-9]|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
_DOMAIN = rf"{_DOMAIN_COMPONENT}(?:\.{_DOMAIN_COMPONENT})*"
_NAME = rf"(?:(?:{_DOMAIN})(?::[0-9]{{1,5}})?/)?{_PATH_COMPONENT}(?:/{_PATH_COMPONENT})*"
_TAG = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
_ALGORITHM = r"[A-Za-z][A-Za-z0-9]*(?:[+._-][A-Za-z][A-Za-z0-9]*)*"
_DIGEST = rf"{_ALGORITHM}:[A-Fa-f0-9]{{32,}}"
_IMAGE_REFERENCE_RE = re.compile(rf"{_NAME}(?::{_TAG})?(?:@{_DIGEST})?\Z", re.ASCII)

_COMMON_DOCKERIGNORE_PATTERNS = (
    ".git",
    ".hg",
    ".svn",
    ".DS_Store",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".venv",
    "venv",
    "build",
    "dist",
)
_SECRET_DOCKERIGNORE_PATTERNS = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "!.env.example",
    "!.env.sample",
    "!**/.env.example",
    "!**/.env.sample",
    ".npmrc",
    "**/.npmrc",
    ".pypirc",
    "**/.pypirc",
    ".netrc",
    "**/.netrc",
    ".aws",
    "**/.aws",
    ".azure",
    "**/.azure",
    ".kube",
    "**/.kube",
    ".config/gcloud",
    "**/.config/gcloud",
    ".docker/config.json",
    "**/.docker/config.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "**/id_rsa",
    "**/id_dsa",
    "**/id_ecdsa",
    "**/id_ed25519",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
    "**/*.jks",
    "**/*.keystore",
    "**/*.kdbx",
    "credentials.json",
    "**/credentials.json",
    "service-account*.json",
    "**/service-account*.json",
    "kubeconfig",
    "**/kubeconfig",
    ".terraform",
    "**/.terraform",
    "*.tfstate",
    "*.tfstate.*",
    "**/*.tfstate",
    "**/*.tfstate.*",
)


@dataclass(frozen=True)
class StackInfo:
    kind: str
    evidence: list[str]


@dataclass(frozen=True)
class SandboxOptions:
    """Validated options that affect generated container security and compatibility."""

    image: str | None = None
    uid: int = 1000
    gid: int = 1000
    build_context: BuildContext = "minimal"

    def __post_init__(self) -> None:
        if self.image is not None:
            validate_image_reference(self.image)
        _validate_linux_id("uid", self.uid)
        _validate_linux_id("gid", self.gid)
        if self.build_context not in _BUILD_CONTEXTS:
            choices = ", ".join(sorted(_BUILD_CONTEXTS))
            raise ValueError(f"build_context must be one of: {choices}")


@dataclass(frozen=True)
class SandboxRuntime:
    image: str
    user: str
    uid: int
    gid: int
    build_context: BuildContext


@dataclass(frozen=True)
class SandboxSuggestion:
    stack: StackInfo
    files: dict[str, str]
    notes: list[str]
    runtime: SandboxRuntime | None = None


def validate_image_reference(reference: str) -> str:
    """Return a safe OCI-style image reference or raise ``ValueError``."""

    if not isinstance(reference, str):
        raise ValueError("image reference must be a string")
    if not reference or len(reference) > _MAX_IMAGE_REFERENCE_LENGTH:
        raise ValueError(
            f"image reference must contain 1 to {_MAX_IMAGE_REFERENCE_LENGTH} characters"
        )
    if reference == "scratch":
        raise ValueError("scratch is incompatible with generated contributor sandboxes")
    if _IMAGE_REFERENCE_RE.fullmatch(reference) is None:
        raise ValueError(f"invalid image reference: {reference!r}")

    first_component, separator, _ = reference.partition("/")
    if separator and ":" in first_component:
        port_text = first_component.rsplit(":", 1)[1]
        port = int(port_text)
        if not 1 <= port <= 65_535:
            raise ValueError("image registry port must be between 1 and 65535")
    return reference


def detect_stack(root: str | Path) -> StackInfo:
    project_root = _project_root(root)
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


def generate_sandbox(
    root: str | Path, options: SandboxOptions | None = None
) -> SandboxSuggestion:
    effective_options = options or SandboxOptions()
    if not isinstance(effective_options, SandboxOptions):
        raise TypeError("options must be a SandboxOptions instance")

    stack = detect_stack(root)
    image = validate_image_reference(effective_options.image or _DEFAULT_IMAGES[stack.kind])
    runtime = SandboxRuntime(
        image=image,
        user="node" if stack.kind == "node" else "sandbox",
        uid=effective_options.uid,
        gid=effective_options.gid,
        build_context=effective_options.build_context,
    )

    if stack.kind == "python":
        files = _python_files(runtime)
        stack_note = (
            "Python sandbox uses Python 3.11 and installs editable project dependencies "
            "when pyproject.toml is present."
        )
    elif stack.kind == "node":
        files = _node_files(runtime)
        stack_note = (
            "Node sandbox uses the Node 22 image and runs npm install when package.json is present."
        )
    elif stack.kind == "go":
        files = _go_files(runtime)
        stack_note = (
            "Go sandbox uses the official Go image and caches modules in a container volume."
        )
    elif stack.kind == "rust":
        files = _rust_files(runtime)
        stack_note = (
            "Rust sandbox uses the official Rust image and caches Cargo registry and target "
            "output in container volumes."
        )
    else:
        files = _generic_files(runtime)
        stack_note = "Generic sandbox provides a Debian base with common contributor tools."

    context_note = (
        "Docker sends only Dockerfile metadata in minimal build-context mode."
        if runtime.build_context == "minimal"
        else "Source build-context mode excludes common credentials, keys, state, and local caches."
    )
    notes = [
        stack_note,
        f"Container commands run as non-root user {runtime.user} ({runtime.uid}:{runtime.gid}).",
        context_note,
    ]
    if "@" not in runtime.image:
        notes.append(
            "The selected image uses a mutable tag; use --image with a digest for reproducible builds."
        )
    return SandboxSuggestion(stack=stack, files=files, notes=notes, runtime=runtime)


def validate_sandbox(sandbox: SandboxSuggestion) -> list[str]:
    errors: list[str] = []
    required = [
        ".devcontainer/devcontainer.json",
        "Dockerfile",
        "docker-compose.yml",
        ".dockerignore",
    ]
    for name in required:
        if name not in sandbox.files:
            errors.append(f"missing generated file: {name}")

    devcontainer_text = sandbox.files.get(".devcontainer/devcontainer.json", "")
    try:
        devcontainer = json.loads(devcontainer_text)
    except (json.JSONDecodeError, TypeError):
        errors.append("devcontainer must contain valid JSON")
        devcontainer = {}
    if not isinstance(devcontainer, dict):
        errors.append("devcontainer JSON must be an object")
        devcontainer = {}

    build = devcontainer.get("build")
    if not isinstance(build, dict) or build.get("dockerfile") != "../Dockerfile":
        errors.append("devcontainer must reference ../Dockerfile")
    for key in ("containerUser", "remoteUser"):
        value = devcontainer.get(key)
        if not isinstance(value, str) or value.lower() in {"root", "0", "0:0"}:
            errors.append(f"devcontainer {key} must select a non-root user")
    run_args = devcontainer.get("runArgs")
    if not isinstance(run_args, list) or "--cap-drop=ALL" not in run_args:
        errors.append("devcontainer must drop Linux capabilities")
    if not isinstance(run_args, list) or "--security-opt=no-new-privileges:true" not in run_args:
        errors.append("devcontainer must enable no-new-privileges")

    dockerfile = sandbox.files.get("Dockerfile", "")
    image = _dockerfile_image(dockerfile, errors)
    users = [
        line.split(maxsplit=1)[1].strip()
        for line in dockerfile.splitlines()
        if line.strip().upper().startswith("USER ")
    ]
    if not users or users[-1].split(":", 1)[0].lower() in {"root", "0"}:
        errors.append("Dockerfile must end with a non-root runtime user")

    compose = sandbox.files.get("docker-compose.yml", "")
    if "no-new-privileges:true" not in compose:
        errors.append("compose sandbox must enable no-new-privileges")
    if "cap_drop:\n      - ALL" not in compose:
        errors.append("compose sandbox must drop Linux capabilities")

    dockerignore = sandbox.files.get(".dockerignore", "")
    patterns = {
        line.strip()
        for line in dockerignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    context_mode = sandbox.runtime.build_context if sandbox.runtime else None
    if context_mode == "minimal" or (context_mode is None and "**" in patterns):
        for pattern in ("**", "!Dockerfile", "!.dockerignore"):
            if pattern not in patterns:
                errors.append(f"minimal .dockerignore must include {pattern}")
    else:
        missing_secrets = sorted(set(_SECRET_DOCKERIGNORE_PATTERNS) - patterns)
        if missing_secrets:
            errors.append("source .dockerignore is missing secret exclusions")

    if sandbox.runtime:
        runtime = sandbox.runtime
        if image is not None and image != runtime.image:
            errors.append("Dockerfile base image must match the validated runtime image")
        if users and users[-1] != runtime.user:
            errors.append("Dockerfile runtime user must match sandbox metadata")
        if devcontainer.get("containerUser") != runtime.user:
            errors.append("devcontainer containerUser must match sandbox metadata")
        expected_compose_user = f'user: "{runtime.uid}:{runtime.gid}"'
        if expected_compose_user not in compose:
            errors.append("compose runtime UID/GID must match sandbox metadata")

    if sandbox.stack.kind == "python" and image and not image.endswith("python:3.11"):
        if "python:3.11" not in image and "python:3.11" not in dockerfile:
            errors.append("python stack must use a Python 3.11-compatible base image")
    if sandbox.stack.kind == "rust" and image and "rust:" not in image:
        if sandbox.runtime is None or sandbox.runtime.image == _DEFAULT_IMAGES["rust"]:
            errors.append("rust stack must use an official Rust base image")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Suggest or write devcontainer, Dockerfile, and compose sandbox files."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("path", help="Existing project root to inspect")
    parser.add_argument("--write", action="store_true", help="Write suggested files")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--output", help="Write preview or result output to a new file")
    parser.add_argument(
        "--force-output",
        action="store_true",
        help="Atomically replace an existing regular --output file",
    )
    parser.add_argument("--image", help="Validated OCI image reference override")
    parser.add_argument("--uid", type=int, default=1000, help="Non-root runtime UID (default: 1000)")
    parser.add_argument("--gid", type=int, default=1000, help="Non-root runtime GID (default: 1000)")
    parser.add_argument(
        "--build-context",
        choices=sorted(_BUILD_CONTEXTS),
        default="minimal",
        help="minimal sends no project source; source applies a secret denylist",
    )
    args = parser.parse_args(argv)

    if args.force_output and not args.output:
        print("error: --force-output requires --output", file=sys.stderr)
        return 2

    try:
        root = _project_root(args.path)
        options = SandboxOptions(
            image=args.image,
            uid=args.uid,
            gid=args.gid,
            build_context=args.build_context,
        )
        sandbox = generate_sandbox(root, options)
        _preflight_output(args, root, sandbox.files)
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    validation_errors = validate_sandbox(sandbox)
    if validation_errors:
        try:
            _emit(
                args,
                {"mode": "invalid", "errors": validation_errors, "sandbox": _sandbox_dict(sandbox)},
            )
        except OSError as exc:
            print(f"error: unable to write output: {exc}", file=sys.stderr)
        return 2

    if args.write:
        try:
            written = _write_sandbox(root, sandbox.files)
            _emit(
                args,
                {"mode": "write", "written": written, "sandbox": _sandbox_dict(sandbox)},
            )
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    try:
        _emit(args, {"mode": "preview", "sandbox": _sandbox_dict(sandbox)})
    except OSError as exc:
        print(f"error: unable to write output: {exc}", file=sys.stderr)
        return 2
    return 0


def _project_root(root: str | Path) -> Path:
    supplied = Path(root).expanduser()
    try:
        resolved = supplied.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"project path is not an existing directory: {supplied}") from exc
    if not resolved.is_dir():
        raise ValueError(f"project path is not an existing directory: {supplied}")
    return resolved


def _validate_linux_id(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not 1 <= value <= _MAX_LINUX_ID:
        raise ValueError(f"{name} must be between 1 and {_MAX_LINUX_ID}")


def _dockerfile_image(dockerfile: str, errors: list[str]) -> str | None:
    from_lines = [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    if len(from_lines) != 1:
        errors.append("Dockerfile must contain exactly one simple FROM instruction")
        return None
    parts = from_lines[0].split()
    if len(parts) != 2:
        errors.append("Dockerfile FROM instruction must contain only a validated image reference")
        return None
    image = parts[1]
    try:
        validate_image_reference(image)
    except ValueError:
        errors.append("Dockerfile contains an invalid base image reference")
        return None
    return image


def _preflight_output(
    args: argparse.Namespace, root: Path, generated_files: dict[str, str]
) -> None:
    if not args.output:
        return
    output = Path(args.output).expanduser()
    output_key = _path_key(output)
    if args.write:
        generated_keys = {_path_key(root / PurePosixPath(name)) for name in generated_files}
        if output_key in generated_keys:
            raise ValueError("--output must not replace a generated sandbox file")

    if os.path.lexists(output):
        if output.is_symlink():
            raise ValueError("refusing to write --output through a symbolic link")
        if output.is_dir():
            raise ValueError("--output must name a file, not a directory")
        if not args.force_output:
            raise ValueError("refusing to overwrite existing --output; use --force-output")

    parent = output.parent if output.parent != Path("") else Path.cwd()
    if not parent.exists():
        generated_devcontainer = root / ".devcontainer"
        if not (args.write and _path_key(parent) == _path_key(generated_devcontainer)):
            raise ValueError(f"--output parent directory does not exist: {parent}")
    elif not parent.is_dir():
        raise ValueError(f"--output parent is not a directory: {parent}")


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _write_sandbox(root: Path, files: dict[str, str]) -> list[str]:
    project_root = _project_root(root)
    destinations: list[tuple[str, PurePosixPath, Path, str]] = []
    conflicts: list[str] = []

    for name, content in files.items():
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or "\\" in name
        ):
            raise ValueError(f"unsafe generated path: {name}")
        destination = project_root.joinpath(*relative.parts)
        _check_no_symlink_components(project_root, relative)
        if os.path.lexists(destination):
            conflicts.append(name)
        destinations.append((name, relative, destination, content))

    if conflicts:
        raise ValueError("refusing to overwrite existing files: " + ", ".join(sorted(conflicts)))

    required_directories = sorted(
        {
            project_root.joinpath(*relative.parts[:depth])
            for _, relative, _, _ in destinations
            for depth in range(1, len(relative.parts))
        },
        key=lambda path: len(path.parts),
    )
    created_directories: list[Path] = []
    created_files: list[Path] = []
    try:
        for directory in required_directories:
            relative = PurePosixPath(directory.relative_to(project_root).as_posix())
            _check_no_symlink_components(project_root, relative)
            if directory.exists():
                if not directory.is_dir():
                    raise ValueError(f"generated parent is not a directory: {relative.as_posix()}")
                continue
            try:
                directory.mkdir()
                created_directories.append(directory)
            except FileExistsError:
                _check_no_symlink_components(project_root, relative)
                if not directory.is_dir():
                    raise ValueError(
                        f"generated parent is not a directory: {relative.as_posix()}"
                    ) from None

        for name, relative, destination, content in destinations:
            _check_no_symlink_components(project_root, relative)
            try:
                with destination.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                created_files.append(destination)
            except FileExistsError:
                raise ValueError(f"refusing to overwrite existing files: {name}") from None
    except Exception:
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise
    return sorted(files)


def _check_no_symlink_components(root: Path, relative: PurePosixPath) -> None:
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            traversed = PurePosixPath(*relative.parts[: index + 1]).as_posix()
            raise ValueError(f"refusing to write through symbolic link: {traversed}")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
            traversed = PurePosixPath(*relative.parts[: index + 1]).as_posix()
            raise ValueError(f"generated parent is not a directory: {traversed}")


def _emit(args: argparse.Namespace, payload: dict[str, object]) -> None:
    if args.json:
        output = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    else:
        sandbox = payload.get("sandbox", {})
        output = _format_text(
            payload.get("mode", "preview"), sandbox if isinstance(sandbox, dict) else {}
        )
    if args.output:
        _write_output(Path(args.output).expanduser(), output, overwrite=args.force_output)
    else:
        sys.stdout.write(output)


def _write_output(path: Path, content: str, *, overwrite: bool) -> None:
    if not overwrite:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        return

    parent = path.parent if path.parent != Path("") else Path.cwd()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _sandbox_dict(sandbox: SandboxSuggestion) -> dict[str, object]:
    return asdict(sandbox)


def _format_text(mode: object, sandbox: dict[str, object]) -> str:
    stack = sandbox.get("stack", {})
    runtime = sandbox.get("runtime", {})
    files = sandbox.get("files", {})
    notes = sandbox.get("notes", [])
    lines = [
        f"Mode: {mode}",
        f"Detected stack: {stack.get('kind', 'unknown') if isinstance(stack, dict) else 'unknown'}",
    ]
    if isinstance(runtime, dict):
        lines.extend(
            [
                f"Base image: {runtime.get('image', 'unknown')}",
                (
                    "Runtime user: "
                    f"{runtime.get('user', 'unknown')} "
                    f"({runtime.get('uid', '?')}:{runtime.get('gid', '?')})"
                ),
                f"Build context: {runtime.get('build_context', 'unknown')}",
            ]
        )
    lines.extend(["", "Suggested files:"])
    if isinstance(files, dict):
        for name in sorted(files):
            lines.append(f"  - {name}")
    lines.extend(["", "Notes:"])
    if isinstance(notes, list):
        for note in notes:
            lines.append(f"  - {note}")
    return "\n".join(lines).rstrip() + "\n"


def _python_files(runtime: SandboxRuntime) -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": _devcontainer(
            "Python Contributor Sandbox",
            runtime,
            (
                "if [ -f pyproject.toml ]; then python -m pip install --user -e .; "
                "elif [ -f requirements.txt ]; then python -m pip install --user "
                "-r requirements.txt; fi"
            ),
        ),
        "Dockerfile": _debian_dockerfile(runtime, ["git", "ca-certificates"]),
        "docker-compose.yml": _compose(runtime),
        ".dockerignore": _dockerignore(runtime.build_context),
    }


def _node_files(runtime: SandboxRuntime) -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": _devcontainer(
            "Node Contributor Sandbox", runtime, "npm install"
        ),
        "Dockerfile": _debian_dockerfile(
            runtime, ["git", "ca-certificates"], existing_user="node"
        ),
        "docker-compose.yml": _compose(runtime),
        ".dockerignore": _dockerignore(
            runtime.build_context, "node_modules", "dist", "coverage"
        ),
    }


def _go_files(runtime: SandboxRuntime) -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": _devcontainer(
            "Go Contributor Sandbox", runtime, "go mod download"
        ),
        "Dockerfile": _debian_dockerfile(
            runtime,
            ["git", "ca-certificates"],
            writable_paths=["/go/pkg/mod"],
        ),
        "docker-compose.yml": _compose(runtime, ["go-mod:/go/pkg/mod"], ["go-mod"]),
        ".dockerignore": _dockerignore(runtime.build_context, "bin"),
    }


def _rust_files(runtime: SandboxRuntime) -> dict[str, str]:
    cargo_registry = f"/home/{runtime.user}/.cargo/registry"
    return {
        ".devcontainer/devcontainer.json": _devcontainer(
            "Rust Contributor Sandbox", runtime, "cargo fetch"
        ),
        "Dockerfile": _debian_dockerfile(
            runtime,
            ["git", "ca-certificates", "pkg-config", "libssl-dev"],
            writable_paths=[cargo_registry, "/workspace/target"],
            environment=[
                f"CARGO_HOME=/home/{runtime.user}/.cargo",
                f'PATH="/home/{runtime.user}/.cargo/bin:${{PATH}}"',
            ],
        ),
        "docker-compose.yml": _compose(
            runtime,
            [f"cargo-registry:{cargo_registry}", "cargo-target:/workspace/target"],
            ["cargo-registry", "cargo-target"],
        ),
        ".dockerignore": _dockerignore(runtime.build_context, "target"),
    }


def _generic_files(runtime: SandboxRuntime) -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": _devcontainer("Contributor Sandbox", runtime),
        "Dockerfile": _debian_dockerfile(
            runtime, ["git", "ca-certificates", "curl", "make"]
        ),
        "docker-compose.yml": _compose(runtime),
        ".dockerignore": _dockerignore(runtime.build_context),
    }


def _devcontainer(
    name: str, runtime: SandboxRuntime, post_create_command: str | None = None
) -> str:
    payload: dict[str, object] = {
        "name": name,
        "build": {"dockerfile": "../Dockerfile", "context": ".."},
        "workspaceFolder": "/workspace",
        "containerUser": runtime.user,
        "remoteUser": runtime.user,
        "updateRemoteUserUID": True,
        "runArgs": [
            "--init",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
        ],
    }
    if post_create_command:
        payload["postCreateCommand"] = post_create_command
    return json.dumps(payload, indent=2) + "\n"


def _debian_dockerfile(
    runtime: SandboxRuntime,
    packages: list[str],
    *,
    existing_user: str | None = None,
    writable_paths: list[str] | None = None,
    environment: list[str] | None = None,
) -> str:
    package_list = " ".join(dict.fromkeys([*packages, "passwd"]))
    paths = ["/workspace", *(writable_paths or [])]
    lines = [
        f"FROM {runtime.image}",
        "USER root",
        "RUN apt-get update \\",
        f"    && apt-get install -y --no-install-recommends {package_list} \\",
    ]
    if existing_user:
        lines.extend(
            [
                (
                    f'    && if [ "$(id -g {existing_user})" != "{runtime.gid}" ]; '
                    f"then groupmod --gid {runtime.gid} {existing_user}; fi \\"
                ),
                (
                    f'    && if [ "$(id -u {existing_user})" != "{runtime.uid}" ]; '
                    f"then usermod --uid {runtime.uid} --gid {runtime.gid} {existing_user}; fi \\"
                ),
            ]
        )
    else:
        lines.extend(
            [
                f"    && groupadd --gid {runtime.gid} {runtime.user} \\",
                (
                    f"    && useradd --uid {runtime.uid} --gid {runtime.gid} --create-home "
                    f"--shell /bin/sh {runtime.user} \\"
                ),
            ]
        )
    lines.extend(
        [
            f"    && mkdir -p {' '.join(paths)} \\",
            f"    && chown -R {runtime.user}:{runtime.user} {' '.join(paths)} \\",
            "    && rm -rf /var/lib/apt/lists/*",
            f"ENV HOME=/home/{runtime.user}",
            f'ENV PATH="/home/{runtime.user}/.local/bin:${{PATH}}"',
        ]
    )
    for value in environment or []:
        lines.append(f"ENV {value}")
    lines.extend(["WORKDIR /workspace", f"USER {runtime.user}"])
    return "\n".join(lines) + "\n"


def _compose(
    runtime: SandboxRuntime,
    extra_volumes: list[str] | None = None,
    named_volumes: list[str] | None = None,
) -> str:
    lines = [
        "services:",
        "  sandbox:",
        "    build:",
        "      context: .",
        "      dockerfile: Dockerfile",
        f'    user: "{runtime.uid}:{runtime.gid}"',
        "    working_dir: /workspace",
        "    init: true",
        "    cap_drop:",
        "      - ALL",
        "    security_opt:",
        "      - no-new-privileges:true",
        "    volumes:",
        "      - .:/workspace",
    ]
    for volume in extra_volumes or []:
        lines.append(f"      - {volume}")
    lines.append('    command: ["sleep", "infinity"]')
    if named_volumes:
        lines.append("volumes:")
        for volume in named_volumes:
            lines.append(f"  {volume}:")
    return "\n".join(lines) + "\n"


def _dockerignore(build_context: BuildContext, *extra_patterns: str) -> str:
    if build_context == "minimal":
        return (
            "# The generated image does not COPY project files. Keep source and secrets local.\n"
            "**\n"
            "!Dockerfile\n"
            "!.dockerignore\n"
        )

    patterns = [
        *_COMMON_DOCKERIGNORE_PATTERNS,
        *extra_patterns,
        *_SECRET_DOCKERIGNORE_PATTERNS,
    ]
    return (
        "# Source context is opt-in; exclude common local credentials and generated data.\n"
        + "\n".join(dict.fromkeys(patterns))
        + "\n"
    )


__all__ = [
    "__version__",
    "SandboxOptions",
    "SandboxRuntime",
    "SandboxSuggestion",
    "StackInfo",
    "detect_stack",
    "generate_sandbox",
    "main",
    "validate_image_reference",
    "validate_sandbox",
]
