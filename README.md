# vector-search-python

A standalone Python 3.12 sample for **IVF, HNSW and DiskANN vector search on Azure DocumentDB**. The primary path uses 50 public hotel records with precomputed 1536-dimensional vectors and a JSON query vector. **No Azure OpenAI resource, model configuration or model call is needed.**

Each algorithm uses a separate `<prefix>_<algorithm>_cos` collection. The runner checks a real vector index, insert-only fixture loading, top-5 known fixture membership and exact cosine ranking. This tiny fixture is a functional contract, **not a performance or semantic-relevance benchmark**. See [TESTED.md](TESTED.md) for proof boundaries and [UPSTREAM.md](UPSTREAM.md) for the pinned Microsoft source and downstream changes.

## Prerequisites and costs

- Patched **Python 3.12** (tested on 3.12.13), Git, and network access to an approved disposable Azure DocumentDB cluster in public Azure.
- A tier/region supporting the requested algorithms. **S0 is not eligible for this full vector gate.** Do not infer DiskANN eligibility from successful authentication. Check current service requirements before provisioning; the sample neither deploys nor upgrades resources.
- Either native database SCRAM credentials, or an Azure CLI sign-in whose Entra principal has been registered/authorized in the database. Azure RBAC/sign-in alone does not grant data-plane access. For OIDC use `az login` outside the sample and verify the intended account/resource.
- Database calls and provisioned resources can incur charges even though embedding inference is absent. Use least-privilege database access and deliberate firewall rules. No China-cloud, arbitrary MongoDB endpoint, local insecure-TLS mode, deployment automation or production-use claim is included.

## 1. Install reproducibly

POSIX shell, from the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
```

PowerShell: use `py -3.12 -m venv .venv` and `.\.venv\Scripts\python.exe` in place of `.venv/bin/python`. Dependency locks include all transitive runtime dependencies and hashes; development/security dependencies are separately locked in `requirements-dev.txt`.

## 2. Retrieve the pinned public fixture

```sh
.venv/bin/python src/fixture.py download
.venv/bin/python src/fixture.py extract
```

These are explicit operations, not install side effects. The downloader accepts only the pinned public GitHub URL, validates SHA-256 and 50x1536 schema, has a 30-second network timeout/8 MiB bound, and rejects redirects. The extractor writes the first hotel's vector to `data/query-vector.json`. Both refuse to overwrite existing files or symlinks. If the fixture already exists, skip `download`; choose a new `--output` for repeated extraction. No environment or credentials are written. See [data/README.md](data/README.md) for dataset provenance/terms; a checksum is not a separate data-rights grant.

## 3. Explicit configuration and authentication

Only the **process environment** is read. `.env` is **not** loaded or executed automatically. `.env.example` documents placeholders; never commit credentials. Do not paste connection strings into tracked files, commands saved in shell history, CI, or reports. Supply `MONGO_CONNECTION_STRING` privately through your normal secret manager/process launcher.

Common settings (POSIX; in PowerShell use `$env:NAME='value'`):

```sh
export MONGO_DATABASE=vectorsearch_disposable_my_run
export MONGO_COLLECTION_PREFIX=native
export DATA_FILE_WITH_VECTORS=data/Hotels_Vector.json
export QUERY_VECTOR_FILE=data/query-vector.json
export EMBEDDING_MODE=offline
export VECTOR_SEARCH_ALLOW_WRITES=true
```

`MONGO_DATABASE` must begin exactly `vectorsearch_disposable`, optionally followed by `_lowercase_run_id`, maximum 63 characters. Prefixes are simple lowercase identifiers, maximum 40 characters. There are **no default destinations**. Use distinct `native` and `oidc` collection prefixes when checking both auth modes.

### Native SCRAM

Set `MONGO_AUTH_MODE=connection-string` and privately inject `MONGO_CONNECTION_STRING`. Unset `MONGO_HOST` and `MONGO_OIDC_USERNAME`. The URI must be:

```text
mongodb+srv://<percent-encoded-user>:<percent-encoded-password>@<cluster>.global.mongocluster.cosmos.azure.com/?tls=true&retryWrites=false&authMechanism=SCRAM-SHA-256
```

Non-global `<cluster>.mongocluster.cosmos.azure.com` is also allowed. No port, database path, fragment, alternative host, duplicate option or unknown URI option is accepted. Allowed URI options are `tls/ssl=true`, `retryWrites=false`, `authSource=admin`, `authMechanism=SCRAM-SHA-256`, and the three TLS-bypass options **only when false**. Remove harmless portal telemetry options such as `appName` when preparing the secret URI. The driver forces verified TLS, SCRAM-SHA-256, admin auth source, bounded networking and disabled retryable writes.

### Entra OIDC

```sh
unset MONGO_CONNECTION_STRING
export MONGO_AUTH_MODE=oidc
export MONGO_HOST='<cluster>.global.mongocluster.cosmos.azure.com'
export MONGO_COLLECTION_PREFIX=oidc
# Optional, when required by the configured database principal:
# export MONGO_OIDC_USERNAME='<registered principal username>'
```

OIDC deliberately uses **AzureCliCredential**, not a broad automatic credential chain. CLI token acquisition is limited to 10 seconds; no fallback to native auth or another credential source is performed. The fixed audience is `https://ossrdbms-aad.database.windows.net/.default`, never an audience supplied by a server. The actual PyMongo 4.18 callback returns `OIDCCallbackResult(access_token=..., expires_in_seconds=...)` after rejecting empty/expired/near-expiry tokens. The Azure CLI owns its existing sign-in cache; the sample stores no credentials. Clients and credentials close on success, failure and cancellation.

