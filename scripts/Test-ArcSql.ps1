param(
    [Parameter(Mandatory = $true)][string]$SqlServer,
    [Parameter(Mandatory = $true)][string]$Database
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Data

$endpoint = 'http://localhost:40342/metadata/identity/oauth2/token' +
    '?api-version=2020-06-01&resource=https%3A%2F%2Fdatabase.windows.net%2F'
$connection = $null
$token = $null
$challenge = $null
try {
    $challengePath = $null
    try {
        Invoke-WebRequest -Uri $endpoint -Headers @{Metadata='true'} `
            -UseBasicParsing -TimeoutSec 30 -Proxy $null | Out-Null
        throw 'Expected an Arc challenge, not an unauthenticated success.'
    } catch {
        $response = $_.Exception.Response
        if (-not $response -or [int]$response.StatusCode -ne 401) { throw }
        $header = $response.Headers['WWW-Authenticate']
        if ($header -notmatch '^Basic realm=(.+)$') { throw 'Unexpected challenge.' }
        $challengePath = [IO.Path]::GetFullPath($Matches[1].Trim('"'))
        $root = Join-Path $env:ProgramData 'AzureConnectedMachineAgent\Tokens\'
        if (-not $challengePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Challenge path is outside the documented Arc token directory.'
        }
    }
    $challenge = (Get-Content -LiteralPath $challengePath -Raw).Trim()
    $reply = Invoke-RestMethod -Uri $endpoint `
        -Headers @{Metadata='true'; Authorization="Basic $challenge"} `
        -TimeoutSec 30 -Proxy $null
    $challenge = $null
    $token = $reply.access_token
    $reply = $null
    if (-not $token) { throw 'Arc did not return a token.' }

    $part = $token.Split('.')[1].Replace('-', '+').Replace('_', '/')
    $part = $part.PadRight($part.Length + ((4 - $part.Length % 4) % 4), '=')
    $claims = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String($part)) | ConvertFrom-Json
    if ($claims.aud.TrimEnd('/') -ne 'https://database.windows.net') {
        throw 'Arc returned a token for an unexpected audience.'
    }

    $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
    $builder['Data Source'] = "tcp:$SqlServer,1433"
    $builder['Initial Catalog'] = $Database
    $builder['Encrypt'] = $true
    $builder['TrustServerCertificate'] = $false
    $builder['Connect Timeout'] = 30
    $builder['Pooling'] = $false
    $connection = New-Object System.Data.SqlClient.SqlConnection
    $connection.ConnectionString = $builder.ConnectionString
    $connection.AccessToken = $token
    $token = $null

    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try { $connection.Open(); break }
        catch [System.Data.SqlClient.SqlException] {
            if ($_.Exception.Number -notin @(40613,40197,40501,10928,10929,49918,49919,49920) -or
                $attempt -eq 4) { throw }
            Write-Warning "Transient SQL error $($_.Exception.Number), attempt $attempt/4."
            Start-Sleep -Seconds 15
        }
    }

    function Read-Query([string]$sql) {
        $command = $connection.CreateCommand()
        $command.CommandText = $sql
        $command.CommandTimeout = 30
        $reader = $null
        try {
            $reader = $command.ExecuteReader()
            $rows = @()
            while ($reader.Read()) {
                $row = [ordered]@{}
                for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                    $row[$reader.GetName($i)] = $reader.GetValue($i)
                }
                $rows += [pscustomobject]$row
            }
            return $rows
        } finally {
            if ($reader) { $reader.Dispose() }
            $command.Dispose()
        }
    }

    $identity = @(Read-Query 'SELECT ORIGINAL_LOGIN() AS originalLogin,
        USER_NAME() AS userName, DB_NAME() AS databaseName;')[0]
    if ($identity.databaseName -ne $Database) { throw 'Unexpected database.' }
    $marker = @(Read-Query 'SELECT id, marker FROM dbo.AuthProbe WHERE id=1;')
    if ($marker.Count -ne 1 -or $marker[0].marker -ne 'passwordless-verified') {
        throw 'Unexpected marker result.'
    }
    $permissions = @(Read-Query "SELECT
        HAS_PERMS_BY_NAME(N'dbo.AuthProbe', N'OBJECT', N'SELECT') AS canSelect,
        HAS_PERMS_BY_NAME(N'dbo.AuthProbe', N'OBJECT', N'INSERT') AS canInsert,
        HAS_PERMS_BY_NAME(N'dbo.AuthProbe', N'OBJECT', N'UPDATE') AS canUpdate,
        HAS_PERMS_BY_NAME(N'dbo.AuthProbe', N'OBJECT', N'DELETE') AS canDelete,
        IS_ROLEMEMBER(N'db_owner') AS dbOwner,
        IS_ROLEMEMBER(N'db_datareader') AS dbDataReader;")[0]
    if ($permissions.canSelect -ne 1 -or $permissions.canInsert -ne 0 -or
        $permissions.canUpdate -ne 0 -or $permissions.canDelete -ne 0 -or
        $permissions.dbOwner -ne 0 -or $permissions.dbDataReader -ne 0) {
        throw 'Effective permissions do not match the approved table SELECT.'
    }
    @{
        result='ARC_SQL_SIMPLIFIED_QUERY_SUCCEEDED'
        runtimeConfiguration=@('SqlServer','Database')
        identity=$identity
        marker=$marker[0].marker
        rowCount=$marker.Count
        permissions=$permissions
        tokenEndpoint='http://localhost:40342/metadata/identity/oauth2/token'
        tokenIdentity=@{
            audience=$claims.aud
            tenantId=$claims.tid
            principalId=$claims.oid
            clientId=$(if ($claims.appid) { $claims.appid } else { $claims.azp })
        }
        authentication='explicit Arc endpoint token only; SqlConnection.AccessToken'
        fallback='none'
    } | ConvertTo-Json -Depth 5 -Compress
} finally {
    if ($connection) { $connection.Dispose() }
    $token = $null
    $challenge = $null
}
