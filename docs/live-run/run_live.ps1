# Live run of linkx over docs/live-run/urls.txt (1,000 real URLs, 712 hostnames on 641 sites).
# Logs stream to the terminal and are saved to log-c<N>.txt; records go to results-c<N>.jsonl.
param([int]$Concurrency = 50)

Set-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$dir = "docs/live-run"
$tag = "c$Concurrency"
Write-Host "=== linkx live run: 1,000 URLs, concurrency $Concurrency, per-host delay 1s, timeout 15s ===" -ForegroundColor Cyan
uv run python "$dir/measure.py" "$dir/urls.txt" `
    --out "$dir/results-$tag.jsonl" `
    --concurrency $Concurrency --per-host-delay 1 --timeout 15 2>&1 |
    ForEach-Object { "$_" } |
    Tee-Object -FilePath "$dir/log-$tag.txt"
Write-Host "=== records: $((Get-Content "$dir/results-$tag.jsonl" | Measure-Object -Line).Lines) lines in $dir/results-$tag.jsonl ===" -ForegroundColor Cyan
