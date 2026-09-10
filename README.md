<div align="center">

# Codex Native ImageGen Register

**原生 image_gen 工具注册 · Windows 兼容配置助手**

沿用现有供应商、模型与 API Key，支持 Responses / Responses Lite 本机自检。

<p><code>Windows</code> · <code>Python 3.11+</code> · <code>无第三方依赖</code> · <code>实验性工具</code></p>

[快速开始](#快速开始) · [操作一览](#操作一览) · [报毒与误报说明](#报毒与误报说明) · [完整文档](docs/usage.zh-CN.md)

</div>

---

> [!IMPORTANT]
> 本项目是非官方实验性兼容工具。本机自检通过，表示本机模拟流程通过；真实中转生图和桌面 UI 仍需实际验证。配置影响与兼容限制见 [中文使用说明](docs/usage.zh-CN.md)。

## 功能概览

| 功能 | 说明 |
| :--- | :--- |
| 基于当前配置补齐 | 每次读取现有配置，保留供应商、模型、地址、API Key 和其他设置 |
| 安装前自检 | 检查后端启动、工具声明、图片接口调用与结果回传 |
| 配置备份与恢复 | 有改动时新建备份，旧备份保留；无需改动时复用恢复点 |
| 源码随包提供 | 使用 Python 标准库，附带构建脚本与 SHA-256 校验清单 |

## 快速开始

**准备环境：** Windows、Python 3.11+，以及本机已安装的 Codex / ChatGPT 桌面端。使用普通用户身份运行即可。

1. **解压文件** — 将 `codex-native-imagegen-register.zip` 解压到独立目录。
2. **退出应用** — 完全退出 ChatGPT/Codex（包括托盘实例）、CodexPlusPlus、CC Switch 和终端中的 Codex。
3. **检查并安装** — 双击 `install.cmd`，脚本会先运行本机自检，通过后才写入配置。只想检查时，使用 `check.cmd`。
4. **重新打开应用** — 新建会话，验证原生工具调用与实际图片结果。

修改模型、供应商配置或登录状态后，可以再次运行 `install.cmd`。脚本以当前文件为准补齐生图配置，不因文件与旧快照不同而拒绝安装。重复运行不会重复添加 `image_gen`；无需改动时不重写配置文件。安装保留 `auth.json` 的原始内容。

已有 `env_key` 或显式 bearer 时继续使用原配置；Key 仅在 `auth.json` 时，会将同一个 Key 补到当前 provider，避免 `requires_openai_auth=false` 后请求丢失认证。配置和备份可能含明文 Key，请勿公开分享。

脚本输出兼容 CC Switch 的常规表头，并会修正本工具此前生成的带引号表头。若文件已出现重复的 `[model_providers.custom]`，须先合并并核对重复段中的字段，再运行脚本；不会自动猜选冲突值。

> [!NOTE]
> 首次下载或运行时，如果安全软件出现报毒、隔离或拦截提示，请先查看下方的 [报毒与误报说明](#报毒与误报说明)。

## 操作一览

| 文件 | 用途 |
| :--- | :--- |
| `install.cmd` | 运行自检，通过后备份并安装配置 |
| `check.cmd` | 只运行本机检查，不提交配置 |
| `status.cmd` | 查看当前安装状态 |
| `restore.cmd` | 撤销最近一次实际写入，恢复到该次安装前；保留未修改的文件 |

恢复时仍会保护安装后新增的配置改动。若需要修复生图字段，可直接重新运行安装；完整备份与恢复规则见 [使用说明](docs/usage.zh-CN.md#重复安装与恢复)。

<details>
<summary><strong>命令行运行方式</strong></summary>

在解压后的发布目录中打开 PowerShell，执行：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\codex-native-imagegen-register.ps1 -Action Check
```

将 `Check` 替换为 `Install`、`Status` 或 `Restore`，即可执行对应操作。

</details>

## 报毒与误报说明

**安全软件可能出现误报，但“报毒”不能直接视为正常或安全。** 当前没有针对具体检测名称的厂商误报确认，需结合文件来源和检测结果判断。

本工具使用 PowerShell 启动器，将内嵌的压缩 Python 源码解压到临时目录执行，并在自检通过后修改本地配置。这些行为可从 [启动模板](scripts/templates/launcher-template.ps1) 和 [Python 源码](src/native_imagegen_register.py) 中核对；它们是否触发了某次告警，需要具体分析。

遇到告警时，建议按以下步骤处理：

1. **核对来源与文件**：确认下载来源，核对可信发布方提供的 SHA-256。校验一致仅能确认文件一致，不能单独证明安全。
2. **保留检测信息**：记录安全软件名称、检测名称及被拦截的文件路径；不要直接关闭安全防护或添加整个目录的排除项。
3. **申请误报复核**：如果认为是误报，可按 [Microsoft 官方说明](https://support.microsoft.com/en-US/defender/troubleshoot-problems-with-detecting-and-removing-malware) 提交文件分析，或联系所用安全软件的厂商。提交前确认文件不含个人配置、密钥或备份。

> [!CAUTION]
> `config.toml`、`auth.json`、安装备份和运行日志可能包含敏感信息，请勿上传到仓库、公开 Issue 或文件分析平台。

## 从源码构建

在项目根目录运行：

```powershell
python scripts/build_bundle.py
```

如果本机使用 Python Launcher，可改为 `py -3 scripts/build_bundle.py`。

| 输出路径 | 内容 |
| :--- | :--- |
| `dist/codex-native-imagegen-register.ps1` | 单文件启动器 |
| `dist/codex-native-imagegen-register.zip` | 包含源码、文档与启动脚本的发布包 |
| `dist/SHA256SUMS.txt` | 两个发布文件的 SHA-256 |
| `dist/codex-native-imagegen-register/` | 展开的发布目录，可直接使用 |

构建使用显式文件清单，不会自动收集工作目录中的其他文件。

<details>
<summary><strong>查看项目结构</strong></summary>

```text
.
├── docs/
│   └── usage.zh-CN.md             # 完整中文使用说明
├── scripts/
│   ├── build_bundle.py            # 构建与打包
│   └── templates/launcher-template.ps1
├── src/native_imagegen_register.py
├── CONTRIBUTING.md               # 开发与发布说明
└── dist/                         # 本地构建产物，不提交 Git
```

</details>

---

进一步了解：[使用原理与诊断说明](docs/usage.zh-CN.md) · [开发与发布](CONTRIBUTING.md) · [MIT License](LICENSE)
