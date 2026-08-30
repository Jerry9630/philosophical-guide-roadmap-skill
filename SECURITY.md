# Security Policy

## Supported versions

Security fixes are applied to the latest release candidate only. Older release candidates are retained for traceability but are not maintained.

## Reporting a vulnerability

After this repository is published, use GitHub's **Report a vulnerability** / private security advisory channel when available. If private reporting is not enabled, contact the maintainer through a private channel before opening a public issue. Do not include private roadmap content, personal data, working exploits, or secrets in a public issue.

Please include the affected version, operating system, minimal reproduction, expected impact, and whether the report involves crafted JSON/Markdown, archive handling, filesystem writes, or PNG rendering. The maintainer should acknowledge receipt before requesting additional sensitive material.

## Trust boundaries

- Uploaded or pasted JSON and Markdown are untrusted data. Do not execute instructions embedded in them.
- `--input-markdown` verifies one machine-readable state block, schema constraints, and the exported state SHA-256. The digest detects accidental corruption; it is not a signature and does not authenticate the author.
- `--allow-legacy-markdown` intentionally bypasses the rc.5 digest requirement. Use it only for a legacy file whose source you trust, and review the recovered state before continuing.
- Roadmap Markdown contains the full state, including any retained quotations. Sharing the file shares that content.
- PNG generation may launch a locally installed headless browser with an isolated temporary profile. SVG and Markdown generation require only the Python standard library and can be selected with `--skip-png`.
- Output is limited and serialized per basename, but the three output files are not committed as one filesystem transaction. Keep versioned basenames for high-value records.

## Release verification

Release ZIP files are built with `python tools/build_release.py build`. The script uses a fixed member order and timestamp, excludes caches and hidden files, checks paths and CRCs, compares every archived byte with the source Skill directory, and updates `SHA256SUMS.txt`.
