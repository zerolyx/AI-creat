# AI-creat

开源工程工具与 Codex 技能集合。

- [AI-FEA Valve Gripper](#ai-fea-valve-gripper)：球阀自动装配夹爪的 Windows 有限元分析助手。
- [robot-competition-copilot](robot-competition-copilot/SKILL.md)：机器人竞赛协作技能。

---

## AI-FEA Valve Gripper

面向 Q41F-16P / 16RL 法兰球阀自动装配夹爪的 Windows 有限元分析助手。软件使用本地 Gmsh 与 CalculiX 完成真实网格和线性静力求解，不依赖云端求解服务。

![球阀夹爪分析界面](docs/gui_valve_gripper_final.png)

## 能做什么

- 导入 STEP / STP，多实体装配体自动识别并生成四面体网格。
- 针对华数 HSR-CR605-790 进行 5 kg 额定负载预检查。
- 根据工件质量、夹爪质量、动态系数、摩擦系数与夹持安全系数，自动估算夹持力，并作为 FEA 总载荷。
- 预置 PLA、PLA+、PETG、ABS、ASA、尼龙 PA、PA-CF、PC、TPU 95A 的 FDM 快速筛选参数。
- 调用 CalculiX 求解，展示真实 von Mises 应力/总位移云图、最大值标记与中文 Markdown 报告。
- 支持中文亮色/深色主题，关键输入均可在界面右侧覆盖。

## 已验证的 P7 示例

在 DN25、球阀 4.05 kg、夹爪 0.60 kg、动态系数 1.5、摩擦系数 0.30、夹持安全系数 2.0 的自动工况下：

| 项目 | 结果 |
| --- | ---: |
| CR605 负载占用 | 93% |
| 自动 FEA 总载荷 | 397.169 N |
| 实体数 | 8 |
| 节点 / C3D4 单元 | 9,729 / 31,329 |
| 最大总位移 | 0.0250423 mm |
| 最大 von Mises 应力 | 2.9611 MPa |
| CalculiX 返回码 | 0 |

这些值来自真实的本地 Gmsh + CalculiX 计算，仅适合方案快速筛选，不是夹爪最终签核结论。

## 快速开始（Windows）

需要 Python 3.10+。仓库包含 CalculiX 运行时文件；Gmsh 和 PySide6 会通过 pip 安装。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[build]"
python run_gui.py
```

执行测试：

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```

构建单文件 EXE：

```powershell
.\packaging\build_exe.ps1
```

产物为 `dist\AI-FEA-Valve-Gripper.exe`。

## 自动工况与工程边界

球阀夹爪模式会阻止超过 CR605 额定负载的计算，并将 `max(建议总夹持力, 装配推力)` 自动传给 FEA。DN25 示例的负载余量约为 0.35 kg，必须把法兰、快换盘、传感器、气管和线缆等随动质量纳入夹爪质量。

当前 MVP 的自动边界条件是：每个实体自身最小 X 端全固定、每个实体最大 X 端节点平均加载。它不等价于真实的夹爪指面接触、摩擦、螺栓连接、装配碰撞或机器人惯性工况。正式工程使用前，请完成真实接触定义、材料/打印方向校核、网格收敛和实验验证。

## 许可证与第三方组件

本项目按 [GPL-2.0](LICENSE) 发布，以适配随仓库分发的 CalculiX 与 Gmsh 相关开源组件。第三方组件说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 贡献

欢迎提交可复现的模型、工况、网格收敛验证和 UI 改进。提交前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
