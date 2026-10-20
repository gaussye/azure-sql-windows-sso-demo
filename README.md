# Windows 当前用户静默 SSO 访问 Azure SQL

本指南记录 **2026-10-20 实际验证成功**的方案：已经登录 Windows 的人类用户，通过 Windows Web Account Manager（WAM）获取自己的 Azure SQL 委托访问令牌，再由 `Microsoft.Data.SqlClient` 连接数据库。**执行查询时不输入 SQL 密码、不执行 `az login`，不弹浏览器，也不触发交互式 MFA。**

“静默”有严格边界：用户此前已经完成 Windows 登录，设备、账户、应用同意和组织策略均满足要求；若需要重新登录、MFA 或同意，示例会明确失败，**不会弹窗或切换身份继续连接**。这不是绕过 MFA，也不保证任何条件下都能静默。

Azure VM 在实验中只用于模拟一台 Entra joined 的 Windows 桌面。**普通本地 Windows 电脑也可以采用此模式，不要求运行在 Azure，不使用 Managed Identity。** 本仓库只提供文档；下面的配置命令需由读者在自己的环境中审阅后执行。

## 1. 已验证什么，没有验证什么

| 项目 | 实测结论 |
| --- | --- |
| 客户端 | Windows 11 Pro 25H2；.NET SDK 8.0.426；目标 `net8.0-windows` |
| 依赖 | `Microsoft.Data.SqlClient` 6.1.4、`Microsoft.Identity.Client.Broker` 4.77.0；在本地及 VM 上编译成功 |
| 用户类型 | Entra 云端托管用户（cloud-managed user），不是联合身份用户，也不是托管身份 |
| Windows 状态 | 在实际桌面用户上下文中检查到 `AzureAdJoined : YES`、`AzureAdPrt : YES` |
| 传统 Integrated | `Authentication=Active Directory Integrated` 失败，错误为 `integrated_windows_auth_not_supported_managed_user` |
| WAM | 两个独立、新启动的控制台进程均成功；`TokenSource == Broker`，`interactiveFallback=false` |
| SQL 结果 | 连接显式指定的数据库 `auth-test`，读取标记 `passwordless-verified`；`ORIGINAL_LOGIN()` 对应实际 Entra 用户 |
| SQL 身份边界 | 实测用户原本就是逻辑服务器的 Entra 管理员，因此 `USER_NAME()` 为 `dbo`；这不是普通用户应获得的权限 |
| 排除的凭据来源 | 未传入 Azure CLI 凭据、Managed Identity 或令牌环境变量；未实现自定义持久化应用令牌缓存 |

两个新进程成功，证明此配置下不依赖单一进程内的 MSAL 缓存；**不意味着没有缓存**，WAM/Windows 自己维护账户、登录会话及令牌状态。`Broker` 也不等于每次都发生新的网络认证。

此结果证明的是 **支持 WAM 的客户端实现**。没有据此证明任意版本的 `sqlcmd -G`、ODBC、SSMS 或其他既有查询工具，都能对该云端用户静默 SSO。

## 2. WAM、IWA 和 Managed Identity 的区别

| 方式 | 使用谁的身份 | 适用边界 |
| --- | --- | --- |
| MSAL Integrated Windows Authentication（IWA）／SqlClient Active Directory Integrated | 人类用户 | 经典 MSAL IWA 面向正确配置的联合身份场景；不支持本次云端托管用户，不能仅凭 PRT 存在推断一定可用 |
| WAM + `AcquireTokenSilent` | 当前 Windows OS 账户 | 借助 Windows broker/PRT 和已有登录状态；需要合适的交互式用户上下文、应用配置、同意与策略 |
| Managed Identity（MI） | Azure 资源／工作负载身份 | 适合服务与后台任务；SQL 看到的不是当前本地人类用户，不是本指南目标 |
| 交互式认证 | 人类用户 | 可能弹窗、浏览器或 MFA；可用于独立的首次初始化，但不能当作静默验证成功 |
| `DefaultAzureCredential` | 取决于选中的凭据 | 可能使用 CLI、环境变量或 MI；成功不足以证明当前 Windows 用户 WAM SSO |

Entra 的 **managed user（云端托管用户）**与 **Managed Identity（托管身份）**完全不同。微软的经典 IWA 文档已标注弃用并推荐 WAM；应依据实际客户端实现判断，不将所有名为“Integrated”的选项视为 WAM。

```text
已完成的 Windows 用户登录
          |
          v
Windows 账户 / PRT / WAM broker
          |
          | 公共客户端 + 已批准的委托权限
          v
Azure SQL 用户访问令牌
          |
          | SQL FQDN、网络可达、TLS 证书校验
          v
Azure SQL 验证令牌
          |
          | 数据库 contained Entra 用户 / 组及 SQL 权限
          v
只执行被授权的查询
```

