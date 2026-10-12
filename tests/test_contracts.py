"""Offline failure-injection contracts. Fake clients never count as Azure proof."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pymongo.auth_oidc import OIDCCallbackContext, OIDCCallbackResult
from pymongo.errors import OperationFailure

import auth
from config import Config, SampleError, SCOPE, load_config, native_uri
import data
import fixture
import runner
import search


@pytest.fixture
def config():
    return Config(database="vectorsearch_disposable_unit", prefix="native", auth_mode="connection-string",
                  uri="mongodb+srv://user:password@example.global.mongocluster.cosmos.azure.com/?tls=true&retryWrites=false",
                  query_file="query.json", allow_writes=True)


def doc(key="1", x=1):
    return {"HotelId": key, "DescriptionVector": [x] + [0] * 1535, "HotelName": "fixture"}


class Cursor:
    def __init__(self, items):
        self.items = items
        self.closed = False
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.closed = True
    def __iter__(self):
        return iter(self.items)


class Collection:
    def __init__(self, c):
        self.name = c.collection("ivf", "COS")
        self.database = SimpleNamespace(name=c.database, command=self.command)
        self.catalog, self.docs, self.calls = [], {}, []
        self.incomplete = False
    def command(self, command):
        self.calls.append("create")
        self.catalog.extend(copy.deepcopy(command["indexes"]))
    def list_indexes(self):
        return Cursor(copy.deepcopy(self.catalog))
    def bulk_write(self, operations, ordered):
        assert ordered is True
        self.calls.append("seed")
        new = existing = 0
        for operation in operations:
            assert operation._upsert is True
            assert set(operation._doc) == {"$setOnInsert"}
            key = operation._filter["_id"]
            if key in self.docs:
                existing += 1
            else:
                self.docs[key] = copy.deepcopy(operation._doc["$setOnInsert"])
                new += 1
        return SimpleNamespace(acknowledged=True, upserted_count=0 if self.incomplete else new, matched_count=existing)


@pytest.mark.parametrize("tail", ["tls=false", "TLSInsecure=true", "tlsAllowInvalidCertificates=true", "tlsAllowInvalidHostnames=true",
    "authMechanism=PLAIN", "authMechanism=MONGODB-OIDC", "authMechanismProperties=ENVIRONMENT:azure", "proxyHost=evil", "tlsCAFile=evil",
    "retryWrites=true", "tls=true&TLS=true", "authSource=other", "appName=anything", "tls=true;tlsInsecure=true"])
def test_native_options_fail_closed(config, tail):
    with pytest.raises(SampleError):
        native_uri(config.uri.split("?")[0] + "?" + tail)


@pytest.mark.parametrize("uri", ["mongodb://user:pass@example.mongocluster.cosmos.azure.com", "mongodb+srv://user:pass@evil.test/",
    "mongodb+srv://user:pass@example.mongocluster.cosmos.azure.com.evil.test/", "mongodb+srv://user:pass@example.mongocluster.cosmos.azure.com:443/",
    "mongodb+srv://user:pass@example.mongocluster.cosmos.azure.cn/", "mongodb+srv://user:pass@localhost/", "mongodb+srv://example.mongocluster.cosmos.azure.com/",
    "mongodb+srv://user:pass@example.mongocluster.cosmos.azure.com/db", "mongodb+srv://user:pass@example.mongocluster.cosmos.azure.com/#",
    "mongodb+srv://user:pass@example.mongocluster.cosmos.azure.com/\n", "mongodb+srv://user:%ZZ@example.mongocluster.cosmos.azure.com/"])
def test_native_hosts_rejected(uri):
    with pytest.raises(SampleError):
        native_uri(uri)


def test_good_native_and_fixed_options(config):
    assert native_uri(config.uri) == config.uri
    opts = auth.client_options(config)
    assert opts["tls"] is True and opts["tlsAllowInvalidHostnames"] is False
    assert opts["authMechanism"] == "SCRAM-SHA-256"
    assert opts["retryWrites"] is False and opts["timeoutMS"] == 120000
    assert "password" not in repr(config)


@pytest.mark.parametrize("change", [dict(database="Hotels"), dict(prefix="../oops"), dict(allow_writes=False), dict(auth_mode="default"),
    dict(query_file=""), dict(dimensions=3), dict(top_k=0), dict(timeout=0), dict(case_timeout=301)])
def test_configuration_and_write_scope(config, change):
    with pytest.raises(SampleError):
        replace(config, **change).validate(writes=True)


def test_offline_model_config_irrelevant(config):
    env = dict(MONGO_AUTH_MODE=config.auth_mode, MONGO_CONNECTION_STRING=config.uri, MONGO_DATABASE=config.database,
               MONGO_COLLECTION_PREFIX=config.prefix, QUERY_VECTOR_FILE="q", AZURE_OPENAI_EMBEDDING_ENDPOINT="not-even-a-url")
    assert load_config(env).query_file == "q"
    for key, value in [("EMBEDDING_MODE", "azure-openai"), ("MONGO_COLLECTION", "Hotels"), ("TOP_K", "secret"), ("VECTOR_SEARCH_ALLOW_WRITES", "yes")]:
        with pytest.raises(SampleError):
            load_config(dict(env, **{key: value}))


def test_validate_before_credentials_dns(config, monkeypatch):
    mongo, credential = Mock(), Mock()
    monkeypatch.setattr(auth, "MongoClient", mongo)
    monkeypatch.setattr(auth, "AzureCliCredential", credential)
    c = replace(config, auth_mode="oidc", uri="", host="evil.test")
    with pytest.raises(SampleError):
        with auth.connect(c):
            pass
    mongo.assert_not_called()
    credential.assert_not_called()


def test_token_current_contract_fixed_audience_and_expiry():
    credential = Mock()
    credential.get_token.return_value = SimpleNamespace(token="test-token", expires_on=2000)
    cb = auth.AzureTokenCallback(credential, clock=lambda: 1000)
    ctx = OIDCCallbackContext(timeout_seconds=10, username="u", version=1,
                              idp_info=SimpleNamespace(scopes=["evil-audience"], issuer="evil"))
    result = cb.fetch(ctx)
    assert isinstance(result, OIDCCallbackResult)
    assert result.access_token == "test-token" and result.expires_in_seconds == 1000
    credential.get_token.assert_called_once_with(SCOPE)


@pytest.mark.parametrize("token,expiry", [("", 2000), ("test", 1000), ("test", 1029)])
def test_bad_tokens(token, expiry):
    cb = auth.AzureTokenCallback(SimpleNamespace(get_token=lambda _: SimpleNamespace(token=token, expires_on=expiry)), clock=lambda: 1000)
    with pytest.raises(SampleError):
        cb.fetch(OIDCCallbackContext(10, "u", 1))


def test_token_failure_sanitized():
    c = Mock()
    c.get_token.side_effect = RuntimeError("password-secret")
    with pytest.raises(SampleError) as exc:
        auth.AzureTokenCallback(c).fetch(OIDCCallbackContext(10, "u", 1))
    assert "password-secret" not in str(exc.value)


def test_transport_closed_success_and_ping_failure(config, monkeypatch):
    for failure in (False, True):
        client, cred = Mock(), Mock()
        if failure:
            client.admin.command.side_effect = RuntimeError("sensitive")
        monkeypatch.setattr(auth, "MongoClient", Mock(return_value=client))
        monkeypatch.setattr(auth, "AzureCliCredential", Mock(return_value=cred))
        c = replace(config, auth_mode="oidc", uri="", host="example.global.mongocluster.cosmos.azure.com")
        try:
            with auth.connect(c):
                pass
        except RuntimeError:
            assert failure
        client.close.assert_called_once()
        cred.close.assert_called_once()


@pytest.mark.parametrize("value", [[0]*1536, [True]*1536, [None]*1536, ["1"]*1536, [float("nan")]*1536, [float("inf")]*1536, [1]*3])
def test_bad_vector_schema(value):
    with pytest.raises(SampleError):
        data.numeric_vector(value)


def test_duplicate_and_unsafe_seed_before_mutations(config):
    original = doc()
    prepared = data.prepare_seed([original])
    assert prepared[0]["_id"] == "vectorsearch:1" and "_id" not in original
    for invalid in ([doc(), doc()], [dict(doc(), _id=1)], [dict(doc(), nest={"$bad": 1})], [None]):
        c = Collection(config)
        with pytest.raises(SampleError):
            search.seed(c, invalid, config, "ivf", "COS")
        assert not c.calls


def test_json_duplicates_nan_and_size(tmp_path):
    for raw in (b'{"x":1,"x":2}', b'[NaN]', b'nope'):
        with pytest.raises((SampleError, ValueError)):
            data.decode(raw)
    p = tmp_path / "large"
    p.write_bytes(b" " * (data.MAX_BYTES + 1))
    with pytest.raises(SampleError):
        data.read_bytes(p)


def test_populated_unindexed_and_insert_only_idempotence(config):
    c = Collection(config)
    c.docs = {"vectorsearch:1": dict(doc(), _id="vectorsearch:1", HotelName="user edit"), "unrelated": {"keep": True}}
    docs = data.prepare_seed([doc(), doc("2")])
    search.ensure_index(c, config, "ivf", "COS")
    first = search.seed(c, docs, config, "ivf", "COS")
    search.ensure_index(c, config, "ivf", "COS")
    second = search.seed(c, docs, config, "ivf", "COS")
    assert c.calls == ["create", "seed", "seed"]
    assert first == dict(total=2, inserted=1, existing=1)
    assert second == dict(total=2, inserted=0, existing=2)
    assert c.docs["vectorsearch:1"]["HotelName"] == "user edit"
    assert c.docs["unrelated"] == {"keep": True}


@pytest.mark.parametrize("extra", [dict(partialFilterExpression={"HotelName": "x"}), dict(sparse=True), dict(unique=True), dict(hidden=True),
    dict(expireAfterSeconds=0), dict(collation={"locale": "en"}), dict(futureOption=False), dict(unique=0), dict(hidden="false")])
def test_full_catalog_conflicts_before_any_mutation(config, extra):
    c = Collection(config)
    c.catalog = [dict(search.index_spec(config, "ivf", "COS"), **extra)]
    with pytest.raises(SampleError):
        search.run_case(c, config, "ivf", "COS", [doc()], doc()["DescriptionVector"], time.monotonic() + 10)
    assert c.calls == [] and c.docs == {}


def test_catalog_numeric_options_and_alternate_names(config):
    spec = search.index_spec(config, "ivf", "COS")
    good = copy.deepcopy(spec)
    good.update(name="other", v=2, ns="metadata", background=True, sparse=False, unique=False, hidden=False)
    good["cosmosSearchOptions"]["numLists"] = 1.0
    assert search.compatible_index([good], spec)
    for change in ({"dimensions": True}, {"dimensions": "1536"}, {"extra": 1}, {"numLists": 2}):
        bad = copy.deepcopy(good)
        bad["cosmosSearchOptions"].update(change)
        with pytest.raises(SampleError):
            search.compatible_index([bad], spec)


def test_bad_name_or_compound_index(config):
    spec = search.index_spec(config, "ivf", "COS")
    for key in ({"Other": 1}, {"DescriptionVector": "cosmosSearch", "Other": 1}):
        with pytest.raises(SampleError):
            search.compatible_index([dict(spec, key=key)], spec)


def test_catalog_missing_after_create_and_namespace(config):
    c = Collection(config)
    c.database.command = Mock()
    with pytest.raises(SampleError):
        search.ensure_index(c, config, "ivf", "COS")
    assert not c.docs
    c.name = "different"
    with pytest.raises(SampleError):
        search.ensure_index(c, config, "ivf", "COS")


def test_bulk_incomplete_and_failure_propagate(config):
    c = Collection(config)
    c.incomplete = True
    with pytest.raises(SampleError):
        search.seed(c, [doc()], config, "ivf", "COS")
    c.bulk_write = Mock(side_effect=OperationFailure("private document", code=11000))
    with pytest.raises(OperationFailure):
        search.seed(c, [doc()], config, "ivf", "COS")


def test_metric_oracle_not_cross_metric_score(config):
    docs = [{"_id": "a", "DescriptionVector": [1, 0]}, {"_id": "b", "DescriptionVector": [2, 1]},
            {"_id": "c", "DescriptionVector": [0, 1]}]
    assert search.expected_ranks(docs, [1, 0], "IP")[0][0] == "b"
    assert search.expected_ranks(docs, [1, 0], "COS")[0][0] == "a"
    assert search.expected_ranks(docs, [1, 0], "L2")[0][0] == "a"
    c = replace(config, top_k=3)
    for metric in ("COS", "L2", "IP"):
        rows = [{"document": {"_id": key}, "score": 17} for key, _ in search.expected_ranks(docs, [1, 0], metric)]
        search.check_quality(rows, docs, [1, 0], c, metric)
        rows[0]["document"]["_id"] = "unknown"
        with pytest.raises(SampleError):
            search.check_quality(rows, docs, [1, 0], c, metric)


@pytest.mark.parametrize("score", [None, "1", True, float("inf")])
def test_result_score_schema(config, score):
    docs = data.prepare_seed([doc()])
    with pytest.raises(SampleError):
        search.check_quality([{"document": docs[0], "score": score}], docs, doc()["DescriptionVector"], config, "COS")


def test_attempt_all_nine_aggregate_failure_and_redaction(config):
    attempted = []
    def invoke(a, m, deadline):
        attempted.append((a, m))
        if a == "diskann" or (a, m) == ("ivf", "COS"):
            raise OperationFailure("token-supersecret", code=67)
        return {"hits": 5}
    report = runner.execute_cases(config, runner.cases(True), invoke, time.monotonic() + 10)
    assert attempted == runner.cases(True)
    assert len({r["collection"] for r in report["cases"]}) == 9
    assert report["succeeded"] == 5 and report["failed"] == 4
    assert "token-supersecret" not in json.dumps(report)


def test_expired_deadline_marks_every_case_failure(config):
    invoke = Mock()
    report = runner.execute_cases(config, runner.cases(True), invoke, time.monotonic() - 1)
    assert report["failed"] == 9
    invoke.assert_not_called()


def test_cli_errors_nonzero_and_no_secret_echo(monkeypatch, capsys):
    monkeypatch.setattr(runner, "load_config", Mock(side_effect=RuntimeError("token-secret")))
    assert runner.cli(argv=[]) == 1
    assert "token-secret" not in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        runner.cli(argv=["--password=secret-marker"])
    assert e.value.code == 2
    assert "secret-marker" not in capsys.readouterr().err


def test_explicit_live_missing_optin_is_failure(monkeypatch, capsys):
    monkeypatch.delenv("VECTOR_SEARCH_LIVE_TEST", raising=False)
    load = Mock()
    monkeypatch.setattr(runner, "load_config", load)
    assert runner.cli(argv=["--live-check"]) == 2
    assert "requires VECTOR_SEARCH_LIVE_TEST=1" in capsys.readouterr().err
    load.assert_not_called()


def test_cli_aggregate_status_and_cancel(config, monkeypatch, capsys):
    monkeypatch.setattr(runner, "load_config", lambda: config)
    for failed in (0, 1):
        monkeypatch.setattr(runner, "run", lambda *a, **kw: dict(failed=failed))
        assert runner.cli(argv=[]) == failed
    monkeypatch.setattr(runner, "run", Mock(side_effect=KeyboardInterrupt))
    assert runner.cli(argv=[]) == 130


def test_safe_output_and_redirect(tmp_path):
    p = tmp_path / "out"
    fixture.write_new(p, b"first")
    with pytest.raises(FileExistsError):
        fixture.write_new(p, b"second")
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(FileExistsError):
        fixture.write_new(link, b"replace")
    assert p.read_bytes() == b"first"
    with pytest.raises(SampleError):
        fixture.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test")


def test_fixture_bad_hash_no_output(tmp_path):
    source, output = tmp_path / "input", tmp_path / "output"
    source.write_text("[]")
    assert fixture.main(["extract", "--input", str(source), "--output", str(output)]) == 1
    assert not output.exists()


def test_actual_entrypoints_exit_without_live_permission():
    src = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ)
    env.pop("VECTOR_SEARCH_LIVE_TEST", None)
    entrypoints = [p for p in src.glob("*.py") if p.stem in ("ivf", "hnsw", "diskann", "run_all", "compare_all")]
    assert entrypoints
    for entry in entrypoints:
        result = subprocess.run([sys.executable, str(entry), "--live-check"], env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 2
        assert "requires VECTOR_SEARCH_LIVE_TEST=1" in result.stderr


def test_no_destructive_code_or_model_dependency():
    src = Path(__file__).resolve().parents[1] / "src"
    for p in src.glob("*.py"):
        text = p.read_text()
        for forbidden in (".drop(", ".drop_index(", ".delete_many(", ".replace_one(", "load_dotenv(", "from openai"):
            assert forbidden not in text


def test_real_driver_accepts_oidc_options_without_network(config):
    # Real driver constructor/credential parsing, with no connection/DNS.
    from pymongo import MongoClient
    c = replace(config, auth_mode="oidc", uri="", host="example.global.mongocluster.cosmos.azure.com")
    with MongoClient("mongodb://localhost:27017", connect=False, **auth.client_options(c, Mock())) as client:
        assert client.options.pool_options.max_pool_size == 5


def test_cursor_error_propagates_and_closes(config):
    class BrokenCursor(Cursor):
        def __iter__(self):
            raise OperationFailure("sensitive cursor", code=91)
    cursor = BrokenCursor([])
    collection = SimpleNamespace(aggregate=lambda _: cursor)
    with pytest.raises(OperationFailure):
        search.search(collection, doc()["DescriptionVector"], config)
    assert cursor.closed


def test_real_run_preflight_precedes_connect(config, tmp_path, monkeypatch):
    path = tmp_path / "bad-data"
    path.write_text("[]")
    connect = Mock()
    monkeypatch.setattr(runner, "connect", connect)
    with pytest.raises(SampleError):
        runner.run(replace(config, data_file=str(path)), runner.cases())
    connect.assert_not_called()


def test_empty_retry_only_and_nonempty_failure_no_retry(config, monkeypatch):
    c = Collection(config)
    docs = data.prepare_seed([doc(str(i), 1 + i) for i in range(5)])
    rows = [{"document": d, "score": 1} for d in docs]
    call = Mock(side_effect=[[], rows])
    monkeypatch.setattr(search, "search", call)
    monkeypatch.setattr(search.time, "sleep", lambda _: None)
    report = search.run_case(c, config, "ivf", "COS", docs, doc()["DescriptionVector"], time.monotonic() + 5)
    assert report["hits"] == 5 and call.call_count == 2
    call = Mock(side_effect=OperationFailure("private", code=2))
    monkeypatch.setattr(search, "search", call)
    with pytest.raises(OperationFailure):
        search.run_case(c, config, "ivf", "COS", docs, doc()["DescriptionVector"], time.monotonic() + 5)
    assert call.call_count == 1


def test_same_name_conflict_late_in_catalog_never_mutates(config):
    c = Collection(config)
    c.catalog = [search.index_spec(config, "ivf", "COS"), dict(search.index_spec(config, "ivf", "COS"), hidden=True)]
    with pytest.raises(SampleError):
        search.ensure_index(c, config, "ivf", "COS")
    assert not c.calls


def test_index_kind_parameters_are_case_specific(config):
    for a in ("ivf", "hnsw", "diskann"):
        for m in ("COS", "L2", "IP"):
            opts = search.index_spec(config, a, m)["cosmosSearchOptions"]
            assert opts["kind"] == "vector-" + a and opts["similarity"] == m
            if a == "diskann":
                assert set(opts) == {"kind", "similarity", "dimensions", "maxDegree", "lBuild"}
            if a == "hnsw":
                assert set(opts) == {"kind", "similarity", "dimensions", "m", "efConstruction"}
