"""Validated, secret-safe process configuration. No dotenv or TLS bypass mode."""
from dataclasses import dataclass, field
import os
import re
from urllib.parse import parse_qsl, unquote, urlsplit


class SampleError(Exception):
    """Only static, non-sensitive messages may be passed to this exception."""


HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.(?:global\.)?mongocluster\.cosmos\.azure\.com", re.ASCII)
NAME = re.compile(r"[a-z][a-z0-9_]{0,39}", re.ASCII)
DATABASE = re.compile(r"vectorsearch_disposable(?:_[a-z0-9_]+)?", re.ASCII)
SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


def native_uri(raw):
    """Allowlist before MongoClient can perform SRV resolution or authentication."""
    try:
        if not raw or any(ord(c) < 33 or ord(c) == 127 for c in raw):
            raise ValueError
        if re.search(r"%(?![0-9a-fA-F]{2})", raw):
            raise ValueError
        u = urlsplit(raw)
        if (u.scheme != "mongodb+srv" or not HOST.fullmatch(u.hostname or "")
                or u.port is not None or u.path not in ("", "/") or u.fragment
                or "#" in raw or not u.username or not u.password):
            raise ValueError
        for value in (unquote(u.username), unquote(u.password)):
            if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError
        seen = set()
        for key, value in parse_qsl(u.query, keep_blank_values=True, strict_parsing=True):
            key = key.lower()
            if key in seen:
                raise ValueError
            seen.add(key)
            # Everything else (proxies, custom certs, alternate auth, etc.) fails closed.
            allowed = {
                "tls": {"true"}, "ssl": {"true"}, "retrywrites": {"false"},
                "tlsinsecure": {"false"}, "tlsallowinvalidcertificates": {"false"},
                "tlsallowinvalidhostnames": {"false"}, "authsource": {"admin"},
                "authmechanism": {"scram-sha-256"},
            }
            if key not in allowed or value.lower() not in allowed[key]:
                raise ValueError
        return raw
    except (ValueError, TypeError):
        raise SampleError("native URI rejected: require public Azure SRV, SCRAM-SHA-256 and verified TLS; see README") from None


@dataclass(frozen=True)
class Config:
    database: str
    prefix: str
    auth_mode: str
    host: str = field(default="", repr=False)
    uri: str = field(default="", repr=False)
    username: str = field(default="", repr=False)
    data_file: str = field(default="data/Hotels_Vector.json", repr=False)
    query_file: str = field(default="", repr=False)
    allow_writes: bool = False
    dimensions: int = 1536
    top_k: int = 5
    batch_size: int = 50
    timeout: int = 600
    case_timeout: int = 120

    def validate(self, writes=False):
        if not DATABASE.fullmatch(self.database) or len(self.database) > 63:
            raise SampleError("MONGO_DATABASE requires vectorsearch_disposable[_lowercase_run_id], at most 63 characters")
        if not NAME.fullmatch(self.prefix):
            raise SampleError("MONGO_COLLECTION_PREFIX must be a lowercase identifier of at most 40 characters")
        if self.auth_mode == "oidc":
            if self.uri or not HOST.fullmatch(self.host):
                raise SampleError("OIDC requires public Azure MONGO_HOST and an unset MONGO_CONNECTION_STRING")
            if any(ord(c) < 32 for c in self.username):
                raise SampleError("invalid OIDC username")
        elif self.auth_mode == "connection-string":
            native_uri(self.uri)
            if self.host or self.username:
                raise SampleError("unset OIDC host and username in connection-string mode")
        else:
            raise SampleError("MONGO_AUTH_MODE must be oidc or connection-string")
        if (self.dimensions != 1536 or self.top_k != 5 or not 1 <= self.batch_size <= 100
                or not 1 <= self.timeout <= 1800 or not 1 <= self.case_timeout <= 300):
            raise SampleError("invalid sample size or timeout: 1536 dimensions, top 5, bounded batches and deadlines required")
        if not self.query_file:
            raise SampleError("QUERY_VECTOR_FILE is required; no model fallback")
        if writes and not self.allow_writes:
            raise SampleError("writes disabled: set VECTOR_SEARCH_ALLOW_WRITES=true only for an approved disposable database")
        return self

    def collection(self, algorithm, metric):
        if algorithm not in ("ivf", "hnsw", "diskann") or metric not in ("COS", "L2", "IP"):
            raise SampleError("invalid algorithm or metric")
        return f"{self.prefix}_{algorithm}_{metric.lower()}"


def load_config(env=None):
    env = os.environ if env is None else env
    if env.get("EMBEDDING_MODE", "offline") != "offline":
        raise SampleError("only precomputed offline embeddings are supported; Azure OpenAI generation was removed")
    for key in ("MONGO_COLLECTION", "MONGO_CLUSTER_NAME", "DOCUMENTDB_CLUSTER_NAME", "AZURE_DOCUMENTDB_DATABASENAME"):
        if env.get(key):
            raise SampleError("legacy destination configuration is not supported; use MONGO_HOST, MONGO_DATABASE and MONGO_COLLECTION_PREFIX")
    if env.get("EMBEDDED_FIELD", "DescriptionVector") != "DescriptionVector":
        raise SampleError("this sample requires DescriptionVector")
    if env.get("VECTOR_SEARCH_ALLOW_WRITES", "false") not in ("true", "false"):
        raise SampleError("VECTOR_SEARCH_ALLOW_WRITES must be true or false")
    try:
        c = Config(database=env.get("MONGO_DATABASE", ""), prefix=env.get("MONGO_COLLECTION_PREFIX", ""),
                   auth_mode=env.get("MONGO_AUTH_MODE", ""), host=env.get("MONGO_HOST", ""),
                   uri=env.get("MONGO_CONNECTION_STRING", ""), username=env.get("MONGO_OIDC_USERNAME", ""),
                   data_file=env.get("DATA_FILE_WITH_VECTORS", "data/Hotels_Vector.json"),
                   query_file=env.get("QUERY_VECTOR_FILE", ""),
                   allow_writes=env.get("VECTOR_SEARCH_ALLOW_WRITES") == "true",
                   dimensions=int(env.get("EMBEDDING_DIMENSIONS", "1536")),
                   top_k=int(env.get("TOP_K", "5")), batch_size=int(env.get("LOAD_SIZE_BATCH", "50")),
                   timeout=int(env.get("VECTOR_SEARCH_TIMEOUT_SECONDS", "600")),
                   case_timeout=int(env.get("VECTOR_SEARCH_CASE_TIMEOUT_SECONDS", "120")))
    except (ValueError, TypeError):
        raise SampleError("sample numeric settings must be integers") from None
    return c.validate()
