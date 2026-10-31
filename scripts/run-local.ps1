<#
.SYNOPSIS
    Prepares the local environment for bank-transactions (does not import anything).

.DESCRIPTION
    Starts (or reuses) a PostgreSQL 16 Docker container, creates the cipher key once (kept in
    the git-ignored .env.local so stored data stays readable), installs the package into .venv,
    applies the schema, and saves BT_DSN / BT_CIPHER_KEY as user environment variables and
    .venv\Scripts on the user PATH. Afterwards run `bank-transactions ...` from any new terminal.

.EXAMPLE
    .\scripts\run-local.ps1
    .\scripts\run-local.ps1 -Reset     # drops the container and its data
    .\scripts\run-local.ps1 -Cleanup   # removes the user env variables and PATH entry
#>
[CmdletBinding()]
param(
    [int]$Port = 5432,
    [switch]$Reset,
    [switch]$Cleanup
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$container = 'bank-transactions-db'
$volume = 'bank-transactions-pgdata'
$dbName = 'bank_transactions'
$dbUser = 'bt'
$envFile = Join-Path $root '.env.local'
$python = Join-Path $root '.venv\Scripts\python.exe'

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

function New-Secret {
    param([int]$Bytes)
    $buffer = [byte[]]::new($Bytes)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    [Convert]::ToBase64String($buffer)
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'docker was not found on PATH.' }
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Docker is not running. Start Docker Desktop and retry.' }

if ($Cleanup) {
    [Environment]::SetEnvironmentVariable('BT_DSN', $null, 'User')
    [Environment]::SetEnvironmentVariable('BT_CIPHER_KEY', $null, 'User')
    $scriptsDir = Join-Path $root '.venv\Scripts'
    $kept = ([Environment]::GetEnvironmentVariable('Path', 'User') -split ';') | Where-Object { $_ -and $_ -ne $scriptsDir }
    [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
    Write-Host 'Removed BT_DSN, BT_CIPHER_KEY and the PATH entry from the user environment.'
    return
}

if ($Reset) {
    docker rm -f $container *> $null
    docker volume rm $volume *> $null
    $LASTEXITCODE = 0
    Write-Host "Removed container and database volume. $envFile kept (cipher key)."
}

# Secrets live in .env.local (ignored by git); generated on first run and reused afterwards.
$settings = @{}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)$') { $settings[$Matches[1]] = $Matches[2] }
    }
}
$changed = $false
if (-not $settings['BT_DB_PASSWORD']) {
    # URL-safe so it can be embedded in the DSN unescaped.
    $settings['BT_DB_PASSWORD'] = (New-Secret 24) -replace '[+/=]', 'x'
    $changed = $true
}
if (-not $settings['BT_CIPHER_KEY']) {
    $settings['BT_CIPHER_KEY'] = New-Secret 64
    $changed = $true
}
if ($changed) {
    $settings.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" } | Set-Content $envFile -Encoding ascii
    Write-Host "Generated secrets in $envFile (git-ignored; keep it, the cipher key cannot be recovered)."
}

# Database container
$existing = docker ps -a --filter "name=^/$container$" --format '{{.Status}}'
if (-not $existing) {
    Write-Host "Creating PostgreSQL container '$container' on port $Port..."
    Invoke-Native docker @('run', '-d', '--name', $container,
        '-e', "POSTGRES_USER=$dbUser",
        '-e', "POSTGRES_PASSWORD=$($settings['BT_DB_PASSWORD'])",
        '-e', "POSTGRES_DB=$dbName",
        '-p', "127.0.0.1:${Port}:5432",
        '-v', "${volume}:/var/lib/postgresql/data",
        'postgres:16')
} elseif ($existing -notmatch '^Up') {
    Write-Host "Starting existing container '$container'..."
    Invoke-Native docker @('start', $container)
}

Write-Host 'Waiting for PostgreSQL...'
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    docker exec $container pg_isready -U $dbUser -d $dbName *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw 'PostgreSQL did not become ready within 60 seconds.' }

# Python environment
if (-not (Test-Path $python)) {
    Write-Host 'Creating .venv...'
    Invoke-Native py @('-m', 'venv', (Join-Path $root '.venv'))
}
& $python -c 'import bank_transactions' *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing bank-transactions...'
    Invoke-Native $python @('-m', 'pip', 'install', '-e', "$root[dev]")
}

# Settings for this process and for every new terminal
$env:BT_DSN = "postgresql+psycopg://${dbUser}:$($settings['BT_DB_PASSWORD'])@127.0.0.1:${Port}/${dbName}"
$env:BT_CIPHER_KEY = $settings['BT_CIPHER_KEY']
[Environment]::SetEnvironmentVariable('BT_DSN', $env:BT_DSN, 'User')
[Environment]::SetEnvironmentVariable('BT_CIPHER_KEY', $env:BT_CIPHER_KEY, 'User')

# Put bank-transactions.exe on the user PATH
$scripts = Join-Path $root '.venv\Scripts'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $scripts)) {
    [Environment]::SetEnvironmentVariable('Path', ((@($userPath, $scripts) | Where-Object { $_ }) -join ';'), 'User')
}
if (-not (($env:Path -split ';') -contains $scripts)) { $env:Path += ";$scripts" }

Write-Host 'Applying schema...'
Invoke-Native $python @('-m', 'bank_transactions', 'init-db')

Write-Host @'

Environment ready. Open a NEW terminal, then run from any folder, e.g.:
  bank-transactions import C:\path\statement.xlsx --dry-run
  bank-transactions import C:\path\statement.xlsx
'@
