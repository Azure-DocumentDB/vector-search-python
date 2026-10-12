"""Explicit pinned public download / query extraction; never writes environment."""
import hashlib
import json
import os
import sys
import urllib.request

from config import SampleError
from data import FIXTURE_SHA256, FIXTURE_URL, MAX_BYTES, decode, prepare_seed, read_bytes
from runner import SafeParser


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SampleError("fixture download redirect rejected")


def write_new(path, raw):
    # O_EXCL refuses existing regular files, symlinks and accidental input overwrite.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def validate_fixture(raw):
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise SampleError("pinned fixture checksum mismatch")
    docs = prepare_seed(decode(raw))
    if len(docs) != 50:
        raise SampleError("pinned fixture count mismatch")
    return docs


def main(argv=None):
    parser = SafeParser(description="Download pinned public data or extract its first vector; never overwrite files.")
    sub = parser.add_subparsers(dest="command", required=True)
    download = sub.add_parser("download")
    download.add_argument("--output", default="data/Hotels_Vector.json")
    extract = sub.add_parser("extract")
    extract.add_argument("--input", default="data/Hotels_Vector.json")
    extract.add_argument("--output", default="data/query-vector.json")
    args = parser.parse_args(argv)
    try:
        if os.path.lexists(args.output):
            raise SampleError("output already exists; choose a new path")
        if args.command == "download":
            # No cookies, credentials, arbitrary URL, redirects or TLS override.
            opener = urllib.request.build_opener(NoRedirect())
            with opener.open(FIXTURE_URL, timeout=30) as response:
                if response.status != 200:
                    raise SampleError("fixture download unsuccessful")
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise SampleError("fixture download exceeds size limit")
            validate_fixture(raw)
        else:
            docs = validate_fixture(read_bytes(args.input))
            raw = json.dumps(docs[0]["DescriptionVector"], allow_nan=False).encode() + b"\n"
        write_new(args.output, raw)
        print("pinned public fixture operation completed; existing files were not overwritten")
        return 0
    except Exception:
        print("error: fixture operation failed; check checksum, input and new output path (details withheld)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
