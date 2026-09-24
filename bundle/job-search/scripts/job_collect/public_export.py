#!/usr/bin/env python3
"""job_collect.public_export — 공개 계약 필드만 담은 JSONL 산출물 생성(job-collect.py 원본에서 이식, 동작 동일)."""
import json
import os

from . import config


def _public_record(row):
    """Return only the deliberately public collector contract fields."""
    if not isinstance(row, dict):
        return None
    out = {k: row[k] for k in config.PUBLIC_KEYS if k in row and k != "provenance"}
    url = out.get("url")
    if not isinstance(url, str) or not url.startswith("https://") or "\n" in url:
        return None
    for k in ("title", "company"):
        if not isinstance(out.get(k), str) or not out[k].strip():
            return None
    if any(token in json.dumps(out, ensure_ascii=False).lower() for token in ("@example.com", "/opt/data/private", "salary_history")):
        return None
    if isinstance(row.get("provenance"), dict):
        out["provenance"] = {k: row["provenance"][k] for k in ("board", "source") if isinstance(row["provenance"].get(k), str)}
    if "jd_text" not in out:
        out["state"] = "metadata_only"
    elif not isinstance(out["jd_text"], str):
        return None
    return out


def export_public_jsonl(rows, path):
    """Atomically write a capped, 0600 public JSONL artifact."""
    records = []
    for row in rows:
        record = _public_record(row)
        if record:
            records.append(record)
        if len(records) >= config.PUBLIC_MAX_RECORDS:
            break
    data = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records)
    if len(data.encode("utf-8")) > config.PUBLIC_MAX_BYTES:
        raise ValueError("public JSONL artifact exceeds size cap")
    target = os.path.abspath(path)
    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    os.chmod(target, 0o600)
    return len(records)


def _fixture_rows(path):
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    if isinstance(obj, dict):
        obj = obj.get("records", obj.get("items", []))
    return obj if isinstance(obj, list) else []
