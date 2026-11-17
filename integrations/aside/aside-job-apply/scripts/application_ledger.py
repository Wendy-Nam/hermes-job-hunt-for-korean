#!/usr/bin/env python3
"""Local duplicate/quota ledger. Browser evidence and job/resume fit are checked by the skill."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid


def job_key(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('A public HTTPS job URL without embedded credentials is required')
    host = p.hostname.lower().removeprefix('www.')
    query = dict(parse_qsl(p.query, keep_blank_values=True))
    if host == 'wanted.co.kr' and (m := re.fullmatch(r'/wd/(\d+)/?', p.path)):
        return 'wanted:' + m[1], host
    if host == 'saramin.co.kr' and query.get('rec_idx'):
        return 'saramin:' + query['rec_idx'], host
    if host == 'jobkorea.co.kr' and (m := re.search(r'/Recruit/GI_Read/(\d+)', p.path, re.I)):
        return 'jobkorea:' + m[1], host
    if host == 'linkedin.com' and (m := re.search(r'/jobs/view/(?:[^/]*-)?(\d+)/?', p.path)):
        return 'linkedin:' + m[1], host
    keep = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not k.lower().startswith('utm_') and k.lower() not in {'gclid', 'fbclid', 'trk', 'ref'}]
    return urlunsplit(('https', host, p.path.rstrip('/'), urlencode(sorted(keep)), '')), host


def validate_policy(policy, url, account):
    if policy.get('mode') != 'auto_submit' or policy.get('authorized') is not True:
        raise ValueError('Final submission is not authorized by this policy')
    expires = datetime.fromisoformat(str(policy.get('expires_at')).replace('Z', '+00:00'))
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise ValueError('Submission authorization has expired or has no timezone')
    key, host = job_key(url)
    allowed = [str(x).lower().removeprefix('www.') for x in policy.get('allowed_domains', [])]
    if host not in allowed or policy.get('accounts', {}).get(host) != account:
        raise ValueError('Job domain or signed-in account is outside the approved scope')
    approved = {job_key(x)[0] for x in policy.get('approved_job_urls', [])}
    discovery = policy.get('discovery', {})
    if key not in approved and not (discovery.get('enabled') is True and discovery.get('conditions')):
        raise ValueError('This job is not approved and discovery is not authorized')
    limit = policy.get('max_submissions')
    if not policy.get('policy_id') or not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError('A policy ID and positive submission limit are required')
    return key


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=20)
    path.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS applications (job_key TEXT PRIMARY KEY, ticket TEXT UNIQUE, policy_id TEXT, resume_id TEXT, account TEXT, status TEXT, evidence TEXT, created_at TEXT)')
    return db


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, required=True)
    sub = p.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check')
    check.add_argument('--url', required=True)
    reserve = sub.add_parser('reserve')
    reserve.add_argument('--policy', type=Path, required=True)
    reserve.add_argument('--url', required=True)
    reserve.add_argument('--resume-id', required=True)
    reserve.add_argument('--account', required=True)
    final = sub.add_parser('finalize')
    final.add_argument('--ticket', required=True)
    final.add_argument('--status', choices=['submitted', 'unknown'], required=True)
    final.add_argument('--evidence', required=True)
    args = p.parse_args()
    if args.command == 'check' and not args.db.exists():
        print(json.dumps({'status': 'new'}))
        return
    policy = None
    if args.command == 'reserve':
        policy = json.loads(args.policy.read_text(encoding='utf-8'))
        key = validate_policy(policy, args.url, args.account)
        if not args.resume_id.strip():
            raise ValueError('The selected platform resume ID is required')
    with connect(args.db) as db:
        if args.command == 'check':
            key, _ = job_key(args.url)
            row = db.execute('SELECT ticket,status,resume_id,evidence FROM applications WHERE job_key=?', (key,)).fetchone()
            result = dict(zip(['ticket', 'status', 'resume_id', 'evidence'], row)) if row else {'status': 'new'}
        elif args.command == 'reserve':
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM applications WHERE job_key=?', (key,)).fetchone():
                raise ValueError('Already submitted, reserved, or uncertain: do not click submit again')
            count = db.execute('SELECT count(*) FROM applications WHERE policy_id=?', (policy['policy_id'],)).fetchone()[0]
            if count >= policy['max_submissions']:
                raise ValueError('Submission quota, including pending/uncertain attempts, is exhausted')
            ticket = uuid.uuid4().hex
            db.execute('INSERT INTO applications VALUES (?,?,?,?,?,?,?,?)',
                       (key, ticket, policy['policy_id'], args.resume_id, args.account, 'intent', '', datetime.now(timezone.utc).isoformat()))
            result = {'ticket': ticket, 'status': 'intent', 'resume_id': args.resume_id}
        else:
            row = db.execute('SELECT status FROM applications WHERE ticket=?', (args.ticket,)).fetchone()
            if not row or not args.evidence.strip():
                raise ValueError('An existing submission ticket and observed evidence are required')
            if row[0] == 'submitted' and args.status != 'submitted':
                raise ValueError('A confirmed submission cannot be downgraded')
            db.execute('UPDATE applications SET status=?,evidence=? WHERE ticket=?', (args.status, args.evidence[:2000], args.ticket))
            result = {'ticket': args.ticket, 'status': args.status}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, sqlite3.Error) as e:
        raise SystemExit(str(e))
