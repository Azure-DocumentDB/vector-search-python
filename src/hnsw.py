"""Run the HNSW COS case with validated precomputed vectors."""
from runner import cli

if __name__ == "__main__":
    raise SystemExit(cli(algorithm="hnsw"))
