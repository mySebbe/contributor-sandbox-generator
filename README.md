# contributor-sandbox-generator

`contributor-sandbox-generator` inspects a project directory and suggests contributor sandbox files:

- `.devcontainer/devcontainer.json`
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

It supports Python, Node, Go, and generic projects in v0.1.

## 0.1.2 Highlights

- Generated sandboxes now include a stack-aware `.dockerignore`.
- Build contexts skip VCS metadata, virtual environments, caches, and common build output.

## Usage

Preview suggestions without writing:

```bash
python -m contributor_sandbox_generator --json /path/to/project
```

Write generated files:

```bash
python -m contributor_sandbox_generator --write /path/to/project
```

Generated containers run as a validated non-root UID/GID, drop all Linux capabilities, and enable
`no-new-privileges`. The default `minimal` build context sends only Dockerfile metadata. Projects
that need source during image builds can opt into a denylist-protected context:

```bash
python -m contributor_sandbox_generator /path/to/project --build-context source --uid 1000 --gid 1000
python -m contributor_sandbox_generator /path/to/project --image python@sha256:<digest>
```

Image references are validated before they are rendered into a Dockerfile. Write mode refuses
existing files and symlinked path components, and file output is atomic.

## Development

```bash
python -m unittest discover -s tests
```
