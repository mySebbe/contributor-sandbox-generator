# Security Review - July 2026

## Scope

The review covered stack detection, generated Docker/devcontainer/Compose content, image and user
input, build-context composition, and filesystem writes.

## Fixed Findings

1. Generated containers previously lacked a consistently enforced non-root runtime and container
   hardening. Output now drops all capabilities, enables `no-new-privileges`, and uses validated
   non-zero UID/GID values.
2. Source build contexts could expose local credentials or state. The secure default now sends no
   source, while opt-in source mode blocks common environment, registry, cloud, SSH, certificate,
   Terraform, and local-state files.
3. User-controlled base image text could be rendered into a Dockerfile without strict validation.
   OCI-style image references, registry ports, tags, and digests are now syntax checked.
4. Multi-file output was vulnerable to partial writes and unsafe path components. Writes now
   reject traversal, symlinks, and conflicts and clean up partial output on failure.

## Residual Risk

Generated files are a secure starting point, not a container sandbox boundary. A chosen image or
project install script may still be malicious. Prefer digest-pinned images and review dependency
install hooks before building untrusted projects.

## Validation

The final PR gate runs unittest, Ruff, Bandit, pip-audit, package build, Trivy, and generated-file
validation for each supported stack.
