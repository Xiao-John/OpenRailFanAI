"""Offline binding-ledger validation; independent of review workspace files."""

def ref_key(e):
    return (e['source_field'],e['start'],e['end'])

def check_bindings(records, bindings, sources, reviewed_docs):
    errors=[]; seen=set(); needed=set()
    occurrences={o['occurrence_id']:(r,o) for r in records if r['doc_id'] in reviewed_docs for o in r['occurrences']}
    for oid,(r,o) in occurrences.items():
        for i,c in enumerate(o['claims']):
            if c['field']=='location' and isinstance(c['value'],dict) and c['value'].get('city'):
                needed.add((oid,i))
    for b in bindings:
        key=(b.get('occurrence_id'),b.get('claim_index'))
        if key in seen: errors.append('duplicate binding')
        seen.add(key)
        if key not in needed:
            errors.append('binding does not reference a reviewed city claim'); continue
        r,o=occurrences[key[0]]; c=o['claims'][key[1]]; src=sources[r['doc_id']]
        if b.get('field')!='location.city' or b.get('binding_mode') not in {'direct','document_scope'} or b.get('review_status')!='reviewed_ok':
            errors.append('invalid binding state')
        keys={'occurrence_id','claim_index','field','binding_mode','city_evidence_refs','position_evidence_refs','scope_region_refs','review_status'}
        if set(b)!=keys: errors.append('invalid binding keys')
        claim_refs={ref_key(e) for e in c['evidence']}
        position_refs={ref_key(e) for e in o['location_evidence']}
        for field in ['city_evidence_refs','position_evidence_refs','scope_region_refs']:
            refs=b.get(field,[])
            if field!='scope_region_refs' and not refs: errors.append('missing '+field)
            for e in refs:
                if set(e)!={'source_field','start','end'} or e['source_field'] not in {'text','title'} or type(e['start']) is not int or type(e['end']) is not int:
                    errors.append('invalid reference'); continue
                text=src[e['source_field']]
                if not 0<=e['start']<e['end']<=len(text): errors.append('reference out of bounds')
                if field!='scope_region_refs' and ref_key(e) not in claim_refs: errors.append('reference not in claim evidence')
                if field=='position_evidence_refs' and ref_key(e) not in position_refs: errors.append('not a position reference')
        if not any(c['value']['city'] in src[e['source_field']][e['start']:e['end']] for e in b.get('city_evidence_refs',[]) if e['source_field'] in src):
            errors.append('city not supported by cited text')
        if b.get('binding_mode')=='document_scope':
            scope=b.get('scope_region_refs',[])
            if not scope: errors.append('missing scope')
            for e in b.get('position_evidence_refs',[]):
                if not any(s['source_field']==e['source_field']=='text' and s['start']<=e['start']<e['end']<=s['end'] for s in scope):
                    errors.append('position outside scope')
    if needed-seen: errors.append('city claim missing binding')
    return errors
