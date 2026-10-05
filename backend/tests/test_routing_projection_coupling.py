from app.display_result import _routing, _project_routing_records

def run():
    a = dict(train_code='G1', date='2026-10-04', time='06:30', emu_no='CR400BF5033', emu_no_display='CR400BF-5033')
    b = dict(a, emu_no='CR400BF5034', emu_no_display='CR400BF-5034')
    records = _project_routing_records([a, b, a])
    assert len(records) == 1 and records[0]['coupled']
    assert len(records[0]['units']) == 2
    for field, other in [('time', '07:30'), ('train_code', 'G2'), ('date', '2026-10-03')]:
        assert len(_project_routing_records([a, dict(b, **{field: other})])) == 2
    assert len(_project_routing_records([dict(a, time=None), dict(b, time=None)])) == 2
    result = _routing(dict(kind='train', query='G1', focus_date='2026-10-04', records=[a, b, a]))
    assert result['query_kind'] == 'train' and len(result['records']) == 1
    assert result['records'][0]['units'][1]['emu_no_display'] == 'CR400BF-5034'
    history = _routing(dict(kind='emu', query='CR400BF5033', focus_date='2026-10-05', records=[a, b]))
    assert history['kind'] == 'empty' and history['historical_records'][0]['coupled']
    recent = _routing(dict(kind='emu', query='CR400BF5033', focus_date=None, query_mode='recent', records=[a, dict(a, date='2026-10-03')]))
    assert recent['kind'] == 'emu_routing' and len(recent['records']) == 2
    assert recent['records'][1]['date'] == '2026-10-03'
    print('routing projection/coupling tests passed')

if __name__ == '__main__': run()
