"""Independent fixtures plus invariants for the parallel candidate parser."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.fuck_cr.parser import parse, Version, Status, Domain


class Parser(unittest.TestCase):
    def test_independent_boundary_corpus(self):
        fixture=Path(__file__).parent/'fixtures/fuck_cr_parser_boundary_cases.json'
        cases=json.loads(fixture.read_text())
        for case in cases:
            with self.subTest(case=case['id']):
                result=parse(case['text'])
                self.assertEqual(list(result.codes),case['expected_codes'])
                self.assertFalse(set(result.codes)&set(case['forbidden_codes']))
                for token in result.tokens:
                    self.assertEqual(case['text'][token.start:token.end],token.raw)

    def test_duplicate_occurrences_and_original_unicode_offsets(self):
        text='🚆Ｇ１、g1，然后G2'
        result=parse(text)
        self.assertEqual(result.codes,('G1','G2'))
        self.assertEqual([t.normalized for t in result.tokens],['G1','G1','G2'])
        self.assertEqual([t.raw for t in result.tokens],['Ｇ１','g1','G2'])
        for t in result.tokens:self.assertEqual(text[t.start:t.end],t.raw)

    def test_opaque_unknown_and_malformed_never_yield_substrings(self):
        result=parse('ABCDE123次 AB123次 G123456次 G1abc fooG1')
        self.assertEqual(result.codes,())
        self.assertEqual([t.normalized for t in result.unknown],['ABCDE123','AB123','FOOG1'])
        self.assertEqual([t.raw for t in result.tokens],['ABCDE123','AB123','G123456','G1abc','fooG1'])

    def test_versions_are_explicit_and_do_not_assert_emu_coverage(self):
        old=parse('T109 K53 X1 V6001 DH1',version='legacy')
        new=parse('T109 K53 X1 V6001 DH1',version='reform_2026')
        self.assertEqual(old.codes,('T109','K53'))
        self.assertEqual(new.codes,('T109','K53','X1','V6001','DH1'))
        self.assertEqual(new.tokens[-1].domain,Domain.FREIGHT)
        self.assertFalse(hasattr(new.tokens[0],'is_emu'))
        with self.assertRaises(ValueError):parse('G1',version='automatic')

    def test_business_hints_are_candidates_not_universal_numeric_work(self):
        result=parse('71001次、80001次、85001次、99999次、DJ55001、DH1、B2001、G55001、0G1')
        self.assertEqual([t.domain for t in result.tokens],
                         [Domain.FREIGHT]*3+[Domain.UNKNOWN,Domain.WORK,Domain.FREIGHT,
                                             Domain.FREIGHT,Domain.UNKNOWN,Domain.UNKNOWN])

    def test_excluded_span_cannot_create_a_partial_tail(self):
        result=parse('G1234 G2',exclude_spans=((0,2),))
        self.assertEqual(result.codes,('G2',))
        self.assertEqual(result.tokens[0].raw,'G1234')
        self.assertEqual(result.tokens[0].reason,'excluded_span')
        for spans in [((-1,2),),((3,3),),((0,99),)]:
            with self.assertRaises(ValueError):parse('G1',exclude_spans=spans)

    def test_shadow_result_not_seeded_from_legacy_false_positive(self):
        from app.fuck_cr.shadow import candidates
        self.assertEqual(candidates('X1次',['1']),(['X1'],[]))
        self.assertEqual(candidates('ABC123次',['123']),([],['ABC123']))

    def test_linked_suffix_preserved_without_identity_inference(self):
        result=parse('Z264/265')
        self.assertEqual(result.codes,('Z264',))
        self.assertEqual(result.tokens[1].status,Status.AMBIGUOUS)
        self.assertEqual(result.tokens[1].reason,'linked_numeric_suffix')

    def test_linked_suffix_chain_cannot_restart_as_a_bare_train_number(self):
        for text,want in [('G1/2/3次',('G1',)),('Z264/2026/266次',('Z264',)),
                          ('AB123/456/789次',()),('G1/12306/3次',('G1',))]:
            with self.subTest(text=text):
                result=parse(text)
                self.assertEqual(result.codes,want)
                self.assertTrue(all(t.reason=='linked_numeric_suffix' for t in result.tokens[1:]))

    def test_numeric_freight_gaps_remain_unknown(self):
        result=parse('84998次 84999次 85000次 85001次')
        self.assertEqual([t.domain for t in result.tokens],
                         [Domain.FREIGHT,Domain.UNKNOWN,Domain.UNKNOWN,Domain.FREIGHT])


if __name__=='__main__':unittest.main()
