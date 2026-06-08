# Consolidation C0 — Evidence Manifest (Workstream B)

> The **raw** evidence package (which contains the IG tree's full unredacted `git diff`) is stored
> **outside both git repositories** in a restricted directory. **This git document is redacted**: it
> records only paths, hashes, and metadata — **no diff contents, no secrets, no account IDs**. The
> raw archive must never be committed.

## 1. Restricted evidence location

```text
restricted directory   /root/consolidation_evidence/            (mode 0700, outside /root/trading and /root/trading-ig)
raw freeze snapshot     /root/consolidation_evidence/ig_freeze_20260608T195936Z/   (dir)
sealed archive          /root/consolidation_evidence/ig_freeze_20260608T195936Z.tar.gz   (mode 0600)
archive SHA-256         c513cad65dde630664d4cc0e639bdb8aec82b0c1988d5b28870f6d86ac10024d
captured (UTC)          2026-06-08T19:59:36Z
source preserved        /root/trading-ig was NOT modified during capture (read-only git/diff/hash)
listable w/o extract    verified: `tar -tzf` lists 10 entries without extracting over the source tree
```

## 2. What the raw package preserves (the dirty IG state)

```text
git status output            git_status.txt        (40 lines)
tracked unstaged diff        tracked_unstaged.diff (2951 lines — UNREDACTED; restricted only)
staged diff                  staged.diff           (empty — nothing staged)
untracked-file inventory     untracked_inventory.txt (19 dir entries)
file hashes (modified+untracked)  file_hashes.txt
db/config hashes             db_config_hashes.txt  (instruments_ig.json + *.db)
branch and HEAD              head.txt  (claude-strategy / d95d258…)
timestamps                   timestamp.txt
service-unit references      recorded in consolidation_c0_inventory.md (redacted)
```

## 3. Redacted hashes of evidence files (safe for git)

These are SHA-256 hashes of the evidence files themselves — hashes are not secrets; the underlying
diff content is **not** reproduced here.

```text
git_status.txt          f6f457b11255ecaa5f6e7dfd05069e4b7b9aafc0dd69c840f10837525bea315c
staged.diff             e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  (empty)
tracked_unstaged.diff   b58c7e53be597a26f6142413b67b62b4f463a29d49927819d3daa1feb02596b0
untracked_inventory.txt 82b8e0150f15f90370740308aa6ed5dbd8be677de408fb5de30989caa392a641
file_hashes.txt         3d96f1d697ef3d45eda97bd1814a01f8107bf409280c324f5a5cf7e277bb359a
db_config_hashes.txt    5c77deab07d473f36b0d7defe4d360200380e6cd8f787041fe0333509babab33
head.txt                6c2618e16629c03285c73d69fb08db0b79fb16cc0dc071fc712bf841e326efa3
timestamp.txt           7cb94d16e93a92891e370779af0725213cd285f937d6c0076e245e00e5f9179d
```

## 4. Handling rules

```text
[ ] The raw archive (and ig_freeze_…/ directory) stay in /root/consolidation_evidence/ (0700).
[ ] Never `git add` the raw diff/archive — tracked_unstaged.diff may contain config that should
    not enter git history.
[ ] Verify integrity before any future consolidation: sha256sum the archive == value in §1.
[ ] Re-create from source ONLY read-only; do not extract over /root/trading-ig.
[ ] Retention: keep until consolidation is approved, verified, and rolled forward; then delete on
    operator instruction.
```
