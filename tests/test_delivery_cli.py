"""CLI JSON stays UTF-8 even when the inherited console uses CP949."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DeliveryCLITests(unittest.TestCase):
    def invoke(self, *arguments):
        environment = dict(os.environ, PYTHONIOENCODING='cp949', PYTHONUTF8='0')
        return subprocess.run([sys.executable, '-B', '-m', 'channelshift', 'delivery-plan', *arguments],
                              capture_output=True, timeout=10, shell=False, env=environment)

    def test_default_plan_outputs_utf8_json_under_cp949(self):
        process = self.invoke()
        self.assertEqual(process.returncode, 0, process.stderr)
        value = json.loads(process.stdout.decode('utf-8'))
        self.assertEqual(value['mode'], 'planning_only')
        self.assertEqual(value['profile']['scope'][0], '회사 소개')
        self.assertFalse(value['runtime_connected'])

    def test_emoji_and_korean_source_roundtrip_without_loss(self):
        source = '문의 양식을 만들어 주세요. 😀'
        brief = {'format': 'channelshift.delivery-brief/v1', 'project_name': '회사 🌿',
                 'goal': '', 'inputs': {}, 'client_request': source}
        with tempfile.TemporaryDirectory(prefix='channelshift-cli-') as folder:
            path = Path(folder) / 'brief.json'
            path.write_text(json.dumps(brief, ensure_ascii=False), encoding='utf-8')
            process = self.invoke('--input', str(path))
        self.assertEqual(process.returncode, 0, process.stderr)
        value = json.loads(process.stdout.decode('utf-8'))
        self.assertEqual(value['brief'], brief)
        self.assertEqual(value['natural_language_processing'], 'not_invoked')

    def test_oversized_input_fails_without_echoing_its_contents(self):
        with tempfile.TemporaryDirectory(prefix='channelshift-cli-') as folder:
            path = Path(folder) / 'too-large.json'
            path.write_bytes(b'private-marker' + b'x' * 65536)
            process = self.invoke('--input', str(path))
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stdout, b'')
        self.assertNotIn(b'private-marker', process.stderr)


if __name__ == '__main__':
    unittest.main()
