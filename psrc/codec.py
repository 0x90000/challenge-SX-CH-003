"""Canonical JSON, bounded input and atomic artifacts; never pickle."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .errors import ContractError

MAX_BYTES = 16 * 1024 * 1024


def encode(value):
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ContractError(
            "SCHEMA_INVALID", "Value is not finite JSON", details={"cause": type(exc).__name__}
        ) from exc


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate key: {key}")
        result[key] = value
    return result


def decode(raw):
    if len(raw) > MAX_BYTES:
        raise ContractError("RESOURCE_LIMIT", "JSON exceeds byte limit")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError(
            "SCHEMA_INVALID", "Invalid JSON", details={"cause": str(exc)[:200]}
        ) from exc


def read_json(path, missing="INPUT_MISSING"):
    try:
        with Path(path).open("rb") as stream:
            return decode(stream.read(MAX_BYTES + 1))
    except FileNotFoundError as exc:
        raise ContractError(
            missing, "Required file does not exist", details={"path": str(path)}
        ) from exc
    except OSError as exc:
        raise ContractError(
            "IO_FAILED",
            "Cannot read file",
            details={"path": str(path), "cause": type(exc).__name__},
        ) from exc


def write_json(path, value):
    path = Path(path)
    raw = encode(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temp = stream.name
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
