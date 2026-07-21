$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"

$ProjectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$OutputRoot = Join-Path $ProjectRoot "BAN_PHAN_PHOI"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BuildRoot = Join-Path $OutputRoot ("_build_" + $Stamp)
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$SpecRoot = Join-Path $BuildRoot "spec"
$PackageName = "VOICE_11_LABS_TAILEMMO"
$PackageDir = Join-Path $DistRoot $PackageName
$ZipPath = Join-Path $OutputRoot ($PackageName + "_" + $Stamp + ".zip")

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Chưa có môi trường .venv. Hãy chạy CAI_DAT.bat trước."
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null

Write-Host "[1/4] Cài/kiểm tra PyInstaller..."
& $Python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    & $Python -m pip install --progress-bar off PyInstaller
    if ($LASTEXITCODE -ne 0) { throw "Không cài được PyInstaller." }
}
& $Python -c "import imageio_ffmpeg"
if ($LASTEXITCODE -ne 0) {
    & $Python -m pip install --progress-bar off "imageio-ffmpeg>=0.6.0,<1"
    if ($LASTEXITCODE -ne 0) { throw "Không cài được imageio-ffmpeg để ghép audio." }
}

Write-Host "[2/4] Đóng gói ứng dụng Windows..."
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name $PackageName `
    --icon (Join-Path $ProjectRoot "assets\tailemmo_icon.ico") `
    --add-data ((Join-Path $ProjectRoot "assets") + ";assets") `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    --specpath $SpecRoot `
    --collect-all certifi `
    --collect-all imageio_ffmpeg `
    (Join-Path $ProjectRoot "app.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller đóng gói thất bại." }

Write-Host "[3/4] Tạo cấu trúc bản phân phối sạch..."
New-Item -ItemType Directory -Path (Join-Path $PackageDir "config") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $PackageDir "outputs") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot "config\settings.example.json") -Destination (Join-Path $PackageDir "config\settings.example.json")
Copy-Item -LiteralPath (Join-Path $ProjectRoot "HUONG_DAN_KHACH_HANG.txt") -Destination (Join-Path $PackageDir "HUONG_DAN_KHACH_HANG.txt")

$Forbidden = @("settings.json", "private_seed.key")
foreach ($Name in $Forbidden) {
    if (Get-ChildItem -LiteralPath $PackageDir -Recurse -Force -File | Where-Object { $_.Name -ieq $Name }) {
        throw "Phát hiện file cấm trong bản phân phối: $Name"
    }
}

Write-Host "[4/4] Nén file phân phối..."
Compress-Archive -LiteralPath $PackageDir -DestinationPath $ZipPath -CompressionLevel Optimal

$ResolvedOutput = [System.IO.Path]::GetFullPath($OutputRoot)
$ResolvedBuild = [System.IO.Path]::GetFullPath($BuildRoot)
if ($ResolvedBuild.StartsWith($ResolvedOutput + [System.IO.Path]::DirectorySeparatorChar) -and (Test-Path -LiteralPath $ResolvedBuild)) {
    Remove-Item -LiteralPath $ResolvedBuild -Recurse -Force
}

Write-Host ""
Write-Host "HOÀN TẤT: $ZipPath" -ForegroundColor Green
Start-Process explorer.exe -ArgumentList "/select,`"$ZipPath`""