应用同意、令牌获取、网络访问和数据库授权是四个不同的关卡。**授予应用同意不会创建数据库用户，也不会增加任何 SQL 角色。**

## 3. 前提与操作角色

1. 一台已登录目标 Entra 用户的 Windows 电脑。本文复现基线是 Entra joined 且有有效 PRT 的 Windows 11；其他加入方式和跨租户来宾场景需另外验证。
2. .NET 8 SDK，能够访问 NuGet 进行一次性构建；运行时能访问组织允许的 Entra/WAM 端点与 SQL 网络路径。
3. 目标租户中的单租户公共客户端应用，无客户端密码或证书；Azure SQL 委托权限和目标用户的预先同意已完成。
4. 已启用 Entra 认证、配置 Entra 管理员的 Azure SQL 逻辑服务器，以及显式目标数据库。
5. 目标用户或其 Entra 组已有适当数据库权限；当前网络能到达 SQL 服务。

| 操作角色 | 负责事项 |
| --- | --- |
| 租户应用／同意管理员 | 应用注册、服务主体和经批准的单用户委托授权；遵守租户同意策略 |
| Azure／网络管理员 | SQL Entra 管理员、DNS、Private Endpoint 或精确公共防火墙规则 |
| SQL 管理员 | 建立数据库用户／组、演示对象及最小 SQL 权限 |
| 普通使用者 | 在自己的 Windows 桌面运行已配置客户端，不需要 Azure CLI 登录 |

下面的 Azure CLI／Graph 命令是**一次性的管理面配置**，需要执行者已具备适当权限。配置阶段可以由管理员显式登录；这与查询阶段不需要 `az login` 不矛盾。不要为了使用应用给普通用户授予目录管理员权限。

示例面向 Azure 公有云，使用 `login.microsoftonline.com`、`graph.microsoft.com` 和 `database.windows.net`；其他云环境的 authority、资源标识、SQL 域名与可用性需按其官方文档调整，不能只换服务器名。

## 4. 变量和标识符

全部示例使用占位符；GUID、UPN、服务器名应替换为自己的值，**不要将真实环境清单、令牌、密码或原始测试结果提交到仓库**。

| 名称 | 含义 |
| --- | --- |
| `TENANT_ID` | 目标 Entra 租户 GUID；应用与本例数据库身份所在租户 |
| `CLIENT_ID` | 应用的 Application (client) ID，传给 MSAL，不是对象 ID |
| `APP_OBJECT_ID` | 应用注册对象的 `id`，用于修改应用 |
| `CLIENT_SP_OBJECT_ID` | 此客户端在目标租户中的 Enterprise application／service principal 对象 `id` |
| `USER_OBJECT_ID` | 被授权用户在目标租户中的对象 `id`；不是 UPN |
| `SQL_RESOURCE_SP_OBJECT_ID` | Azure SQL 资源服务主体在**目标租户内**的对象 `id`，必须查找，不能复制其他租户的值 |
| `SQL_SERVER_FQDN` | 如 `your-server.database.windows.net`；不包含 `tcp:`、端口或数据库名 |
| `SQL_DATABASE` | 要连接的实际数据库名，如 `auth-test` |
| `USER_UPN` | 如 `reader@contoso.example`；仅为虚构展示值 |

Azure SQL 的以下两个定义 ID 是资源协议常量，并非本实验的私有标识：

```text
资源应用 appId : 022907d3-0f1b-48f7-badc-1ba6abab6d66
委托权限名称   : user_impersonation
委托权限 id   : c39ef2d1-04ce-46dc-8b5f-e9a5c60f0fc9
```

## 5. 注册公共客户端并预先完成单用户同意

### 5.1 管理权限与同意边界

可在 Entra 管理中心完成同等配置，也可由获授权的管理员使用下面的 Graph REST 示例。门户中应设置：单租户、Mobile and desktop applications 下的 broker redirect URI、允许公共客户端流，以及 Azure SQL 的 delegated `user_impersonation`。

Graph 写入需要**调用方权限与执行者的目录角色／对象权限同时满足要求**。创建和修改应用、创建服务主体通常涉及 `Application.ReadWrite.All`（或适用的所有者权限）；读取服务主体涉及 `Application.Read.All`；写入 `oauth2PermissionGrants` 的最小 Graph 权限为 `DelegatedPermissionGrant.ReadWrite.All`，并受目录角色和租户策略约束。官方 API 文档列出受支持角色，例如 Application Administrator／Cloud Application Administrator；具体操作应按组织职责划分。使用 Azure CLI 并不自动获得这些 Graph 权限，Azure subscription Owner 也不自动等于 Entra 管理员。

上述 Graph 权限属于**配置工具**，不要加到本示例 SQL 公共客户端。遇到 403 应让目录管理员核查配置工具权限、目录角色和策略，不应绕过限制或给普通使用者扩大权限。

