#!/usr/bin/env python3
"""Experimental native image_gen registration adapter; Python 3.11+, no third-party packages.

Uses the existing provider's API key, NOT a fabricated ChatGPT account.
A noncredential x-openai-actor-authorization marker opts into the current native
registration predicate. This is an undocumented compatibility workaround, not
valid OpenAI actor authentication. The marker is sent to the configured relay.
The relay must ignore/strip it; upstream image API compatibility is not assumed.

Before changing user files, run the ACTUAL app-bundled backend against a temporary
loopback-only mock Responses/Images server. A successful mock is NOT a real image
model request, a real OAuth login, or proof that the desktop UI accepts the change.
"""
from __future__ import annotations
import argparse, base64, copy, datetime as dt, gzip, hashlib, http.server, json, math, os
from pathlib import Path
import queue, re, socket, stat, struct, subprocess, sys, tempfile, threading, time, tomllib, uuid, zlib
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# Persistent identifiers must remain stable so existing backups can be restored.
STATE_DIR = ".native-imagegen-registration-v2"
PRODUCT = "codex-native-imagegen-register-v2"
FILES = ("config.toml", "auth.json")
ACTOR_HEADER = "x-openai-actor-authorization"
ACTOR_MARKER = "local-relay-imagegen-opt-in-not-an-openai-credential"
PROBE_KEY = "local-mock-only-not-a-real-api-key"
_SID: str | None = None

class SetupError(Exception):
    """Safe-to-display error; do not include credential/config values."""

def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)

def toml_value(value: Any) -> str:
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return repr(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(x) for x in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(toml_string(k) + " = " + toml_value(v)
                                  for k, v in value.items()) + " }"
    raise SetupError("配置包含本工具无法无损保留的数据类型；未写入配置。")

def equivalent(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, float) and math.isnan(a) and math.isnan(b):
        return True
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equivalent(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(equivalent(x, y) for x, y in zip(a, b))
    return a == b

def parse_config(data: bytes | None) -> dict[str, Any]:
    if data is None:
        return {}
    if len(data) > 8 * 1024 * 1024:
        raise SetupError("config.toml 超过 8 MiB，请先检查配置；未写入。")
    try:
        return tomllib.loads(data.decode("utf-8-sig"))
    except (UnicodeError, tomllib.TOMLDecodeError):
        raise SetupError("原 config.toml 不是有效的 UTF-8 TOML；未写入配置。") from None

def assert_regular(path: Path) -> None:
    if path.is_symlink():
        raise SetupError(f"拒绝修改符号链接：{path.name}")
    if path.exists():
        st = path.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            raise SetupError(f"拒绝修改非常规文件或硬链接：{path.name}")
        if getattr(st, "st_file_attributes", 0) & 0x400:
            raise SetupError(f"拒绝修改重解析点：{path.name}")

def read_optional(path: Path) -> bytes | None:
    assert_regular(path)
    return path.read_bytes() if path.exists() else None

def digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None

def secure_path(path: Path, is_dir: bool = False) -> None:
    """Protect new artifacts before writing secrets; never change CODEX_HOME ACLs."""
    global _SID
    if os.name != "nt":
        path.chmod(0o700 if is_dir else 0o600)
        return
    if _SID is None:
        r = subprocess.run(["whoami.exe", "/user", "/fo", "csv", "/nh"],
                           capture_output=True, check=False)
        match = re.search(rb"S-1-(?:\d+-)+\d+", r.stdout)
        if r.returncode or match is None:
            raise SetupError("无法确定当前 Windows 用户 SID；未写入凭据。")
        _SID = match.group().decode("ascii")
    rights = "(OI)(CI)F" if is_dir else "F"
    r = subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r",
                        f"*{_SID}:{rights}", f"*S-1-5-18:{rights}"],
                       capture_output=True, check=False)
    if r.returncode:
        raise SetupError("无法限制新文件/备份目录的 Windows 权限；已停止。")

def secure_dir(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise SetupError("备份目录路径不是普通目录；已停止。")
    if path.exists() and getattr(path.stat(), "st_file_attributes", 0) & 0x400:
        raise SetupError("备份目录是重解析点；已停止。")
    path.mkdir(parents=False, exist_ok=True)
    secure_path(path, True)

def atomic_write(path: Path, data: bytes, staging: Path) -> None:
    assert_regular(path)
    fd, name = tempfile.mkstemp(prefix=".stage-", dir=staging)
    temp = Path(name)
    try:
        secure_path(temp)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if fd != -1:
            os.close(fd)
        temp.unlink(missing_ok=True)

def write_json(path: Path, obj: Any, staging: Path) -> None:
    atomic_write(path, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode(), staging)

def normalize_base_url(raw: str) -> str:
    raw = raw.strip()
    if not raw or any(c.isspace() or ord(c) < 32 for c in raw) or "\\" in raw:
        raise SetupError("现有 API 基础地址无效，不要包含空白、反斜杠或控制字符。")
    try:
        p = urlsplit(raw)
        host = (p.hostname or "").lower().rstrip(".")
        _ = p.port
    except ValueError:
        raise SetupError("API 基础地址格式错误。") from None
    if not host or p.username is not None or p.password is not None or p.query or p.fragment:
        raise SetupError("API 地址不能含账户、密码、查询参数或 # 片段。")
    if any(host == d or host.endswith("." + d) for d in ("openai.com", "chatgpt.com", "chat.openai.com")):
        raise SetupError("本工具只适配你有权使用的中转站，不将本地兼容标记用于官方地址。")
    if p.scheme != "https" and not (p.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}):
        raise SetupError("公网 API 地址必须使用 HTTPS；HTTP 仅允许本机回环地址。")
    path = p.path.rstrip("/")
    if any(path.endswith(s) for s in ("/responses", "/chat/completions", "/images/generations", "/images/edits")):
        raise SetupError("现有 base_url 是具体接口而非 API 基础地址；未写入。")
    return urlunsplit((p.scheme, p.netloc, path or "/v1", "", ""))

