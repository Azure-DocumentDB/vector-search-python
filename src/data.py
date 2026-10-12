"""Strict bounded JSON and deterministic insert-only seed preparation."""
import copy
import hashlib
import json
import math
from pathlib import Path

from config import SampleError

FIXTURE_SHA256 = "60c08c2cdd439444ba8601d94a1f3487f4a9ee56cd48ba201d983c96722de6e4"
FIXTURE_URL = "https://raw.githubusercontent.com/Azure-Samples/documentdb-samples/f5a2675bbf349737234d0391e3ebee24b260e06f/ai/data/Hotels_Vector.json"
MAX_BYTES = 8 * 1024 * 1024


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SampleError("duplicate JSON key")
        value[key] = item
    return value


def decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise SampleError("invalid strict JSON input") from None


def read_bytes(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SampleError("input exceeds 8 MiB")
        return raw
    except OSError:
        raise SampleError("input file could not be read") from None


def numeric_vector(value, dimensions=1536):
    if not isinstance(value, list) or len(value) != dimensions:
        raise SampleError("vector dimensions do not match")
    if any(type(n) not in (int, float) or not math.isfinite(n) for n in value):
        raise SampleError("vector requires finite numbers, not booleans, strings or null")
    norm = math.hypot(*value)
    if not math.isfinite(norm) or norm == 0:
        raise SampleError("vector must have finite nonzero norm")
    return [float(n) for n in value]


def _keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key or key.startswith("$") or "." in key or "\x00" in key:
                raise SampleError("unsafe document field")
            _keys(child)
    elif isinstance(value, list):
        for child in value:
            _keys(child)
    elif type(value) is float and not math.isfinite(value):
        raise SampleError("non-finite document value")


def prepare_seed(data, dimensions=1536):
    if not isinstance(data, list) or not 1 <= len(data) <= 1000:
        raise SampleError("seed requires 1 to 1000 documents")
    docs, seen = [], set()
    for source in data:
        if not isinstance(source, dict):
            raise SampleError("seed requires JSON objects")
        _keys(source)
        doc = copy.deepcopy(source)
        key = doc.get("_id")
        if "_id" not in doc:
            hotel = doc.get("HotelId")
            if not isinstance(hotel, str) or not hotel:
                raise SampleError("seed needs string _id or HotelId")
            key = "vectorsearch:" + hotel
        if not isinstance(key, str) or not key or len(key) > 200 or any(ord(c) < 32 for c in key) or key in seen:
            raise SampleError("seed IDs must be unique nonempty bounded strings")
        seen.add(key)
        doc["_id"] = key
        doc["DescriptionVector"] = numeric_vector(doc.get("DescriptionVector"), dimensions)
        docs.append(doc)
    return docs


def load_inputs(c, pinned=False):
    raw = read_bytes(c.data_file)
    if pinned and hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise SampleError("live gate requires the pinned public fixture checksum")
    docs = prepare_seed(decode(raw), c.dimensions)
    query = numeric_vector(decode(read_bytes(c.query_file)), c.dimensions)
    if len(docs) != 50:
        raise SampleError("sample requires 50 documents")
    if pinned and query != docs[0]["DescriptionVector"]:
        raise SampleError("live gate requires first fixture vector as self-match query")
    return docs, query