本文只为**一个被批准用户**预置 `Principal` 授权。不要使用面向整个组织的“Grant admin consent for …”按钮代替本步骤，也不要将 `consentType` 改成 `AllPrincipals`。组织允许时，普通用户也可以在独立的初始化流程中同意允许用户同意的权限，但不能静默授予自己管理员权限；初始化时的交互不属于后面的静默查询。

### 5.2 管理员登录和安全 JSON 辅助函数

以下命令在 **PowerShell 7** 中执行，需预先安装 Azure CLI。使用 UTF-8 JSON 临时文件，避免 Windows 命令行内嵌 JSON 转义问题。命令会实际创建目录对象；先审阅，切勿直接在不熟悉的生产租户运行。

```powershell
$ErrorActionPreference = 'Stop'
$TenantId = '<TENANT_ID>'
$UserObjectId = '<USER_OBJECT_ID>'
$SqlResourceAppId = '022907d3-0f1b-48f7-badc-1ba6abab6d66'
$SqlScopeId = 'c39ef2d1-04ce-46dc-8b5f-e9a5c60f0fc9'

# 只用于一次性管理配置，不是运行客户端的前置命令。
az login --tenant $TenantId --allow-no-subscriptions
if ($LASTEXITCODE -ne 0) { throw '管理员登录失败。' }

function Invoke-GraphJson {
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Uri,
        [object]$Body
    )
    $jsonPath = $null
    try {
        $arguments = @(
            'rest', '--method', $Method, '--url', $Uri,
            '--output', 'json', '--only-show-errors'
        )
        if ($PSBoundParameters.ContainsKey('Body')) {
            $jsonPath = Join-Path ([IO.Path]::GetTempPath()) (
                'wam-sql-' + [Guid]::NewGuid().ToString('N') + '.json'
            )
            $json = ConvertTo-Json -InputObject $Body -Depth 12
            [IO.File]::WriteAllText(
                $jsonPath, $json, [Text.UTF8Encoding]::new($false)
            )
            $arguments += @(
                '--headers', 'Content-Type=application/json',
                '--body', "@$jsonPath"
            )
        }
        $output = & az @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Graph $Method 失败；检查上方错误与管理员权限。"
        }
        if ($output) { ($output -join "`n") | ConvertFrom-Json }
    }
    finally {
        if ($jsonPath -and (Test-Path -LiteralPath $jsonPath)) {
            Remove-Item -LiteralPath $jsonPath
        }
    }
}
```

辅助函数只删除自己创建的那一个临时 JSON 文件，不持久化访问令牌。第 5 节的 PowerShell 片段按顺序在同一会话中执行；不要把管理员的 CLI 登录或输出复制到查询用户的会话。

### 5.3 注册单租户应用与 Windows broker redirect URI

```powershell
$application = Invoke-GraphJson -Method POST `
    -Uri 'https://graph.microsoft.com/v1.0/applications' -Body @{
        displayName = 'Windows SQL WAM silent demo'
        signInAudience = 'AzureADMyOrg'
        isFallbackPublicClient = $true
        requiredResourceAccess = @(
            @{
                resourceAppId = $SqlResourceAppId
                resourceAccess = @(
                    @{ id = $SqlScopeId; type = 'Scope' }
                )
            }
        )
    }
$ClientId = $application.appId
$AppObjectId = $application.id

Invoke-GraphJson -Method PATCH `
    -Uri "https://graph.microsoft.com/v1.0/applications/$AppObjectId" -Body @{
        publicClient = @{
            redirectUris = @("ms-appx-web://microsoft.aad.brokerplugin/$ClientId")
        }
    }

$clientSp = Invoke-GraphJson -Method POST `
    -Uri 'https://graph.microsoft.com/v1.0/servicePrincipals' -Body @{
        appId = $ClientId
    }
$ClientSpObjectId = $clientSp.id
```

这是 **public client**，不要创建 client secret，也不要将客户端示例改为 client credentials。`requiredResourceAccess` 声明“应用请求什么”，本身不代表“用户已同意”。WAM redirect URI 必须登记在应用中；客户端代码的 `.WithDefaultRedirectUri()` 不替代该登记。

这些创建命令不是幂等部署脚本。重复执行会创建新的应用；已存在应用时应核对并复用其正确对象 ID，避免留下孤立身份。新对象可能有短暂目录传播延迟；出现对象尚未可见的错误时，先确认创建结果和 ID，再有限重试后续步骤，不要重跑创建整段。

### 5.4 查找目标租户中的 SQL 服务主体

```powershell
$filter = [Uri]::EscapeDataString("appId eq '$SqlResourceAppId'")
$sqlResources = Invoke-GraphJson -Method GET `
    -Uri "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=$filter"
