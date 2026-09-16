"""Windows regression checks using fake UIA elements; no desktop interaction."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1] / 'windows/workbuddy-desktop-checkin/scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fake_uia = types.ModuleType('uia_win')
fake_uia.walk_all = lambda root: root
fake_uia.rect_of = lambda element: element.rect
with patch.dict(sys.modules, {'uia_win': fake_uia}):
    checkin = load('checkin')
runner = load('run_checkin')
installer = load('install_task')


def element(name, aid='', x=20, height=30):
    return types.SimpleNamespace(CurrentName=name, CurrentAutomationId=aid,
                                 rect=(x, 100, 120, height))


class WindowsRegressionTests(unittest.TestCase):
    def test_chat_and_authentication_are_not_claimed(self):
        root = [element('今日已领'), element('认证领积分', 'fuel-action'),
                element('立即领取', checkin.CLAIM_AID)]
        self.assertEqual(checkin.claim_state(root, 500), 'ready')

    def test_off_area_and_zero_height_are_ignored(self):
        root = [element('今日已领', checkin.CLAIM_AID, x=800),
                element('今日已领', checkin.CLAIM_AID, height=0)]
        self.assertEqual(checkin.claim_state(root, 500), 'unknown')

    def test_real_claimed_and_unknown_label(self):
        self.assertEqual(checkin.claim_state([element('今日已领', checkin.CLAIM_AID)], 500), 'claimed')
        self.assertEqual(checkin.claim_state([element('加载中', checkin.CLAIM_AID)], 500), 'unknown')

    def test_json_has_no_forced_log_prefix(self):
        def run(**kwargs):
            checkin.log('今日已签', quiet=kwargs['quiet'], force=True)
            return {'status': 'already_claimed'}
        out = io.StringIO()
        with patch.object(checkin, 'run', side_effect=run), patch.object(sys, 'argv', ['checkin.py', '--json']), contextlib.redirect_stdout(out):
            self.assertEqual(checkin.main(), 0)
        self.assertEqual(json.loads(out.getvalue())['status'], 'already_claimed')

    def test_task_xml_preserves_special_paths(self):
        path = Path('C:/用户/A&B/checkin.py')
        with patch.object(installer, 'RUNNER', path), patch.object(installer, 'SKILL_DIR', path.parent):
            xml = installer.build_xml('09:00', 'PC\\A&B', '2026-09-16')
        root = ET.fromstring(xml)
        ns = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        self.assertEqual(root.find('.//t:Arguments', ns).text, f'"{path}"')
        self.assertEqual(root.find('.//t:UserId', ns).text, 'PC\\A&B')

    def test_runner_locked_writes_skipped_result(self):
        with tempfile.TemporaryDirectory() as d, patch.object(runner, 'LOG_DIR', Path(d)), patch.object(runner, 'session_locked', return_value=True), patch.object(sys, 'argv', ['run_checkin.py']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 4)
            self.assertEqual(json.loads((Path(d) / 'last_result.json').read_text())['status'], 'skipped_locked')

    def test_runner_startup_exception_is_logged(self):
        with tempfile.TemporaryDirectory() as d, patch.object(runner, 'LOG_DIR', Path(d)), patch.object(runner, 'session_locked', return_value=False), patch.object(runner, 'ensure_running', side_effect=ImportError('comtypes')), patch.object(sys, 'argv', ['run_checkin.py']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 3)
            self.assertEqual(json.loads((Path(d) / 'last_result.json').read_text())['status'], 'error')

    def test_runner_error_code_and_utf8_subprocess(self):
        response = types.SimpleNamespace(stdout='{"status":"error","error":"测试"}', stderr='', returncode=3)
        with tempfile.TemporaryDirectory() as d, patch.object(runner, 'LOG_DIR', Path(d)), patch.object(runner, 'session_locked', return_value=False), patch.object(runner, 'ensure_running', return_value=True), patch.object(runner.subprocess, 'run', return_value=response) as child, patch.object(sys, 'argv', ['run_checkin.py']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 3)
            self.assertEqual(child.call_args.kwargs['env']['PYTHONIOENCODING'], 'utf-8')
            self.assertEqual(json.loads((Path(d) / 'last_result.json').read_text())['checkin']['error'], '测试')


if __name__ == '__main__':
    unittest.main()
