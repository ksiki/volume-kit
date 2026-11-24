$inputJson = [Console]::In.ReadToEnd()
if ($inputJson -match '(?i)(\.venv[/\\]?|[/\\]\.venv\b)' -and $inputJson -notmatch '(?i)\.venv[/\\]Scripts[/\\](python|pip|pytest|activate)') {
    Write-Output '{"cancel": true, "errorMessage": "Access to .venv is blocked by PreToolUse hook."}'
    exit 0
}
Write-Output '{"cancel": false}'