$sqlResourceItems = @($sqlResources.value)
if ($sqlResourceItems.Count -ne 1) {
    throw '未唯一找到当前租户的 Azure SQL 资源服务主体，请目录管理员核查。'
}
$sqlResource = $sqlResourceItems[0]
$SqlResourceSpObjectId = $sqlResource.id
$scope = @($sqlResource.oauth2PermissionScopes | Where-Object {
    $_.id -eq $SqlScopeId -and
    $_.value -eq 'user_impersonation' -and $_.isEnabled
})
if ($scope.Count -ne 1) {
    throw 'Azure SQL user_impersonation 权限定义不符合预期。'
}
```

这里的资源 `appId` 跨租户识别 Azure SQL；返回的服务主体 `id` 是租户本地对象 ID。若缺少资源服务主体，交给目录管理员按组织批准流程处理，不要填入其他租户的对象 ID。

### 5.5 只给目标用户创建 delegated permission grant

管理员应先在 Entra 中核对 `$UserObjectId`，确认是获批准的那个用户，而不是服务主体或错误租户的用户。

```powershell
$grant = Invoke-GraphJson -Method POST `
    -Uri 'https://graph.microsoft.com/v1.0/oauth2PermissionGrants' -Body @{
        clientId = $ClientSpObjectId
        consentType = 'Principal'
        principalId = $UserObjectId
        resourceId = $SqlResourceSpObjectId
        scope = 'user_impersonation'
    }
$GrantId = $grant.id
if ($grant.consentType -ne 'Principal' -or
    $grant.principalId -ne $UserObjectId -or
    $grant.clientId -ne $ClientSpObjectId -or
    $grant.resourceId -ne $SqlResourceSpObjectId -or
    $grant.scope -ne 'user_impersonation') {
    throw '授权响应不符合单用户授权预期，请管理员立即核查。'
}
```

Graph 此处名为 `clientId` 的字段需要 **客户端服务主体对象 ID**，不是 MSAL 使用的 Application (client) ID。`resourceId` 同样需要资源服务主体对象 ID。

首次配置可记录 `$ClientId` 给客户端，以及应用／服务主体／授权对象 ID 供管理员后续撤销。记录应存于受控运维记录，不能把实际环境清单提交到此仓库。若已有同一用户的授权，应先读取并核对既有授权，由管理员合并或更新所需 scope；不要盲目重复 POST 或删除其他应用的授权。

## 6. 配置网络和数据库最小权限

### 6.1 网络不是认证

使用真实 SQL FQDN，允许客户端访问 `tcp:<SQL_SERVER_FQDN>,1433`，并使用 `Encrypt=true`、`TrustServerCertificate=false` 验证 TLS。不要以 IP 地址替代 FQDN 来绕过证书校验。

- **优先遵循组织的私有网络方案**：Private Endpoint、正确的 Private DNS、VPN／专线／受控虚拟网络路径。
- 若组织允许公共访问，需同时启用符合策略的公共网络配置与精确的客户端出口 IP 防火墙规则。HTTP 查询公网 IP 的出口可能不同于 SQL TCP 出口，尤其是代理环境；不要据此自动放宽大量地址范围，也不要无条件启用广泛的 Azure 服务访问。
- Azure SQL 连接策略还可能涉及 Redirect 数据端口／路径；按官方连接架构及实际部署核对所需端口，不要把 TCP 1433 测试通过当作完整连接保证。

可在目标桌面做只读连通性检查：

```powershell
Resolve-DnsName '<SQL_SERVER_FQDN>'
Test-NetConnection '<SQL_SERVER_FQDN>' -Port 1433
```

实验环境曾受管理组 Modify 策略影响，SQL 公共网络访问被强制设为 Disabled。**不要反复改回公共访问、复制实验例外标签或自动绕过策略。** 首选合规的 Private Endpoint；确需例外时，由组织治理流程批准其范围、期限与审计，再由管理员实施。本指南不提供策略绕过步骤。

若用 Azure VM 模拟桌面，不要开放面向整个 Internet 的 RDP。使用来源 IP 限制、Bastion 或组织批准的访问路径。

### 6.2 在目标数据库创建演示对象与用户

以下 SQL 由具备相应权限的 SQL 管理员在**目标数据库**内执行。连接时显式指定该数据库，不要误在 `master` 创建演示表或用户。首次演示使用新建对象；若已存在同名对象，先审查，不要直接覆盖。

```sql
CREATE TABLE dbo.WamSsoDemo
(
    Id int NOT NULL CONSTRAINT PK_WamSsoDemo PRIMARY KEY,
    Marker nvarchar(100) NOT NULL
);
INSERT INTO dbo.WamSsoDemo (Id, Marker)
VALUES (1, N'passwordless-verified');

-- 用目标租户中真实、唯一的用户 UPN 替换此虚构值。
CREATE USER [reader@contoso.example] FROM EXTERNAL PROVIDER;
GRANT SELECT ON OBJECT::dbo.WamSsoDemo TO [reader@contoso.example];
```

