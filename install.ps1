<#
hub-desk installer. Run from the unzipped folder (install.cmd does that).
  1. finds Python 3.11+            4. checks the CLIs and tools hub-desk uses (report only, installs nothing else)
  2. creates .venv in this folder  5. creates the desktop shortcut (skip with -NoShortcut)
  3. installs requirements.txt and runs the self-tests (skip tests with -NoTests)
Writes only inside this folder, plus the one shortcut on the desktop.
#>
param([switch]$NoShortcut, [switch]$NoTests)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
function Say($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }

Say "hub-desk 설치: $here" 'Cyan'
if ($env:OS -ne 'Windows_NT') { Say 'hub-desk는 Windows 전용입니다.' 'Red'; exit 1 }

# 1. Python 3.11+ (tomllib); py launcher first, then python on PATH
$py = $null
foreach ($cand in @(@('py', '-3'), @('python'))) {
    try {
        $v = & $cand[0] $cand[1..9] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$v -ge [version]'3.11') { $py = $cand; break }
    } catch { }
}
if (-not $py) {
    Say 'Python 3.11 이상을 찾지 못했습니다. https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행하세요.' 'Red'
    Say '(설치할 때 "py launcher"와 "Add python.exe to PATH"를 켜 두세요.)'
    exit 1
}
Say "Python $v ($($py -join ' '))"

# 2-3. virtual environment and packages
$venvPy = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Say '가상환경 만드는 중 (.venv)...'
    & $py[0] $py[1..9] -m venv .venv
    if ($LASTEXITCODE) { Say '가상환경을 만들지 못했습니다.' 'Red'; exit 1 }
}
Say '패키지 설치 중 (requirements.txt)...'
& $venvPy -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE) {
    Say '패키지를 설치하지 못했습니다. 인터넷 연결을 확인하세요. 이 Python 버전에 맞는 pywinpty 배포판이 없을 수도 있습니다.' 'Red'
    exit 1
}
if (-not $NoTests) {
    Say '자체 테스트 실행 중...'
    $env:PYTHONPATH = $here
    & $venvPy -m pytest tests -q -p no:cacheprovider 2>&1 | Select-Object -Last 3 | ForEach-Object { Say "  $_" }
    if ($LASTEXITCODE) { Say '테스트가 실패했습니다. 위 내용을 확인하세요 (설치는 끝났습니다).' 'Yellow' } else { Say '테스트 통과' 'Green' }
}

# 4. what hub-desk can use on this PC (nothing is installed or changed here)
Say ''
Say '이 PC에서 쓸 수 있는 것:' 'Cyan'
$checks = @(
    @{ n = 'claude'; what = 'Claude Code CLI (Claude 에이전트)'; how = 'https://docs.claude.com/en/docs/claude-code 설치 후 claude 실행해 로그인' },
    @{ n = 'codex'; what = 'Codex CLI (Codex 에이전트)'; how = 'https://github.com/openai/codex 설치 후 codex 실행해 로그인' },
    @{ n = 'git'; what = 'git (빌더 작업 사본)'; how = 'https://git-scm.com/download/win' },
    @{ n = 'npx'; what = 'Node.js (브라우저 도구 Playwright)'; how = 'https://nodejs.org/' }
)
$haveCli = $false
foreach ($c in $checks) {
    $p = Get-Command $c.n -ErrorAction SilentlyContinue
    if ($p) { Say ("  [있음] {0}" -f $c.what) 'Green'; if ($c.n -in 'claude', 'codex') { $haveCli = $true } }
    else { Say ("  [없음] {0} -> {1}" -f $c.what, $c.how) 'Yellow' }
}
if (Test-Path (Join-Path $env:USERPROFILE '.codex\AGENTS.md')) { Say '  [있음] 전역 하네스 ~/.codex/AGENTS.md' 'Green' }
else { Say '  [없음] 전역 하네스 ~/.codex/AGENTS.md (선택: 프로젝트 폴더의 AGENTS.md를 대신 씁니다)' 'Gray' }
if (-not $haveCli) { Say '  Claude Code나 Codex 중 하나는 있어야 에이전트를 띄울 수 있습니다.' 'Red' }

# 5. desktop shortcut
if (-not $NoShortcut) {
    $desk = [Environment]::GetFolderPath('Desktop')
    $lnk = Join-Path $desk 'hub-desk.lnk'
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $here 'hub-desk.cmd'
    $s.WorkingDirectory = $here
    $s.IconLocation = (Join-Path $here 'hub_desk\web\hub-desk.ico') + ',0'
    $s.WindowStyle = 7
    $s.Description = 'hub-desk: Claude Code / Codex 에이전트 팀 대시보드'
    $s.Save()
    Say ''
    Say "바탕화면 바로가기: $lnk" 'Green'
}
Say ''
Say '설치 끝. 바탕화면의 hub-desk 아이콘이나 hub-desk.cmd로 실행하세요. 남은 설정은 INSTALL.md를 보세요.' 'Cyan'
