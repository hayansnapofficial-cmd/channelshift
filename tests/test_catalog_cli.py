"""Public planning commands reject oversized input and never imply execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CatalogCLITests(unittest.TestCase):
    def invoke(self, command, value=None):
        with tempfile.TemporaryDirectory(prefix='channelshift-catalog-cli-') as folder:
            args = [sys.executable, '-B', '-m', 'channelshift', command]
            if value is not None:
                path = Path(folder) / 'config.json'
                path.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8'))
                args += ['--input', str(path)]
            return subprocess.run(args, capture_output=True, timeout=10, shell=False,
                                  env=dict(os.environ, PYTHONIOENCODING='cp949'))

    def test_catalog_and_korean_source_plan(self):
        result = self.invoke('project-catalog')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('SEO_BASIC', [x['id'] for x in json.loads(result.stdout)['catalog']['modules']])
        result = self.invoke('project-plan', {'project_id': 'sample', 'sources': [{'id': 'SRC-001', 'text': '문의 화면 😀'}],
                                             'preset': 'custom', 'modules': []})
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertFalse(plan['execution_enabled'])
        self.assertFalse(plan['approved'])
        self.assertEqual(plan['requirements'][0]['origin'], 'internal_proposal')

    def test_missing_sources_rejected_without_echo(self):
        result = self.invoke('project-plan', {'project_id': 'private-marker', 'sources': [], 'preset': 'custom', 'modules': []})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'')
        self.assertNotIn(b'private-marker', result.stderr)

    def test_oversized_and_malformed_payloads_rejected_for_each_command(self):
        for command in ('project-plan', 'dns-plan', 'seo-metadata'):
            for payload in (b'private-marker' + b'x' * 131072, b'{malformed'):
                with self.subTest(command=command, length=len(payload)):
                    result = self.invoke(command, payload)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, b'')
                    self.assertNotIn(b'private-marker', result.stderr)


if __name__ == '__main__':
    unittest.main()
