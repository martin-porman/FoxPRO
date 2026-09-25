param(
  [Parameter(Mandatory=$true)][string]$DotenvPath
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $DotenvPath)) { throw "VM dotenv file not found: $DotenvPath" }
$lines = Get-Content -LiteralPath $DotenvPath | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') }
$line = $lines | Where-Object { $_ -match '^\s*ZAI_API_KEY\s*=' } | Select-Object -First 1
if ($line) {
  $token = $line.Split('=', 2)[1].Trim().Trim('"').Trim("'")
} else {
  $token = $lines | Where-Object { -not $_.Contains('=') } | Select-Object -First 1
}
if (-not $token) { throw 'ZAI_API_KEY is empty' }
& npx --yes @z_ai/coding-helper auth glm_coding_plan_global $token
if ($LASTEXITCODE -ne 0) { throw "Z.ai helper auth failed with exit code $LASTEXITCODE" }
& npx --yes @z_ai/coding-helper auth reload claude
if ($LASTEXITCODE -ne 0) { throw "Z.ai helper Claude reload failed with exit code $LASTEXITCODE" }
& npx --yes @z_ai/coding-helper doctor
if ($LASTEXITCODE -ne 0) { throw "Z.ai helper doctor failed with exit code $LASTEXITCODE" }