def check_conflicts(config: dict[str, Any]) -> None:
    for name in ("CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
        if os.environ.get(name):
            raise SetupError(f"检测到 {name} 环境变量，可能覆盖本地认证。请自行处理后重试；本工具不会删除它。")
    keys = ("forced_login_method", "forced_chatgpt_workspace_id", "allowed_login_methods")
    if any(config.get(k) is not None for k in keys):
        raise SetupError("配置中存在强制登录/工作区限制。本工具不修改这些限制，未写入配置。")
    if os.name == "nt":
        import winreg
        for root, sub in ((winreg.HKEY_CURRENT_USER, "Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, sub) as key:
                    for name in ("CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
                        try:
                            value, _kind = winreg.QueryValueEx(key, name)
                        except FileNotFoundError:
                            continue
                        if value:
                            raise SetupError(f"检测到持久化 {name} 环境变量。请先处理冲突；未写入配置。")
            except FileNotFoundError:
                pass

def selected_profile(config: dict[str, Any]) -> str | None:
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        raise SetupError("profiles 必须为 TOML 表；未写入。")
    profile = config.get("profile")
    if profile is None:
        return None
    if not isinstance(profile, str) or not profile or not isinstance(profiles.get(profile), dict):
        raise SetupError("当前 profile 无法从 config.toml 内解析；本版不会猜选配置或要求输入。")
    return profile

def local_environment(name: str) -> tuple[str | None, str]:
    """Read only the explicitly named variable, never enumerate user secrets."""
    if name in os.environ:
        return os.environ[name], "process"
    if os.name == "nt":
        import winreg
        for root, path, label in (
            (winreg.HKEY_CURRENT_USER, "Environment", "user"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "machine"),
        ):
            try:
                with winreg.OpenKey(root, path) as key:
                    value, kind = winreg.QueryValueEx(key, name)
                if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
                    raise SetupError("指定环境变量不是字符串；未写入。")
                if kind == winreg.REG_EXPAND_SZ:
                    value = winreg.ExpandEnvironmentStrings(value)
                return value, label
            except FileNotFoundError:
                continue
            except PermissionError:
                raise SetupError("无权读取指定环境变量；不会尝试提权或要求输入。") from None
    return None, "missing"

def validate_secret(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 65536:
        raise SetupError("现有凭据为空或类型/长度异常；未写入，也不会要求输入 Key。")
    if value != value.strip() or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise SetupError("现有凭据含空白、控制字符或非 ASCII 字符；未写入。")
    if value.lower().startswith("bearer ") or "LOCAL-PLACEHOLDER" in value:
        raise SetupError("现有凭据不是可直接使用的中转站 Key；未写入。")
    # Do not accidentally move one of this tool's placeholders into real API auth.
    try:
        payload = value.split(".")[1]
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if isinstance(decoded, dict) and decoded.get("local_relay_placeholder") is True:
            raise SetupError("不能把模拟登录占位令牌当作中转站 Key；未写入。")
    except (IndexError, ValueError, UnicodeError):
        pass
    return value

def parse_auth(data: bytes | None) -> dict[str, Any]:
    if data is None:
        return {}
    if len(data) > 8 * 1024 * 1024:
        raise SetupError("auth.json 超过 8 MiB；未写入。")
    try:
        result = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        raise SetupError("原 auth.json 不是有效的 UTF-8 JSON；未写入。") from None
    if not isinstance(result, dict):
        raise SetupError("原 auth.json 不是 JSON 对象；未写入。")
    return result

def auth_file_key(auth_data: bytes | None) -> str:
    auth = parse_auth(auth_data)
    candidates = [auth[name] for name in ("OPENAI_API_KEY", "openai_api_key")
                  if auth.get(name) is not None]
    if not candidates:
        raise SetupError("未找到当前供应商的可用 Key：未配置 env_key/显式 bearer，auth.json 也没有 API Key。密钥库或其他应用私有存储不会被扫描；未写入。")
    if any(x != candidates[0] for x in candidates):
        raise SetupError("auth.json 内的 API Key 字段冲突；未写入，不猜选凭据。")
    mode = auth.get("auth_mode")
    if mode is not None and mode not in ("apikey", "api_key"):
        raise SetupError("auth.json 当前不是 API Key 登录模式；不能确认其中的 Key 对应当前供应商，未写入。")
    if auth.get("tokens") is not None:
        raise SetupError("auth.json 同时存在登录令牌与 API Key，来源不明确；未写入。")
    return validate_secret(candidates[0])

def ensure_local_scope(home: Path, config: dict[str, Any]) -> None:
    for filename in ("requirements.toml", "managed_config.toml"):
        if (home / filename).exists():
            raise SetupError(f"CODEX_HOME 内存在 {filename}。本工具不处理受管配置，未写入。")
    name = config.get("profile")
    if isinstance(name, str):
        if not name or any(c in name for c in '/\\\0') or name in (".", ".."):
            raise SetupError("profile 名称不是普通本地名称；未写入。")
        if (home / (name + ".config.toml")).exists():
            raise SetupError("当前 profile 使用独立 .config.toml 文件；本版仅处理主配置及其内嵌 profiles，不会忽略独立覆盖层后继续写入。")

def ensure_app_closed() -> None:
    if os.name != "nt":
        return
    r = subprocess.run(["tasklist.exe", "/FO", "CSV", "/NH"], capture_output=True, check=False)
    if r.returncode:
        raise SetupError("无法检查进程。请在本机终端运行并完全退出 Codex。")
    names = (b'"codex.exe"', b'"codex-app.exe"', b'"codexplusplus.exe"', b'"chatgpt.exe"')
    if any(line.lower().startswith(names) for line in r.stdout.splitlines()):
        raise SetupError("请完全退出 Codex/CodexPlusPlus（含托盘和终端实例）后重试；脚本不会强制结束进程。")

def resolve_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().absolute()
    existing, _ = local_environment("CODEX_HOME")
    if existing is not None:
        if not existing.strip():
            raise SetupError("CODEX_HOME 已设置但为空；不会猜用其他目录。")
        return Path(existing).expanduser().absolute()
    return (Path.home() / ".codex").absolute()


def dump_toml(document: dict[str, Any]) -> bytes:
    """Round-trip all TOML values; preserve original formatting in the backup."""
    lines = ['# Native image_gen registration compatibility (experimental).',
             '# Original bytes/comments are preserved in the local backup.',
             '# May contain API credentials. Do not share this file.', '']
    def emit(table: dict, path: tuple[str, ...]) -> None:
        if path:
            lines.append('[' + '.'.join(toml_string(k) for k in path) + ']')
        for k, v in table.items():
            if not isinstance(v, dict):
                lines.append(toml_string(k) + ' = ' + toml_value(v))
        lines.append('')
        for k, v in table.items():
            if isinstance(v, dict):
                emit(v, path + (k,))
    emit(document, ())
    text = '\n'.join(lines)
    if not equivalent(tomllib.loads(text), document):
        raise SetupError('TOML 往返校验失败；未修改配置。')
    return text.encode('utf-8')


def plan(home: Path) -> dict[str, Any]:
    before = {f: read_optional(home / f) for f in ('config.toml', 'auth.json')}
    if before['config.toml'] is None:
        raise SetupError('没有找到当前 CODEX_HOME/config.toml；不会猜选供应商或提示输入。')
    config = parse_config(before['config.toml'])
    original_config = copy.deepcopy(config)
    check_conflicts(config)
    ensure_local_scope(home, config)
    # Provider credentials take precedence here. Leave unrelated login state
    # untouched; only parse auth.json if it is needed as the API key source.
    profile = selected_profile(config)
    effective = config['profiles'][profile] if profile else config
    if any(effective.get(k) is not None for k in ('forced_login_method','forced_chatgpt_workspace_id','allowed_login_methods')):
        raise SetupError('当前 profile 有登录策略限制；本工具不修改受管限制。')
    pid = effective.get('model_provider', config.get('model_provider'))
    model = effective.get('model', config.get('model'))
    if not isinstance(pid, str) or not pid.strip() or pid in {'openai','ollama','lmstudio','amazon-bedrock','amazon-bedrock-runtime'}:
        raise SetupError('需要配置中明确选中的自定义中转供应商；不会替换内置供应商。')
    if not isinstance(model, str) or not model.strip() or any(ord(c) < 32 for c in model) or model.lower().startswith('gpt-image'):
        raise SetupError('没有找到有效的当前聊天/编码模型；不会切换默认模型。')
    providers = config.get('model_providers', {})
    if not isinstance(providers, dict) or not isinstance(providers.get(pid), dict):
        raise SetupError('选中供应商的 model_providers 配置不存在或不是表。')
    provider = providers[pid]
    url = provider.get('base_url')
    if not isinstance(url, str):
        raise SetupError('当前供应商没有明确的 base_url。')
    normalize_base_url(url)  # Validation only: never normalize/replace the user's address.
    if provider.get('wire_api', 'responses') != 'responses':
        raise SetupError('本版仅处理 Responses 供应商，不静默改变协议。')
    if 'auth' in provider or 'aws' in provider:
        raise SetupError('本版不执行凭据助手或修改 AWS 登录。')
    for htype in ('http_headers', 'env_http_headers'):
        hs = provider.get(htype, {})
        if not isinstance(hs, dict):
            raise SetupError('供应商 HTTP headers 不是 TOML 表。')
        if any(k.lower() == 'authorization' for k in hs):
            raise SetupError('已有自定义 Authorization 请求头；凭据优先级有歧义，未修改。')
        # This one header is part of the requested compatibility patch. Remove
        # case variants and environment overrides so it is sent exactly once.
        for k in list(hs):
            if k.lower() == ACTOR_HEADER:
                del hs[k]
    notes = []
    key_source = None
    if 'env_key' in provider:
        en = provider['env_key']
        if not isinstance(en,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', en):
            raise SetupError('当前 env_key 不是有效的环境变量名称。')
        key, scope = local_environment(en)
        validate_secret(key)
        key_source = 'provider_env_key'
        notes.append('保留 env_key 引用；正常启动的 App 必须能继承这个环境变量。')
    elif 'experimental_bearer_token' in provider:
        validate_secret(provider['experimental_bearer_token'])
        key_source = 'provider_bearer'
    else:
        store = effective.get('cli_auth_credentials_store',config.get('cli_auth_credentials_store','file'))
        if store not in ('file',None):
            raise SetupError('凭据可能位于系统密钥库；本版不会猜用旧 auth.json。')
        auth_file_key(before['auth.json'])
        key_source = 'auth_file'
        notes.append('保留 auth.json 中现有的 API Key；不迁移或复制到供应商配置。')
    provider['requires_openai_auth'] = False
    provider.setdefault('http_headers', {})[ACTOR_HEADER] = ACTOR_MARKER
    # Configuration uses [features.code_mode], not a top-level [code_mode].
    # Convert an existing boolean to a table without changing its enabled value.
    for layer in ([config,effective] if profile else [config]):
        features = layer.setdefault('features', {})
        if not isinstance(features,dict):
            raise SetupError('features 不是表。')
        features['image_generation'] = True
        cm = features.get('code_mode')
        if isinstance(cm,bool):
            cm = {'enabled':cm}
        elif cm is None:
            cm = {}
        if not isinstance(cm,dict):
            raise SetupError('features.code_mode 不是布尔值或表。')
        direct = cm.setdefault('direct_only_tool_namespaces', [])
        if not isinstance(direct,list) or any(not isinstance(v,str) for v in direct):
            raise SetupError('direct_only_tool_namespaces 不是字符串数组。')
        # Preserve other namespaces and their order; deduplicate image_gen only.
        direct[:] = [v for i, v in enumerate(direct)
                     if v != 'image_gen' or 'image_gen' not in direct[:i]]
        if 'image_gen' not in direct:
            direct.append('image_gen')
        features['code_mode'] = cm
    catalog = None
    catalog_name = effective.get('model_catalog_json', config.get('model_catalog_json'))
    if catalog_name is not None:
        if not isinstance(catalog_name,str):
            raise SetupError('model_catalog_json 必须为文件路径。')
        cp = Path(catalog_name).expanduser()
        if not cp.is_absolute():
            cp = home / cp
        catalog = read_optional(cp)
        if catalog is None or len(catalog) > 16*1024*1024:
            raise SetupError('无法安全读取现有模型目录文件。')
        try:
            obj = json.loads(catalog)
        except (ValueError, UnicodeError):
            raise SetupError('现有模型目录不是有效 JSON。') from None
        if not isinstance(obj,dict) or not isinstance(obj.get('models'),list):
            raise SetupError('现有模型目录结构不受支持。')
    config_bytes = (before['config.toml'] if equivalent(config, original_config)
                    else dump_toml(config))
    after = {'config.toml': config_bytes, 'auth.json': before['auth.json']}
    return {'before':before, 'after':after, 'config':config, 'provider_id':pid,
            'model':model, 'catalog':catalog, 'summary':{
                'provider_model_and_address_preserved':True,
                'credential_source':key_source,'auth_file_preserved':True,
                'added_noncredential_actor_header':True,
                'native_tool':'image_gen.imagegen','notes':notes}}


def png_fixture() -> bytes:
    """A tiny synthetic test pixel, never presented as model-generated artwork."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',struct.pack('!2I5B',8,8,8,2,0,0,0))
            + chunk(b'IDAT',zlib.compress((b'\0'+b'\x80\x80\x80'*8)*8)) + chunk(b'IEND',b''))


def has_native_schema(obj: Any) -> bool:
    """Recognize an advertised namespace; never search descriptions/JSON strings."""
    candidates = obj if isinstance(obj, list) else [obj]
    for namespace in candidates:
        if not isinstance(namespace, dict):
            continue
        if namespace.get('type') != 'namespace' or namespace.get('name') != 'image_gen':
            continue
        children = namespace.get('tools')
        if isinstance(children, list) and any(
            isinstance(tool, dict) and tool.get('type') == 'function'
            and tool.get('name') == 'imagegen' for tool in children
        ):
            return True
    return False


def schema_evidence(body: dict[str, Any]) -> dict[str, Any]:
    """0.153.4 can place tools in input[].additional_tools (Responses Lite).

    Only protocol-defined containers count. Echoed user text, function arguments,
    model output and arbitrary nested dictionaries cannot advertise a tool here.
    The report deliberately contains no tool descriptions, model IDs or input text.
    """
    locations: set[str] = set()
    modes: set[str] = set()
    top = body.get('tools')
    top_count = len(top) if isinstance(top, list) else 0
    if isinstance(top, list):
        modes.add('top_level_tools')
        if has_native_schema(top):
            locations.add('tools')
    items = body.get('input')
    additional_count = 0
    additional_tool_count = 0
    if isinstance(items, list):
        for item in items:
            if (not isinstance(item, dict) or item.get('type') != 'additional_tools'
                    or item.get('role') != 'developer'):
                continue
            additional_count += 1
            tools = item.get('tools')
            if isinstance(tools, list):
                modes.add('responses_lite_additional_tools')
                additional_tool_count += len(tools)
                if has_native_schema(tools):
                    locations.add('input.additional_tools.tools')
    return {'native_schema_locations': sorted(locations),
            'wire_formats_seen': sorted(modes),
            'top_level_tool_count': top_count,
            'additional_tools_item_count': additional_count,
            'additional_tools_tool_count': additional_tool_count}


PROBE_CALL_ID = 'call_local_native_probe'


def output_evidence(body: dict[str, Any], fixture: str) -> dict[str, Any]:
    """Require the issued call ID, a typed tool output, and the exact test image.

    Validate the typed tool result instead of searching the request for a substring.
    A picture/phrase in a user message or a text-only claim cannot pass the test.
    """
    result = {'tool_output_seen': False, 'matching_test_image_seen': False,
              'tool_output_error_class': None}
    items = body.get('input')
    if not isinstance(items, list):
        return result
    for item in items:
        if (not isinstance(item, dict) or item.get('type') != 'function_call_output'
                or item.get('call_id') != PROBE_CALL_ID):
            continue
        result['tool_output_seen'] = True
        output = item.get('output')
        texts = []
        if isinstance(output, list):
            for content in output:
                if not isinstance(content, dict):
                    continue
                if (content.get('type') == 'input_image'
                        and content.get('image_url') == 'data:image/png;base64,' + fixture):
                    result['matching_test_image_seen'] = True
                if content.get('type') == 'input_text' and isinstance(content.get('text'), str):
                    texts.append(content['text'][:16384])
        elif isinstance(output, str):
            texts.append(output[:16384])
        text = '\n'.join(texts).lower()
        # Only allowlisted classifications leave the temporary probe process.
        for needles, category in [
            (('unknown tool', 'unrecognized function', 'unsupported tool', 'not found in tool'), 'TOOL_NOT_ROUTABLE'),
            (('401', 'unauthorized', 'authentication'), 'AUTH_REJECTED'),
            (('403', 'forbidden'), 'REQUEST_FORBIDDEN'),
            (('timed out', 'timeout'), 'TOOL_TIMEOUT'),
            (('invalid image', 'decode', 'base64'), 'IMAGE_DECODE_ERROR'),
        ]:
            if any(word in text for word in needles):
                result['tool_output_error_class'] = category
                break
        if texts and not result['matching_test_image_seen'] and result['tool_output_error_class'] is None:
            result['tool_output_error_class'] = 'TEXT_ONLY_TOOL_OUTPUT'
    return result


def probe_stage(flags: dict[str, bool], request_count: int, exit_code: int | None,
                problem: str | None) -> str:
    if problem:
        return 'PROBE_ERROR'
    if not flags['app_server_no_login_required']:
        return 'APP_SERVER_AUTH_CHECK_FAILED'
    if request_count == 0:
        return 'NO_RESPONSES_REQUEST_CAPTURED'
    if not flags['native_schema_seen']:
        return 'NATIVE_SCHEMA_NOT_ADVERTISED'
    if not flags['native_images_request_seen']:
        return 'NATIVE_TOOL_CALL_DID_NOT_REACH_IMAGES'
    if not flags['native_image_returned_to_model']:
        return 'IMAGE_RESULT_NOT_RETURNED_TO_MODEL'
    return 'LOCAL_NATIVE_ROUNDTRIP_PASSED' if exit_code == 0 else 'EXEC_EXIT_NOT_ZERO'


def sse_output(items: list[dict], rid: str) -> bytes:
    response = {'id':rid,'object':'response','status':'in_progress','output':[]}
    events = [{'type':'response.created','response':response}]
    for i,item in enumerate(items):
        events.append({'type':'response.output_item.added','output_index':i,'item':item})
        events.append({'type':'response.output_item.done','output_index':i,'item':item})
    events.append({'type':'response.completed','response':{
        'id':rid,'object':'response','status':'completed','output':items,
        'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}})
    return ''.join('event: '+e['type']+'\ndata: '+json.dumps(e,separators=(',',':'))+'\n\n' for e in events).encode()


class ProbeServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self):
        super().__init__(('127.0.0.1',0),ProbeHandler)
        self.prefix = '/probe-'+uuid.uuid4().hex+'/v1'
        self.fixture = base64.b64encode(png_fixture()).decode()
        self.flags = {'native_schema_seen':False,'native_images_request_seen':False,
                      'native_image_returned_to_model':False,'app_server_no_login_required':False}
        self.sent_call = False
        self.response_count = 0
        self.schema_locations: set[str] = set()
        self.wire_formats: set[str] = set()
        self.request_shapes: list[dict[str, Any]] = []
        self.tool_output_seen = False
        self.tool_output_error_class = None
        self.responses_lite_header_seen = False
        self.actor_header_seen = False
        self.problem = None
        self.guard = threading.Lock()


class ProbeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def setup(self):
        super().setup()
        self.connection.settimeout(15)
    def log_message(self,*args):
        pass
    def reply(self,status: int,body: bytes,ctype: str='application/json'):
        self.send_response(status)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Connection','close')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):
            pass
        self.close_connection = True
    def do_CONNECT(self):
        self.reply(403,b'{}')
    def do_GET(self):
        # No forward proxying: ALL other destinations are denied.
        if self.path == self.server.prefix+'/models':
            self.reply(200,b'{"models":[],"data":[]}')
        else:
            self.reply(403,b'{}')
    def read_body(self) -> bytes:
        maximum = 32*1024*1024
        if self.headers.get('Transfer-Encoding','').lower() == 'chunked':
            chunks, total = [],0
            while True:
                line = self.rfile.readline(128)
                n = int(line.strip().split(b';',1)[0],16)
                if n == 0:
                    while self.rfile.readline(8192) not in (b'\r\n',b'\n',b''):
                        pass
                    break
                total += n
                if total > maximum:
                    raise ValueError('body_limit')
                chunk = self.rfile.read(n)
                if len(chunk)!=n or self.rfile.read(2)!=b'\r\n':
                    raise ValueError('invalid_chunk')
                chunks.append(chunk)
            raw = b''.join(chunks)
        else:
            n = int(self.headers.get('Content-Length','0'))
            if not 0<n<=maximum:
                raise ValueError('body_limit')
            raw = self.rfile.read(n)
        encoding = self.headers.get('Content-Encoding','identity').lower()
        if encoding == 'gzip':
            # Use a bounded decompressor, not gzip.decompress on untrusted HTTP.
            dec = zlib.decompressobj(16+zlib.MAX_WBITS)
            raw = dec.decompress(raw,maximum+1)
        elif encoding == 'deflate':
            dec = zlib.decompressobj()
            raw = dec.decompress(raw,maximum+1)
        elif encoding != 'identity':
            raise ValueError('unsupported_request_encoding')
        if len(raw)>maximum:
            raise ValueError('body_limit')
        return raw
    def do_POST(self):
        s = self.server
        if self.path not in (s.prefix+'/responses',s.prefix+'/images/generations'):
            self.reply(403,b'{}'); return
        if self.headers.get('Authorization') != 'Bearer '+PROBE_KEY:
            s.problem = 'probe_auth_not_preserved'
            self.reply(401,b'{"error":{"message":"mock-only auth mismatch"}}'); return
        try:
            raw = self.read_body()
            body = json.loads(raw)
            if not isinstance(body,dict):
                raise ValueError('body_shape')
        except (ValueError,UnicodeError,zlib.error,OverflowError):
            s.problem = 'unsupported_mock_request'
            self.reply(400,b'{}'); return
        if self.path.endswith('/images/generations'):
            if not s.sent_call:
                s.problem = 'image_request_before_advertised_tool_call'
                self.reply(400,b'{}'); return
            if body.get('prompt') != 'NATIVE_IMAGEGEN_LOCAL_PROTOCOL_TEST' or not isinstance(body.get('model'),str):
                s.problem = 'unexpected_native_image_request'
                self.reply(400,b'{}'); return
            s.flags['native_images_request_seen'] = True
            self.reply(200,json.dumps({'created':0,'data':[{'b64_json':s.fixture}],
                                      'background':'opaque','output_format':'png'}).encode())
            return
        with s.guard:
            s.response_count += 1
            if s.response_count > 4:
                self.reply(400,b'{}'); return
            evidence = schema_evidence(body)
            s.schema_locations.update(evidence['native_schema_locations'])
            s.wire_formats.update(evidence['wire_formats_seen'])
            s.request_shapes.append(evidence)
            s.flags['native_schema_seen'] |= bool(evidence['native_schema_locations'])
            s.responses_lite_header_seen |= (
                self.headers.get('x-openai-internal-codex-responses-lite','').lower() == 'true')
            s.actor_header_seen |= self.headers.get(ACTOR_HEADER) == ACTOR_MARKER
            returned = output_evidence(body, s.fixture)
            if s.sent_call:
                s.tool_output_seen |= returned['tool_output_seen']
                if returned['tool_output_error_class']:
                    s.tool_output_error_class = returned['tool_output_error_class']
                if s.flags['native_images_request_seen'] and returned['matching_test_image_seen']:
                    s.flags['native_image_returned_to_model'] = True
            if s.flags['native_schema_seen'] and not s.sent_call:
                s.sent_call = True
                items = [{'type':'function_call','id':'fc_local_native_probe',
                          'call_id':PROBE_CALL_ID,'namespace':'image_gen',
                          'name':'imagegen','arguments':json.dumps({'prompt':'NATIVE_IMAGEGEN_LOCAL_PROTOCOL_TEST'}),
                          'status':'completed'}]
            else:
                items = [{'type':'message','id':'msg_local_probe','role':'assistant',
                          'status':'completed','content':[{'type':'output_text','text':'LOCAL_MOCK_COMPLETE','annotations':[]}]}]
            payload = sse_output(items,'resp_local_probe_'+str(s.response_count))
        self.reply(200,payload,'text/event-stream')


def terminate_probe(p: subprocess.Popen) -> None:
    """Terminate only the subprocess tree created for this disposable probe."""
    if p.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill.exe','/PID',str(p.pid),'/T','/F'],capture_output=True,timeout=10,check=False)
    else:
        import signal
        try:
            os.killpg(p.pid,signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait(timeout=3)


def popen_probe(command: list[str],env: dict[str,str],cwd: Path,stderr) -> subprocess.Popen:
    return subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                            stderr=stderr,creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0),
                            start_new_session=(os.name!='nt'))


def account_probe(command: list[str], env: dict[str,str], cwd: Path) -> bool:
    with tempfile.TemporaryFile() as errors:
        p = popen_probe(command+['app-server'],env,cwd,errors)
        messages: queue.Queue = queue.Queue()
        def collect():
            try:
                for line in p.stdout:
                    if len(line)<4*1024*1024:
                        try: messages.put(json.loads(line))
                        except (ValueError,UnicodeError): pass
            finally:
                messages.put(None)
        th = threading.Thread(target=collect,daemon=True); th.start()
        def send(obj):
            p.stdin.write((json.dumps(obj)+'\n').encode()); p.stdin.flush()
        def wait_id(n,seconds=15):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                try: item=messages.get(timeout=max(.01,deadline-time.monotonic()))
                except queue.Empty: break
                if item is None: break
                if isinstance(item,dict) and item.get('id')==n:
                    return item
            return {}
        try:
            send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'native_imagegen_local_probe','version':'unversioned'},
                                                       'capabilities':{'experimentalApi':True}}})
            if 'result' not in wait_id(1): return False
            send({'method':'initialized','params':{}})
            send({'id':2,'method':'account/read','params':{'refreshToken':False}})
            result=wait_id(2).get('result',{})
            return isinstance(result,dict) and result.get('requiresOpenaiAuth') is False
        except (OSError,BrokenPipeError):
            return False
        finally:
            terminate_probe(p)
            th.join(timeout=2)
            for stream in (p.stdin,p.stdout):
                if stream: stream.close()


# Distinguish OS launch failures from native-tool registration failures.
VERSION_TIMEOUT = 30.0

class BackendStartError(SetupError):
    def __init__(self, report: dict[str, Any]):
        super().__init__('应用后端未启动，未写入 config.toml/auth.json。见 backend_start 明细。')
        self.report = report


def error_fields(exc: OSError) -> dict[str, Any]:
    # Do not print str(exc), filenames, command lines, or unfiltered stderr.
    win = getattr(exc, 'winerror', None)
    eno = getattr(exc, 'errno', None)
    kinds = {2:'FILE_NOT_FOUND', 3:'PATH_NOT_FOUND', 5:'ACCESS_DENIED',
             126:'MODULE_NOT_FOUND', 193:'BAD_EXE_FORMAT', 216:'EXE_MACHINE_MISMATCH',
             225:'SECURITY_PRODUCT_BLOCK', 577:'IMAGE_SIGNATURE_REJECTED',
             740:'ELEVATION_REQUIRED', 1260:'BLOCKED_BY_POLICY'}
    kind = kinds.get(win, 'OS_LAUNCH_ERROR')
    if win is None and eno == 13:
        kind = 'ACCESS_DENIED'
    return {'status':kind, 'winerror':win if isinstance(win,int) else None,
            'errno':eno if isinstance(eno,int) else None}


def isolated_env(root: Path, proxy: str = 'http://127.0.0.1:9') -> dict[str,str]:
    for part in ('home','appdata','tmp','workspace'):
        (root/part).mkdir(exist_ok=True)
    allow = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','SYSTEMDRIVE',
             'PROCESSOR_ARCHITECTURE','PROCESSOR_ARCHITEW6432','NUMBER_OF_PROCESSORS'}
    env = {k:v for k,v in os.environ.items() if k.upper() in allow}
    env.update({'CODEX_HOME':str(root/'home'),'HOME':str(root),'USERPROFILE':str(root),
                'APPDATA':str(root/'appdata'),'LOCALAPPDATA':str(root/'appdata'),
                'TEMP':str(root/'tmp'),'TMP':str(root/'tmp'),'TMPDIR':str(root/'tmp'),
                'HTTP_PROXY':proxy,'HTTPS_PROXY':proxy,'ALL_PROXY':proxy,
                'http_proxy':proxy,'https_proxy':proxy,'all_proxy':proxy,
                'NO_PROXY':'127.0.0.1,localhost','no_proxy':'127.0.0.1,localhost',
                'OPENAI_BASE_URL':proxy+'/version-probe-only/v1',
                'RUST_LOG':'off','DO_NOT_TRACK':'1','NO_COLOR':'1'})
    return env


def parse_backend_version(stdout: bytes, stderr: bytes) -> str | None:
    # Allow diagnostic lines before/after the version, never treat arbitrary
    # numbers from the GUI's About dialog as proof that this is the CLI.
    raw = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', stdout+b'\n'+stderr)
    found = re.findall(rb'^\s*codex(?:-cli)?\s+([0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)\s*$',
                       raw, re.MULTILINE)
    versions = set(found)
    return next(iter(versions)).decode('ascii') if len(versions)==1 else None


def probe_backend_version(binary: Path, timeout: float = VERSION_TIMEOUT) -> dict[str,Any]:
    result: dict[str,Any] = {'ok':False,'timeout_seconds':timeout}
    start = time.monotonic()
    try:
        if not binary.is_file():
            return {**result,'status':'FILE_NOT_FOUND'}
        with tempfile.TemporaryDirectory(prefix='codex-backend-version-') as td:
            root=Path(td); env=isolated_env(root)
            (root/'home'/'config.toml').write_text('check_for_update_on_startup = false\n',encoding='utf-8')
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                p=None
                try:
                    p=subprocess.Popen([str(binary.absolute()),'--version'],
                        cwd=binary.parent.absolute(),env=env,stdin=subprocess.DEVNULL,
                        stdout=out,stderr=err,
                        creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0),
                        start_new_session=(os.name!='nt'))
                    try:
                        p.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        result.update(status='VERSION_TIMEOUT')
                        return result
                    result['exit_code']=p.returncode
                    result['exit_code_hex']='0x%08X' % (p.returncode & 0xffffffff)
                    out.seek(0); err.seek(0)
                    stdout=out.read(65536); stderr=err.read(65536)
                    result['stdout_present']=bool(stdout); result['stderr_present']=bool(stderr)
                    v=parse_backend_version(stdout,stderr)
                    if p.returncode == 0 and v is not None:
                        result.update(ok=True,status='VERSION_OK',backend_version=v)
                    elif p.returncode != 0:
                        statuses={0xc0000135:'DLL_NOT_FOUND',0xc000007b:'INVALID_IMAGE_FORMAT',
                                  0xc0000142:'DLL_INIT_FAILED',0xc0000005:'PROCESS_ACCESS_VIOLATION',
                                  0xc0000428:'IMAGE_SIGNATURE_REJECTED'}
                        result['status']=statuses.get(p.returncode & 0xffffffff,'BACKEND_EXITED_NONZERO')
                    else:
                        result['status']='VERSION_OUTPUT_UNRECOGNIZED'
                    return result
                finally:
                    if p is not None:
                        terminate_probe(p)
    except OSError as exc:
        result.update(error_fields(exc)); return result
    finally:
        result['elapsed_ms']=int((time.monotonic()-start)*1000)


def hash_binary(path: Path) -> str:
    # Streaming; a large bundled executable should not be read into memory.
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def select_backend(inventory: dict[str,Any], timeout: float = VERSION_TIMEOUT) -> tuple[Path,dict[str,Any]]:
    """Use the selected App's bundle or a pre-existing byte-identical cache copy.

    No PATH CLI fallback, no executable copy, no ACL/policy changes. Cache
    candidates with a different digest are skipped without being executed.
    """
    report: dict[str,Any]={'status':'BACKEND_NOT_SELECTED','attempts':[],
                          'cache_skipped_hash_mismatch':0,'cache_unreadable':0,
                          'configs_modified':False}
    refs=inventory.get('references',[])
    caches=inventory.get('cache_candidates',[])
    if not isinstance(refs,list) or not isinstance(caches,list):
        raise SetupError('后端清单格式错误；未修改配置。')
    if not refs:
        report['status']='APP_BACKEND_NOT_FOUND'; raise BackendStartError(report)
    if len(refs)>32 or len(caches)>128:
        report['status']='TOO_MANY_BACKEND_CANDIDATES'; raise BackendStartError(report)
    paths=[]; digests=set(); ref_errors=[]
    for raw in refs:
        if not isinstance(raw,str): raise SetupError('后端清单包含无效路径。')
        p=Path(raw).absolute()
        try:
            if not p.is_file():
                ref_errors.append({'status':'REFERENCE_NOT_FOUND'}); continue
            dg=hash_binary(p); digests.add(dg); paths.append((p,dg))
        except OSError as exc:
            ref_errors.append(error_fields(exc))
    # Never use an unrelated cache binary just because it happens to run.
    if len(digests)>1:
        report['status']='AMBIGUOUS_APP_BACKENDS'; raise BackendStartError(report)
    if not paths:
        report.update(status='APP_BACKEND_UNREADABLE',reference_errors=ref_errors)
        raise BackendStartError(report)
    reference_hash=paths[0][1]
    expected_size=paths[0][0].stat().st_size
    report['reference_sha256']=reference_hash
    choices=[]; seen={str(p).casefold() for p,_ in paths}
    for raw in caches:
        if not isinstance(raw,str): continue
        p=Path(raw).absolute()
        if str(p).casefold() in seen: continue
        seen.add(str(p).casefold())
        try:
            if not p.is_file(): continue
            if p.stat().st_size != expected_size or hash_binary(p)!=reference_hash:
                report['cache_skipped_hash_mismatch']+=1; continue
            choices.append((p,'app_cache_sha256_match'))
        except OSError:
            report['cache_unreadable']+=1
    choices.extend((p,'app_bundle') for p,_ in paths)
    security_stops={'BLOCKED_BY_POLICY','IMAGE_SIGNATURE_REJECTED','SECURITY_PRODUCT_BLOCK'}
    for index,(p,source) in enumerate(choices):
        # No full paths/GUI command lines are written to the shareable report.
        check=probe_backend_version(p,timeout)
        check.update(candidate=index+1,source=source,sha256=reference_hash)
        report['attempts'].append(check)
        print('后端候选 %d (%s): %s' % (index+1,source,check['status']),flush=True)
        if check.get('ok'):
            # Re-check after execution, before trusting a cached executable.
            try: unchanged=hash_binary(p)==reference_hash
            except OSError: unchanged=False
            if not unchanged:
                report['status']='BACKEND_CHANGED_DURING_PROBE'; raise BackendStartError(report)
            report.update(status='BACKEND_READY',selected=check)
            return p,report
        if check['status'] in security_stops:
            report['status']='SECURITY_POLICY_STOP'; raise BackendStartError(report)
    report['status']='NO_LAUNCHABLE_MATCHING_BACKEND'
    raise BackendStartError(report)



def native_probe(binary: Path, proposal: dict[str, Any],
                 version_result: dict[str,Any] | None = None) -> dict[str, Any]:
    if not binary.is_file():
        raise SetupError('没有找到应用自带的后端程序。')
    command=[str(binary)]
    checked=version_result or probe_backend_version(binary)
    if not checked.get('ok'):
        raise BackendStartError({'status':'BACKEND_START_FAILED','attempts':[checked],
                                 'configs_modified':False})
    # Detect an in-place update between the selector and the native probe.
    if checked.get('sha256') and hash_binary(binary)!=checked['sha256']:
        raise BackendStartError({'status':'BACKEND_CHANGED_BEFORE_NATIVE_PROBE',
                                 'configs_modified':False})
    version=checked['backend_version']
    with tempfile.TemporaryDirectory(prefix='codex-native-imagegen-probe-') as td:
        root=Path(td); h=root/'home'; cwd=root/'workspace'
        h.mkdir(); cwd.mkdir(); (root/'appdata').mkdir(); (root/'tmp').mkdir()
        server=ProbeServer()
        worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
        proxy='http://127.0.0.1:'+str(server.server_port)
        test_config={
            'model':proposal['model'],'model_provider':'local_native_probe',
            'cli_auth_credentials_store':'file','approval_policy':'never','sandbox_mode':'read-only',
            'web_search':'disabled','check_for_update_on_startup':False,
            'features':{'image_generation':True,
                        'code_mode':{'direct_only_tool_namespaces':['image_gen']},
                        'enable_request_compression':False},
            'analytics':{'enabled':False},'feedback':{'enabled':False},
            'model_providers':{'local_native_probe':{
                'name':'Local native protocol probe','base_url':proxy+server.prefix,
                'wire_api':'responses','requires_openai_auth':False,
                'supports_websockets':False,'experimental_bearer_token':PROBE_KEY,
                'request_max_retries':0,'stream_max_retries':0,
                'http_headers':{ACTOR_HEADER:ACTOR_MARKER}}}}
        if proposal.get('catalog') is not None:
            cp=h/'models.json'; cp.write_bytes(proposal['catalog'])
            test_config['model_catalog_json']=str(cp)
        (h/'config.toml').write_bytes(dump_toml(test_config))
        # Do not inherit user credential env vars, Codex host overrides, MCP configs,
        # project files, or a real auth.json. External HTTP proxy destinations are denied.
        env={k:v for k,v in os.environ.items() if k.upper() in {
            'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','SYSTEMDRIVE','PROCESSOR_ARCHITECTURE'}}
        env.update({'CODEX_HOME':str(h),'HOME':str(root),'USERPROFILE':str(root),
                    'APPDATA':str(root/'appdata'),'LOCALAPPDATA':str(root/'appdata'),
                    'TEMP':str(root/'tmp'),'TMP':str(root/'tmp'),'TMPDIR':str(root/'tmp'),
                    'HTTP_PROXY':proxy,'HTTPS_PROXY':proxy,'ALL_PROXY':proxy,
                    'http_proxy':proxy,'https_proxy':proxy,'all_proxy':proxy,
                    'NO_PROXY':'127.0.0.1,localhost','no_proxy':'127.0.0.1,localhost',
                    'OPENAI_BASE_URL':proxy+server.prefix,'RUST_LOG':'off',
                    'DO_NOT_TRACK':'1','NO_COLOR':'1'})
        exit_code = None
        stdout_present = False
        stderr_present = False
        try:
            try:
                server.flags['app_server_no_login_required']=account_probe(command,env,cwd)
            except OSError as exc:
                return {'passed':False,'backend_version':version,
                        'probe_problem':'APP_SERVER_START_FAILED','launch_error':error_fields(exc),
                        'upstream_relay_tested':False,'desktop_ui_tested':False}
            if not server.flags['app_server_no_login_required']:
                server.problem='APP_SERVER_ACCOUNT_PROBE_FAILED'
            if server.flags['app_server_no_login_required']:
                with tempfile.TemporaryFile() as errors:
                    p=popen_probe(command+['exec','--skip-git-repo-check','--ephemeral','--json',
                                          '-s','read-only','-C',str(cwd),
                                          'Local protocol test. Follow the provided local tool request. Do not execute shell commands.'],env,cwd,errors)
                    try:
                        out, _ = p.communicate(timeout=60)
                        stdout_present = bool(out)
                        exit_code=p.returncode
                    except subprocess.TimeoutExpired:
                        server.problem='native_probe_timeout'
                    finally:
                        terminate_probe(p)
                        for stream in (p.stdin,p.stdout):
                            if stream: stream.close()
                        stderr_present = errors.tell() > 0
            diagnostics = {
                'stage': probe_stage(server.flags, server.response_count, exit_code, server.problem),
                'responses_request_count': server.response_count,
                'native_schema_locations': sorted(server.schema_locations),
                'wire_formats_seen': sorted(server.wire_formats),
                'request_shapes': server.request_shapes,
                'responses_lite_header_seen': server.responses_lite_header_seen,
                'compatibility_actor_header_seen': server.actor_header_seen,
                'native_tool_call_issued': server.sent_call,
                'native_tool_output_seen': server.tool_output_seen,
                'tool_output_error_class': server.tool_output_error_class,
                'exec_stdout_present': stdout_present,
                'exec_stderr_present': stderr_present,
            }
            result={'backend_version':version,**server.flags,'diagnostics':diagnostics,
                    'upstream_relay_tested':False,'desktop_ui_tested':False,
                    'test_used_synthetic_fixture':True,'native_exec_exit_code':exit_code}
            result['passed']=all(server.flags.values()) and exit_code==0 and server.problem is None
            if server.problem:
                result['probe_problem']=server.problem
            return result
        except OSError as exc:
            return {'passed':False,'backend_version':version,
                    'probe_problem':'NATIVE_EXEC_START_FAILED','launch_error':error_fields(exc),
                    'upstream_relay_tested':False,'desktop_ui_tested':False}
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)


@contextmanager
def locked(home: Path):
    if home.is_symlink() or (home.exists() and getattr(home.stat(), "st_file_attributes", 0) & 0x400):
        raise SetupError("CODEX_HOME 是链接/重解析点；为保护目标文件，本版停止写入。")
    home.mkdir(parents=True, exist_ok=True)
    root = home / STATE_DIR
    secure_dir(root)
    lock = root / "operation.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SetupError("另一安装/恢复操作正在运行，或上次被中断。确认没有操作运行后，删除 .native-imagegen-registration-v2/operation.lock 再执行 Restore。") from None
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        yield root
    finally:
        lock.unlink(missing_ok=True)


def active_state(root: Path) -> tuple[Path, dict[str, Any]] | None:
    raw = read_optional(root / "active.json")
    if raw is None:
        return None
    try:
        pointer = json.loads(raw)
        run_id = pointer["run"]
        if pointer["product"] != PRODUCT or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError()
        run = root / run_id
        if run.is_symlink() or not run.is_dir():
            raise ValueError()
        meta = json.loads(read_optional(run / "manifest.json") or b"null")
        if meta["product"] != PRODUCT or set(meta["before"]) != set(FILES) or set(meta["after"]) != set(FILES):
            raise ValueError()
        return run, meta
    except (ValueError, TypeError, KeyError):
        raise SetupError("备份索引/清单不完整，未改动配置。请保留备份目录进行人工检查。") from None


def restore(home: Path, force: bool = False) -> dict[str, Any]:
    if not (home / STATE_DIR).exists():
        return {"state": "NOT_INSTALLED", "codex_home": str(home)}
    with locked(home) as root:
        state = active_state(root)
        if state is None:
            return {"state": "NOT_INSTALLED", "codex_home": str(home)}
        run, meta = state
        if meta["phase"] == "restored":
            return {"state": "ALREADY_RESTORED", "backup": str(run)}
        # Old manifests are also supported: their hashes identify exactly which
        # files that installation changed. Never restore an untouched auth.json.
        affected = [name for name in FILES if meta['before'][name] != meta['after'][name]]
        original: dict[str, bytes | None] = {}
        current = {name: read_optional(home / name) for name in affected}
        for name in affected:
            data = read_optional(run / (name + ".before"))
            if digest(data) != meta["before"][name]:
                raise SetupError("原始备份校验失败；拒绝恢复，未改动配置。")
            original[name] = data
            if digest(current[name]) not in (meta["before"][name], meta["after"][name]) and not force:
                raise SetupError("安装后配置被修改。为保护新改动，恢复已停止。明确需要覆盖时用 -ForceRestore；它会先另存当前文件。")
        emergency = run / ("pre-restore-" + uuid.uuid4().hex)
        secure_dir(emergency)
        for name, data in current.items():
            if data is not None:
                atomic_write(emergency / name, data, emergency)
        for name in affected:
            if digest(read_optional(home / name)) != digest(current[name]):
                raise SetupError("恢复期间配置被其他进程修改，已停止；请关闭 Codex 后检查 Status。")
            if original[name] is None:
                (home / name).unlink(missing_ok=True)
            else:
                atomic_write(home / name, original[name], run)
        if any(digest(read_optional(home / name)) != meta["before"][name] for name in affected):
            raise SetupError("恢复后校验失败；请保留备份目录。")
        meta["phase"] = "restored"
        write_json(run / "manifest.json", meta, run)
        return {"state": "RESTORED", "backup": str(run), "pre_restore_backup": str(emergency)}


def status(home: Path) -> dict[str, Any]:
    root=home/STATE_DIR
    state=active_state(root) if root.exists() else None
    if state is None:
        return {'state':'NOT_INSTALLED'}
    run,meta=state
    return {'state':meta['phase'].upper(),
            'files_match_installed_snapshot':all(digest(read_optional(home/f))==meta['after'][f] for f in FILES),
            'configuration':meta.get('configuration',{}),'local_native_probe':meta.get('native_probe',{}),
            'backup':str(run),'real_oauth_performed':False,'upstream_relay_tested':False,'desktop_ui_tested':False}


def commit(home: Path, proposal: dict[str, Any], probe: dict[str, Any], root: Path) -> dict[str, Any]:
    if probe.get('passed') is not True:
        raise SetupError('原生工具自检未通过，禁止写入配置。')
    before,after=proposal['before'],proposal['after']
    if any(read_optional(home/f)!=before[f] for f in FILES):
        raise SetupError('配置在自检期间被其他进程修改；未提交修改。')
    prior_pointer = read_optional(root/'active.json')
    run=root/uuid.uuid4().hex; secure_dir(run)
    for name,data in before.items():
        if data is not None:
            atomic_write(run/(name+'.before'),data,run)
    meta={'product':PRODUCT,'phase':'prepared',
          'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
          'before':{n:digest(v) for n,v in before.items()},
          'after':{n:digest(v) for n,v in after.items()},
          'configuration':proposal['summary'],'native_probe':probe}
    write_json(run/'manifest.json',meta,run)
    write_json(root/'active.json',{'product':PRODUCT,'run':run.name},root)
    changed=[]
    try:
        for name in FILES:
            if read_optional(home/name)!=before[name]:
                raise SetupError('配置写入期间发生并发修改；已停止。')
            if after[name]==before[name]:
                continue
            if after[name] is None:
                (home/name).unlink(missing_ok=True)
            else:
                atomic_write(home/name,after[name],run)
            changed.append(name)
        if any(read_optional(home/f)!=after[f] for f in FILES):
            raise SetupError('写入后校验失败。')
        meta['phase']='installed'
        write_json(run/'manifest.json',meta,run)
    except BaseException as exc:
        for name in reversed(changed):
            if read_optional(home/name)==after[name]:
                if before[name] is None:
                    (home/name).unlink(missing_ok=True)
                else:
                    atomic_write(home/name,before[name],run)
        if all(read_optional(home/f)==before[f] for f in changed):
            meta['phase']='restored'; write_json(run/'manifest.json',meta,run)
            # A failed reinstallation must not hide the previous recovery point.
            if prior_pointer is None:
                (root/'active.json').unlink(missing_ok=True)
            else:
                atomic_write(root/'active.json',prior_pointer,root)
        if isinstance(exc,(SetupError,KeyboardInterrupt)):
            raise
        raise SetupError('写入失败，已尝试回滚。请保留备份并执行 Status。') from None
    return status(home)


def install(home: Path,binary: Path, version_result: dict[str,Any] | None=None) -> dict[str,Any]:
    with locked(home) as root:
        prior=active_state(root)
        if prior and prior[1]['phase'] not in ('restored','installed'):
            raise SetupError('存在未完成的本版安装记录；请先 Restore。')
        proposal=plan(home)
        print('正在用 App 自带后端检查原生 image_gen：仅本机模拟接口，不调用中转模型。',flush=True)
        result=native_probe(binary,proposal,version_result)
        if not result['passed']:
            return {'state':'NOT_APPLIED_NATIVE_PROBE_FAILED','files_modified':False,
                    'local_native_probe':result,
                    'message':'该后端未通过这条兼容路径的本机自检；没有写入新配置。已有旧版改动也未自动撤销。'}
        # Probe subprocesses have exited; recheck that no actual application has reopened.
        ensure_app_closed()
        if any(read_optional(home/f)!=proposal['before'][f] for f in FILES):
            raise SetupError('配置在自检期间发生改动；未提交修改，请重新执行。')
        modified = any(proposal['before'][f] != proposal['after'][f] for f in FILES)
        if not modified and prior and prior[1]['phase']=='installed':
            run,meta=prior; meta['native_probe']=result
            write_json(run/'manifest.json',meta,run)
            out=status(home); out.update(state='ALREADY_INSTALLED_NATIVE_PROBE_PASSED',files_modified=False)
            return out
        # Each modifying install gets its own snapshot. A no-op keeps the last
        # recovery point, even after unrelated user edits or a login refresh.
        out=commit(home,proposal,result,root)
        out['state']='INSTALLED_NATIVE_PROBE_PASSED'
        out['files_modified']=modified
        return out


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['install','check','status','restore'],nargs='?',default='install')
    parser.add_argument('--home',type=Path)
    parser.add_argument('--backend',type=Path)
    parser.add_argument('--inventory',type=Path)
    parser.add_argument('--version-timeout',type=float,default=VERSION_TIMEOUT)
    parser.add_argument('--force-restore',action='store_true')
    a=parser.parse_args(argv)
    try:
        if a.force_restore and a.action!='restore':
            raise SetupError('ForceRestore 仅适用于 Restore。')
        if not (5 <= a.version_timeout <= 120):
            raise SetupError('version-timeout 必须在 5 到 120 秒之间。')
        home=resolve_home(a.home)
        if a.action=='status':
            result=status(home)
        elif a.action=='restore':
            ensure_app_closed(); result=restore(home,a.force_restore)
        else:
            ensure_app_closed()
            if a.inventory is not None:
                if a.inventory.stat().st_size > 256*1024:
                    raise SetupError('后端清单过大；未修改配置。')
                inv=json.loads(a.inventory.read_text(encoding='utf-8-sig'))
            elif a.backend is not None:
                inv={'references':[str(a.backend.absolute())],'cache_candidates':[]}
            else:
                raise SetupError('未定位到 App 自带后端；未修改配置。')
            binary,launch=select_backend(inv,a.version_timeout)
            if a.action=='check':
                p=plan(home)
                print('只读本机工具检查：不会写入用户 config.toml/auth.json。',flush=True)
                probe=native_probe(binary,p,launch['selected'])
                result={'state':'CHECK_PASSED' if probe['passed'] else 'CHECK_NATIVE_PROBE_FAILED',
                        'configs_modified':False,'local_native_probe':probe}
            else:
                result=install(home,binary,launch['selected'])
            result['backend_start']=launch
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if result['state'].startswith(('INSTALLED_','ALREADY_INSTALLED_')):
            print('\n配置已就绪，且应用后端在本机模拟接口上完成原生工具调用。')
            print('这不是实际中转生图成功，也不是 OAuth 登录成功；请重开 App 新建对话实测。')
            print('中转必须忽略/去除兼容用 actor 请求头，并兼容原生 Images API。')
        return 2 if result['state'] in ('NOT_APPLIED_NATIVE_PROBE_FAILED','CHECK_NATIVE_PROBE_FAILED') else 0
    except KeyboardInterrupt:
        print('CANCELLED: 已中止。若处于写入阶段，请 Status/Restore。',file=sys.stderr); return 130
    except BackendStartError as exc:
        print(json.dumps({'state':'NOT_APPLIED_BACKEND_START_FAILED','configs_modified':False,
                          'backend_start':exc.report},ensure_ascii=False,indent=2))
        print('ERROR: '+str(exc),file=sys.stderr); return 3
    except SetupError as exc:
        print('ERROR: '+str(exc),file=sys.stderr); return 1
    except Exception:
        print('ERROR: 操作未完成。未输出异常细节以避免泄露路径/凭据；请保留备份并执行 Status。',file=sys.stderr); return 1

if __name__=='__main__':
    raise SystemExit(main())
