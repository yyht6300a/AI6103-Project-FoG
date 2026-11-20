$ErrorActionPreference = "Stop"
$repo = "https://hf-mirror.com/czzhuhu/FoG-B-Models/resolve/main"
$root = "D:\Repo\03-Project"

$files = @(
    @{ src = "cache/embeddings_v2/entity_emb.npy";       dst = "cache\embeddings_v2\entity_emb.npy" },
    @{ src = "cache/embeddings_v2/entity_is_mid.npy";    dst = "cache\embeddings_v2\entity_is_mid.npy" },
    @{ src = "cache/embeddings_v2/entity_progress.json"; dst = "cache\embeddings_v2\entity_progress.json" },
    @{ src = "cache/embeddings_v2/meta.json";            dst = "cache\embeddings_v2\meta.json" },
    @{ src = "cache/embeddings_v2/queries.json";         dst = "cache\embeddings_v2\queries.json" },
    @{ src = "cache/embeddings_v2/queries.npy";          dst = "cache\embeddings_v2\queries.npy" },
    @{ src = "cache/embeddings_v2/query_meta.json";      dst = "cache\embeddings_v2\query_meta.json" },
    @{ src = "cache/embeddings_v2/relation_emb.npy";     dst = "cache\embeddings_v2\relation_emb.npy" },
    @{ src = "cache/embeddings_v2/relation_meta.json";   dst = "cache\embeddings_v2\relation_meta.json" },
    @{ src = "webqsp/epoch5.pt"; dst = "checkpoints\webqsp\epoch5.pt" },
    @{ src = "cwq/epoch4.pt";    dst = "checkpoints\cwq\epoch4.pt" }
)

foreach ($f in $files) {
    $target = Join-Path $root $f.dst
    $dir = Split-Path $target -Parent
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $url = "$repo/$($f.src)"
    Write-Output "downloading $($f.src) -> $($f.dst)"
    & curl.exe -L -C - -sS --retry 5 --retry-delay 2 -o $target $url
    if ($LASTEXITCODE -ne 0) { Write-Output "FAILED $($f.src)"; exit 1 }
}
Write-Output "ALL_DONE"
