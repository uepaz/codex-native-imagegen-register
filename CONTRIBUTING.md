# 开发与发布

## 本地构建

使用 Python 3.11+，无需安装依赖。在仓库根目录运行：

```powershell
python scripts/build_bundle.py
```

修改业务逻辑时编辑 `src/native_imagegen_register.py`，修改 Windows 启动流程时编辑 `scripts/templates/launcher-template.ps1`。不要直接修改 `dist/` 中的生成文件；修改后重新构建。

新增需要随发布包交付的文件时，更新 `scripts/build_bundle.py` 中的 `RELEASE_FILES`。构建只将明确列出的文件写入 ZIP；不要使用整个工作目录打包。

## GitHub 提交

提交前检查：

```powershell
git status --short
git diff --check
```

确认暂存清单只包含源码、模板、文档和仓库配置。`dist/`、本地凭据、安装备份和运行日志已由 `.gitignore` 排除。

仓库创建后，根据自己的 GitHub 仓库地址配置 `origin`，再提交并推送 `main`。此项目未预设远程地址。

## 发布

1. 执行构建，检查 `dist/codex-native-imagegen-register/` 内的说明、脚本和校验清单。
2. 将 `dist/` 顶层的 `.ps1`、`.zip` 和 `SHA256SUMS.txt` 上传为 GitHub Release 附件。
3. 发布说明写明实际验证范围，区分本机自检、桌面 UI 验证和真实中转验证。

本项目采用 [MIT License](LICENSE)。发布包包含许可证，分发时请保留版权声明和许可文本。