生产环境优先为 Entra 安全组建立 contained user 并管理组成员；按需要授予对象／schema 权限，而不是把所有用户设为服务器 Entra 管理员、`db_owner` 或一律授予整个数据库读取权。若使用组，SQL 中的主体名称与上例不同，需相应替换。

`CREATE USER ... FROM EXTERNAL PROVIDER` 需要执行者的数据库权限，以及适用的 Entra 主体解析权限；具体取决于执行者、SQL 服务器身份与租户策略。主体重名或目录读取失败，应按官方数据库身份配置文档处理，不要为排障无条件扩大目录权限。

实测 `USER_NAME()=dbo` 只是因为测试账户已是服务器 Entra 管理员。按上述普通 contained user 方式复现，通常应看到相应的数据库主体名称，而非要求结果必须是 `dbo`。

## 7. 构建完整、仅静默的 C# 客户端

在目标用户自己的工作目录中创建示例工程；下列文件由读者本地保存，本仓库不提供需要部署的应用或基础设施。

```powershell
dotnet new console --framework net8.0 --name WamSqlProbe
Set-Location .\WamSqlProbe
```

将项目文件 `WamSqlProbe.csproj` 替换为：

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0-windows</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.Data.SqlClient" Version="6.1.4" />
    <PackageReference Include="Microsoft.Identity.Client.Broker" Version="4.77.0" />
  </ItemGroup>
</Project>
```

`Microsoft.Identity.Client.Broker` 提供本示例使用的 broker 扩展及 MSAL 依赖。上面固定的是实测版本组合；升级后应重新核对 broker 行为和依赖兼容性。若偏好包管理命令，在把目标框架改为 `net8.0-windows` 后，可以用以下两条命令生成相同的包引用：

```powershell
dotnet add package Microsoft.Data.SqlClient --version 6.1.4
dotnet add package Microsoft.Identity.Client.Broker --version 4.77.0
```

将 `Program.cs` 替换为以下完整内容：

```csharp
using Microsoft.Data.SqlClient;
using Microsoft.Identity.Client;
using Microsoft.Identity.Client.Broker;
using System.Runtime.InteropServices;

try
{
    string tenantId = RequiredEnvironment("WAM_SQL_TENANT_ID");
    string clientId = RequiredEnvironment("WAM_SQL_CLIENT_ID");
    string server = RequiredEnvironment("WAM_SQL_SERVER");
    string database = RequiredEnvironment("WAM_SQL_DATABASE");

    if (!Guid.TryParse(tenantId, out _) || !Guid.TryParse(clientId, out _))
        throw new ArgumentException("Tenant ID and client ID must be GUIDs.");

    using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(3));
    CancellationToken cancellationToken = timeout.Token;

    var app = PublicClientApplicationBuilder.Create(clientId)
        .WithAuthority($"https://login.microsoftonline.com/{tenantId}")
        .WithDefaultRedirectUri()
        .WithParentActivityOrWindow(() => NativeMethods.GetConsoleWindow())
        .WithBroker(new BrokerOptions(BrokerOptions.OperatingSystems.Windows))
        .Build();

    string[] scopes = { "https://database.windows.net//.default" };
    var auth = await app.AcquireTokenSilent(
            scopes, PublicClientApplication.OperatingSystemAccount)
        .ExecuteAsync(cancellationToken);

    Console.WriteLine($"TokenSource={auth.AuthenticationResultMetadata.TokenSource}");
    Console.WriteLine("interactiveFallback=false");
    Console.WriteLine($"Account={auth.Account.Username}");
    Console.WriteLine($"Tenant={auth.TenantId}");

    if (!string.Equals(
            auth.AuthenticationResultMetadata.TokenSource.ToString(),
            "Broker", StringComparison.Ordinal))
    {
        Console.Error.WriteLine("Expected a broker token; verification failed.");
        return 2;
    }
    if (!string.Equals(auth.TenantId, tenantId, StringComparison.OrdinalIgnoreCase))
    {
        Console.Error.WriteLine("Unexpected token tenant; verification failed.");
        return 2;
    }

    var csb = new SqlConnectionStringBuilder
    {
        DataSource = $"tcp:{server},1433",
        InitialCatalog = database,
        Encrypt = true,
        TrustServerCertificate = false,
        ConnectTimeout = 60,
        Pooling = false,
        ApplicationName = "WamSqlProbe-silent-only"
    };

    await using var connection = new SqlConnection(csb.ConnectionString);
    connection.AccessToken = auth.AccessToken;
    await connection.OpenAsync(cancellationToken);

    await using var command = connection.CreateCommand();
    command.CommandTimeout = 60;
    command.CommandText = """
        SELECT ORIGINAL_LOGIN() AS OriginalLogin,
               USER_NAME() AS DatabaseUser,
               DB_NAME() AS DatabaseName;
        SELECT Marker FROM dbo.WamSsoDemo WHERE Id = 1;
        """;

    await using var reader = await command.ExecuteReaderAsync(cancellationToken);
    if (!await reader.ReadAsync(cancellationToken))
    {
        Console.Error.WriteLine("SQL identity result was empty.");
        return 3;
    }
    Console.WriteLine($"OriginalLogin={reader.GetString(0)}");
    Console.WriteLine($"DatabaseUser={reader.GetString(1)}");
    string actualDatabase = reader.GetString(2);
    Console.WriteLine($"Database={actualDatabase}");
    if (!string.Equals(actualDatabase, database, StringComparison.OrdinalIgnoreCase))
    {
        Console.Error.WriteLine("Unexpected database; verification failed.");
        return 3;
    }

    if (!await reader.NextResultAsync(cancellationToken) ||
        !await reader.ReadAsync(cancellationToken))
    {
        Console.Error.WriteLine("Demo marker was not found.");
        return 3;
    }
    string marker = reader.GetString(0);
    Console.WriteLine($"Marker={marker}");
    if (marker != "passwordless-verified")
    {
        Console.Error.WriteLine("Unexpected demo marker; verification failed.");
        return 3;
    }
    return 0;
}
catch (MsalUiRequiredException ex)
{
    Console.Error.WriteLine($"Silent authentication needs attention: {ex.ErrorCode}");
    Console.Error.WriteLine(
        "Check Windows sign-in/PRT, per-user consent, tenant and policy. " +
        "No interactive fallback was attempted.");
    return 10;
}
catch (MsalException ex)
{
    Console.Error.WriteLine($"MSAL authentication failed: {ex.ErrorCode}");
    return 11;
}
catch (SqlException ex)
{
    Console.Error.WriteLine($"SQL connection/query failed: Number={ex.Number}");
    Console.Error.WriteLine(
        "Check network, explicit database, Entra user and SQL permissions.");
    return 12;
}
catch (OperationCanceledException)
{
    Console.Error.WriteLine("The operation timed out or was canceled.");
    return 13;
}
catch (ArgumentException ex)
{
    Console.Error.WriteLine($"Invalid configuration: {ex.Message}");
    return 14;
}

