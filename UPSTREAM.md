# Upstream provenance

- Source repository: https://github.com/Azure-Samples/documentdb-samples
- Exact source revision: [f5a2675bbf349737234d0391e3ebee24b260e06f](https://github.com/Azure-Samples/documentdb-samples/commit/f5a2675bbf349737234d0391e3ebee24b260e06f)
- Source directory: [ai/vector-search-python](https://github.com/Azure-Samples/documentdb-samples/tree/f5a2675bbf349737234d0391e3ebee24b260e06f/ai/vector-search-python)
- Extraction date: 2026-09-30
- License: original Microsoft MIT notice preserved verbatim in [LICENSE.md](LICENSE.md)
- History model: independently maintained source snapshot, not a GitHub fork or ownership transfer

## Standalone adaptations

2026-10-12 downstream safety/runtime rewrite (not an upstream revision change):

- Python 3.12.13 baseline; complete hash-locked runtime and test/security dependencies.
- Precomputed 50x1536 hotel-vector path only. Removed the broken optional OpenAI generator/model coupling and unsafe legacy helpers; no model credentials required.
- Strict public-Azure SRV/TLS validation before credentials/DNS, native SCRAM-SHA-256 or explicit Azure CLI OIDC with fixed audience and current PyMongo relative-expiry callback contract.
- Explicit disposable namespace/write opt-in, isolated per-case collections, full-catalog index compatibility checks, deterministic insert-only upserts, no reset/drop/delete behavior.
- All-case failure aggregation, independent metric-specific fixture oracle, bounded operations, sanitized CLI output, reproducible pinned fixture downloader/extractor.
- Permanent offline failure-injection tests, separately explicit real tests, read-only SHA-pinned CI without Azure secrets/deployment.
- README and placeholder-only process configuration rewritten as standalone instructions. Public-data source/terms remain linked in data/README.md; no hotel fixtures are redistributed.

The original Microsoft MIT notice is preserved verbatim. These are downstream adaptations, not a claim of upstream authorship or automatic synchronization. Safety-contract concepts were reviewed against the independently maintained Azure-DocumentDB Go sibling samples; Python uses its actual driver APIs rather than a language-blind translation.

## Maintenance

There is no automatic synchronization. Review upstream changes against this pinned baseline, apply only relevant fixes, retain notices, and record the imported revision and local adaptations here.
Issue/PR history is not transferred. The maintainers of the repository hosting this extraction own its downstream fixes; contribute reusable fixes upstream under its contribution policy.
