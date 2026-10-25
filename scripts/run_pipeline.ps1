param(
    [Parameter(Mandatory = $true)]
    [int]$McpPort,
    [string]$Config = (Join-Path $PSScriptRoot 'pipeline_models.example.json'),
    [string]$Results = (Join-Path (Split-Path $PSScriptRoot -Parent) 'results'),
    [int]$SliceTimeout = 180,
    [int]$HttpTimeout = 120,
    [switch]$Overwrite
)

$ErrorActionPreference = 'Stop'
$token = Read-Host 'Вставьте MCP-токен из вкладки Настройки AI Assistant'
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'MCP-токен не указан.'
}
$env:STE_MCP_TOKEN = $token
$env:NO_PROXY = '127.0.0.1,localhost'
$env:no_proxy = '127.0.0.1,localhost'
try {
    $arguments = @(
        '-3.12',
        (Join-Path $PSScriptRoot 'pipeline_runner.py'),
        '--url', "http://127.0.0.1:$McpPort/mcp",
        '--config', $Config,
        '--results', $Results,
        '--slice-timeout', $SliceTimeout,
        '--http-timeout', $HttpTimeout
    )
    if ($Overwrite) {
        $arguments += '--overwrite'
    }
    & py @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Pipeline завершился с кодом $LASTEXITCODE. См. pipeline.log."
    }
}
finally {
    Remove-Item Env:STE_MCP_TOKEN -ErrorAction SilentlyContinue
}