## 4. Run

```sh
.venv/bin/python src/run_all.py
# Or choose one:
.venv/bin/python src/ivf.py
.venv/bin/python src/hnsw.py
.venv/bin/python src/diskann.py
```

Every CLI error exits nonzero. `run_all.py` records all three case outcomes; any failed/unsupported case makes the aggregate exit nonzero. JSON reports contain controlled status/count fields, never raw server exceptions, document contents, URIs or tokens. Errors with a server code retain that numeric code, not sensitive error text. Global connection/input failures end before any cases. Ctrl-C/SIGTERM cancels and closes clients; data already inserted is preserved.

Timeout controls: `VECTOR_SEARCH_TIMEOUT_SECONDS` defaults to 600 (1–1800); `VECTOR_SEARCH_CASE_TIMEOUT_SECONDS` defaults to 120 (1–300). PyMongo client-side operation deadlines and bounded connect/select/socket waits are enabled. Only successful **empty** searches are retried while index visibility catches up. Authentication, tier, schema and ranking errors are never silently retried or downgraded to success.

### Write-safety and reruns

- Every write needs `VECTOR_SEARCH_ALLOW_WRITES=true` and the disposable namespace. Invalid inputs/destinations are rejected **before credential construction or network access**.
- Each case validates the **entire index catalog document before any mutation**, even if its collection is populated. Incompatible kind, dimensions, metric, algorithm parameters, partial/sparse/unique/hidden/TTL/collation or unknown semantics fail closed. No existing index is dropped or modified. Missing indexes are created and read back.
- Seed IDs are existing string `_id` values or deterministic `vectorsearch:<HotelId>`. Ordered `$setOnInsert` upserts only add missing IDs. Existing edits and unrelated records remain untouched. All input is validated before any writes; failures/partial batch acknowledgements are not swallowed. Retrying an interrupted run is insert-only and idempotent.
- Preserved edits/unrelated vectors can legitimately fail the known-fixture quality gate. Use a fresh approved collection prefix; **the sample will not delete or overwrite data to manufacture a pass**. Concurrent DDL/edits by another operator are outside this small sample's isolation guarantees.
- There are no reset/drop/DeleteMany commands or automatic cleanup. Inspect retained sample data and have the authorized resource owner delete only the intended disposable database/resource afterward. Billing continues until resources are stopped/deleted as appropriate.

## Offline tests, security checks, and explicit real gate

```sh
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
.venv/bin/python -m compileall -q src tests tests_live
.venv/bin/python -m pytest -q
.venv/bin/python -m pip_audit -r requirements-dev.txt --no-deps --disable-pip --vulnerability-service osv
```

Default tests are no-network failure-injection contracts, not fake Azure readiness. CI uses SHA-pinned actions, read-only permissions, exact Python 3.12.13, hash-locked installs, compile/tests and current advisory scanning; no Azure secrets/deployments.

After separately authorizing an eligible cluster and configuring it above:

```sh
export VECTOR_SEARCH_LIVE_TEST=1
.venv/bin/python src/run_all.py --live-check
.venv/bin/python -m pytest tests_live -q
```

The explicit gate **fails**, rather than skipping, if live opt-in is absent. It requires the checksum-pinned 50-record fixture and first-record self query, runs each case twice, checks top 5/exact metric ranking/self-match, and checks second-run zero inserts/all 50 existing. Repeat under **both auth modes with separate prefixes**. It never drops its data. A blocked tier/network/credential is a failed/unverified gate, not a mock pass.

## Scope, maintenance and license

The broken optional live embedding generator and index-inspection legacy entry points were removed, not left as an untested side path. Precomputed vectors are the only supported embedding mode. Supply a compatible JSON vector via `QUERY_VECTOR_FILE`; this repo does not generate embeddings. Missing OpenAI configuration is irrelevant.

Original Microsoft sample code/notice is preserved under [MIT](LICENSE.md). This is an independently maintained downstream snapshot, not an automatically synchronized fork. Review and port upstream fixes manually and record changes in [UPSTREAM.md](UPSTREAM.md). A passing small-fixture gate does not certify production scale, availability, custom datasets, model relevance or every service tier.
