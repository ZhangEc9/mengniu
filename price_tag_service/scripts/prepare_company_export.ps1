param(
    [Parameter(Mandatory = $true)]
    [string]$Destination
)

$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$repository = [IO.Path]::GetFullPath((Join-Path $project '..'))
$target = [IO.Path]::GetFullPath($Destination)
$ddl = @(Get-ChildItem -LiteralPath $repository -File -Filter '*V1.2*PG.sql')
if ($ddl.Count -ne 1) {
    throw 'Expected exactly one V1.2 PostgreSQL schema reference in the experiment repository.'
}
if ($target.Equals($project, [StringComparison]::OrdinalIgnoreCase) -or
    $target.StartsWith($project + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    (Test-Path -LiteralPath $target)) {
    throw 'Destination must be a new directory outside the source project; existing files are never overwritten.'
}

$paths = @(
    'app/__init__.py',
    'app/agent_api/__init__.py', 'app/agent_api/main.py', 'app/agent_api/service.py',
    'app/business_api/__init__.py', 'app/business_api/check_schema.py',
    'app/business_api/demo_fusion.py', 'app/business_api/main.py',
    'app/business_api/models.py', 'app/business_api/schema.py',
    'app/business_api/service.py', 'app/business_api/status.py',
    'app/business_api/verify_write.py',
    'app/contracts/__init__.py', 'app/contracts/agents.py',
    'app/clients/__init__.py', 'app/clients/aism.py',
    'app/clients/models.py', 'app/clients/sku.py',
    'app/core/__init__.py', 'app/core/config.py',
    'app/core/errors.py', 'app/core/jsonutil.py',
    'app/processing/__init__.py', 'app/processing/image_size.py',
    'app/processing/postprocess.py', 'app/processing/quality.py',
    'app/processing/sku_match.py',
    'app/resources/prompts/quality_system_prompt.txt',
    'app/resources/prompts/quality_user_prompt.txt',
    'app/resources/prompts/price_system_prompt_final.txt',
    'app/resources/prompts/price_user_prompt_final.txt',
    'app/resources/sku_samples/49098981_7_first_normal_1788763058147_19960398.sku.json',
    'tests/test_service_boundary.py'
)
$copies = @(
    @{ From = 'company/pyproject.toml'; To = 'pyproject.toml'; Base = $project },
    @{ From = 'company/README.md'; To = 'README.md'; Base = $project },
    @{ From = 'company/config.example.json'; To = 'config.example.json'; Base = $project },
    @{ From = '.gitignore'; To = '.gitignore'; Base = $project },
    @{ From = $ddl[0].Name; To = 'docs/schema_reference.sql'; Base = $repository }
)

foreach ($relative in $paths) {
    if (-not (Test-Path -LiteralPath (Join-Path $project $relative) -PathType Leaf)) {
        throw "Source file missing: $relative"
    }
}
foreach ($entry in $copies) {
    if (-not (Test-Path -LiteralPath (Join-Path $entry.Base $entry.From) -PathType Leaf)) {
        throw "Source file missing: $($entry.From)"
    }
}

New-Item -ItemType Directory -Path $target | Out-Null
foreach ($relative in $paths) {
    $destinationFile = Join-Path $target $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destinationFile) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $project $relative) -Destination $destinationFile
}
foreach ($entry in $copies) {
    $destinationFile = Join-Path $target $entry.To
    New-Item -ItemType Directory -Path (Split-Path -Parent $destinationFile) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $entry.Base $entry.From) -Destination $destinationFile
}
Write-Output "Prepared $target with $($paths.Count + $copies.Count) allowlisted files; no Git remote or secrets copied."
