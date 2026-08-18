# Contributing

> **English** · [中文](CONTRIBUTING.md) · Also available in the repo
> [English README](README.en.md) / [中文 README](README.md).

Welcome to the AI-creat repository. Please keep changes focused and
reproducible, and include a reproducible test case when your change touches
meshing, boundary conditions, result parsing, robot-load calculations or report
output.

## Commit conventions (Conventional Commits)

Please use [Conventional Commits](https://www.conventionalcommits.org/) so the
changelog and history stay machine-readable:

```
<type>(<scope>): <subject>

[body]
```

Common types:

| type | purpose |
| --- | --- |
| `feat` | new feature |
| `fix` | bug fix |
| `docs` | documentation changes |
| `refactor` | refactor that does not change behavior |
| `test` | add or modify tests |
| `chore` | build / tooling / misc |
| `ci` | CI configuration changes |

Examples:

```
docs: document CalculiX runtime fetch step

refactor: extract mesh generation into its own module
```

## Branch and PR workflow

1. Create a feature branch from `main`: `git checkout -b feat/xxx` (or
   `fix/xxx`, `docs/xxx`).
2. Run the tests locally (below).
3. Commit and push the branch, then open a pull request into `main`.
4. Describe the motivation, scope, and test results.

## Local tests

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```

CI (`.github/workflows/ci.yml`) runs the same suite on every push / PR.

## Repository red lines

- **Do not** commit customer STEP files, personal information, proprietary
  valve drawings, build directories, virtual environments, or solver output
  directories.
- **Do not** commit binary runtime files: the CalculiX solver
  (`runtime/ccx/*.exe`, `*.dll`) is fetched with `scripts/fetch-ccx.ps1`.
- **Do not** break sub-project boundaries: `industrial-voice-control/` is an
  independent MIT sub-project — keep changes inside its directory and update
  its LICENSE / README / third-party notices there.
- When modifying `pyproject.toml`, packaging scripts or dependencies, state
  the reason and note the verification results in the PR.

## Sub-projects

- The main **AI-FEA Valve Gripper** code lives in `src/ai_fea_mvp/`, with tests
  in `tests/`.
- **robot-competition-copilot** is a skill (`SKILL.md`); documentation changes
  can edit its files directly.
- **industrial-voice-control** is an independent sub-project with its own
  complete engineering and documentation.
