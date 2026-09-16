from __future__ import annotations
import copy
import hashlib
from pathlib import Path
import sqlite3
from evaluator_audit.certification.requirements import Branch


def read_snapshot(directory: Path, collections: tuple[str, ...]) -> dict:
    result = {}
    for collection in collections:
        app, table = collection.split(".", 1)
        if not app.isidentifier() or not table.isidentifier():
            raise ValueError("Invalid collection identifier")
        db = directory / f"{app}.db"
        if not db.is_file():
            raise FileNotFoundError(db)
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            # The adapter never changes the saved database or substitutes an
            # empty collection when data are unavailable.
            result[collection] = [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY id')]
    return result


def branch_from_snapshots(initial: Path, final: Path, trace: list[dict], collections: tuple[str, ...], *, success: bool) -> Branch:
    a, b = read_snapshot(initial, collections), read_snapshot(final, collections)
    calls = tuple({"tool": x["api_identity"], "arguments": copy.deepcopy(x["arguments"])} for x in trace)
    responses = tuple({"response": copy.deepcopy(x.get("response")), "exception": copy.deepcopy(x.get("exception"))} for x in trace)
    digest = hashlib.sha256()
    files = sorted(p for p in initial.rglob("*") if p.is_file())
    if not files:
        raise ValueError("Empty initial environment")
    for path in files:
        digest.update(str(path.relative_to(initial)).encode() + b"\0")
        file_digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                file_digest.update(chunk)
        digest.update(file_digest.digest())
    return Branch(a, b, calls, responses, success, frozenset(collections), initial_environment_digest=digest.hexdigest())
