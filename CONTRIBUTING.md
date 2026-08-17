# Contributing

欢迎为 AI-creat 仓库贡献。请保持改动聚焦、可复现，并在涉及网格划分、边界条件、结果解析、机器人负载计算或报告输出时附上可复现的测试用例。

## 提交规范（Conventional Commits）

请使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式，便于自动生成变更日志与回放历史：

```
<type>(<scope>): <subject>

[body]
```

常用 type：

| type | 用途 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `docs` | 文档改动 |
| `refactor` | 不改变行为的重构 |
| `test` | 测试新增或修改 |
| `chore` | 构建/工具/杂项 |
| `ci` | CI 配置改动 |

示例：

```
docs: document CalculiX runtime fetch step

refactor: extract mesh generation into its own module
```

## 分支与 PR 流程

1. 从 `main` 检出特性分支：`git checkout -b feat/xxx`（或 `fix/xxx`、`docs/xxx`）。
2. 在本机通过测试（见下）。
3. 提交并推送分支，打开 Pull Request 到 `main`。
4. 说明改动动机、影响范围与测试结论。

## 本机测试

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```

CI（`.github/workflows/ci.yml`）会在每个 push / PR 上以相同方式自动运行。

## 仓库红线

- **不要**提交客户 STEP 文件、个人信息、专有阀体图纸、构建目录、虚拟环境或求解器输出目录。
- **不要**提交二进制运行时文件：CalculiX 求解器（`runtime/ccx/*.exe`、`*.dll`）通过 `scripts/fetch-ccx.ps1` 获取，请勿提交。
- **不要**破坏子项目边界：`industrial-voice-control/` 是独立的 MIT 子项目，改动应保持在其目录内，并同步更新其目录内的 LICENSE / README / 第三方说明。
- 修改 `pyproject.toml`、打包脚本或依赖时，请说明理由并在 PR 中标注验证结果。

## 子项目

- 主项目 **AI-FEA Valve Gripper** 的代码在 `src/ai_fea_mvp/`，测试在 `tests/`。
- **robot-competition-copilot** 是一个技能（SKILL.md），文档性改动直接编辑其目录内文件即可。
- **industrial-voice-control** 是独立子项目，有自己完整的工程与文档。
