# Native registration with Responses Lite probe support; experimental.
# Reuses provider/model/key. No downloads, ACL bypass, OAuth, or PATH-CLI substitution.
[CmdletBinding()]
param(
    [ValidateSet('Install','Check','Status','Restore')][string]$Action='Install',
    [string]$CodexHome='',
    [string]$BackendPath='',
    [ValidateRange(5,120)][int]$VersionTimeoutSeconds=30,
    [switch]$ForceRestore
)
$ErrorActionPreference='Stop'
$codeFile=$null; $inventoryFile=$null; $exitCode=1
function Get-BackendInventory {
    $roots=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $rootVersions=@{}
    $cacheRoots=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $packages=@()
    if (Get-Command Get-AppxPackage -ErrorAction SilentlyContinue) {
        try {
            $packages=@(Get-AppxPackage -ErrorAction SilentlyContinue | Where-Object {
                $_.Name -match '^OpenAI\.(Codex|ChatGPT(?:-Desktop)?)$'
            })
        } catch { $packages=@() }
    }
    foreach ($pkg in $packages) {
        if ($pkg.InstallLocation) {
            foreach ($r in @((Join-Path $pkg.InstallLocation 'app'),$pkg.InstallLocation)) {
                [void]$roots.Add($r); $rootVersions[$r]=[version]$pkg.Version
            }
        }
        if ($env:LOCALAPPDATA -and $pkg.PackageFamilyName) {
            $localCache=Join-Path $env:LOCALAPPDATA ('Packages\'+$pkg.PackageFamilyName+'\LocalCache\Local')
            foreach ($rel in @('OpenAI\Codex\bin','OpenAI\ChatGPT\bin')) {
                [void]$cacheRoots.Add((Join-Path $localCache $rel))
            }
        }
    }
    foreach ($base in @($env:LOCALAPPDATA,$env:ProgramFiles,${env:ProgramFiles(x86)})) {
        if ($base) {
            foreach ($rel in @('Programs\Codex','Programs\ChatGPT','Codex','ChatGPT',
                                'Programs\OpenAI\Codex','Programs\OpenAI\ChatGPT')) {
                [void]$roots.Add((Join-Path $base $rel))
            }
        }
    }
    if ($env:LOCALAPPDATA) {
        foreach ($rel in @('OpenAI\Codex\bin','OpenAI\ChatGPT\bin')) {
            [void]$cacheRoots.Add((Join-Path $env:LOCALAPPDATA $rel))
        }
    }
    foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
                        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
        foreach ($entry in @(Get-ItemProperty -Path $key -ErrorAction SilentlyContinue | Where-Object {
                $_.DisplayName -match '^(Codex|ChatGPT)$'
        })) {
            if ($entry.InstallLocation) { [void]$roots.Add($entry.InstallLocation) }
        }
    }
    $found=New-Object 'System.Collections.Generic.List[object]'
    $seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($root in $roots) {
        $resources=Join-Path $root 'resources'
        if (-not (Test-Path -LiteralPath $resources -PathType Container)) { continue }
        $v=[version]'0.0'; if ($rootVersions.ContainsKey($root)) { $v=$rootVersions[$root] }
        $hasApp=$false
        foreach ($appName in @('Codex.exe','ChatGPT.exe')) {
            $app=Join-Path $root $appName
            if (Test-Path -LiteralPath $app -PathType Leaf) {
                $hasApp=$true
                try {
                    $info=(Get-Item -LiteralPath $app).VersionInfo.ProductVersion
                    if ($info -match '^\s*(\d+\.\d+\.\d+(?:\.\d+)?)') { $v=[version]$Matches[1] }
                } catch {}
            }
        }
        if (-not $hasApp) { continue }
        foreach ($rel in @('codex.exe','bin\codex.exe','codex\codex.exe')) {
            $binary=Join-Path $resources $rel
            if ((Test-Path -LiteralPath $binary -PathType Leaf) -and $seen.Add([IO.Path]::GetFullPath($binary))) {
                $found.Add([pscustomobject]@{Path=[IO.Path]::GetFullPath($binary);Version=$v})
            }
        }
    }
    # Collect only known App-owned runtime cache locations. Do not scan PATH.
    $cacheFiles=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($cacheRoot in $cacheRoots) {
        if (-not (Test-Path -LiteralPath $cacheRoot -PathType Container)) { continue }
        $dirs=New-Object 'System.Collections.Generic.List[object]'
        $dirs.Add([pscustomobject]@{Path=$cacheRoot;Depth=0})
        for ($i=0; $i -lt $dirs.Count -and $i -lt 128; $i++) {
            $node=$dirs[$i]
            $folder=Get-Item -LiteralPath $node.Path -Force -ErrorAction SilentlyContinue
            if ($null -eq $folder -or ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
            foreach ($f in @(Get-ChildItem -LiteralPath $node.Path -File -Filter 'codex*.exe' -ErrorAction SilentlyContinue)) {
                if (-not ($f.Attributes -band [IO.FileAttributes]::ReparsePoint)) { [void]$cacheFiles.Add($f.FullName) }
            }
            if ($node.Depth -lt 2) {
                foreach ($d in @(Get-ChildItem -LiteralPath $node.Path -Directory -ErrorAction SilentlyContinue)) {
                    if (-not ($d.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                        $dirs.Add([pscustomobject]@{Path=$d.FullName;Depth=($node.Depth+1)})
                    }
                }
            }
        }
    }
    $refs=@()
    if ($found.Count -gt 0) {
        $ordered=@($found | Sort-Object Version -Descending)
        $refs=@($ordered | Where-Object {$_.Version -eq $ordered[0].Version} | ForEach-Object {$_.Path})
    }
    return @{references=$refs;cache_candidates=@($cacheFiles | Sort-Object)}
}
function Write-NewTempFile([string]$Path,[byte[]]$Bytes) {
    $s=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $s.Write($Bytes,0,$Bytes.Length) } finally { $s.Dispose() }
}
try {
    if ($ForceRestore -and $Action -ne 'Restore') { throw 'ForceRestore is only valid with Restore.' }
    if ($Action -ne 'Status') { Write-Host 'NATIVE_IMAGEGEN_REGISTER (experimental)' }
    $python=$null; $prefix=@()
    foreach ($candidate in @(@{Name='py.exe';Prefix=@('-3')},@{Name='python.exe';Prefix=@()},@{Name='python3.exe';Prefix=@()})) {
        $found=Get-Command $candidate.Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $found -or $found.Source -match '\\Microsoft\\WindowsApps\\python[0-9.]*\.exe$') { continue }
        try {
            $a=@($candidate.Prefix)+@('-I','-c',"import sys; print('NATIVE_PROBE_PY_OK' if sys.version_info >= (3,11) else 'OLD')")
            $out=& $found.Source @a 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -contains 'NATIVE_PROBE_PY_OK') { $python=$found.Source; $prefix=@($candidate.Prefix); break }
        } catch {}
    }
    if ($null -eq $python) { throw 'Python 3.11+ is required. No runtimes are downloaded or installed.' }
    if ($Action -eq 'Install' -or $Action -eq 'Check') {
        $inventory=Get-BackendInventory
        if (-not [string]::IsNullOrWhiteSpace($BackendPath)) {
            if (-not (Test-Path -LiteralPath $BackendPath -PathType Leaf)) { throw 'The explicit app-bundled backend does not exist.' }
            # Explicit path is a user assertion that this is the App's CLI backend.
            $inventory.references=@((Get-Item -LiteralPath $BackendPath).FullName)
        }
        $inventoryFile=Join-Path ([IO.Path]::GetTempPath()) ('codex-backend-inventory-'+[Guid]::NewGuid().ToString('N')+'.json')
        $json=ConvertTo-Json -InputObject $inventory -Depth 5 -Compress
        Write-NewTempFile $inventoryFile ([Text.Encoding]::UTF8.GetBytes($json))
        if ($Action -eq 'Install') {
            Write-Host 'Compatibility actor header may be sent to your relay after installation; it is not an OpenAI credential.'
            Write-Host 'Backend startup and local native-tool checks must pass before configuration changes.'
        }
    }
    $payload='__PAYLOAD__'
    $inputStream=[IO.MemoryStream]::new([Convert]::FromBase64String($payload))
    $gzip=[IO.Compression.GzipStream]::new($inputStream,[IO.Compression.CompressionMode]::Decompress)
    $outputStream=[IO.MemoryStream]::new()
    try { $gzip.CopyTo($outputStream); $bytes=$outputStream.ToArray() }
    finally { $gzip.Dispose(); $inputStream.Dispose(); $outputStream.Dispose() }
    $codeFile=Join-Path ([IO.Path]::GetTempPath()) ('codex-native-register-'+[Guid]::NewGuid().ToString('N')+'.py')
    Write-NewTempFile $codeFile $bytes
    $invokeArgs=$prefix+@('-I','-X','utf8',$codeFile,$Action.ToLowerInvariant(),'--version-timeout',[string]$VersionTimeoutSeconds)
    if (-not [string]::IsNullOrWhiteSpace($CodexHome)) { $invokeArgs+=@('--home',$CodexHome) }
    if ($inventoryFile) { $invokeArgs+=@('--inventory',$inventoryFile) }
    if ($ForceRestore) { $invokeArgs+='--force-restore' }
    & $python @invokeArgs
    $exitCode=$LASTEXITCODE
} catch {
    Write-Host ('ERROR: '+$_.Exception.Message) -ForegroundColor Red
    $exitCode=1
} finally {
    foreach ($f in @($codeFile,$inventoryFile)) {
        if ($f -and (Test-Path -LiteralPath $f)) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
    }
}
exit $exitCode
