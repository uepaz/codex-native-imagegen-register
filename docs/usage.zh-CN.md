# 原生 image_gen 注册脚本

这是非官方实验性兼容配置工具；不是已验证的桌面端解锁。
不使用 MCP，不另做图片生成器，不登录真实账号，不生成假 OAuth Token。
供应商、模型、地址和 Key 沿用本地当前配置，不要求重新输入。

## Responses Lite 自检

只检查请求顶层 `tools` 可能漏判工具声明。官方 rust-v0.153.4 的
`build_responses_request()` 在模型启用 Responses Lite 时，会将工具定义放到
`input` 内 `type = additional_tools`、`role = developer` 的项中，顶层 tools 不提供。
因此，自检同时识别顶层工具声明和 `input` 中的 additional_tools 工具声明。

诊断输出记录工具声明的位置和请求结构摘要，帮助区分失败阶段。
实际检查通过之前不会提交用户配置。

## 使用

需要 Windows 本机已有 Python 3.11 或以上版本；无需额外依赖包。
完全退出 ChatGPT/Codex（包括托盘实例）、CodexPlusPlus 和终端中的 Codex。
解压到新目录，双击 `install.cmd`，或者运行单文件：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\codex-native-imagegen-register.ps1 -Action Install -VersionTimeoutSeconds 60
```

普通用户身份运行，不需管理员权限。只有当前安装包与 App 运行时缓存
SHA-256 相同时，才使用对应缓存后端。
`files_modified: false` 表示本次没有提交配置；已有改动不会因此自动撤销。

`check.cmd`：只运行启动和原生工具的本机检查，不提交 config.toml/auth.json。
`status.cmd`：读安装状态。Status 不输出启动横幅，正常结果可直接作 JSON 解析。
`restore.cmd`：在退出 App 后恢复本系列安装前的文件；发现安装后的配置改动会停止。
单文件对应参数为 `-Action Check` / `Status` / `Restore`。

## 自检与验证边界

同时识别：

- 普通 Responses：`tools` 中的 `image_gen` namespace 及其 `imagegen` function。
- Responses Lite：`input` 的 developer additional_tools 项内，同一个 namespace/function。

不会把用户文字、提示词里提到的 image_gen、MCP 名称、任意嵌套 JSON 当作工具已注册。
只有后端真实发出的工具声明中存在该工具，模拟服务才发出相应 function_call；
随后必须看到原生 Images 请求，以及匹配 call_id 的 function_call_output 中的测试图片。
图片回传检查会验证工具结果类型和图片内容，不只匹配请求里的 base64 子串。
没有跳过条件，也没有在模拟请求中凭空塞入一个不存在的工具后假报成功。

测试只使用临时配置、临时工作目录、本机模拟 Responses/Images 接口和合成 PNG。
不读取用户会话，不将真实 Key 传给检查子进程，不向真实中转发出生图请求。
测试图片不是模型生成的图片，不会当作你的生成结果交付。
检查后模拟服务退出、临时目录清理，不安装常驻服务。

## 如何看输出

状态包括：

- `NOT_APPLIED_BACKEND_START_FAILED`：启动或来源校验失败，未提交配置。
- `NOT_APPLIED_NATIVE_PROBE_FAILED`：未通过原生工具往返检查，未提交配置。
- `INSTALLED_NATIVE_PROBE_PASSED`：原生后端本机检查通过，已备份并写入配置。
- `CHECK_PASSED`：只检查通过，没有安装。

`local_native_probe.diagnostics` 只输出计数、固定枚举和布尔值：

| 字段 | 用途 |
| --- | --- |
| `responses_request_count` | 实际截获多少个本机 Responses 请求 |
| `native_schema_locations` | 在 `tools` 还是 `input.additional_tools.tools` 找到原生工具 |
| `wire_formats_seen` | 观察到的普通或 Lite 工具容器，不推断真实中转支持情况 |
| `request_shapes` | 每个请求工具容器的数量和位置，不含模型、输入正文、描述或密钥 |
| `native_tool_call_issued` | 模拟服务是否已基于后端声明发出原生工具调用 |
| `native_tool_output_seen` | 是否看到相同 call_id 的工具结果 |
| `tool_output_error_class` | 必要时只给预定义错误分类，不输出原文 |
| `stage` | 以下失败阶段或本机往返通过 |

`stage` 可能为：

- `NO_RESPONSES_REQUEST_CAPTURED`：没有截获模型请求，不能认定工具未注册。
- `NATIVE_SCHEMA_NOT_ADVERTISED`：有请求，但在支持的两种工具容器中都没找到原生工具。
- `NATIVE_TOOL_CALL_DID_NOT_REACH_IMAGES`：工具声明已看到，但调用未到达模拟图片接口。
- `IMAGE_RESULT_NOT_RETURNED_TO_MODEL`：模拟图片接口已调用，但没有看到匹配的图片工具结果。
- `LOCAL_NATIVE_ROUNDTRIP_PASSED`：本机完整往返通过，仍不代表真实中转或桌面 UI 测试通过。
- `PROBE_ERROR` / `EXEC_EXIT_NOT_ZERO`：检查发生错误或后端非零退出。

若仍失败，可以分享 `local_native_probe` 整段。不要分享 config.toml、auth.json、
备份文件或真实 API 日志。安装成功后的其他字段可能包含本地备份路径。

## 兼容配置与风险

只对当前供应商增加/设置注册兼容项：requires_openai_auth=false，
features.image_generation=true，features.code_mode.direct_only_tool_namespaces 加入 image_gen，
以及非凭据的 x-openai-actor-authorization 固定标记。
不更换模型 ID，不编造模型图像能力，不修改应用二进制，不关闭用户的审批或沙箱。

该 actor 标记不是 OpenAI 凭据，也不提供账号、订阅或云端权限。
它可能出现在普通聊天及图片请求中；中转需要忽略/去除，否则中转或上游可能拒绝请求。
这不是官方支持承诺，不能保证任意 App/中转兼容。

按原规则复用环境变量或显式 bearer；Key 仅位于 API Key 模式的 auth.json 时，
会复制到当前 provider 配置中。配置和备份可能含明文 Key，不要分享。
只处理之前助手脚本的已知占位认证，未知或真实账号令牌不擅自覆盖。
受管登录限制、密钥库状态、配置歧义会停止，而不是绕过。
TOML 的值保留，但排版/注释会重写，原始字节先备份。

本机检查使用相同模型 ID，但并非真实桌面会话；项目配置、App 覆盖参数、
缓存模型信息和服务端协议均可能改变实际结果。
安装通过后仍需重开 App、新建会话，检查原生工具调用、真实图片和中转请求。
不会将本机检查通过说成 OAuth 成功或实际中转生图已完成。

## 源码与构建

源码和构建器均在包内。在项目根目录执行 `python scripts/build_bundle.py` 生成发布文件。

## 核对的固定版本源码

- 模型请求将工具放入 AdditionalTools 的分支：
  https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/core/src/client.rs#L882-L930
- AdditionalTools 的 JSON 类型与 role/tools 字段：
  https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/protocol/src/models.rs#L908-L918
- Lite 工具序列化（保留 image_gen namespace）：
  https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/tools/src/tool_spec.rs#L89-L134
- 原生图片工具及其工具结果：
  https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/ext/image-generation/src/tool.rs#L549-L627
