[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot
$InstallerDir = Join-Path $Root 'runtime\installers'
$PythonExe = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
$OllamaExe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
$ExpectedPython = '3.11.9'
$ExpectedOllama = '0.20.4'
$Model = 'qwen2.5:7b'

Write-Host '================================================================' -ForegroundColor DarkGray
Write-Host ' 工业机器人语音控制示例 - 一键部署' -ForegroundColor White
Write-Host '================================================================' -ForegroundColor DarkGray
Write-Host '本操作严格按安装文档准备以下组件：' -ForegroundColor Yellow
Write-Host '  1. Microsoft Visual C++ 2015-2022 x64'
Write-Host '  2. Python 3.11.9 x64 与锁定依赖'
Write-Host '  3. Ollama 0.20.4'
Write-Host '  4. qwen2.5:7b 模型（约 4.5 GB）'
Write-Host ''
Write-Host '部署需要外网、约 10 GB 可用空间，并可能触发 Windows UAC。' -ForegroundColor Yellow
$Consent = Read-Host '输入 DEPLOY 继续，其他输入取消'
if ($Consent -cne 'DEPLOY') {
    Write-Host '部署已取消。'
    exit 2
}

New-Item -ItemType Directory -Path $InstallerDir -Force | Out-Null

function Download-VerifiedInstaller {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [Parameter(Mandatory=$true)][string]$Destination,
        [Parameter(Mandatory=$true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Destination)) {
        Write-Host "下载 $Label ..." -ForegroundColor Cyan
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
    }
    $Signature = Get-AuthenticodeSignature -LiteralPath $Destination
    if ($Signature.Status -ne 'Valid') {
        throw "$Label 数字签名无效：$($Signature.Status)"
    }
    Write-Host "$Label 签名验证通过。" -ForegroundColor Green
}

function Get-CommandText {
    param([string]$FilePath, [string[]]$Arguments)
    try {
        return (& $FilePath @Arguments 2>&1 | Out-String).Trim()
    } catch {
        return ''
    }
}

$VcInstalled = $false
try {
    $Vc = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction Stop
    $VcInstalled = $Vc.Installed -eq 1
} catch {}

if (-not $VcInstalled) {
    $VcInstaller = Join-Path $InstallerDir 'VC_redist.x64.exe'
    Download-VerifiedInstaller -Url 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -Destination $VcInstaller -Label 'VC++ 运行库'
    Write-Host '安装 VC++ 运行库，需要确认 UAC ...' -ForegroundColor Cyan
    $Process = Start-Process -FilePath $VcInstaller -ArgumentList '/install','/quiet','/norestart' -Verb RunAs -Wait -PassThru
    if ($Process.ExitCode -notin @(0, 1638, 3010)) {
        throw "VC++ 安装失败，退出码 $($Process.ExitCode)"
    }
}

$PythonVersion = if (Test-Path -LiteralPath $PythonExe) { Get-CommandText $PythonExe @('--version') } else { '' }
if ($PythonVersion -notmatch [regex]::Escape($ExpectedPython)) {
    $PythonInstaller = Join-Path $InstallerDir 'python-3.11.9-amd64.exe'
    Download-VerifiedInstaller -Url 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -Destination $PythonInstaller -Label 'Python 3.11.9'
    Write-Host '安装 Python 3.11.9 x64 ...' -ForegroundColor Cyan
    $Process = Start-Process -FilePath $PythonInstaller -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_test=0','Include_launcher=1' -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "Python 安装失败，退出码 $($Process.ExitCode)"
    }
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw 'Python 3.11.9 安装后仍未找到 python.exe'
}

$InstalledOllama = if (Test-Path -LiteralPath $OllamaExe) { Get-CommandText $OllamaExe @('--version') } else { '' }
$InstallOllama = -not ($InstalledOllama -match [regex]::Escape($ExpectedOllama))
if ($InstalledOllama -and $InstallOllama) {
    Write-Host "检测到 $InstalledOllama，文档基线为 $ExpectedOllama。" -ForegroundColor Yellow
    $Choice = Read-Host '输入 BASELINE 安装文档版本；输入 KEEP 保留现有版本；其他输入取消'
    if ($Choice -ceq 'KEEP') {
        $InstallOllama = $false
    } elseif ($Choice -cne 'BASELINE') {
        Write-Host '部署已取消。'
        exit 3
    }
}

if ($InstallOllama) {
    $OllamaInstaller = Join-Path $InstallerDir 'OllamaSetup-0.20.4.exe'
    Download-VerifiedInstaller -Url 'https://github.com/ollama/ollama/releases/download/v0.20.4/OllamaSetup.exe' -Destination $OllamaInstaller -Label 'Ollama 0.20.4'
    Write-Host '安装 Ollama 0.20.4 ...' -ForegroundColor Cyan
    $Process = Start-Process -FilePath $OllamaInstaller -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "Ollama 安装失败，退出码 $($Process.ExitCode)"
    }
}
if (-not (Test-Path -LiteralPath $OllamaExe)) {
    throw 'Ollama 安装后仍未找到 ollama.exe'
}

Write-Host '安装锁定的 Python 依赖，此步骤可能需要数分钟 ...' -ForegroundColor Cyan
& $PythonExe -m pip install --disable-pip-version-check -i 'https://pypi.tuna.tsinghua.edu.cn/simple' -r (Join-Path $Root 'requirements-lock.txt')
if ($LASTEXITCODE -ne 0) {
    throw "Python 依赖安装失败，退出码 $LASTEXITCODE"
}

$OllamaReady = $false
try {
    Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3 | Out-Null
    $OllamaReady = $true
} catch {}
if (-not $OllamaReady) {
    Write-Host '启动 Ollama 本地服务 ...' -ForegroundColor Cyan
    Start-Process -FilePath $OllamaExe -ArgumentList 'serve' -WindowStyle Hidden
    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 | Out-Null
            $OllamaReady = $true
            break
        } catch {}
    }
}
if (-not $OllamaReady) {
    throw 'Ollama 服务未能在 10 秒内启动'
}

Write-Host "拉取 $Model（约 4.5 GB，请保持网络稳定）..." -ForegroundColor Cyan
& $OllamaExe pull $Model
if ($LASTEXITCODE -ne 0) {
    throw "模型拉取失败，退出码 $LASTEXITCODE"
}

Write-Host '执行只读环境检查 ...' -ForegroundColor Cyan
& $PythonExe (Join-Path $Root 'deployment_check.py')
if ($LASTEXITCODE -ne 0) {
    Write-Host '环境检查仍有 FAIL 项，已停止自动启动。请查看 deployment_report.json。' -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host '部署完成，正在启动工业机器人语音控制示例。' -ForegroundColor Green
& (Join-Path $Root '启动语音助手.bat')
exit $LASTEXITCODE
