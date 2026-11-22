---
name: release-check
description: Checklist to run before publishing a TicoRom release on GitHub. Checks tests, the lock file, version numbers, the Docker image and the notes, and lists the hardware tests the maintainer runs before publishing.
disable-model-invocation: true
argument-hint: "[version, e.g. 1.1.0]"
---

# Before publishing a release

Publishing a GitHub release runs `.github/workflows/docker-publish.yml`. It builds the image for amd64 and arm64 and pushes it to `ghcr.io` tagged `X.Y.Z`, `X.Y` and `X`, plus `latest` unless the release is a pre-release. Everything below must pass first. Report each step as passed or failed, with the output of any failure. Don't tag, push or publish anything yourself: the maintainer does that.

1. **Clean tree.** `git status` shows nothing uncommitted.
2. **Version.** If a version was given ($ARGUMENTS), it must appear in both `pyproject.toml` (`version`) and `src/ticorom/__init__.py` (`__version__`). After changing them, run `uv lock` so `uv.lock` records it.
3. **Lock file.** `uv lock --check` passes.
4. **Tests.** `uv run pytest` passes with no failures.
5. **Image.** `docker compose build` succeeds, and `docker compose run --rm ticorom check` passes against the maintainer's RomM.
6. **Trial run.** The `trial-run` skill passes for a cartridge platform (`.claude/skills/trial-run/trial.sh gba`).
7. **Notes and README.** Every behaviour change since the last release is reflected in `.claude/rules/`, `.claude/docs/` and `README.md`. Compare with `git log <last tag>..HEAD`.
8. **Hardware (the maintainer).** On a Switch with tico, from the new image:
   - add the repository and check games and covers are listed;
   - install a small cartridge game and launch it;
   - install a larger game, interrupt it once (sleep the console or switch Wi-Fi off briefly), and check it resumes and launches.

   The README promises that releases are tested before they are published, so this step is not optional.
9. **Release notes.** Summarise the changes since the last tag for the GitHub release text.