static string RequiredEnvironment(string name)
{
    string? value = Environment.GetEnvironmentVariable(name);
    if (string.IsNullOrWhiteSpace(value))
        throw new ArgumentException($"Set {name} before running.");
    return value.Trim();
}

internal static class NativeMethods
{
    [DllImport("kernel32.dll")]
    internal static extern IntPtr GetConsoleWindow();
}
```

本示例没有 `AcquireTokenInteractive`、设备代码、用户名密码、CLI 或 MI 兜底。官方 WAM 通用示例包含交互式 fallback；这里为验证“仅静默”而**刻意不采用**该 fallback。未预期的运行时异常不会被吞掉，而会使进程失败；预期认证／SQL 错误只输出错误代码，不打印令牌或完整异常。需要深入诊断时，在受控本地日志中查看详情，分享前脱敏，不公开包含 PII／token 的日志。

**为什么 scope 有两个 `/`？** 本次实测使用的完整 scope 是 `https://database.windows.net//.default`。按带结尾 `/` 的 SQL 资源标识 `https://database.windows.net/` 拼接 `/.default`，保留双斜杠。官方 scopes 文档解释了资源结尾斜杠与 `.default` 的组合规则；本指南保留已验证字符串，不推断所有客户端都要求相同写法。不要替换为 Graph scope 或管理 API audience；`.default` 不代表自动授权全租户。

连接字符串不设置 `Authentication=...`、`User ID` 或 `Password`，由应用显式提供 `AccessToken`。`Pooling=false` 便于短进程验证，不是长期服务的连接池设计建议。生产客户端还需设计令牌刷新、连接生命周期与可观测性，不能无限复用已到期的令牌。

## 8. 在当前 Windows 桌面验证两个新进程

在目标用户**已登录的交互式桌面会话**中直接打开控制台。不要用“以其他用户运行”，也不要在 SYSTEM、服务账号、无人值守远程执行器或 CI 中验证当前桌面用户 SSO。

```powershell
whoami
dsregcmd /status
```

在本地检查账户是否正确，以及 `AzureAdJoined`、`AzureAdPrt` 和 PRT 对应租户是否符合预期。`dsregcmd /status` 可能含设备／租户／用户标识，**不要把整段输出上传**。PRT 为 YES 是本实验的前提证据之一，不保证应用同意、条件访问、数据库授权或网络都正确。

在工程目录中设置非凭据配置并构建：

