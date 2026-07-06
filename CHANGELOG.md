# Changelog

All notable changes to `contributor-sandbox-generator` will be documented in this file.

The format is based on Keep a Changelog, and this project uses semantic versioning.

## [Unreleased]

- No unreleased changes yet.

## [0.1.2] - 2026-07-06

- Updated GitHub Actions workflow dependencies to current major versions.
- Modernized package license metadata to avoid current Setuptools deprecation warnings.
- Added generated `.dockerignore` files for every supported stack.
- Excluded VCS metadata, virtual environments, caches, and stack-specific build output from Docker build contexts.

## [0.1.1] - 2026-06-17

- Added Rust/Cargo stack detection.
- Generated Rust devcontainer, Dockerfile, and docker-compose suggestions with Cargo caches.
- Fixed GitHub Actions workflow pins to supported action versions.

## [0.1.0] - 2026-06-03

- Initial open-source release with CLI, examples, tests, GitHub workflows, security policy, and contributor docs.
