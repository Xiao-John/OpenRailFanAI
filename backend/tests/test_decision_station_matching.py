"""保留站名包含消歧，并按出现位置稳定排序，不依赖 set 遍历顺序。"""
import random
import unittest
from unittest.mock import patch
from app.pipeline import fastpath

def old_hits(text,names):
    found=[n for n in names if 2<=len(n)<=8 and n in text]
    kept=[]
    for n in sorted(found,key=len,reverse=True):
        if not any(n in k for k in kept):kept.append(n)
    return kept

class StationMatching(unittest.TestCase):
    def test_equivalence_including_ties_and_contained_names(self):
        names={'北京','北京南','上海','上海南','上海虹桥','南京','南京南','深圳北','厦门北','九','超长站点名称十个字'}
        texts=['北京南到上海虹桥','深圳北到厦门北','北京上海','换乘车站编号怎么看','', '上海南以及上海']
        rng=random.Random(12)
        texts += [''.join(rng.choices(list(sorted(names)),k=3)) for _ in range(100)]
        with patch.object(fastpath,'_station_names',return_value=names):
            for text in texts:
                expected=sorted(old_hits(text,names), key=lambda name:(text.find(name), -len(name), name))
                self.assertEqual(fastpath._station_hits(text),expected)
                candidates=[n for n in names if 2<=len(n)<=8 and n in text]
                self.assertEqual(fastpath._station_in_text(text),expected[0] if candidates else '')
    def test_in_place_dictionary_changes_are_seen(self):
        names={'北京'}
        with patch.object(fastpath,'_station_names',return_value=names):
            self.assertEqual(fastpath._station_in_text('北京南'),'北京')
            names.add('北京南')
            self.assertEqual(fastpath._station_in_text('北京南'),'北京南')
            names.clear()
            self.assertEqual(fastpath._station_hits('北京南'),[])

if __name__=='__main__':unittest.main()
