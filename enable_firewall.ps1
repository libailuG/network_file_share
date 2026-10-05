#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$shareConfig = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
$sharePort = [int]$shareConfig.port
if ($sharePort -lt 1 -or $sharePort -gt 65535) { throw 'Invalid port in config.json' }
$sharePrograms = @{
    'LANFileShare-Qt-Exe' = (Join-Path $PSScriptRoot 'LAN_File_Share.exe')
}
foreach ($shareRuleName in $sharePrograms.Keys) {
    $shareExisting = Get-NetFirewallRule -Name $shareRuleName -ErrorAction SilentlyContinue
    $shareParameters = @{
        Direction = 'Inbound'
        Action = 'Allow'
        Enabled = 'True'
        Profile = @('Private', 'Public')
        Protocol = 'TCP'
        LocalPort = $sharePort
        RemoteAddress = 'LocalSubnet'
        Program = $sharePrograms[$shareRuleName]
    }
    if ($shareExisting) {
        Set-NetFirewallRule -Name $shareRuleName @shareParameters | Out-Null
    } else {
        New-NetFirewallRule -Name $shareRuleName -DisplayName $shareRuleName @shareParameters | Out-Null
    }
}
Write-Host "LAN file share firewall rules enabled for TCP $sharePort (local subnet only)."
