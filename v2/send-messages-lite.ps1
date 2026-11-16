[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Message,

    [Parameter(Mandatory = $true)]
    [ValidateScript({ $_ -eq -1 -or $_ -gt 0 })]
    [int]$Number,

    [Alias('delay-min')]
    [double]$DelayMin = 1,

    [Alias('delay-max')]
    [double]$DelayMax = 2,

    [Alias('dry-run')]
    [switch]$DryRun,

    [switch]$Apply,

    [Alias('activate-adb-keyboard')]
    [switch]$ActivateAdbKeyboard,

    [Alias('fast-focus')]
    [switch]$FastFocus,

    [guid]$Resume,

    [Alias('inbox-only')]
    [switch]$InboxOnly
)

if ($DryRun -and $Apply) {
    throw 'Usa -dry-run o -apply como opciones excluyentes.'
}

$culture = [System.Globalization.CultureInfo]::InvariantCulture
$arguments = @(
    '--number'; $Number.ToString($culture)
    '--delay-min'; $DelayMin.ToString('R', $culture)
    '--delay-max'; $DelayMax.ToString('R', $culture)
    if ($Apply) { '--apply' } else { '--dry-run' }
    if ($ActivateAdbKeyboard) { '--activate-adb-keyboard' }
    if ($FastFocus) { '--fast-focus' }
    if ($PSBoundParameters.ContainsKey('Resume')) { '--resume'; $Resume.ToString() }
    if ($InboxOnly) { '--inbox-only' }
)
$uv = Get-Command uv -CommandType Application -ErrorAction Stop | Select-Object -First 1
$previousMessage = [Environment]::GetEnvironmentVariable('AUTODATE_OUTREACH_MESSAGE', 'Process')
Push-Location -LiteralPath $PSScriptRoot
try {
    [Environment]::SetEnvironmentVariable('AUTODATE_OUTREACH_MESSAGE', $Message, 'Process')
    & $uv.Source run --no-project --offline --python 3.12 python -u -X utf8 -m autodate.outreach_lite @arguments
    $result = $LASTEXITCODE
} finally {
    [Environment]::SetEnvironmentVariable('AUTODATE_OUTREACH_MESSAGE', $previousMessage, 'Process')
    Pop-Location
}
exit $result