```powershell
$env:WAM_SQL_TENANT_ID = '<TENANT_ID>'
$env:WAM_SQL_CLIENT_ID = '<CLIENT_ID>'
$env:WAM_SQL_SERVER = '<SQL_SERVER_FQDN>'
$env:WAM_SQL_DATABASE = 'auth-test' # 或自己的数据库名

dotnet build --configuration Release
if ($LASTEXITCODE -ne 0) { throw '构建失败。' }

# 每次 dotnet 调用均启动一个新的应用进程。
dotnet .\bin\Release\net8.0-windows\WamSqlProbe.dll
if ($LASTEXITCODE -ne 0) { throw '第一个静默验证进程失败。' }
dotnet .\bin\Release\net8.0-windows\WamSqlProbe.dll
if ($LASTEXITCODE -ne 0) { throw '第二个静默验证进程失败。' }
```

查询运行阶段不执行 `az login`，不注入访问令牌，不依赖 Azure CLI 或 Managed Identity。示例只读取上述四个配置变量，不使用通用 Azure 凭据链。

预期输出示意（虚构身份，**不是实验原始日志**）：

```text
TokenSource=Broker
interactiveFallback=false
Account=reader@contoso.example
Tenant=<TENANT_ID>
OriginalLogin=reader@contoso.example
DatabaseUser=reader@contoso.example
Database=auth-test
Marker=passwordless-verified
```

验收时同时确认：两个进程退出码均为 0、`TokenSource=Broker`、无 UI／MFA／密码提示、账户与目标用户一致、`OriginalLogin` 是人类用户而非 MI、数据库和 marker 正确。数据库主体名称会随直接授权／组授权／管理员身份而变化；`interactiveFallback=false` 是示例执行路径的声明，不能只看这一行而忽略代码、UI 行为和退出码。

实验曾用 `LogonType=Interactive`、`RunLevel=Limited` 的一次性计划任务，在已有桌面用户 token 下运行程序；任务随后删除。这只是远程实验的执行手段，不是 WAM 必需组件。断开桌面会话时第一次没有得到结果，重新连接用户桌面后才完成；复现首选直接在活跃桌面运行，不要把无人值守计划任务当作等价环境。

## 9. 排障

| 现象 | 优先检查／处理 |
| --- | --- |
| `integrated_windows_auth_not_supported_managed_user` | 是否仍在使用经典 IWA／`Active Directory Integrated`；云端托管用户应使用本文 WAM 路径，不能用 PRT 为 YES 反驳 IWA 的支持边界 |
| `consent_required` | 单用户 grant 是否存在、`Principal` 和用户对象 ID 是否正确；声明 API permission 不等于完成同意；让获授权管理员修正，不在客户端自动弹同意 |
| `interaction_required`／`MsalUiRequiredException` | PRT／会话状态、条件访问、登录频率、设备合规、账户选择；在独立获批准流程完成所需交互后重试，本次静默运行应判失败 |
| 要求 MFA／重新登录 | 组织策略可能需要新认证；静默不能绕过，不要禁用 MFA 或将交互式成功记作静默成功 |
| WAM 找不到账户／broker 错误 | 是否为真正登录的 Entra 桌面用户、正确租户、有效 PRT、可用 WAM 与控制台宿主；检查 broker redirect URI 与客户端 ID |
| SYSTEM、服务、远程 shell 中失败 | 当前 token 不等于桌面用户 token；移到用户交互式会话，后台任务另行设计工作负载身份 |
| tenant／issuer 不匹配 | authority、grant、OS 账户和 SQL 的租户是否一致；本指南没有验证跨租户来宾自动切换 |
| Graph 403／无法建 grant | 配置执行者的 Graph 权限、目录角色和租户同意策略；不是 SQL 权限问题，也不是改 scope 字符串能解决 |
| 已有 token 但 SQL 登录失败（如 18456） | 数据库名、用户／组对象、Entra 管理员配置、权限传播；查看受控详细错误，不能直接授予 `db_owner` 作为通用修复 |
| 能连接但 SELECT 被拒绝／找不到表 | 是否进入正确数据库，演示表是否建立，用户或组是否被授予该对象 SELECT |
| 连接超时／DNS／防火墙错误 | Private DNS、VPN／路由、真实 SQL TCP 出口、连接策略、端口及组织策略；令牌成功并不证明网络可达 |
| TLS 证书错误 | 是否使用正确 SQL FQDN、系统信任链／时间／代理检查；不要设 `TrustServerCertificate=true` 掩盖问题 |
| Serverless 首次连接慢或短暂失败 | 数据库可能正在恢复；确认状态后有限重试，分别记录恢复与认证结果，避免无穷重试隐藏失败 |

WAM 需要父窗口支持，即使业务只尝试静默也应按 API 要求配置。本例保留实测的 `GetConsoleWindow()` 模式。在其他终端／GUI 宿主中，窗口句柄可能不适用；应按官方 WAM 指南提供宿主的有效父窗口 HWND（官方控制台示例还使用 `GetAncestor` 获取 root owner），不要删除 broker 并静默退回其他认证方式。

## 10. 接入已有 SQL CLI 或应用

已有 CLI 必须支持 Windows broker／MSAL WAM，或支持由可信本地组件安全地提供访问令牌；**不能仅增加 `-G` 就宣称完成本方案**。先检查具体工具、驱动和版本的认证实现，再用相同的无交互、用户身份和新进程标准验收。

