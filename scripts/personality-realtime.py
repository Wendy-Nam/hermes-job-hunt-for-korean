#!/usr/bin/env python3
"""Small, fail-open Korean-aware personality rule engine (stdlib only).
Dry-run is the default; use --apply to change personality.json and append audit JSONL.
"""
from __future__ import annotations
import argparse, fcntl, json, os, re, shutil, tempfile
from datetime import datetime, timezone
from pathlib import Path

# NOTE: default profile is 'default' (the 'coder' profile dir never existed;
# the plugin always sets HERMES_PROFILE explicitly — this is only the fallback).
# PERSONALITY_ROOT override exists for tests (default: /opt/data).
_ROOT_BASE = Path(os.getenv('PERSONALITY_ROOT', '/opt/data'))
ROOT = _ROOT_BASE / f"profiles/{os.getenv('HERMES_PROFILE', 'default')}/skills/personality"
RULES = ROOT / 'personality-rules.yaml'
PERSONALITY = ROOT / 'personality.json'
AUDIT = ROOT / 'personality-realtime-audit.jsonl'
LOCK = ROOT / 'personality-realtime.lock'
# Deliberately conservative: only clear clause boundaries; never split Korean morphology.
BOUNDARY = re.compile(r'(?<=[.!?。！？])\s+|[\n]+')
NEGATION = re.compile(r'(?:안|못|않|없|아니|말고|하지|하진|실패하진|성공하진|될까|할까|인지|같아|싶다)')
THIRD = re.compile(r'(?:그가|그녀가|누가|사람이|친구가|남이|다른 사람이|고객이)')
QUOTE = re.compile(r"(?:['\"“”‘’]|라고\s*(?:했|말했|함)|인용)")
HYPOTHETICAL = re.compile(r'(?:면|다면|라면|할까|될까|일지도|가능성|가정|만약|했으면|했더라면)')

def now(): return datetime.now(timezone.utc)
def parse_scalar(v):
    v=v.strip()
    if v.startswith('[') and v.endswith(']'):
        return [x.strip().strip('"\'') for x in v[1:-1].split(',') if x.strip()]
    return v.strip('"\'')

def load_rules():
    # Supports this intentionally tiny YAML subset: key/value, rule list, inline arrays.
    rules=[]; current=None; section=''
    for raw in RULES.read_text(encoding='utf-8').splitlines():
        s=raw.strip()
        if not s or s.startswith('#'): continue
        if s == 'rules:': section='rules'; continue
        if section=='rules' and s.startswith('- '):
            current={}; rules.append(current); s=s[2:].strip()
        if ':' not in s: continue
        k,v=s.split(':',1); k=k.strip(); v=v.strip()
        if current is not None and section=='rules': current[k]=parse_scalar(v)
    return rules

def clauses(text): return [x.strip() for x in BOUNDARY.split(text) if x.strip()]
def guarded(clause, pattern):
    # Guards are clause-local and conservative; quoted text never drives a rule.
    if QUOTE.search(clause) or THIRD.search(clause) or HYPOTHETICAL.search(clause): return True
    # Negation immediately before or within a short window of the hit suppresses it.
    for m in re.finditer(re.escape(pattern), clause):
        if NEGATION.search(clause[max(0,m.start()-8):m.start()]) or NEGATION.search(clause[m.end():m.end()+8]): return True
    return False

def cooldown_ids():
    """Return rules still cooling down; malformed audit lines fail open."""
    blocked=set(); now_ts=now().timestamp()
    if not AUDIT.exists(): return blocked
    try:
        for line in AUDIT.read_text(encoding='utf-8').splitlines():
            event=json.loads(line); at=datetime.fromisoformat(event['applied_at']).timestamp()
            for h in event.get('matches',[]):
                seconds=next((int(r.get('cooldown_seconds',600)) for r in load_rules() if r.get('id')==h.get('rule_id')),600)
                if now_ts-at < seconds: blocked.add(h.get('rule_id'))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return set()
    return blocked

def evaluate(text):
    rules=load_rules(); hits=[]; blocked=cooldown_ids()
    # Narrative precedence is explicit and stable, not input-order dependent.
    rank={'failure':0,'success':1,'relief':2,'affectionate-address':3}
    for clause in clauses(text):
        for rule in rules:
            found=next((p for p in rule.get('patterns',[]) if p and p in clause),None)
            if found and not guarded(clause, found):
                h={'rule_id':rule['id'],'emotion':rule['emotion'],'pattern':found,'clause':clause,'rank':rank.get(rule['id'],99),'cooldown':rule['id'] in blocked}
                if not h['cooldown']: hits.append(h)
    hits.sort(key=lambda h:(h['rank'], h['rule_id']))
    return hits

def atomic_write(path, data):
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.', dir=str(path.parent)); os.close(fd)
    try:
        Path(tmp).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def bootstrap():
    """Never crash on a fresh/unknown profile — seed state instead of dying."""
    ROOT.mkdir(parents=True, exist_ok=True)
    if not RULES.exists():
        _seed = Path("/opt/data/profiles/default/skills/personality/personality-rules.yaml")
        if _seed.exists():
            shutil.copy(_seed, RULES)
    if not PERSONALITY.exists():
        atomic_write(PERSONALITY, {"name": "Hermes", "version": "1.0",
            "mood": "curious", "traits": {}, "memories": [],
            "mood_history": [], "evolution_log": []})

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--message',required=True); ap.add_argument('--apply',action='store_true'); args=ap.parse_args()
    bootstrap()
    hits=evaluate(args.message); result={'message':args.message,'dry_run':not args.apply,'matches':hits,'selected':hits[-1] if hits else None}
    if args.apply and hits:
        with LOCK.open('w', encoding='utf-8') as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            p=json.loads(PERSONALITY.read_text(encoding='utf-8')); old=p.get('mood'); p['mood']=hits[-1]['emotion']
            p.setdefault('mood_history',[]).append({'from':old,'to':p['mood'],'time':now().isoformat(),'source':'personality-realtime','rules':[h['rule_id'] for h in hits]})
            atomic_write(PERSONALITY,p)
            with AUDIT.open('a',encoding='utf-8') as f: f.write(json.dumps({**result,'applied_at':now().isoformat()},ensure_ascii=False)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
