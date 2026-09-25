param(
  [Parameter(Mandatory=$true)][string]$RunnerPath,
  [Parameter(Mandatory=$true)][string]$PromptFile,
  [Parameter(Mandatory=$true)][string]$OutputFile,
  [Parameter(Mandatory=$true)][string]$DotenvPath,
  [Parameter(Mandatory=$true)][string]$Model,
  [Parameter(Mandatory=$true)][string]$MaxBudgetUsd
)
$ErrorActionPreference = 'Stop'
$arguments = @(
  '-NoProfile',
  '-ExecutionPolicy', 'Bypass',
  '-File', $RunnerPath,
  '-PromptFile', $PromptFile,
  '-OutputFile', $OutputFile,
  '-DotenvPath', $DotenvPath,
  '-Model', $Model,
  '-MaxBudgetUsd', $MaxBudgetUsd
)
Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WindowStyle Hidden
@{ started = $true; output = $OutputFile } | ConvertTo-Json -Compress
