import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from channelshift.jev_review import JevError
from channelshift.shared_services import SharedServices, ServiceError


class SharedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'usage.sqlite3'
        self.member = 'a' * 32
        self.source = '관리자만 문의를 조회합니다.'
        self.rows = [{'id': 'REQ-1', 'text': '관리자 문의 조회', 'origin': 'client', 'quote': self.source}]
        self.review = Mock(return_value={'status': 'REVIEW_REQUIRED', 'approved': False})
        self.collect = Mock(return_value={'kind': 'reference', 'approved': False})
        self.availability = {key: lambda: True for key in ('requirements_review', 'reference_collect')}
        self.services = self.make()

    def make(self, **kwargs):
        return SharedServices(self.path, review=self.review, collect=self.collect,
                              availability=self.availability, **kwargs)

    def test_status_is_configuration_only_and_no_provider_or_secret(self):
        status = self.services.status()
        self.assertEqual(status['requirements_review']['label'], '요구사항 검토')
        self.assertEqual(status['reference_collect']['label'], '참고자료 수집')
        self.assertEqual(status['reference_collect']['status'], 'configured')
        self.review.assert_not_called()
        self.collect.assert_not_called()
        self.assertNotIn('token', json.dumps(status))

    def test_unconfigured_makes_no_paid_call_or_reservation(self):
        self.availability['requirements_review'] = lambda: False
        with self.assertRaisesRegex(ServiceError, '^service_not_configured$'):
            self.services.review_for(self.member)(self.source, self.rows)
        self.review.assert_not_called()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM feature_usage').fetchone()[0], 0)
        db.close()

    def test_review_calls_existing_adapter_without_delegating_secret(self):
        result = self.services.review_for(self.member)(self.source, self.rows)
        self.review.assert_called_once_with(self.source, self.rows)
        self.assertFalse(result['approved'])
        with sqlite3.connect(self.path) as db:
            stored = db.execute('SELECT * FROM feature_usage').fetchall()
        db.close()
        self.assertNotIn(self.source, str(stored))

    def test_invalid_input_rejected_before_quota_or_provider(self):
        bad = [('', self.rows), ('x' * 12001, self.rows), (self.source, []),
               (self.source, self.rows * 2), (self.source, [dict(self.rows[0], actor='evil')]),
               (self.source, [dict(self.rows[0], quote='not in source')]),
               (self.source, [dict(self.rows[0], origin='internal')]),
               (self.source, [dict(self.rows[0], text='\ud800')])]
        for source, rows in bad:
            with self.subTest(source=repr(source)[:30]), self.assertRaisesRegex(ServiceError, '^service_invalid_input$'):
                self.services.review_for(self.member)(source, rows)
        self.review.assert_not_called()

    def test_member_daily_limit_survives_restart_and_resets_next_utc_day(self):
        clock = [100000.0]
        self.services = self.make(clock=lambda: clock[0])
        for _ in range(10):
            self.services.review_for(self.member)(self.source, self.rows)
        restarted = self.make(clock=lambda: clock[0])
        with self.assertRaisesRegex(ServiceError, '^service_rate_limited$'):
            restarted.review_for(self.member)(self.source, self.rows)
        restarted.review_for('b' * 32)(self.source, self.rows)
        clock[0] += 86400
        restarted.review_for(self.member)(self.source, self.rows)
        self.assertEqual(self.review.call_count, 12)

    def test_global_daily_limit_spans_members_and_instances(self):
        limits = {'requirements_review': (10, 2), 'reference_collect': (3, 30)}
        with patch('channelshift.shared_services.LIMITS', limits):
            self.services.review_for(self.member)(self.source, self.rows)
            self.make().review_for('b' * 32)(self.source, self.rows)
            with self.assertRaisesRegex(ServiceError, '^service_rate_limited$'):
                self.make().review_for('c' * 32)(self.source, self.rows)
        self.assertEqual(self.review.call_count, 2)

    def test_member_and_global_concurrency_cover_both_features(self):
        other = self.make()
        with self.services._reserve(self.member, 'requirements_review'):
            with self.assertRaisesRegex(ServiceError, '^service_busy$'):
                other.collect_reference(self.member, 'https://www.python.org/')
            with other._reserve('b' * 32, 'reference_collect'):
                with self.assertRaisesRegex(ServiceError, '^service_busy$'):
                    self.services.review_for('c' * 32)(self.source, self.rows)
        self.services.review_for(self.member)(self.source, self.rows)

    def test_unfinished_reservations_survive_restart_elapsed_time_and_cleanup(self):
        clock = [100000.0]
        self.services = self.make(clock=lambda: clock[0])
        old_day = int(clock[0] // 86400)
        with self.services._reserve(self.member, 'requirements_review'):
            self.services.review_for('b' * 32)(self.source, self.rows)
            clock[0] += 8 * 86400
            restarted = self.make(clock=lambda: clock[0])
            # A new completed request triggers retention cleanup.
            restarted.review_for('b' * 32)(self.source, self.rows)
            with restarted._connect() as db:
                old_rows = db.execute('SELECT member_id,finished FROM feature_usage WHERE day=?',
                                      (old_day,)).fetchall()
            self.assertEqual(old_rows, [(self.member, 0)])
            with self.assertRaisesRegex(ServiceError, '^service_busy$'):
                restarted.review_for(self.member)(self.source, self.rows)
            with restarted._reserve('b' * 32, 'reference_collect'):
                with self.assertRaisesRegex(ServiceError, '^service_busy$'):
                    restarted.review_for('c' * 32)(self.source, self.rows)
        restarted.review_for(self.member)(self.source, self.rows)

    def test_failed_requests_are_counted_and_errors_are_sanitized(self):
        self.review.side_effect = RuntimeError('PRIVATE-SECRET')
        for _ in range(10):
            with self.assertRaisesRegex(ServiceError, '^service_unavailable$'):
                self.services.review_for(self.member)(self.source, self.rows)
        with self.assertRaisesRegex(ServiceError, '^service_rate_limited$'):
            self.services.review_for(self.member)(self.source, self.rows)
        self.assertEqual(self.review.call_count, 10)

    def test_known_provider_error_becomes_feature_error(self):
        self.review.side_effect = JevError('jev_rate_limited')
        with self.assertRaisesRegex(ServiceError, '^service_rate_limited$'):
            self.services.review_for(self.member)(self.source, self.rows)
        self.collect.side_effect = ServiceError('PRIVATE-SECRET')
        with self.assertRaisesRegex(ServiceError, '^service_unavailable$'):
            self.services.collect_reference(self.member, 'https://www.python.org/')

    def test_collection_has_its_own_attempt_limit(self):
        for _ in range(3):
            self.services.collect_reference(self.member, 'https://www.python.org')
        with self.assertRaisesRegex(ServiceError, '^service_rate_limited$'):
            self.services.collect_reference(self.member, 'https://www.python.org')
        self.collect.assert_called_with('https://www.python.org/')
        self.services.review_for(self.member)(self.source, self.rows)

    def test_member_id_and_raw_options_cannot_be_injected(self):
        for value in (None, '../admin', 'a' * 33, 123):
            with self.assertRaisesRegex(ServiceError, '^service_invalid_input$'):
                self.services.review_for(value)
        for value in ({'actor': 'arbitrary', 'url': 'https://www.python.org'}, 'https://127.0.0.1'):
            with self.assertRaisesRegex(ServiceError, '^reference_url_invalid$'):
                self.services.collect_reference(self.member, value)
        self.collect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
