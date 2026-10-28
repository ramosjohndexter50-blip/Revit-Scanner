$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework
$repoRoot = Split-Path $PSScriptRoot -Parent
$files = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot 'Hamdan.tab'),(Join-Path $repoRoot 'lib') -Filter '*.xaml' -Recurse)
foreach ($file in $files) {
    $xml = [xml](Get-Content -LiteralPath $file.FullName -Raw)
    # Python binds these handlers at runtime; native WPF can validate the remaining layout.
    foreach ($node in $xml.SelectNodes('//*')) {
        foreach ($name in @('Click', 'TextChanged', 'SelectionChanged', 'Checked', 'Unchecked')) {
            $node.RemoveAttribute($name)
        }
    }
    $reader = New-Object System.Xml.XmlNodeReader $xml
    $window = [Windows.Markup.XamlReader]::Load($reader)
    $window.Close()
    Write-Output ('WPF layout loaded: ' + $file.Directory.Name)
}
