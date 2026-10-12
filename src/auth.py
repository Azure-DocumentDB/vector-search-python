"""Native SCRAM or explicit Azure CLI OIDC. Never fall back between modes."""
from contextlib import contextmanager
import time

from azure.identity import AzureCliCredential
from pymongo import MongoClient
from pymongo.auth_oidc import OIDCCallback, OIDCCallbackResult

from config import SCOPE, SampleError


class AzureTokenCallback(OIDCCallback):
    def __init__(self, credential, clock=time.time):
        self.credential = credential
        self.clock = clock

    def fetch(self, context):
        # idp_info/scopes from the server are deliberately not trusted.
        if context.version != 1 or context.timeout_seconds <= 0:
            raise SampleError("unsupported or expired OIDC callback context")
        started = time.monotonic()
        try:
            token = self.credential.get_token(SCOPE)
        except Exception:
            raise SampleError("Azure CLI token acquisition failed; no credential fallback") from None
        remaining = token.expires_on - self.clock()
        if (not isinstance(token.token, str) or not token.token or remaining <= 30
                or time.monotonic() - started >= context.timeout_seconds):
            raise SampleError("OIDC token empty, expired, near expiry, or acquired after callback deadline")
        # PyMongo 4.18 uses a relative lifetime, NOT expires_at or expires_on.
        return OIDCCallbackResult(access_token=token.token, expires_in_seconds=remaining)


def client_options(c, credential=None):
    c.validate()
    options = dict(tls=True, tlsAllowInvalidCertificates=False, tlsAllowInvalidHostnames=False,
                   retryWrites=False, connectTimeoutMS=15000, serverSelectionTimeoutMS=20000,
                   socketTimeoutMS=30000, timeoutMS=c.case_timeout * 1000,
                   maxPoolSize=5, maxIdleTimeMS=60000, appname="documentdb-python-precomputed")
    if c.auth_mode == "oidc":
        if credential is None:
            raise SampleError("OIDC credential required")
        options.update(authMechanism="MONGODB-OIDC", authSource="$external",
                       authMechanismProperties={"OIDC_CALLBACK": AzureTokenCallback(credential)})
        if c.username:
            options["username"] = c.username
    else:
        options.update(authMechanism="SCRAM-SHA-256", authSource="admin")
    return options


@contextmanager
def connect(c):
    c.validate()  # must precede credential construction and SRV resolution
    credential = None
    client = None
    try:
        if c.auth_mode == "oidc":
            # Explicitly choose CLI, rather than a broad credential chain. The
            # token subprocess has a hard 10s limit and no application-owned cache.
            credential = AzureCliCredential(process_timeout=10)
        uri = c.uri if c.auth_mode == "connection-string" else f"mongodb+srv://{c.host}/"
        client = MongoClient(uri, **client_options(c, credential))
        client.admin.command("ping")
        yield client
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            if credential is not None:
                credential.close()
