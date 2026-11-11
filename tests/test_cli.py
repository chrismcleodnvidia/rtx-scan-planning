"""Portable entrypoint checks; native GPU checks are explicit in preflight."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Entrypoints(unittest.TestCase):
    def test_cli_help(self):
        for name in ['pipeline.py', 'scan_refinement.py', 'density_plan.py']:
            with self.subTest(name=name):
                result = subprocess.run([sys.executable, str(ROOT / 'runtime' / name), '--help'],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)

    def preflight(self, args, extra_env=None):
        with tempfile.TemporaryDirectory(prefix='scan test ') as directory:
            env = dict(os.environ, BIMSCAN_PYTHON=sys.executable, BIMSCAN_DATA=directory)
            env.update(extra_env or {})
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/preflight.py'), *args],
                                    env=env, capture_output=True, text=True, timeout=90)
            return result, json.loads(result.stdout)

    def test_unrequested_gpu_is_not_reported_as_verified(self):
        result, report = self.preflight([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(report['capture_environment_checked'])
        self.assertFalse(report['native_scan_verified'])
        self.assertFalse(report['checks'][0]['details']['gpu_verified'])
        self.assertEqual(next(c for c in report['checks'] if c['name']=='isaac')['status'], 'not_checked')

    def test_missing_requested_runtime_fails_with_actionable_result(self):
        result, report = self.preflight(['--isaac'], {'ISAAC_SIM_PYTHON': str(ROOT/'not-installed'/'python')})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['status'], 'failed')
        isaac = next(c for c in report['checks'] if c['name']=='isaac')
        self.assertEqual(isaac['status'], 'failed')
        self.assertIn('Executable not found', isaac['error'])
        self.assertFalse(report['capture_environment_checked'])


if __name__ == '__main__':
    unittest.main()
