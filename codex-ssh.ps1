param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteScriptBase64
)

$ErrorActionPreference = "Stop"

$scriptText = [System.Text.Encoding]::UTF8.GetString(
    [System.Convert]::FromBase64String($RemoteScriptBase64)
)

$ask = Join-Path $env:TEMP "codex-askpass.cmd"
$tmp = Join-Path $env:TEMP "codex-remote.sh"

try {
    [System.IO.File]::WriteAllText($ask, "@echo off`necho @chenjunhong123456", [System.Text.Encoding]::ASCII)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, $scriptText, $utf8NoBom)

    $env:SSH_ASKPASS = $ask
    $env:SSH_ASKPASS_REQUIRE = "force"
    $env:DISPLAY = "codex"

    Get-Content -Raw $tmp | & "C:\Windows\System32\OpenSSH\ssh.exe" `
        -T `
        -o StrictHostKeyChecking=no `
        -o UserKnownHostsFile=NUL `
        -o PreferredAuthentications=password `
        -o PubkeyAuthentication=no `
        -p 22 `
        chenjunhong@101.37.24.171 `
        "bash -s"

    exit $LASTEXITCODE
}
finally {
    Remove-Item $ask -ErrorAction SilentlyContinue
    Remove-Item $tmp -ErrorAction SilentlyContinue
}
