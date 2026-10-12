"""Bounded all-case runner and sanitized CLI. Local mocks are not Azure proof."""
import argparse
import json
import logging
import os
import signal
import sys
import time

import pymongo
from pymongo.errors import OperationFailure

from auth import connect
from config import SampleError, load_config
from data import load_inputs
from search import expected_ranks, run_case

NOTICE = "Precomputed vector/index contract only; not model relevance or a performance benchmark. Scores across metrics are not comparable. Unsupported cases are failures, not engine proof."


def cases(comparison=False):
    return [(a, m) for a in ("ivf", "hnsw", "diskann") for m in (("COS", "L2", "IP") if comparison else ("COS",))]


def execute_cases(c, selected, invoke, deadline):
    report = dict(database=c.database, auth_mode=c.auth_mode, embedding_mode="offline", notice=NOTICE,
                  cases=[], succeeded=0, failed=0)
    for algorithm, metric in selected:
        row = dict(algorithm=algorithm, metric=metric, collection=c.collection(algorithm, metric))
        try:
            if time.monotonic() >= deadline:
                raise SampleError("operation deadline expired")
            case_deadline = min(deadline, time.monotonic() + c.case_timeout)
            with pymongo.timeout(max(0.001, case_deadline - time.monotonic())):
                result = invoke(algorithm, metric, case_deadline)
            if time.monotonic() >= case_deadline:
                raise SampleError("case deadline expired")
            row.update(result, status="passed")
            report["succeeded"] += 1
        except Exception as exc:
            # Never stringify driver errors: they can contain credentials/documents.
            row.update(status="failed", error="case failed; verify tier, auth, index compatibility, fixture and connectivity")
            if isinstance(exc, SampleError):
                row["error"] = str(exc)
            elif isinstance(exc, OperationFailure) and type(exc.code) is int:
                row["server_code"] = exc.code
            report["failed"] += 1
        report["cases"].append(row)
    return report


def run(c, selected, pinned=False):
    c.validate(writes=True)
    docs, query = load_inputs(c, pinned=pinned)
    for _, metric in selected:
        expected_ranks(docs, query, metric)
    deadline = time.monotonic() + c.timeout
    with pymongo.timeout(c.timeout):
        with connect(c) as client:
            return execute_cases(c, selected, lambda a, m, d: run_case(
                client[c.database][c.collection(a, m)], c, a, m, docs, query, d), deadline)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default echoes unknown arguments, which may be secrets.
        self.print_usage(sys.stderr)
        self.exit(2, "error: invalid command arguments; use --help (values withheld)\n")


def quiet_dependencies():
    for name in ("azure", "msal", "pymongo"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        logger.setLevel(logging.CRITICAL + 1)


def cli(comparison=False, algorithm=None, argv=None):
    parser = SafeParser(description="Precomputed Azure DocumentDB vector contract; mutates only an explicitly approved disposable database.")
    parser.add_argument("--live-check", action="store_true", help="require live opt-in and pinned 50x1536 fixture/self query")
    args = parser.parse_args(argv)
    quiet_dependencies()
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if args.live_check and os.environ.get("VECTOR_SEARCH_LIVE_TEST") != "1":
            raise SampleError("live gate requires VECTOR_SEARCH_LIVE_TEST=1; no network calls made")
        c = load_config()
        selected = [(algorithm, "COS")] if algorithm else cases(comparison)
        report = run(c, selected, pinned=args.live_check)
        print(json.dumps(report, allow_nan=False, sort_keys=True))
        return int(report["failed"] != 0)
    except SampleError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: canceled; partial insert-only data may remain", file=sys.stderr)
        return 130
    except Exception:
        print("error: operation failed (details withheld to protect credentials/data)", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
