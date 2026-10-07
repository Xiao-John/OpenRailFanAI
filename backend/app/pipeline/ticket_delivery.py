"""Record-based ticket presentation selection before any public answer event."""
from copy import deepcopy
import os
from app.fare_result import project_fares

CAPABILITY = 'ticket_fare_availability_v1'


def fragment(name, params, result, text):
    record = dict(tool=name, query_context=deepcopy(params), data=deepcopy(result.data),
                  sources=list(result.sources), fetched_at=result.fetched_at, note=result.note)
    if not result.ok:
        record['message'] = result.error or '工具未返回查询结果'
    supplement = ''
    if name == 'ticket.query' and (result.filters or result.truncated or
            (result.total is not None and result.total > 1)):
        details = []
        if result.total is not None:
            shown = result.shown if result.shown is not None else result.total
            details.append(f"命中 {result.total} 趟，已展示 {shown} 趟。")
        if result.filters:
            details.append("筛选条件：" + "、".join(f"{k}={v}" for k,v in result.filters.items()))
        if result.truncated:
            details.append("结果已截断，以上不是全部车次；可缩小区间或筛选条件后重查。")
        supplement = f"{params.get('train') or '区间查询'} · {params.get('date') or ''}\n" + "\n".join(details)
    return dict(text=text, record=record, is_error=not result.ok, supplement=supplement)


def _identity(card):
    return tuple(card.get(k) for k in ('train_code','date','from_station','to_station'))


def _covered(piece, cards):
    record = piece.get('record')
    if not isinstance(record, dict) or record.get('tool') not in {'ticket.price','ticket.query'}:
        return False
    is_error = piece.get('is_error') is True
    expected = project_fares([] if is_error else [record], [record] if is_error else [])
    if not expected:
        return False
    for item in expected:
        key = _identity(item)
        if not all(isinstance(v,str) and v for v in key):
            return False
        matched = False
        for card in cards:
            if card.get('kind')!='ticket_fare' or card.get('schema_version')!=1 or _identity(card)!=key:
                continue
            if record['tool']=='ticket.price':
                # A projection rejection does not establish coverage of a malformed success receipt.
                if not is_error and item['status']=='failed':
                    continue
                fields=('prices','fare_basis','sources','fetched_at','error','note','start_time','arrive_time','duration')
                matched=(card.get('fare_status',card.get('status'))==item['status'] and
                         all(card.get(k)==item.get(k) for k in fields))
            else:
                wanted=item.get('availability');actual=card.get('availability')
                if not isinstance(wanted,dict) or not isinstance(actual,dict):
                    continue
                if not is_error and wanted['status']=='failed':
                    continue
                fields=('status','seats','sources','fetched_at','error','start_time','arrive_time','duration')
                note=wanted.get('note') or ''
                matched=all(actual.get(k)==wanted.get(k) for k in fields) and (
                    actual.get('note')==wanted.get('note') or
                    (actual.get('time_discrepancy') and (actual.get('note') or '').startswith(note)))
            if matched:
                break
        if not matched:
            return False
    return True


def prepare(retrieval, capabilities, display_results):
    """Keep legacy text unless the declared client can render each exact component."""
    if os.environ.get('APP_VARIANT','main').lower()=='lm' or not isinstance(capabilities,list) or CAPABILITY not in capabilities:
        return retrieval
    pieces=retrieval.get('direct_fragments')
    if not isinstance(pieces,list) or not pieces or not all(isinstance(p,dict) and isinstance(p.get('text'),str) for p in pieces):
        return retrieval
    # Untracked clarification or mixed prose must never be accidentally dropped.
    if '\n\n'.join(p['text'] for p in pieces if p['text'])!=retrieval.get('direct_answer'):
        return retrieval
    texts=[];removed=False
    for piece in pieces:
        if _covered(piece,display_results):
            removed=True
            if piece.get('supplement'):
                texts.append(piece['supplement'])
        else:
            texts.append(piece['text'])
    if not removed:
        return retrieval
    result=dict(retrieval)
    result['direct_answer']='\n\n'.join(t for t in texts if t)
    result['direct_complete']=True
    return result
