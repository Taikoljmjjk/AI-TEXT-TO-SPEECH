$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDir

Add-Type -AssemblyName PresentationFramework

function Show-Error([string] $Message) {
    [System.Windows.MessageBox]::Show(
        $Message,
        'TOOL CLONE GIỌNG TÀI LÊ MMO',
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
}

try {
    $VenvPython = Join-Path $ProjectDir '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        $Python = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $Python) {
            $Python = Get-Command py.exe -ErrorAction SilentlyContinue
            if (-not $Python) { throw 'Không tìm thấy Python 3.10 trở lên trên máy.' }
            & $Python.Source -3 -m venv '.venv'
        } else {
            & $Python.Source -m venv '.venv'
        }
        if ($LASTEXITCODE -ne 0) { throw 'Không thể tạo môi trường Python.' }
        & $VenvPython -m pip install -r 'requirements.txt'
        if ($LASTEXITCODE -ne 0) { throw 'Không thể cài thư viện.' }
    }
    $Pythonw = Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'
    Start-Process -FilePath $Pythonw -ArgumentList 'main.py' -WorkingDirectory $ProjectDir
} catch {
    Show-Error ("Không thể khởi động ứng dụng.`n`n" + $_.Exception.Message)
    exit 1
}
