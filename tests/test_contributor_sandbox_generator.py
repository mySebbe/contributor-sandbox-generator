import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contributor_sandbox_generator import (
    SandboxOptions,
    detect_stack,
    generate_sandbox,
    main,
    validate_image_reference,
    validate_sandbox,
)


class ContributorSandboxGeneratorTests(unittest.TestCase):
    def test_detects_python_stack_from_pyproject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nrequires-python = '>=3.11'\n", encoding="utf-8")

            stack = detect_stack(root)

            self.assertEqual("python", stack.kind)
            self.assertIn("pyproject.toml", stack.evidence)

    def test_detects_node_stack_from_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")

            stack = detect_stack(root)

            self.assertEqual("node", stack.kind)
            self.assertIn("package.json", stack.evidence)

    def test_generates_python_devcontainer_dockerfile_and_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "requirements.txt").write_text("requests==2.32.0\n", encoding="utf-8")

            sandbox = generate_sandbox(root)

            self.assertIn(".devcontainer/devcontainer.json", sandbox.files)
            self.assertIn("Dockerfile", sandbox.files)
            self.assertIn("docker-compose.yml", sandbox.files)
            self.assertIn(".dockerignore", sandbox.files)
            self.assertIn("python:3.11", sandbox.files["Dockerfile"])
            self.assertIn("**", sandbox.files[".dockerignore"])
            self.assertIn("!Dockerfile", sandbox.files[".dockerignore"])
            self.assertIn("USER sandbox", sandbox.files["Dockerfile"])
            self.assertIn("no-new-privileges", sandbox.files["docker-compose.yml"])
            self.assertEqual([], validate_sandbox(sandbox))

    def test_generates_rust_sandbox_with_cargo_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text("[package]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8")

            sandbox = generate_sandbox(root)

            self.assertEqual("rust", sandbox.stack.kind)
            self.assertIn("rust:", sandbox.files["Dockerfile"])
            self.assertIn("cargo-registry", sandbox.files["docker-compose.yml"])
            self.assertIn("**", sandbox.files[".dockerignore"])
            self.assertEqual([], validate_sandbox(sandbox))

    def test_source_context_excludes_common_credentials_and_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

            sandbox = generate_sandbox(root, SandboxOptions(build_context="source"))

        dockerignore = sandbox.files[".dockerignore"]
        for secret_pattern in (".env", "**/.npmrc", "**/*.pem", "**/*.tfstate"):
            self.assertIn(secret_pattern, dockerignore)
        self.assertEqual([], validate_sandbox(sandbox))

    def test_image_reference_and_linux_identity_options_are_validated(self):
        self.assertEqual("python:3.12-slim", validate_image_reference("python:3.12-slim"))
        with self.assertRaises(ValueError):
            validate_image_reference("python:latest; RUN curl evil.test")
        with self.assertRaises(ValueError):
            SandboxOptions(uid=0)

    def test_cli_preview_does_not_write_without_write_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
            output = Path(tmp) / "preview.json"

            code = main(["--json", "--output", str(output), str(root)])

            self.assertEqual(0, code)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("preview", payload["mode"])
            self.assertFalse((root / "Dockerfile").exists())

    def test_cli_write_refuses_to_overwrite_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
            (root / "Dockerfile").write_text("FROM custom\n", encoding="utf-8")

            code = main(["--write", str(root)])

            self.assertEqual(2, code)
            self.assertEqual("FROM custom\n", (root / "Dockerfile").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
