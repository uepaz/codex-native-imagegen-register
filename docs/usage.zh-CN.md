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
完全退出 ChatGPT/Codex（包括托盘实例）、CodexPlusPlus、CC Switch 和终端中的 Codex。
解压到新目录，双击 `install.cmd`，或者运行单文件：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\codex-native-imagegen-register.ps1 -Action Install -VersionTimeoutSeconds 60
```

普通用户身份运行，不需管理员权限。只有当前安装包与 App 运行时缓存
SHA-256 相同时，才使用对应缓存后端。
`files_modified: false` 表示本次没有提交配置；已有改动不会因此自动撤销。

`check.cmd`：只运行启动和原生工具的本机检查，不提交 config.toml/auth.json。
`status.cmd`：读安装状态。Status 不输出启动横幅，正常结果可直接作 JSON 解析。
`restore.cmd`：在退出 App 后恢复到最近一次实际写入前的状态，只处理该次安装修改过的文件。
单文件对应参数为 `-Action Check` / `Status` / `Restore`。

## 重复安装与恢复

每次安装都重新读取当前 `config.toml` 和 `auth.json`，不要求它们与旧安装快照相同。
已更换供应商、模型、地址、Key 或更新登录状态时，直接重新执行安装即可。

安装只补齐当前供应商及当前生效配置层中的生图兼容设置：

- `requires_openai_auth = false`。
- 统一兼容 actor header，清理该 header 的大小写重复项及环境变量覆盖；其他 headers 保留。
- `features.image_generation = true`。
- `features.code_mode.direct_only_tool_namespaces` 包含且仅包含一个 `image_gen`，保留其他 namespace 及其顺序。

供应商、模型、`base_url`、现有 Key、审批与沙箱设置及其他配置值保持不变。
已有 `env_key` 或显式 bearer 时保留原有凭据配置。若 Key 仅存在于 API Key 模式的
`auth.json`，会把同一个 Key 补到当前供应商的 `experimental_bearer_token`，
确保 `requires_openai_auth=false` 后仍有可用的认证入口；不会更换 Key 的值。
安装不删除、不重写 `auth.json`，也不清理其中的旧登录占位数据。

自检通过后，需要写入时会新建备份，保存本次执行前的原始文件；之前的备份目录继续保留。
无需修改时不重写配置文件，也不替换已有恢复点。首次执行即无需修改时，会记录当前状态供查询与恢复。
本工具此前生成的带引号表头会进行一次兼容性重写并备份，之后再次运行保持字节不变。
`files_modified` 表示本次是否实际改动配置；Status 中的快照匹配结果仅供诊断，不再作为安装前提。

恢复操作撤销最近一次实际写入，保留那次安装前已有的模型、Key 和其他设置。
未被该次安装修改的文件不会恢复，因此后续更新的 `auth.json` 不会被改回旧内容。
若 `config.toml` 在最近一次写入后又被编辑，恢复仍会停止；明确要覆盖时可用
`-Action Restore -ForceRestore`，脚本会先另存当前待恢复文件。历史安装记录仍可读取，
若历史安装曾修改过认证文件，则按对应记录恢复。

安装自检期间或写入期间发生新的文件修改时，仍会停止以避免覆盖并发编辑。
未完成的安装事务、损坏的备份记录或受管限制也需要先处理；这与旧快照不一致是不同情况。

## CC Switch 兼容与已有重复表

普通字段与表头使用 `model_provider`、`base_url` 和 `[model_providers.custom]`
等常规写法，只有 TOML 语法要求时才给键名加引号。这样 CC Switch 按文本编辑供应商时，
能定位原表和字段，不会因本工具的过度引号格式漏判并追加同名表。

若文件已经包含重复表，脚本会明确报错且不写入。请在故障电脑上先备份文件，
将重复供应商段的字段合并到同一个表中，并核对冲突值；不要仅删除表头，否则字段可能归入错误的表。
TOML 解析恢复正常后，再执行安装。此修复不自动操作 CC Switch 的数据库或供应商列表。

## 自检与验证边界

生成安装计划、启动自检和提交配置前，都会检查实际待写入配置中的凭据入口。
自检沿用该配置的 `env_key` 或显式 bearer 认证方式，仅将密钥替换为本机模拟 Key；
不会为缺失凭据的配置无条件添加 bearer 再判断成功。诊断字段
`provider_authentication_route` 记录所验证的方式，不包含真实 Key 或环境变量名称。

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

保留现有环境变量或显式 bearer；Key 仅位于 API Key 模式的 auth.json 时，
将原值补到当前 provider，原 auth.json 仍保持不变。
配置和备份可能含明文 Key，不要分享。
已有供应商凭据时，不依赖、不改写其他登录状态。需要从认证文件读取 Key 时，
受管登录限制、密钥库状态或凭据来源歧义仍会停止，而不是猜选或绕过。
需要补齐字段时保留所有 TOML 配置值，但排版/注释会重写，原始字节先备份；
无需修改时保持文件原始字节。

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
