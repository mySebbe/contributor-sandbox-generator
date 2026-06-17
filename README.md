# contributor-sandbox-generator

`contributor-sandbox-generator` inspects a project directory and suggests contributor sandbox files:

- `.devcontainer/devcontainer.json`
- `Dockerfile`
- `docker-compose.yml`

It supports Python, Node, Go, and generic projects in v0.1.

## 0.1.1 Highlights

- Rust projects are now detected from `Cargo.toml`.
- Generated Rust sandboxes include Cargo registry and target cache volumes.

## Usage

Preview suggestions without writing:

```bash
python -m contributor_sandbox_generator --json /path/to/project
```

Write generated files:

```bash
python -m contributor_sandbox_generator --write /path/to/project
```

The write mode refuses to overwrite existing sandbox files.

## Development

```bash
python -m unittest discover -s tests
```