可将本文 MSAL 获取令牌逻辑集成到自己的 .NET CLI，随后通过 `SqlConnection.AccessToken` 接入。不要通过命令行参数、shell 历史、共享文件或仓库传递 token。长运行客户端需在正确用户上下文中处理过期和重新静默获取；需要交互时显式报告失败或转入**单独且用户明确触发**的登录流程。

## 11. 成本、安全与清理

本实验使用的 SQL Serverless 配置是 General Purpose `GP_S_Gen5`、最大 1 vCore、最小 0.5 vCore、自动暂停延迟 60 分钟、最大 5 GB、Local 备份冗余，区域为 `southeastasia`。当时订阅限制了美国区域，**区域与 SKU 只是实验记录，不是通用部署建议或当前供应／价格承诺**。部署前核对实际区域可用性、订阅配额、策略和账单。自动暂停减少计算费用，但不消除存储等费用，首次恢复会有延迟。

本地 Windows 不需要额外 Azure VM。若用 Azure VM 模拟 Windows 11 桌面，需满足 Windows 桌面 Azure 许可资格，另计计算、磁盘、网络、Bastion 等费用。VM 停机／解除分配也不一定消除磁盘与相关资源费用；私有端点和日志亦可能产生费用。

安全与清理均由资源所有者核对后执行，本指南不自动执行：

- 不保存用户密码、客户端 secret、访问令牌或包含真实标识的原始诊断结果；保持证书验证和最小权限。
- 验证完毕后按需撤销**本次专用应用／用户的** `oauth2PermissionGrant`，核对 grant ID、client SP 和 principal，再删除专用服务主体／应用；不要误删租户共享的 Azure SQL 资源服务主体。
- 撤销 SQL 对象权限，按需要删除演示 contained user／组映射与 `dbo.WamSsoDemo`；删除前检查是否已被其他业务使用。不要为清理实验移除业务 Entra 管理员。
- 删除自己的演示工程及受控临时日志，清除本地四个配置环境变量；这不等于注销 Windows 或清除 WAM 缓存。撤销同意也不保证已签发令牌立即失效，需按组织会话撤销策略处置。
- 若专门创建了 VM、SQL 数据库、服务器、Private Endpoint 或其他实验资源，先核对归属和依赖，再经批准删除；不要按名称模糊匹配批量删资源组。
- 若存在经批准的临时网络／策略例外，由治理流程到期撤销并核查审计；不要将例外固化成应用部署前提。

## 12. 官方参考

编写时核对了 WAM、经典 IWA、Graph 创建委托授权、scope 及 SqlClient 指南；其余链接供配置时进一步查阅。实验结论及固定依赖版本见第 1 节，不能将官方“支持某能力”替代实际的静默验证。

- [MSAL.NET：使用 Windows Web Account Manager（WAM）](https://learn.microsoft.com/en-us/entra/msal/dotnet/acquiring-tokens/desktop-mobile/wam)
- [MSAL.NET：Integrated Windows Authentication 支持范围与限制](https://learn.microsoft.com/en-us/entra/msal/dotnet/acquiring-tokens/desktop-mobile/integrated-windows-authentication)
- [Microsoft Graph：创建 oauth2PermissionGrant（含权限要求）](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-post?view=graph-rest-1.0)
- [Microsoft Graph：oauth2PermissionGrant 字段定义](https://learn.microsoft.com/en-us/graph/api/resources/oauth2permissiongrant?view=graph-rest-1.0)
- [Microsoft identity platform：scopes、资源标识结尾斜杠与 `.default`](https://learn.microsoft.com/en-us/entra/identity-platform/scopes-oidc)
- [SqlClient：Microsoft Entra authentication](https://learn.microsoft.com/en-us/sql/connect/ado-net/sql/azure-active-directory-authentication?view=sql-server-ver17)
- [SqlConnection.AccessToken](https://learn.microsoft.com/en-us/dotnet/api/microsoft.data.sqlclient.sqlconnection.accesstoken)
- [Azure SQL：配置 Microsoft Entra authentication](https://learn.microsoft.com/en-us/azure/azure-sql/database/authentication-aad-configure?view=azuresql)
- [Azure SQL：连接架构与连接策略](https://learn.microsoft.com/en-us/azure/azure-sql/database/connectivity-architecture?view=azuresql)
- [Azure SQL：Private Endpoint](https://learn.microsoft.com/en-us/azure/azure-sql/database/private-endpoint-overview?view=azuresql)
- [Azure SQL：Serverless 计算层](https://learn.microsoft.com/en-us/azure/azure-sql/database/serverless-tier-overview?view=azuresql)
- [Windows 客户端在 Azure 中的许可要求](https://learn.microsoft.com/en-us/azure/virtual-machines/windows/client-images)
