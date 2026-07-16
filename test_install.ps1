# 避免进度条开销；PowerShell 7+ 中 -UseBasicParsing 已弃用并会输出警告
$ProgressPreference = 'SilentlyContinue'

# 仅在 Windows PowerShell 5.1 及以下才需要 -UseBasicParsing（用于绕过 IE 解析引擎）
$basicParsing = @{}
if ($PSVersionTable.PSVersion.Major -le 5 -and $PSVersionTable.PSEdition -eq 'Desktop') {
    $basicParsing = @{ UseBasicParsing = $true }
}

function Invoke-QRequest {
    param(
        [string]$Uri,
        [string]$Method = 'GET'
    )
    (Invoke-WebRequest -Uri $Uri -Method $Method @basicParsing).Content
}

$resp = Invoke-QRequest -Uri http://127.0.0.1:5000/api/install_tools -Method POST
Write-Host ("start=" + $resp)
$deadline = (Get-Date).AddSeconds(280)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    try {
        $s = (Invoke-QRequest -Uri http://127.0.0.1:5000/api/install_status) | ConvertFrom-Json
        Write-Host ((Get-Date -UFormat '%H:%M:%S') + " progress=" + $s.progress + "% step=" + $s.step + " running=" + $s.running + " done=" + $s.done + " err=" + $s.error)
        if ($s.done -or $s.error) { break }
    } catch {
        Write-Host ("poll_err=" + $_.Exception.Message)
    }
}
Write-Host ("FINAL tools=" + (Invoke-QRequest -Uri http://127.0.0.1:5000/api/tools))
