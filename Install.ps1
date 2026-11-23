param(
    [string]$Python,
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$environment = Join-Path $project '.artifact-venv'
$runtime = Join-Path $environment 'Scripts\python.exe'
$windowedRuntime = Join-Path $environment 'Scripts\pythonw.exe'

function Install-EgoIfMissing {
    param([string]$Runtime)

    & $Runtime -c 'import sys; from ego_installation import ego_cli; sys.exit(0 if ego_cli() else 1)'
    if ($LASTEXITCODE -eq 0) { return }
    if ($LASTEXITCODE -ne 1) { throw 'Could not detect Ego Lite.' }

    $url = 'https://cdn.ego.app/setup/windows/x64/egoliteSetup.exe'
    $installer = Join-Path ([IO.Path]::GetTempPath()) ('egoliteSetup-' + [guid]::NewGuid().ToString('N') + '.exe')
    Write-Host 'Downloading the official Windows Ego Lite installer...'
    try {
        Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $installer -TimeoutSec 180
        $authenticode = Get-AuthenticodeSignature -LiteralPath $installer
        $publisher = if ($authenticode.SignerCertificate) {
            $authenticode.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)
        }
        if ($authenticode.Status -ne 'Valid' -or $publisher -ne 'CITRO LABS PTE. LIMITED') {
            throw 'The Ego Lite installer did not pass the publisher signature check.'
        }
        $setup = Start-Process -FilePath $installer -PassThru -Wait
        if ($setup.ExitCode -notin @(0, 3010)) { throw "Ego Lite setup exited with code $($setup.ExitCode)." }
        & $Runtime -c 'import sys; from ego_installation import ego_cli; sys.exit(0 if ego_cli() else 1)'
        if ($LASTEXITCODE -ne 0) {
            throw 'Ego Lite is not ready yet. Finish setup (and restart Windows if requested), then run Install.ps1 again.'
        }
    } finally {
        if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue }
    }
}

if (-not (Test-Path -LiteralPath $runtime)) {
    $pythonArgs = @()
    if (-not $Python) {
        $py = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
        $native = Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue |
            Where-Object Source -NotLike '*\Microsoft\WindowsApps\*' | Select-Object -First 1
        if ($py -and $py.Source -notlike '*\Microsoft\WindowsApps\*') {
            & $py.Source -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
            if ($LASTEXITCODE -eq 0) {
                $Python = $py.Source
                $pythonArgs = @('-3')
            }
        }
        if (-not $Python -and $native) { $Python = $native.Source }
    }
    if (-not $Python) { throw 'Install Python 3.12+ from python.org, or supply -Python with the full python.exe path.' }
    & $Python @pythonArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ with Tkinter is required. You can supply -Python with its full executable path.' }
    & $Python @pythonArgs -m venv $environment
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
}

& $runtime -m pip --version
if ($LASTEXITCODE -ne 0) {
    & $runtime -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize pip.' }
}
& $runtime -m pip install -r (Join-Path $project 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the Python dependencies.' }
& $runtime -c 'import tkinter, PIL, fitz, imageio_ffmpeg, docx'
if ($LASTEXITCODE -ne 0) { throw 'The Python installation must provide Tkinter and the listed dependencies.' }

Push-Location -LiteralPath $project
try { Install-EgoIfMissing -Runtime $runtime } finally { Pop-Location }

if (-not $NoShortcut) {
    if (-not (Test-Path -LiteralPath $windowedRuntime)) { throw 'Pythonw.exe is missing from the environment.' }
    $desktop = [Environment]::GetFolderPath('Desktop')
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Gemini Codex Web.lnk'))
    $link.TargetPath = $windowedRuntime
    $link.Arguments = '"' + (Join-Path $project 'desktop_launcher.pyw') + '"'
    $link.WorkingDirectory = $project
    $link.IconLocation = (Join-Path $project 'Gemini-Codex.ico') + ',0'
    $link.Save()
    Write-Host 'Installed. Open the Gemini Codex Web desktop shortcut.'
}

New-Item -ItemType File -Path (Join-Path $environment 'gemini-codex-installed') -Force | Out-Null
