param(
  [Parameter(Mandatory=$true)][string]$RunnerPath,
  [Parameter(Mandatory=$true)][string]$PromptFile,
  [Parameter(Mandatory=$true)][string]$OutputFile,
  [Parameter(Mandatory=$true)][string]$DotenvPath,
  [Parameter(Mandatory=$true)][string]$Model,
  [Parameter(Mandatory=$true)][string]$MaxBudgetUsd
)
$ErrorActionPreference = 'Stop'
# Start-Process flattens a string array differently across Windows PowerShell
# builds.  An encoded command keeps every path and parameter boundary intact.
function Quote-PowerShell([string]$value) { "'" + $value.Replace("'", "''") + "'" }
$command = "& $(Quote-PowerShell $RunnerPath) -PromptFile $(Quote-PowerShell $PromptFile) -OutputFile $(Quote-PowerShell $OutputFile) -DotenvPath $(Quote-PowerShell $DotenvPath) -Model $(Quote-PowerShell $Model) -MaxBudgetUsd $(Quote-PowerShell $MaxBudgetUsd)"
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
$process = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -RedirectStandardOutput ($OutputFile + '.launch.stdout') -RedirectStandardError ($OutputFile + '.launch.stderr') -WindowStyle Hidden -PassThru
@{ started = $true; pid = $process.Id; output = $OutputFile } | ConvertTo-Json -Compress
