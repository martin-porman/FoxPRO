param(
  [Parameter(Mandatory=$true)][string]$PromptFile,
  [Parameter(Mandatory=$true)][string]$OutputFile,
  [Parameter(Mandatory=$true)][string]$DotenvPath,
  [Parameter(Mandatory=$true)][string]$Model,
  [Parameter(Mandatory=$true)][string]$MaxBudgetUsd
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $DotenvPath)) { throw "VM dotenv file not found: $DotenvPath" }
Get-Content -LiteralPath $DotenvPath | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
    $parts = $line.Split('=', 2)
    $name = $parts[0].Trim()
    $value = $parts[1].Trim().Trim('"').Trim("'")
    if ($name -in @('ZAI_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL','ANTHROPIC_DEFAULT_HAIKU_MODEL','ANTHROPIC_DEFAULT_SONNET_MODEL','ANTHROPIC_DEFAULT_OPUS_MODEL','API_TIMEOUT_MS','CLAUDE_CODE_AUTO_COMPACT_WINDOW','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC')) {
      Set-Item -Path ("Env:" + $name) -Value $value
    }
  }
}
if (-not $env:ANTHROPIC_AUTH_TOKEN -and -not $env:ZAI_API_KEY) {
  $bareToken = Get-Content -LiteralPath $DotenvPath | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') -and -not $_.Contains('=') } | Select-Object -First 1
  if ($bareToken) { $env:ZAI_API_KEY = $bareToken.Trim('"').Trim("'") }
}
if (-not $env:ANTHROPIC_AUTH_TOKEN -and $env:ZAI_API_KEY) { $env:ANTHROPIC_AUTH_TOKEN = $env:ZAI_API_KEY }
if (-not $env:ANTHROPIC_BASE_URL) { $env:ANTHROPIC_BASE_URL = 'https://api.z.ai/api/anthropic' }
if (-not $env:ANTHROPIC_AUTH_TOKEN) { throw 'VM dotenv must define ZAI_API_KEY or ANTHROPIC_AUTH_TOKEN' }
if (-not (Get-Command claude.cmd -ErrorAction SilentlyContinue) -and -not (Get-Command claude -ErrorAction SilentlyContinue)) { throw 'Claude Code is not installed in the VM user account' }
$prompt = Get-Content -LiteralPath $PromptFile -Raw
$claudeModel = if ($Model -eq 'glm-5.3-flash') { 'sonnet' } else { $Model }
try {
  $prompt | & claude.cmd -p --no-session-persistence --output-format json --model $claudeModel --max-budget-usd $MaxBudgetUsd | Set-Content -LiteralPath $OutputFile -Encoding utf8
  if ($LASTEXITCODE -ne 0) { throw "Claude Code failed with exit code $LASTEXITCODE" }
} finally {
  Remove-Item -LiteralPath $PromptFile -Force -ErrorAction SilentlyContinue
}
