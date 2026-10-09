"""Reviewed photo annotations, field eligibility and lazy issue explanations."""
import hashlib
import json
import sqlite3

TABLES = ('photo_annotation_doc', 'photo_annotation_issue')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS photo_annotation_doc (
 url TEXT PRIMARY KEY, doc_id TEXT NOT NULL, text_sha256 TEXT NOT NULL,
 reviewed INTEGER NOT NULL, annotation TEXT NOT NULL, city_bindings TEXT NOT NULL,
 contract_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photo_annotation_issue (
 issue_id TEXT PRIMARY KEY, url TEXT NOT NULL, occurrence_id TEXT,
 field TEXT, reason_code TEXT NOT NULL, explanation TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_photo_issue_url ON photo_annotation_issue(url);
CREATE INDEX IF NOT EXISTS ix_photo_issue_occ ON photo_annotation_issue(occurrence_id);
'''
LABELS = {'ambiguous_reference':'名称或指代待确认', 'unclear_binding':'对应关系待确认',
          'entity_unresolved':'铁路实体待确认', 'insufficient_location':'具体位置待确认',
          'unsupported_value':'分类待确认', 'insufficient_text':'原文不足',
          'conflicting_sources':'来源有分歧', 'unreviewed':'尚未复核', 'stale_source':'原文已变化'}

def exists(conn):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='photo_annotation_doc'").fetchone())

def digest(text): return hashlib.sha256(text.encode()).hexdigest()

def issue_id(doc, oid, field, item):
    identity=[doc['doc_id'],doc['text_sha256'],oid,field,item['reason_code'],item.get('evidence',[])]
    return 'psi_'+digest(json.dumps(identity,ensure_ascii=False,sort_keys=True))[:24]

def install(conn, annotations, sources, reviewed_docs, bindings, contract):
    """Caller validates the batch and supplies a transaction; fail before replacing tables."""
    docs=[]; issues=[]
    for doc in annotations:
        src=sources[doc['doc_id']]; url=src['url']
        current=conn.execute('SELECT page_text,snippet FROM photo_spot_doc WHERE url=?',(url,)).fetchone()
        if not current or digest(current[0] or current[1] or '')!=doc['text_sha256']:
            raise ValueError('annotation source mismatch: '+doc['doc_id'])
        reviewed=doc['doc_id'] in reviewed_docs
        cities=[b for b in bindings if b['occurrence_id'].startswith(doc['doc_id']+':')]
        docs.append((url,doc['doc_id'],doc['text_sha256'],int(reviewed),json.dumps(doc,ensure_ascii=False),json.dumps(cities,ensure_ascii=False),contract))
        items=[(None,None,u) for u in doc['unresolved']]
        items += [(o['occurrence_id'],p['field'],p) for o in doc['occurrences'] for p in o['pending_fields']]
        if not reviewed:
            items=[(None,None,dict(reason_code='unreviewed',evidence=[],note='原标注尚未经语义复核；不能作为结构化检索事实。'))]+items
        for oid,field,item in items:
            iid=issue_id(doc,oid,field,item)
            detail=dict(issue_id=iid,doc_id=doc['doc_id'],text_sha256=doc['text_sha256'],url=url,
                        occurrence_id=oid,field=field,reason_code=item['reason_code'],
                        title=LABELS.get(item['reason_code'],'待确认'),note=item['note'],evidence=item.get('evidence',[]))
            issues.append((iid,url,oid,field,item['reason_code'],json.dumps(detail,ensure_ascii=False)))
    # Repeated same-field/evidence issues share one index; retain all distinct explanations.
    unique={}
    for item in issues:
        if item[0] in unique:
            prior=json.loads(unique[item[0]][-1]); current=json.loads(item[-1])
            if current['note'] not in prior['note']: prior['note']+='\n'+current['note']
            unique[item[0]]=(*item[:-1],json.dumps(prior,ensure_ascii=False))
        else: unique[item[0]]=item
    issues=list(unique.values())
    # Incremental source checking above is complete before any delete.
    conn.execute('DELETE FROM photo_annotation_issue')
    conn.execute('DELETE FROM photo_annotation_doc')
    conn.executemany('INSERT INTO photo_annotation_doc VALUES(?,?,?,?,?,?,?)',docs)
    conn.executemany('INSERT INTO photo_annotation_issue VALUES(?,?,?,?,?,?)',issues)
    return dict(documents=len(docs),reviewed_documents=sum(r[3] for r in docs),issues=len(issues))

def load(conn,url):
    if not exists(conn): return None
    row=conn.execute('SELECT * FROM photo_annotation_doc WHERE url=?',(url,)).fetchone()
    if not row: return None
    source=conn.execute('SELECT page_text,snippet FROM photo_spot_doc WHERE url=?',(url,)).fetchone()
    row=dict(row)
    row['stale']=not source or digest(source[0] or source[1] or '')!=row['text_sha256']
    row['invalid']=False
    try:
        row['annotation']=json.loads(row['annotation']); row['city_bindings']=json.loads(row['city_bindings'])
        if not isinstance(row['annotation'],dict) or not isinstance(row['annotation'].get('occurrences'),list) or not isinstance(row['city_bindings'],list):
            raise ValueError('invalid annotation index')
    except (ValueError,TypeError):
        row.update(stale=True,invalid=True,annotation={'occurrences':[]},city_bindings=[])
    return row

def markers(conn,url,oid=None):
    if not exists(conn): return []
    sql='SELECT issue_id,occurrence_id,field,reason_code FROM photo_annotation_issue WHERE url=?'
    args=[url]
    if oid is not None:
        sql+=' AND (occurrence_id=? OR occurrence_id IS NULL)'; args.append(oid)
    return [dict(issue_id=r['issue_id'],field=r['field'],label='待确认',
                 occurrence_id=r['occurrence_id'],applies_to='occurrence' if r['occurrence_id'] else 'document',
                 detail_url='/api/photo-spots/issues/'+r['issue_id']) for r in conn.execute(sql,args)]

def document_index(conn,url):
    record=load(conn,url)
    if not record: return dict(url=url,status='not_annotated',issue_count=0,issue_markers=[])
    ms=markers(conn,url)
    if record['stale']:
        iid='pss_'+record['doc_id'][4:]
        ms=[dict(issue_id=iid,field=None,occurrence_id=None,applies_to='document',label='待确认',detail_url='/api/photo-spots/issues/'+iid)]+ms
    return dict(url=url,doc_id=record['doc_id'],status='stale' if record['stale'] else 'reviewed' if record['reviewed'] else 'unreviewed',
                issue_count=len(ms),issue_markers=ms,contract_version=record['contract_version'])

def issue_detail(conn,iid):
    if not exists(conn): return None
    if iid.startswith('pss_'):
        row=conn.execute('SELECT url FROM photo_annotation_doc WHERE doc_id=?',('doc_'+iid[4:],)).fetchone()
        if not row: return None
        doc=load(conn,row['url'])
        if not doc['stale']: return None
        return dict(issue_id=iid,doc_id=doc['doc_id'],url=row['url'],occurrence_id=None,field=None,
                    reason_code='stale_source',title=LABELS['stale_source'],source_status='stale',
                    note='标注索引无法读取，需要重新导入；旧标注不参与确定性过滤。' if doc['invalid'] else '原文已变化，旧标注不参与确定性过滤；需要对新原文重新复核。',evidence=[])
    row=conn.execute('SELECT url,explanation FROM photo_annotation_issue WHERE issue_id=?',(iid,)).fetchone()
    if not row: return None
    detail=json.loads(row['explanation']); doc=load(conn,row['url'])
    detail['source_status']='stale' if doc['stale'] else 'current'
    return detail

def eligible(record,o):
    """Only confirmed fields; a pending field never disqualifies unrelated fields."""
    if record['stale'] or not record['reviewed']: return []
    pending={p['field'] for p in o['pending_fields']}
    approved_city=any(b['occurrence_id']==o['occurrence_id'] and b['review_status']=='reviewed_ok' for b in record['city_bindings'])
    result=[]
    for c in o['claims']:
        if c['state'] not in {'stated','resolved'}: continue
        c=dict(c)
        if c['field']=='location':
            v=dict(c['value'])
            if 'location' in pending: v={k:(v[k] if k=='city' and approved_city else None) for k in v}
            elif not approved_city: v['city']=None
            if not any(x is not None for x in v.values()): continue
            c['value']=v
        elif c['field'] in pending: continue
        if c['field']=='rail_relation' and c['state']!='resolved': continue
        result.append(c)
    return result

def candidates(conn, scope='', filters=None, limit=3):
    if not exists(conn): return dict(items=[],total=0,shown=0,truncated=False,available=False)
    filters=filters or {}; hits=[]
    for row in conn.execute('SELECT url FROM photo_annotation_doc'):
        record=load(conn,row['url'])
        if record['stale'] or not record['reviewed']: continue
        for o in record['annotation']['occurrences']:
            cs=eligible(record,o)
            group_fields={}
            for claim in o['claims']:
                if claim['group_id'] and claim['value'] is not None:
                    group_fields.setdefault(claim['group_id'],set()).add(claim['field'])
            searchable=[o['name_raw'] or ''] + [str(v) for c in cs for v in (c['value'].values() if isinstance(c['value'],dict) else [c['value']]) if v is not None]
            if scope and not any(scope in value for value in searchable): continue
            groups=None
            for field,wanted in filters.items():
                found=[c for c in cs if (c['field']=='location' and field=='city' and c['value']['city']==wanted)
                       or (c['field']==field and c['value']==wanted)]
                if not found: break
                # Unconditional claims apply to every group; conditional filters must coexist.
                # Single-field groups are independent recommendations, not cross-field conditions.
                gs={c['group_id'] if len(group_fields.get(c['group_id'],set()))>1 else None for c in found}
                if None not in gs: groups=gs if groups is None else groups & gs
                if groups==set(): break
            else:
                hits.append(dict(occurrence_id=o['occurrence_id'],name_raw=o['name_raw'],url=row['url'],
                    claims=cs,issue_markers=markers(conn,row['url'],o['occurrence_id']),review_status=o['review_status']))
    shown=hits[:max(1,min(int(limit),20))]
    return dict(items=shown,total=len(hits),shown=len(shown),truncated=len(shown)<len(hits),available=True)

def reviewed_text(conn,url):
    record=load(conn,url)
    if not record: return None
    if record['stale'] or not record['reviewed']:
        return '此篇标注尚未复核或原文已变化，不提供确定性机位事实。'
    blocks=[]
    for o in record['annotation']['occurrences']:
        cs=eligible(record,o)
        # Quotes and pending explanations stay in lazy detail; generation receives vetted values.
        facts=[dict(field=c['field'],value=c['value'],group_id=c['group_id'],valid_time_raw=c['valid_time_raw']) for c in cs]
        if facts: blocks.append(json.dumps(dict(name_raw=o['name_raw'],facts=facts),ensure_ascii=False))
    return '\n'.join(blocks) or '此篇未确认具体摄影站位，不根据标题、区域或被摄对象补造位置。'
