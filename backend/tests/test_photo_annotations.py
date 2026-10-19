import copy
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.data import photo_annotations as a
from app.api.photo_spots import router
from app.tools.photo_spot import PhotoSpotTool

ROOT=Path(__file__).resolve().parents[2]
FIXTURE=json.loads((Path(__file__).parent/'fixtures/photo_annotations.json').read_text())
def rows(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]

class AnnotationIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.path=Path(self.temp.name)/'dict.db'
        self.conn=sqlite3.connect(self.path); self.conn.row_factory=sqlite3.Row
        self.conn.executescript('CREATE TABLE photo_spot_doc(url TEXT PRIMARY KEY,page_text TEXT,snippet TEXT);'+a.SCHEMA)
        self.sources={r['doc_id']:r for r in FIXTURE['sources']}
        docs=copy.deepcopy(FIXTURE['annotations']); self.docs=docs
        self.conn.executemany('INSERT INTO photo_spot_doc VALUES(?,?,?)',[(self.sources[r['doc_id']]['url'],self.sources[r['doc_id']]['text'],'') for r in docs])
        self.reviewed=set(FIXTURE['reviewed_docs'])
        self.result=a.install(self.conn,docs,self.sources,self.reviewed,FIXTURE['city_bindings'],'photo-spot-v3');self.conn.commit()
    def tearDown(self):self.conn.close();self.temp.cleanup()
    def test_review_scope_and_lazy_detail(self):
        self.assertEqual(self.result['reviewed_documents'],6)
        url=next(self.sources[r['doc_id']]['url'] for r in self.docs if r['doc_id'][4:12]=='55c48fd7')
        index=a.document_index(self.conn,url)
        self.assertTrue(index['issue_markers'])
        self.assertNotIn('note',str(index.keys()))
        iid=index['issue_markers'][0]['issue_id']
        detail=a.issue_detail(self.conn,iid)
        self.assertTrue(detail['note']);self.assertTrue(detail['evidence'])
        self.assertNotIn(detail['note'],a.reviewed_text(self.conn,url))
    def test_confirmed_fields_survive_other_disputes(self):
        result=a.candidates(self.conn,'深圳',{'city':'深圳','point_type':'bridge'},20)
        self.assertTrue(result['items'])
        self.assertTrue(any(i['name_raw']=='笋岗桥' for i in result['items']))
        self.assertTrue(all(c['field']!='rail_relation' for i in result['items'] for c in i['claims']))
    def test_no_title_area_or_unreviewed_facts(self):
        self.assertEqual(a.candidates(self.conn,'芹峪口',{},20)['total'],0)
        self.assertEqual(a.candidates(self.conn,'人和隧道',{},20)['total'],0)
        for item in a.candidates(self.conn,'',{},20)['items']:
            self.assertIn(item['occurrence_id'].split(':')[0],self.reviewed)
    def test_condition_binding_and_truncation(self):
        result=a.candidates(self.conn,'',{'time_of_day':'morning','train_direction_raw':'下行'},20)
        self.assertEqual(result['total'],1)
        self.assertNotIn('西安桥',[i['name_raw'] for i in result['items']])
        self.assertEqual(a.candidates(self.conn,'',{'time_of_day':'afternoon','train_direction_raw':'下行'},20)['total'],0)
        self.assertEqual(a.candidates(self.conn,'',{'season':'summer','time_of_day':'morning'},20)['total'],1)
        result=a.candidates(self.conn,'长春',{},1)
        self.assertGreater(result['total'],result['shown']);self.assertTrue(result['truncated'])
    def test_changed_source_and_atomic_rejection(self):
        url=next(self.sources[r['doc_id']]['url'] for r in self.docs if r['doc_id'][4:12]=='0cfdad55')
        self.conn.execute('UPDATE photo_spot_doc SET page_text=? WHERE url=?',('changed',url));self.conn.commit()
        self.assertEqual(a.document_index(self.conn,url)['status'],'stale')
        self.assertEqual(a.candidates(self.conn,'上海',{},20)['total'],0)
        count=self.conn.execute('SELECT COUNT(*) FROM photo_annotation_doc').fetchone()[0]
        with self.assertRaises(ValueError):a.install(self.conn,self.docs,self.sources,self.reviewed,[], 'photo-spot-v3')
        self.assertEqual(count,self.conn.execute('SELECT COUNT(*) FROM photo_annotation_doc').fetchone()[0])
    def test_http_detail_is_only_expanded_on_detail_endpoint(self):
        app=FastAPI();app.include_router(router,prefix='/api')
        with patch('app.api.photo_spots.db_path',return_value=self.path),TestClient(app) as client:
            search=client.get('/api/photo-spots/search',params={'scope':'深圳','limit':20})
            self.assertEqual(search.status_code,200)
            self.assertNotIn('evidence',search.text);self.assertNotIn('note',search.text)
            marker=next(m for i in search.json()['items'] for m in i['issue_markers'])
            detail=client.get(marker['detail_url'])
            self.assertEqual(detail.status_code,200);self.assertIn('note',detail.json())
            self.assertEqual(client.get('/api/photo-spots/issues/missing').status_code,404)
    def test_legacy_dictionary(self):
        self.conn.executescript('DROP TABLE photo_annotation_doc;DROP TABLE photo_annotation_issue;')
        self.assertEqual(a.document_index(self.conn,'url')['status'],'not_annotated')
        self.assertFalse(a.candidates(self.conn,'深圳')['available'])

    def test_invalid_index_is_excluded_and_has_clickable_explanation(self):
        url=next(self.sources[r['doc_id']]['url'] for r in self.docs if r['doc_id'][4:12]=='55c48fd7')
        self.conn.execute('UPDATE photo_annotation_doc SET annotation=? WHERE url=?',('broken',url))
        index=a.document_index(self.conn,url)
        self.assertEqual(index['status'],'stale')
        self.assertIn('重新导入',a.issue_detail(self.conn,index['issue_markers'][0]['issue_id'])['note'])
        self.assertEqual(a.candidates(self.conn,'深圳',{},20)['total'],0)

if __name__=='__main__':unittest.main()
