"""Additive v1 fare/availability projection preserves independent fact scope."""
import copy
import json
import unittest
from app.fare_result import project_fares

DAY = '2026-10-07'
CTX = dict(train='G1', date=DAY, from_station='北京南', to_station='上海虹桥')


def fare():
    return dict(tool='ticket.price', query_context=dict(CTX), fetched_at='fare-time', sources=['fare-source'],
                data={'query_date': DAY, 'data': [dict(train_code='G1', from_station='北京南',
                      to_station='上海虹桥', start_time='09:00', prices={'二等座': 661})]})


def ticket():
    return dict(tool='ticket.query', query_context=dict(CTX), fetched_at='ticket-time', sources=['ticket-source'],
                data={'train_date': DAY, 'from_station': '北京南', 'to_station': '上海虹桥',
                      'trains': [dict(train_no='G1', from_station='北京南', to_station='上海虹桥',
                                     start_time='09:00', seats={'second_class': '有', 'first_class': '候补',
                                     'business_class': '0', 'no_seat': '--'})]})


class CombinedProjection(unittest.TestCase):
    def test_price_only_receipts_keep_original_shape(self):
        value = project_fares([fare()], [])[0]
        self.assertEqual(value['status'], 'success')
        self.assertNotIn('availability', value)
        self.assertNotIn('fare_status', value)

    def test_exact_identity_one_card_independent_sources_time_and_states(self):
        values = project_fares([fare(), ticket()], [])
        self.assertEqual(len(values), 1)
        value = values[0]
        self.assertEqual(value['fare_status'], 'success')
        self.assertEqual(value['status'], 'partial')  # '--' is unknown, not zero.
        self.assertEqual(value['sources'], ['fare-source'])
        self.assertEqual(value['fetched_at'], 'fare-time')
        availability = value['availability']
        self.assertEqual(availability['sources'], ['ticket-source'])
        self.assertEqual(availability['fetched_at'], 'ticket-time')
        self.assertEqual([v['seat'] for v in availability['seats']], ['二等座', '一等座', '商务座', '无座'])
        self.assertEqual([v['status'] for v in availability['seats']], ['available', 'waitlist', 'unavailable', 'unknown'])
        self.assertEqual(value['prices'][0]['amount'], '661')
        json.dumps(value, allow_nan=False)

    def test_both_success_and_empty(self):
        receipt = ticket(); receipt['data']['trains'][0]['seats'] = {'second_class': '2'}
        self.assertEqual(project_fares([fare(), receipt], [])[0]['status'], 'success')
        f, t = fare(), ticket(); f['data']['data'] = []; t['data']['trains'] = []
        self.assertEqual(project_fares([f, t], [])[0]['status'], 'empty')

    def test_either_failure_does_not_discard_other_component(self):
        error = dict(tool='ticket.price', query_context=CTX, message='价格超时', sources=['error-source'])
        value = project_fares([ticket()], [error])[0]
        self.assertEqual(value['fare_status'], 'failed')
        self.assertEqual(value['fare_error'], '价格超时')
        self.assertEqual(value['availability']['seats'][0]['status'], 'available')
        self.assertEqual(value['status'], 'partial')
        error = dict(tool='ticket.query', query_context=CTX, message='余票超时')
        value = project_fares([fare()], [error])[0]
        self.assertEqual(value['availability']['status'], 'failed')
        self.assertEqual(value['availability']['error'], '余票超时')
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['prices'][0]['amount'], '661')

    def test_semantic_empty_error_preserves_canonical_scope_and_error(self):
        error = dict(tool="ticket.query", query_context=dict(CTX, from_station="北京"),
                     data={"availability_status": "empty", "from_station": "北京南",
                           "to_station": "上海虹桥", "train_date": DAY}, message="未找到精确车次")
        f = fare(); f["data"]["data"] = []
        value = project_fares([f], [error])[0]
        self.assertEqual(value["status"], "empty")
        self.assertEqual(value["availability"]["error"], "未找到精确车次")
        error["data"]["train_date"] = "2026-10-08"
        self.assertEqual(project_fares([], [error])[0]["availability"]["status"], "failed")

    def test_ticket_only_has_no_price_and_no_borrowed_price_provenance(self):
        value = project_fares([ticket()], [])[0]
        self.assertEqual(value['fare_status'], 'not_requested')
        self.assertEqual(value['prices'], [])
        self.assertEqual(value['sources'], [])
        self.assertIsNone(value['fetched_at'])
        self.assertEqual(value['availability']['sources'], ['ticket-source'])

    def test_mismatched_row_identity_is_rejected_without_relabeling(self):
        for field, raw in [('train_no', 'G2'), ('to_station', '南京南'), ('from_station', None), ('date', '2026-10-08')]:
            t = ticket(); t['data']['trains'][0][field] = raw
            value = project_fares([fare(), t], [])[0]
            self.assertEqual(value['availability']['status'], 'failed')
            self.assertEqual(value['availability']['seats'], [])
            self.assertEqual(value['train_code'], 'G1')
            self.assertEqual(value['to_station'], '上海虹桥')

    def test_other_dates_are_separate_and_payload_date_conflict_failed(self):
        t = ticket(); t['query_context']['date'] = '2026-10-08'; t['data']['train_date'] = '2026-10-08'
        values = project_fares([fare(), t], [])
        self.assertEqual(len(values), 2)
        self.assertEqual(values[0]['availability']['status'], 'not_requested')
        self.assertEqual(values[1]['date'], '2026-10-08')
        t['data']['train_date'] = DAY
        value = project_fares([t], [])[0]
        self.assertEqual(value['availability']['status'], 'failed')
        self.assertEqual(value['availability']['seats'], [])

    def test_missing_query_day_never_borrows_request_date(self):
        t = ticket(); t['data'].pop('train_date')
        value = project_fares([fare(), t], [])[0]
        self.assertEqual(value['availability']['status'], 'failed')
        self.assertIn('缺少查询日期', value['availability']['error'])
        self.assertEqual(value['availability']['seats'], [])
        t['query_date'] = DAY
        self.assertEqual(project_fares([fare(), t], [])[0]['availability']['status'], 'partial')

    def test_error_status_cannot_claim_success(self):
        for status in ['success', 'partial', 'not_requested', 'invented', {}]:
            error = dict(tool='ticket.query', query_context=CTX,
                         data={'availability_status': status}, message='接口失败')
            value = project_fares([fare()], [error])[0]
            self.assertEqual(value['availability']['status'], 'failed')

    def test_discrepant_times_preserved_and_input_unmodified(self):
        f, t = fare(), ticket(); t['data']['trains'][0]['start_time'] = '09:01'
        original = copy.deepcopy([f, t])
        value = project_fares([f, t], [])[0]
        self.assertEqual(value['start_time'], '09:00')
        self.assertEqual(value['availability']['start_time'], '09:01')
        self.assertEqual(value['availability']['time_discrepancy'], ['start_time'])
        self.assertEqual([f, t], original)

    def test_missing_values_and_bools_never_mean_sold_out(self):
        for raw in [None, '', '--', True, float('nan'), '待定']:
            t = ticket(); t['data']['trains'][0]['seats'] = {'second_class': raw}
            value = project_fares([t], [])[0]
            self.assertEqual(value['availability']['seats'][0]['status'], 'unknown')
            self.assertEqual(value['availability']['status'], 'partial')
            json.dumps(value, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
