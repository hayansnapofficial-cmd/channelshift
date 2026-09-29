"""Process ownership, persistence and provider-alias boundaries with synthetic jobs."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from channelshift.delivery_workspace import DeliveryWorkspace
from channelshift import pipeline_workspace as workspace
from test_pipeline_workspace import SOURCE, candidate, erd, generated


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='channelshift-pipeline-boundary-')
        self.addCleanup(self.temp.cleanup)
        self.extract = Mock(side_effect=candidate)
        self.generator = Mock(side_effect=generated)
        self.delivery = DeliveryWorkspace(Path(self.temp.name) / 'delivery.sqlite3',
                                          extract=self.extract, generate_erd=erd)
        self.pipeline = workspace.PipelineWorkspace(self.delivery, generate=self.generator)
        self.addCleanup(self.delivery.close)
        self.addCleanup(self.pipeline.close)
        self.view = self.pipeline.create('합성 경계 검사', SOURCE, 'service')
        self.project_id = self.view['project']['id']

    def refresh(self):
        self.view = self.pipeline.get(self.project_id)
        return self.view

    def act(self, action, **payload):
        self.view = self.pipeline.action(self.project_id, self.view['pipeline']['revision'], action, payload)
        return self.view

    def drain(self):
        self.delivery._jobs.submit(lambda: None).result(5)
        self.pipeline._jobs.submit(lambda: None).result(10)
        self.refresh()

    def through(self, target='requirements'):
        self.act('analyze', answers=[])
        self.drain()
        self.act('confirm')
        if target == 'requirements':
            return
        for stage in ('wireframe', 'erd', 'api', 'database', 'backend', 'frontend'):
            self.act('generate', stage=stage)
            self.drain()
            self.assertEqual(self.view['pipeline']['job']['state'], 'completed', self.view['pipeline']['job'])
            self.act('approve', stage=stage, note='합성 결과 검토')
            if stage == target:
                return

    def fill_events(self, target):
        with self.pipeline._db() as db:
            current = db.execute('SELECT COUNT(*) FROM pipeline_events WHERE project_id=?', (self.project_id,)).fetchone()[0]
            db.executemany('INSERT INTO pipeline_events(project_id,at,kind,payload) VALUES(?,?,?,?)',
                           [(self.project_id, 'synthetic', 'test-padding', '{}')] * (target - current))

    def test_mutating_provider_cannot_replace_the_schema_used_for_validation(self):
        self.through('erd')
        original = copy.deepcopy(self.view['project']['erd_draft'])

        def malicious(stage, spec, dependencies):
            spec['requirements']['candidate']['requirements'][0]['id'] = 'REQ-999'
            schema = json.loads(dependencies['erd']['files'][0]['content'])
            schema['entities'][0]['name'] = 'unapproved_table'
            dependencies['erd']['files'][0]['content'] = json.dumps(schema)
            result = generated(stage)
            result['files'][0]['content'] = result['files'][0]['content'].replace('"inquiries"', '"unapproved_table"')
            return result

        self.generator.side_effect = malicious
        self.act('generate', stage='api')
        self.drain()
        self.assertEqual(self.view['pipeline']['job']['error'], 'invalid_pipeline_artifact')
        self.assertEqual(self.view['project']['erd_draft'], original)
        self.assertIsNone(next(stage for stage in self.view['pipeline']['stages'] if stage['id'] == 'api')['artifact'])

    def test_screen_ids_must_belong_to_confirmed_client_requirements(self):
        self.through()
        result = generated('wireframe')
        result['files'][1]['content'] = result['files'][1]['content'].replace('REQ-001', 'REQ-999')
        self.generator.side_effect = None
        self.generator.return_value = result
        self.act('generate', stage='wireframe')
        self.drain()
        self.assertEqual(self.view['pipeline']['job']['error'], 'invalid_pipeline_artifact')

    def test_event_capacity_is_reserved_before_start_and_terminal_failure_is_recorded(self):
        self.through()
        self.fill_events(workspace.MAX_EVENTS - 2)
        original = workspace.PipelineWorkspace._write

        def failing_once(db, project_id, state, version, kind, payload):
            if kind == 'artifact_generated':
                raise OSError('private-storage-path')
            return original(db, project_id, state, version, kind, payload)

        with patch.object(workspace.PipelineWorkspace, '_write', side_effect=failing_once):
            self.act('generate', stage='wireframe')
            self.drain()
        self.assertEqual(self.view['pipeline']['job']['state'], 'failed')
        self.assertEqual(self.view['pipeline']['job']['error'], 'pipeline_job_failed')
        self.assertNotIn('private-storage-path', json.dumps(self.view))
        calls = self.generator.call_count
        with self.assertRaisesRegex(ValueError, '^pipeline_storage_limit$'):
            self.act('generate', stage='wireframe')
        self.assertEqual(self.generator.call_count, calls)
        with self.pipeline._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pipeline_events WHERE project_id=?',
                                        (self.project_id,)).fetchone()[0], workspace.MAX_EVENTS)

    def test_full_event_storage_blocks_delivery_side_effect_before_start(self):
        self.fill_events(workspace.MAX_EVENTS)
        with self.assertRaisesRegex(ValueError, '^pipeline_storage_limit$'):
            self.act('analyze', answers=[])
        self.extract.assert_not_called()
        self.assertEqual(self.delivery.get(self.project_id)['state'], 'RECEIVED')

    def test_second_instance_cannot_recover_or_duplicate_an_active_job(self):
        self.through()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def wait(stage, *_):
            entered.set()
            release.wait(5)
            return generated(stage)

        self.generator.side_effect = wait
        self.act('generate', stage='wireframe')
        self.assertTrue(entered.wait(3))
        second = workspace.PipelineWorkspace(self.delivery, generate=self.generator)
        self.addCleanup(second.close)
        try:
            current = second.get(self.project_id)
            self.assertEqual(current['pipeline']['job']['state'], 'running')
            with self.assertRaisesRegex(ValueError, '^pipeline_busy$'):
                second.action(self.project_id, current['pipeline']['revision'], 'generate', {'stage': 'wireframe'})
            self.assertEqual(self.generator.call_count, 1)
        finally:
            release.set()
        self.drain()
        self.assertEqual(self.view['pipeline']['job']['state'], 'completed')

    def test_os_identity_distinguishes_a_live_child_and_proven_exit(self):
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        try:
            state, identity = workspace._process_identity(child.pid)
            self.assertEqual(state, 'alive')
            owner = {'pid': child.pid, 'identity': identity}
            self.assertFalse(workspace._owner_dead(owner))
        finally:
            child.terminate()
            child.wait(5)
        self.assertTrue(workspace._owner_dead(owner))
        self.assertFalse(workspace._owner_dead(None))
        with patch.object(workspace, '_process_identity', return_value=('unknown', None)):
            self.assertFalse(workspace._owner_dead(owner))
        with patch.object(workspace, '_process_identity', return_value=('alive', 'another-process-lifetime')):
            self.assertTrue(workspace._owner_dead(owner))

    def test_dead_pipeline_erd_recovery_matches_child_job_and_fences_late_completion(self):
        self.through('wireframe')
        with patch.object(self.pipeline._jobs, 'submit', return_value=Mock()):
            self.act('generate', stage='erd')
        job = self.view['pipeline']['job']
        before = self.delivery.get(self.project_id)
        with patch.object(self.delivery._jobs, 'submit', return_value=Mock()):
            waiting = self.delivery.generate_erd(self.project_id, 'sqlite', before['intervention_revision'],
                                                 pipeline_job_id=job['id'])
        started = next(event['payload'] for event in reversed(waiting['events']) if event['kind'] == 'erd_started')
        replacement_delivery = DeliveryWorkspace(self.delivery.path, extract=self.extract, generate_erd=erd)
        self.addCleanup(replacement_delivery.close)
        with patch.object(workspace, '_owner_dead', return_value=True):
            replacement = workspace.PipelineWorkspace(replacement_delivery, generate=self.generator)
        self.addCleanup(replacement.close)
        recovered = replacement.get(self.project_id)
        self.assertEqual(recovered['pipeline']['job']['error'], 'pipeline_recovery_required')
        self.assertEqual(recovered['project']['state'], before['state'])
        self.assertEqual(recovered['project']['events'][-1]['kind'], 'erd_failed')
        # A stale callback cannot reinsert a recovered result or restore its old state.
        self.delivery._run_erd(before, started['input_snapshot'], 'sqlite', started['job_id'])
        after = replacement.get(self.project_id)
        self.assertIsNone(after['project']['erd_draft'])
        self.assertEqual(after['project']['events'][-1]['kind'], 'erd_failed')
        replacement.action(self.project_id, after['pipeline']['revision'], 'generate', {'stage': 'erd'})
        replacement._jobs.submit(lambda: None).result(10)
        self.assertEqual(replacement.get(self.project_id)['pipeline']['job']['state'], 'completed')

    def test_dead_job_recovery_does_not_reset_an_unrelated_legacy_erd(self):
        self.through('wireframe')
        with patch.object(self.pipeline._jobs, 'submit', return_value=Mock()):
            self.act('generate', stage='erd')
        project = self.delivery.get(self.project_id)
        with patch.object(self.delivery._jobs, 'submit', return_value=Mock()):
            self.delivery.generate_erd(self.project_id, 'sqlite', project['intervention_revision'])
        with patch.object(workspace, '_owner_dead', return_value=True):
            self.pipeline._recover_dead_jobs()
        self.assertEqual(self.delivery.get(self.project_id)['state'], 'DESIGNING_ERD')
        self.assertEqual(self.refresh()['pipeline']['job']['error'], 'pipeline_recovery_required')

    def test_added_client_scope_is_append_only_requires_reanalysis_and_invalidates_reviews(self):
        self.through('wireframe')
        original = copy.deepcopy(self.view['project']['source'])
        previous = next(stage for stage in self.view['pipeline']['stages'] if stage['id'] == 'wireframe')['artifact']
        old_revision = self.view['pipeline']['revision']
        calls = self.extract.call_count
        self.act('add_request', text='고객이 문의를 취소할 수 있는 화면도 추가해 주세요.')
        self.assertEqual(self.extract.call_count, calls)
        self.assertEqual(self.view['project']['source'], original)
        addition = self.view['project']['source_additions'][0]
        self.assertEqual(addition['id'], 'SRC-002')
        self.assertEqual(set(addition), {'id', 'text', 'digest', 'created_at'})
        self.assertFalse(self.view['pipeline']['analysis_current'])
        self.assertFalse(self.view['pipeline']['confirmed'])
        wire = next(stage for stage in self.view['pipeline']['stages'] if stage['id'] == 'wireframe')
        self.assertEqual((wire['state'], wire['artifact']), ('stale', previous))
        with self.assertRaisesRegex(ValueError, '^pipeline_analysis_required$'):
            self.act('confirm')
        with self.assertRaisesRegex(ValueError, '^pipeline_revision_conflict$'):
            self.pipeline.action(self.project_id, old_revision, 'add_request', {'text': 'old tab'})
        self.assertEqual(len(self.refresh()['project']['source_additions']), 1)
        self.act('analyze', answers=[])
        self.drain()
        self.assertIn(SOURCE, self.extract.call_args.args[0])
        self.assertIn(addition['text'], self.extract.call_args.args[0])
        self.assertTrue(self.view['pipeline']['analysis_current'])
        self.act('confirm')
        self.assertTrue(self.view['pipeline']['confirmed'])
        self.assertEqual(self.view['project']['requirements_review']['source_additions'], [addition])

    def test_additions_are_bounded_against_combined_source_before_any_write(self):
        self.act('add_request', text='a' * 4000)
        self.act('add_request', text='b' * 4000)
        self.assertEqual([item['id'] for item in self.view['project']['source_additions']], ['SRC-002', 'SRC-003'])
        previous = self.view['pipeline']['revision']
        for text, error in [('c' * 4000, 'delivery_input_limit'), ('x' * 4001, 'invalid_delivery_input'),
                            ('bad\x00text', 'invalid_delivery_input'), ('\ud800', 'invalid_delivery_input')]:
            with self.assertRaisesRegex(ValueError, '^' + error + '$'):
                self.act('add_request', text=text)
            self.assertEqual(self.refresh()['pipeline']['revision'], previous)
            self.assertEqual(len(self.view['project']['source_additions']), 2)
        self.extract.assert_not_called()

    def test_direct_legacy_scope_addition_during_generation_discards_the_result(self):
        self.through()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def delayed(stage, *_):
            entered.set()
            release.wait(5)
            return generated(stage)

        self.generator.side_effect = delayed
        self.act('generate', stage='wireframe')
        self.assertTrue(entered.wait(3))
        project = self.delivery.get(self.project_id)
        self.delivery.add_request(self.project_id, '새 문의 분류도 필요합니다.', project['intervention_revision'])
        release.set()
        self.drain()
        self.assertEqual(self.view['pipeline']['job']['error'], 'pipeline_artifact_stale')
        self.assertFalse(self.view['pipeline']['analysis_current'])
        self.assertIsNone(next(stage for stage in self.view['pipeline']['stages'] if stage['id'] == 'wireframe')['artifact'])


if __name__ == '__main__':
    unittest.main()
