# 工业机器人语音控制示例

面向工业机器人 Modbus TCP 集成的本地语音控制示例。系统在 Windows 11 本机完成语音识别、指令理解、中文语音反馈和工程师诊断；公开版本默认不连接或控制任何现场设备。

当前发布版本：v2.6.0-open-source

| 发布状态 | 默认行为 | 许可证 |
| --- | --- | --- |
| 安全开源示例 | 控制禁用，不探测现场设备 | [MIT](LICENSE) |

## 核心能力

- 手动按住式语音交互，避免环境噪声持续触发。
- Whisper `small` 中文语音识别，带识别置信度和环境信噪比安全门。
- Qwen `qwen2.5:7b` 本地意图理解，控制数据不依赖云端服务。
- 针对本地私有配置寄存器的示例动作白名单，语音和鼠标点击共用同一安全下发路径。
- 单次 Modbus 写入确认后立即释放控制台状态，不等待机器人动作完成回传。
- 写入超时不自动重发，避免同一动作被重复执行。
- 示例工艺顺序仅作前端参考，使用者可替换为自己的私有工艺定义。
- 中文语音反馈、麦克风/扬声器状态、实时安全门和会话记录。
- 工程师诊断中心：日志会话、筛选、搜索、暂停、刷新、问题定位和设备状态。
- 浅色、深色和跟随 Windows 系统主题三种模式。
- 只读部署检查、锁定依赖和自动化测试。

## 运行环境

以 [语音模型安装步骤.docx](语音模型安装步骤.docx) 为部署基线：

- Windows 11 x64
- Python 3.11.9 x64
- Microsoft Visual C++ 2015-2022 x64
- .NET Framework 4.8+
- Ollama 与 `qwen2.5:7b`
- 麦克风、中文语音输出设备和机器人所在局域网

Python 依赖版本见 `requirements-lock.txt`。

## 快速开始

1. 首次部署：双击 `一键部署并启动.bat`。
2. 复制 `robot_config.example.env` 为 `robot_config.local.env`，保持 `ROBOT_CONTROL_ENABLED=false`。
3. 日常运行：双击 `启动语音助手.bat`。
4. 运行环境检查：双击 `运行环境检查.bat`，或执行 `deployment_check.py`。
5. 浏览器访问本地操作台，进入工程师诊断中心可查看诊断日志。

## 机器人安全说明

本项目直接连接工业机器人，发布和使用前必须完成以下事项：

1. 复制 `robot_config.example.env` 为 `robot_config.local.env`，仅在私有现场填写机器人地址、端口、寄存器和启用标志。
2. `ROBOT_CONTROL_ENABLED` 默认是 `0`；只有完成私有安全审查后才可改为 `1`。
3. 在低速、空载、危险区无人条件下，逐条验证所有动作。
4. 验证急停、安全门、碰撞检测、软限位、占用互锁和 PLC/控制器侧工艺互锁。
5. 通信异常时保持锁止；不得移除单次写入确认、白名单、低置信度拦截或超时不重发逻辑。

真实动作映射必须保留在受限的私有部署分支或经审批的控制器项目中，不能写入公开仓库或 `robot_config.local.env`。

工程师诊断中心只能管理诊断视图，不提供机器人运动控制。每次打开会建立新的日志会话；历史 `voice_assistant.log` 保留用于故障追溯。

## 项目结构

```text
1.py                         语音、识别、意图和 Modbus 控制核心
web_app.py                   本地 Web 服务和工程师诊断 API
web/                         操作台、诊断中心、主题和本地静态资源
scripts/                     部署与中文语音合成脚本
*.bat                        一键部署、启动和环境检查入口
deployment_check.py          只读部署检查
test_*.py                    自动化回归测试
robot_config.example.env     安全示例配置模板
开源版-v2.6.0-安全发布说明.md 当前版本安全发布说明
```

## 许可证与发布说明

- 本发行包不包含运行日志、部署报告、安装过程日志、缓存或历史界面备份。
- 运行日志、部署报告和本地临时文件已写入 `.gitignore`，请勿提交现场状态信息。
- 机器人 IP、端口、寄存器和动作映射属于现场配置；公开版不包含实际值。
- 本子项目的自有代码按 [MIT](LICENSE) 发布。
- JetBrains Mono 和 Lucide 等第三方资源不转换为 MIT，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 `web/assets/licenses/`。
- 本版本的变更和发布验证见 [RELEASE_NOTES.md](RELEASE_NOTES.md)。

## 验证

在 Python 3.11.9 环境中执行：

```powershell
python -m unittest -v test_voice_assistant.py test_web_app.py
python deployment_check.py
```

v2.6.0-open-source 已覆盖语音安全门、默认禁用控制、单次写入释放、超时不重发、鼠标指令白名单、工程师日志会话和本机 API 安全边界。
