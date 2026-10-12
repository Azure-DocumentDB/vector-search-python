"""Non-destructive catalog/seed/search contracts, independent of CLI output."""
import math
import time

from pymongo import UpdateOne
from pymongo.errors import OperationFailure

from config import SampleError
from data import numeric_vector, prepare_seed


def index_spec(c, algorithm, metric):
    c.validate(writes=True)
    c.collection(algorithm, metric)
    options = dict(kind="vector-" + algorithm, dimensions=c.dimensions, similarity=metric)
    options.update({"ivf": dict(numLists=1), "hnsw": dict(m=16, efConstruction=64),
                    "diskann": dict(maxDegree=20, lBuild=50)}[algorithm])
    return dict(name=f"{algorithm}_{metric}_index_DescriptionVector",
                key={"DescriptionVector": "cosmosSearch"}, cosmosSearchOptions=options)


def _equal(actual, expected):
    if isinstance(expected, str):
        return isinstance(actual, str) and actual == expected
    return type(actual) in (int, float) and math.isfinite(actual) and actual == expected


def compatible_index(indexes, expected):
    found = False
    for index in indexes:
        key = index.get("key", {})
        options = index.get("cosmosSearchOptions", {})
        relevant = (index.get("name") == expected["name"] or
                    ("DescriptionVector" in key and (key["DescriptionVector"] == "cosmosSearch" or options)))
        if not relevant:
            continue
        if key != expected["key"] or not isinstance(options, dict):
            raise SampleError("existing vector index conflicts; use a new collection")
        for name, value in index.items():
            if name in ("name", "key", "cosmosSearchOptions", "v", "ns", "background"):
                continue
            if name in ("sparse", "unique", "hidden") and value is False:
                continue
            # Includes partial filters, TTL, collation, and unknown future semantics.
            raise SampleError("existing vector index has incompatible catalog options; no mutations performed")
        if set(options) != set(expected["cosmosSearchOptions"]) or any(
                not _equal(options[k], v) for k, v in expected["cosmosSearchOptions"].items()):
            raise SampleError("existing vector index settings conflict; no mutations performed")
        found = True
    return found


def indexes(collection):
    try:
        with collection.list_indexes() as cursor:
            return list(cursor)
    except OperationFailure as exc:
        if exc.code == 26:  # namespace not yet created; all other failures propagate
            return []
        raise


def assert_scope(collection, c, algorithm, metric):
    c.validate(writes=True)
    if collection.database.name != c.database or collection.name != c.collection(algorithm, metric):
        raise SampleError("collection does not match authorized namespace")


def ensure_index(collection, c, algorithm, metric):
    assert_scope(collection, c, algorithm, metric)
    expected = index_spec(c, algorithm, metric)
    if compatible_index(indexes(collection), expected):
        return
    collection.database.command({"createIndexes": collection.name, "indexes": [expected]})
    if not compatible_index(indexes(collection), expected):
        raise SampleError("created vector index not visible; retry safely, no cleanup performed")


def seed(collection, docs, c, algorithm, metric):
    assert_scope(collection, c, algorithm, metric)
    # Validate the ENTIRE seed before constructing any write request.
    docs = prepare_seed(docs, c.dimensions)
    stats = dict(total=len(docs), inserted=0, existing=0)
    for start in range(0, len(docs), c.batch_size):
        batch = docs[start:start + c.batch_size]
        operations = [UpdateOne({"_id": d["_id"]}, {"$setOnInsert": d}, upsert=True) for d in batch]
        result = collection.bulk_write(operations, ordered=True)
        if not result.acknowledged or result.upserted_count + result.matched_count != len(batch):
            raise SampleError("incomplete seed acknowledgement; partial writes may remain, retry is insert-only")
        stats["inserted"] += result.upserted_count
        stats["existing"] += result.matched_count
    return stats


def expected_ranks(docs, query, metric):
    if metric not in ("COS", "L2", "IP"):
        raise SampleError("invalid metric")
    query = numeric_vector(query, len(query))
    qnorm = math.hypot(*query)
    values = {}
    for doc in docs:
        key = doc["_id"]
        if not isinstance(key, str) or key in values:
            raise SampleError("oracle requires unique string IDs")
        vector = numeric_vector(doc["DescriptionVector"], len(query))
        if metric == "L2":
            value = math.fsum((a - b) ** 2 for a, b in zip(query, vector))
        elif metric == "COS":
            norm = math.hypot(*vector)
            value = -math.fsum((a / qnorm) * (b / norm) for a, b in zip(query, vector))
        else:
            value = -math.fsum(a * b for a, b in zip(query, vector))
        if not math.isfinite(value):
            raise SampleError("metric oracle overflow")
        values[key] = value
    return sorted(values.items(), key=lambda row: (row[1], row[0]))


def check_quality(results, docs, query, c, metric):
    expected = expected_ranks(docs, query, metric)
    if len(results) != min(c.top_k, len(docs)):
        raise SampleError("quality failure: wrong top-k count")
    values, seen = dict(expected), set()
    for i, result in enumerate(results):
        if not isinstance(result, dict) or not isinstance(result.get("document"), dict):
            raise SampleError("quality failure: malformed result")
        key, score = result["document"].get("_id"), result.get("score")
        if not isinstance(key, str) or key not in values or key in seen:
            raise SampleError("quality failure: unknown or duplicate fixture membership")
        if type(score) not in (int, float) or not math.isfinite(score):
            raise SampleError("quality failure: missing or non-finite numeric score")
        seen.add(key)
        if not math.isclose(values[key], expected[i][1], rel_tol=1e-6, abs_tol=1e-6):
            raise SampleError("quality failure: rank differs from exact metric oracle")
    # No comparison of score magnitudes across COS/L2/IP is meaningful.


def search(collection, query, c):
    numeric_vector(query, c.dimensions)
    pipeline = [{"$search": {"cosmosSearch": {"vector": query, "path": "DescriptionVector", "k": c.top_k},
                              "returnStoredSource": True}},
                {"$project": {"document": "$$ROOT", "score": {"$meta": "searchScore"}}}]
    with collection.aggregate(pipeline) as cursor:
        results = []
        for row in cursor:
            results.append(row)
            if len(results) > c.top_k:
                raise SampleError("search returned too many results")
        return results


def run_case(collection, c, algorithm, metric, docs, query, deadline):
    # Full input/oracle/catalog validation precedes mutations. No collection reset.
    docs = prepare_seed(docs, c.dimensions)
    numeric_vector(query, c.dimensions)
    expected_ranks(docs, query, metric)
    ensure_index(collection, c, algorithm, metric)  # even on populated collections
    stats = seed(collection, docs, c, algorithm, metric)
    while True:
        if time.monotonic() >= deadline:
            raise SampleError("case deadline expired")
        rows = search(collection, query, c)
        if rows:
            check_quality(rows, docs, query, c, metric)
            return dict(seed=stats, hits=len(rows), quality="exact-metric-top-k-verified",
                        self_match=rows[0]["document"]["_id"] == docs[0]["_id"])
        # Only successful EMPTY responses are retried. No retry of auth, schema,
        # index, tier or ranking failures, and no retries after the deadline.
        time.sleep(min(1, max(0, deadline - time.monotonic())))
