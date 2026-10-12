# Verification scope

## Local verification — 2026-10-12

- Python 3.12.13; PyMongo 4.18.3; Azure Identity 1.26.0; pytest 9.1.1.
- Hash-locked runtime and development dependencies install successfully in an isolated environment; dependency compatibility and compile checks pass.
- **86 passed, zero skipped** in the permanent offline failure-injection suite. Coverage includes strict URI/TLS/destination validation, pre-credential rejection, actual installed PyMongo OIDC callback result/expiry contract, fixed audience, token failures, client/credential closure, deterministic insert-only reruns, populated-unindexed collections, full catalog semantic conflicts, partial writes, metric-specific oracle, schema, sanitized/nonzero CLI failures, all-case aggregation, deadlines, safe file output and explicit live refusal. These fake-client tests are **not** an Azure engine/authentication proof.
- pip-audit 2.10.1: current OSV and PyPI advisory queries against all 42 pinned runtime/test/scanner packages report **no known vulnerabilities** on this date. This is a time-bounded dependency advisory result, not a general security guarantee.
- Actual public pinned fixture downloader and extractor run successfully: SHA-256 `60c08c2cdd439444ba8601d94a1f3487f4a9ee56cd48ba201d983c96722de6e4`, 50 documents, 1536 dimensions, self-query extracted. No model/Azure call was made by these operations.
- Explicit `tests_live` invocation without `VECTOR_SEARCH_LIVE_TEST=1` fails (does not skip), before network access.

## Managed Azure proof remains separate

The source/tests above do **not** establish a successful managed Azure run. Before declaring the full walkthrough verified, run the documented pinned live gates on an approved eligible cluster under **native SCRAM and Entra OIDC** with separate collection prefixes. Record exact commit, Python/dependency versions, service tier, case outcomes, idempotent second-run counts and cleanup confirmation without credentials/resource identifiers.

S0 is not eligible for the required full vector gate. Unsupported tier/region, absent permissions, connectivity or a failed algorithm must remain a failed/unverified result; no mock substitutes for a real gate. Optional OpenAI generation is not supported or claimed. Production scale, performance, semantic relevance, custom datasets and other clouds are outside this proof.
