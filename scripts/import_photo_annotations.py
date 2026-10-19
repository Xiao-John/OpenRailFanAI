"""Validated offline annotation import with a recoverable SQLite backup."""
import argparse, importlib.util, json, sqlite3, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.data import photo_annotations as annotations, photo_spots
from app.data.photo_annotation_validation import check_bindings

def read(p): return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]

def run(batch, decisions, target, backup):
    spec=importlib.util.spec_from_file_location('batch_validator',batch/'validate.py')
    validator=importlib.util.module_from_spec(spec); spec.loader.exec_module(validator)
    errors=[]; validator.validate_file(batch/'annotations-adjudicated.jsonl',errors)
    if errors: raise ValueError('batch validation failed: '+str(errors[:3]))
    reviewed={r['doc_id'] for r in read(decisions)}
    rows=read(batch/'annotations-adjudicated.jsonl')
    sources={r['doc_id']:r for r in read(batch/'source_documents.jsonl')}
    bindings=read(batch/'city-bindings.jsonl')
    # City eligibility must have passed the binding ledger, not just string presence.
    errors=check_bindings(rows,bindings,sources,reviewed)
    if errors: raise ValueError('city validation failed: '+str(errors[:3]))
    if target.resolve()==backup.resolve() or backup.exists(): raise ValueError('backup must be new and separate')
    backup.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(f'file:{target}?mode=ro',uri=True) as src, sqlite3.connect(backup) as saved:
        src.backup(saved)
    with sqlite3.connect(target) as conn:
        conn.executescript('BEGIN IMMEDIATE;\n'+annotations.SCHEMA)
        result=annotations.install(conn,rows,sources,reviewed,bindings,'photo-spot-v3')
        photo_spots.stamp(conn)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--decisions',type=Path,required=True); p.add_argument('--db',type=Path,required=True)
    p.add_argument('--backup',type=Path,required=True)
    a=p.parse_args(); print(json.dumps(run(a.batch,a.decisions,a.db,a.backup),ensure_ascii=False))
