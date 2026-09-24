#!/usr/bin/env python3
"""job_collect.identity — 회사/포지션 dedup·정규화 키 헬퍼(job-collect.py 원본에서 이식, 동작 동일)."""
import re
import sys

from . import config


def nrm(x):
    return re.sub(r"[^0-9a-z가-힣]", "", re.sub(r"\(.*?\)|\[.*?\]", "", x or "").lower())


def key(comp, pos):
    try:
        sys.path.insert(0, config.S)
        import jobfilter as _jf
        return _jf.norm_key(comp, pos)   # 공유 정규화(크로스플랫폼 dedup)
    except Exception:
        return nrm(comp)[:10] + "|" + nrm(pos)[:16]  # 폴백


def slug(company, position):
    s = re.sub(r"[\\/:*?\"<>|#\[\]^]", "", f"{company}-{position}")
    return re.sub(r"\s+", " ", s).strip()[:70]


def esc(v):
    return f'"{(v or "").replace(chr(34), chr(39)).strip()}"'
