"""Local Codex intake boundaries; all model calls are mocked."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from channelshift import codex_intake as intake


REQUEST = '회사 소개와 문의 폼을 만들어 주세요. 문의는 관리자만 보게 해 주세요.'


def candidate():
    return {
        'requirements': [{'id': 'REQ-001', 'text': '문의 제출 화면을 제공한다.',
                          'quote': '문의 폼을 만들어 주세요.', 'origin': 'client'}],
        'questions': [{'id': 'Q-001', 'text': '문의 보관 기간은 얼마인가요?', 'blocking': True}],
        'out_of_scope': [],
    }


def ready():
    return {'available': True, 'authenticated': True, 'auth_mode': 'chatgpt',
            'can_execute': True, 'cli_version': '0.158.0-alpha.2.1', 'reason': 'ready'}


class CandidateTests(unittest.TestCase):
    def test_quotes_and_internal_suggestions_are_distinct_and_immutable(self):
        value = candidate()
        value['requirements'].append({'id': 'REQ-002', 'text': '제안: 입력 길이를 제한한다.',
                                      'quote': '', 'origin': 'internal'})
        result = intake.validate_candidate(value, REQUEST)
        result['requirements'].clear()
        self.assertEqual(len(value['requirements']), 2)

    def test_fabricated_quotes_approval_flags_and_invalid_types_are_rejected(self):
        changes = [
            ('requirements', 0, 'quote', '고객이 승인했습니다.'),
            ('requirements', 0, 'quote', ''),
            ('requirements', 0, 'origin', 'approved'),
            ('requirements', 0, 'origin', ['client']),
            ('requirements', 0, 'text', 'x' * 2001),
            ('requirements', 0, 'text', '\ud800'),
            ('requirements', 0, 'id', 'REQ-001\n'),
            ('questions', 0, 'blocking', 1),
            ('questions', 0, 'text', '\x00'),
        ]
        for array, index, key, replacement in changes:
            with self.subTest(key=key, replacement=repr(replacement)[:40]):
                value = candidate()
                value[array][index][key] = replacement
                with self.assertRaisesRegex(intake.CodexIntakeError, '^codex_invalid_output$'):
                    intake.validate_candidate(value, REQUEST)
        for value in (None, [], {**candidate(), 'approved': True}):
            with self.assertRaises(intake.CodexIntakeError):
                intake.validate_candidate(value, REQUEST)

    def test_duplicate_ids_empty_output_and_unbounded_lists_are_rejected(self):
        value = candidate()
        value['requirements'] *= 2
        with self.assertRaises(intake.CodexIntakeError):
            intake.validate_candidate(value, REQUEST)
        for array in ('requirements', 'questions', 'out_of_scope'):
            value = candidate()
            value[array] = [{}] * 33
            with self.assertRaises(intake.CodexIntakeError):
                intake.validate_candidate(value, REQUEST)
        with self.assertRaises(intake.CodexIntakeError):
            intake.validate_candidate({'requirements': [], 'questions': [], 'out_of_scope': []}, REQUEST)

    def test_out_of_scope_also_requires_a_real_quote(self):
        value = candidate()
        value['out_of_scope'] = [{'text': '결제는 후속 범위입니다.', 'quote': '결제도 넣어 주세요.'}]
        with self.assertRaises(intake.CodexIntakeError):
            intake.validate_candidate(value, REQUEST)
        intake.validate_candidate(value, REQUEST + ' 결제도 넣어 주세요.')


class StatusTests(unittest.TestCase):
    def probe_outputs(self, login, code=0):
        return [(0, b'codex-cli 0.158.0-alpha.2.1\n', b''),
                (0, ' '.join(intake._REQUIRED_FLAGS).encode(), b''),
                (0, '\n'.join((*intake._DISABLED_FEATURES, 'skip_host_skill_discovery')).encode(), b''),
                (code, b'', login.encode())]

    def test_chatgpt_status_is_exact_and_never_returns_raw_output(self):
        for login, code, mode, allowed in [
            ('Logged in using ChatGPT', 0, 'chatgpt', True),
            ('Logged in using an API key - sk-private-value', 0, 'api_key', False),
            ('Not logged in', 1, 'none', False),
            ('Logged in using ChatGPT\nsk-private-value', 0, 'unknown', False),
        ]:
            with self.subTest(mode=mode, allowed=allowed), patch.dict(os.environ, {}, clear=True), \
                 patch.object(intake, '_executable', return_value='codex'), \
                 patch.object(intake, '_run_bounded', side_effect=self.probe_outputs(login, code)) as runner:
                result = intake.status()
                self.assertEqual(result['auth_mode'], mode)
                self.assertEqual(result['can_execute'], allowed)
                self.assertNotIn('sk-private-value', json.dumps(result))
                self.assertEqual(runner.call_args.args[0], ['codex', 'login', 'status'])

    def test_provider_environment_prevents_probe_and_typesafe_secret_is_not_inherited(self):
        environment = {'PATH': 'safe-path', 'USERPROFILE': 'profile', 'CODEX_HOME': 'codex-home',
                       'TYPESAFE_API_KEY': 'private-jev', 'ANTHROPIC_API_KEY': 'private-other',
                       'CUSTOM_API_KEY': 'private-custom', 'OPENAI_API_KEY': 'private-openai'}
        with patch.dict(os.environ, environment, clear=True), \
             patch.object(intake, '_executable', return_value='codex'), \
             patch.object(intake, '_probe') as probe:
            result = intake.status()
            self.assertEqual(result['reason'], 'codex_unsafe_provider_environment')
            probe.assert_not_called()
            child = intake._child_environment()
            self.assertEqual(set(child), {'PATH', 'USERPROFILE', 'CODEX_HOME'})

    def test_missing_capability_fails_closed_without_auth_or_model_call(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(intake, '_executable', return_value='codex'), \
             patch.object(intake, '_run_bounded', side_effect=[(0, b'codex-cli 1.0', b''),
                                                              (0, b'old help', b'')]) as runner:
            self.assertEqual(intake.status()['reason'], 'codex_unsupported_cli')
            self.assertEqual(runner.call_count, 2)


class ExtractionTests(unittest.TestCase):
    def call_with(self, transport, request=REQUEST):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(intake, '_executable', return_value='codex'), \
             patch.object(intake, '_probe', return_value=ready()), \
             patch.object(intake, '_run_bounded', side_effect=transport):
            return intake.extract_requirements(request)

    def test_real_command_contract_uses_stdin_temp_workspace_and_no_project_write(self):
        workspace_seen = []

        def transport(arguments, **options):
            self.assertEqual(arguments[:5], ['codex', '--no-daemon', '-a', 'never', 'exec'])
            self.assertEqual(arguments[-1], '-')
            self.assertNotIn(REQUEST, arguments)
            self.assertIn(REQUEST, options['input_text'])
            self.assertEqual(arguments[arguments.index('--sandbox') + 1], 'read-only')
            self.assertIn('forced_login_method="chatgpt"', arguments)
            self.assertIn('model_provider="openai"', arguments)
            self.assertIn('--ignore-user-config', arguments)
            workspace = Path(options['cwd'])
            workspace_seen.append(workspace)
            self.assertTrue(workspace.name == 'workspace' and workspace.parent.name.startswith('channelshift-codex-intake-'))
            self.assertEqual(set(path.name for path in workspace.iterdir()), {'.git'})
            schema_path = Path(arguments[arguments.index('--output-schema') + 1])
            self.assertEqual(json.loads(schema_path.read_text(encoding='utf-8')), intake.OUTPUT_SCHEMA)
            output_path = Path(arguments[arguments.index('--output-last-message') + 1])
            output_path.write_text(json.dumps(candidate(), ensure_ascii=False), encoding='utf-8')
            return 0, b'', b''

        self.assertEqual(self.call_with(transport), candidate())
        self.assertFalse(workspace_seen[0].exists())

    def test_auth_failure_does_not_start_a_model_and_invalid_input_does_not_probe(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(intake, '_executable', return_value='codex'), \
             patch.object(intake, '_probe', return_value={**ready(), 'can_execute': False,
                                                        'reason': 'codex_subscription_required'}), \
             patch.object(intake, '_run_bounded') as runner:
            with self.assertRaisesRegex(intake.CodexIntakeError, '^codex_subscription_required$'):
                intake.extract_requirements(REQUEST)
            runner.assert_not_called()
        for value in (None, True, '', ' ', 'x' * 12001, '\ud800', '\x00'):
            with self.subTest(value=repr(value)[:30]), patch.object(intake, '_probe') as probe:
                with self.assertRaisesRegex(intake.CodexIntakeError, '^invalid_client_request$'):
                    intake.extract_requirements(value)
                probe.assert_not_called()

    def test_safe_errors_do_not_include_process_output_and_never_retry(self):
        for code, message in [('codex_rate_limited', b'usage limit private-token'),
                              ('codex_authentication_required', b'401 private-token'),
                              ('codex_execution_failed', b'failure private-token')]:
            calls = []

            def transport(arguments, **options):
                calls.append(arguments)
                return 1, b'', message

            with self.subTest(code=code), self.assertRaisesRegex(intake.CodexIntakeError, '^' + code + '$'):
                self.call_with(transport)
            self.assertEqual(len(calls), 1)

    def test_malformed_duplicate_nonfinite_and_oversized_model_json_fail_closed(self):
        for raw in (b'not json', b'{}', b'{"requirements":[],"requirements":[]}',
                    b'{"requirements":NaN}', b'\xff', b'x' * (intake.MAX_RESULT + 1)):
            def transport(arguments, **options):
                Path(arguments[arguments.index('--output-last-message') + 1]).write_bytes(raw)
                return 0, b'', b''

            with self.subTest(size=len(raw)), self.assertRaises(intake.CodexIntakeError):
                self.call_with(transport)

    def test_missing_output_and_transport_timeout_do_not_fabricate_a_candidate(self):
        with self.assertRaisesRegex(intake.CodexIntakeError, '^codex_invalid_output$'):
            self.call_with(lambda *args, **kwargs: (0, b'{"approved":true}', b''))
        with self.assertRaisesRegex(intake.CodexIntakeError, '^codex_timeout$'):
            self.call_with(intake.CodexIntakeError('codex_timeout'))
        self.assertTrue(intake._EXECUTION_LOCK.acquire(blocking=False))
        intake._EXECUTION_LOCK.release()

    def test_concurrent_extraction_is_rejected_without_second_process(self):
        intake._EXECUTION_LOCK.acquire()
        try:
            with patch.object(intake, '_probe') as probe:
                with self.assertRaisesRegex(intake.CodexIntakeError, '^codex_busy$'):
                    intake.extract_requirements(REQUEST)
                probe.assert_not_called()
        finally:
            intake._EXECUTION_LOCK.release()


class BoundedProcessTests(unittest.TestCase):
    def test_real_subprocess_drains_both_pipes_and_preserves_utf8_stdin(self):
        script = 'import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stderr.buffer.write(b"err")'
        with tempfile.TemporaryDirectory() as folder:
            code, out, err = intake._run_bounded([sys.executable, '-B', '-c', script],
                                                cwd=folder, timeout=5, input_text=REQUEST)
        self.assertEqual(code, 0)
        self.assertEqual(out.decode('utf-8'), REQUEST)
        self.assertEqual(err, b'err')

    def test_real_subprocess_timeout_and_output_limit(self):
        for script, timeout, limit, expected in [
            ('import time; time.sleep(30)', 0.1, 10000, 'codex_timeout'),
            ('import sys; sys.stdout.buffer.write(b"x"*10000)', 5, 1024, 'codex_output_limit'),
        ]:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder:
                with self.assertRaisesRegex(intake.CodexIntakeError, '^' + expected + '$'):
                    intake._run_bounded([sys.executable, '-B', '-c', script], cwd=folder,
                                        timeout=timeout, output_limit=limit)


if __name__ == '__main__':
    unittest.main()
