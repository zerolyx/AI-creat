# AI-creat

工业机器人装配比赛相关的工程代码与工具集合。

这是一个 **monorepo**：根目录承载主项目（AI-FEA Valve Gripper），同时收纳配套的机器人竞赛协作技能与独立的工业语音控制示例。每个子项目都有自己独立的职责、文档与许可证。

> 主项目 | [AI-FEA Valve Gripper](#ai-fea-valve-gripper) · [robot-competition-copilot](robot-competition-copilot/SKILL.md) · [Industrial Voice Control](industrial-voice-control/README.md)

---

## 仓库结构

```
AI-creat/
├── src/ai_fea_mvp/            # 主项目：AI-FEA Valve Gripper 源码（Gmsh + CalculiX 本地求解）
├── tests/                     # 主项目自动化测试
├── runtime/ccx/               # CalculiX 求解器运行时（不入库，见 scripts/fetch-ccx.ps1）
├── packaging/                 # 单文件 EXE 打包脚本（PyInstaller）
├── assets/                    # 主项目 GUI 运行所需资源（字体等）
├── docs/                      # 主项目文档与界面截图
├── scripts/                   # 工程辅助脚本
│   └── fetch-ccx.ps1          #   下载 CalculiX 求解器运行时
├── robot-competition-copilot/ # 机器人竞赛协作技能（SKILL.md）
├── industrial-voice-control/  # 独立子项目：工业机器人语音控制安全开源示例（MIT）
├── .github/workflows/ci.yml   # CI：主项目自动化测试
└── pyproject.toml             # 主项目打包元数据
```

---

## AI-FEA Valve Gripper

面向 Q41F-16P / 16RL 法兰球阀自动装配夹爪的 Windows 有限元分析助手。软件使用本地 Gmsh 与 CalculiX 完成真实网格和线性静力求解，不依赖云端求解服务。

![球阀夹爪分析界面](docs/gui_valve_gripper_final.png)

### 能做什么

- 导入 STEP / STP，多实体装配体自动识别并生成四面体网格。
- 针对华数 HSR-CR605-790 进行 5 kg 额定负载预检查。
- 根据工件质量、夹爪质量、动态系数、摩擦系数与夹持安全系数，自动估算夹持力，并作为 FEA 总载荷。
- 预置 PLA、PLA+、PETG、ABS、ASA、尼龙 PA、PA-CF、PC、TPU 95A 的 FDM 快速筛选参数。
- 调用 CalculiX 求解，展示真实 von Mises 应力/总位移云图、最大值标记与中文 Markdown 报告。
- 支持中文亮色/深色主题，关键输入均可在界面右侧覆盖。

### 快速开始（Windows）

需要 Python 3.10+。CalculiX 求解器运行时不随仓库分发，首次使用先执行：

```powershell
# 1) 准备运行时（下载 CalculiX 求解器到 runtime/ccx/）
.\scripts\fetch-ccx.ps1

# 2) 安装 Python 依赖
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[build]"

# 3) 启动 GUI
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

### 已验证的 P7 示例

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

### 自动工况与工程边界

球阀夹爪模式会阻止超过 CR605 额定负载的计算，并将 `max(建议总夹持力, 装配推力)` 自动传给 FEA。DN25 示例的负载余量约为 0.35 kg，必须把法兰、快换盘、传感器、气管和线缆等随动质量纳入夹爪质量。

当前 MVP 的自动边界条件是：每个实体自身最小 X 端全固定、每个实体最大 X 端节点平均加载。它不等价于真实的夹爪指面接触、摩擦、螺栓连接、装配碰撞或机器人惯性工况。正式工程使用前，请完成真实接触定义、材料/打印方向校核、网格收敛和实验验证。详见 [docs/mvp_validation.md](docs/mvp_validation.md)。

---

## robot-competition-copilot

面向工业机器人竞赛（阀门分拣与装配）的协作型技能说明，指导 CR605/华数机器人 PRG 程序、夹爪迭代、视觉、PLC/TIA 信号、IO、点位、仿真与现场调试验证。详见 [SKILL.md](robot-competition-copilot/SKILL.md)。

---

## Industrial Voice Control

面向工业机器人 Modbus TCP 集成的本地语音控制参考实现。公开版本默认不连接、不探测、不控制任何现场机器人；地址、端口和寄存器只能放在被忽略的私有配置文件中。

完整安装、安全边界、测试与发布说明见 [industrial-voice-control/README.md](industrial-voice-control/README.md)。该子项目按 [MIT](industrial-voice-control/LICENSE) 发布，第三方字体和图标许可证见[其第三方说明](industrial-voice-control/THIRD_PARTY_NOTICES.md)。

---

## 开发与贡献

- **提交规范**：使用 Conventional Commits（`feat:` / `fix:` / `docs:` / `refactor:` / `chore:` / `test:`），详见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- **CI**：`.github/workflows/ci.yml` 在 push / PR 时自动运行主项目测试（headless 模式）。

## 许可证矩阵

| 路径 | 许可证 |
| --- | --- |
| 根（主项目 AI-FEA Valve Gripper 及其随附 CalculiX/Gmsh 组件） | [GPL-2.0](LICENSE) |
| `industrial-voice-control/`（独立子项目） | [MIT](industrial-voice-control/LICENSE) |
| 第三方组件 | [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) |

> 使用、修改或分发前请阅读对应许可证全文。
