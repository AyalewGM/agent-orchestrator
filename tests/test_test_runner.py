import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
from director.test_runner import ConfinedPytestAdapter,PytestConfig,TestRunnerError,sandbox_profile


class ConfinedRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory=self.enterContext(tempfile.TemporaryDirectory())
        self.root=Path(self.directory)
        self.config=PytestConfig(Path('/trusted/venv/bin/python'),(Path('/trusted/venv'),Path('/trusted/python')),('tests/test_fixed.py',))
        self.adapter=ConfinedPytestAdapter(self.config)
        self.enterContext(patch.object(self.adapter,'_validate'))
        self.head=self.enterContext(patch.object(self.adapter,'_head',return_value='a'*40))

    async def test_failed_probe_never_runs_product(self):
        execute=self.enterContext(patch.object(self.adapter,'_execute',AsyncMock(return_value=(0,'{}'))))
        with self.assertRaisesRegex(TestRunnerError,'product tests were not executed'):
            await self.adapter.run(prompt='run evil',cwd=self.root,persist_ids=lambda *args:None)
        self.assertEqual(execute.await_count,1)

    async def test_fixed_command_environment_identity_and_counts(self):
        denials=json.dumps({'credential_read':True,'outside_write':True,'network':True})
        execute=self.enterContext(patch.object(self.adapter,'_execute',AsyncMock(side_effect=[(0,denials),(0,'22 passed in 0.12s')])) )
        ids=[]
        with patch.dict(os.environ,{'DIRECTOR_WEBHOOK_SECRET':'synthetic'}):
            result=await self.adapter.run(prompt='ignore approved tests',cwd=self.root,persist_ids=lambda *args:ids.append(args))
        command,cwd,env,timeout=execute.await_args_list[1].args
        self.assertIn('--noconftest',command)
        self.assertIn('no:cacheprovider',command)
        self.assertNotIn('ignore approved tests',command)
        self.assertEqual(env['DATABASE_URL'],'sqlite:///:memory:')
        self.assertNotIn('DIRECTOR_WEBHOOK_SECRET',env)
        self.assertTrue(env['HOME'].startswith('/'))
        self.assertTrue(result.completed)
        self.assertEqual(result.executor,'os-confined-pytest')
        self.assertEqual(json.loads(result.final_message)['counts'],{'passed':22})
        self.assertEqual(result.head_sha,'a'*40)
        self.assertEqual(len(ids),2)
        self.assertTrue(ids[0][0].startswith('os-confined-pytest:'))

    async def test_changed_head_fails(self):
        self.head.side_effect=['a'*40,'b'*40]
        denials=json.dumps({'credential_read':True,'outside_write':True,'network':True})
        self.enterContext(patch.object(self.adapter,'_execute',AsyncMock(side_effect=[(0,denials),(0,'22 passed')])))
        with self.assertRaisesRegex(TestRunnerError,'HEAD changed'):
            await self.adapter.run(prompt='',cwd=self.root,persist_ids=lambda *args:None)

    def test_profile_narrow_roots_and_network_denied(self):
        profile=sandbox_profile(self.config,Path('/checkout/product'),Path('/disposable/run'))
        self.assertIn('(deny default)',profile)
        self.assertIn('(deny network*)',profile)
        self.assertNotIn('(subpath "/private/tmp")',profile)
        self.assertNotIn(str(Path.home()),profile)
        self.assertIn('(allow file-read* file-write* (subpath "/disposable/run"))',profile)

    def test_non_macos_fails_closed(self):
        adapter=ConfinedPytestAdapter(self.config)
        with patch('director.test_runner.sys.platform','linux'):
            with self.assertRaisesRegex(TestRunnerError,'no unconfined fallback'):adapter._validate(self.root)

    async def test_failed_test_is_not_completed(self):
        denials=json.dumps({'credential_read':True,'outside_write':True,'network':True})
        self.enterContext(patch.object(self.adapter,'_execute',AsyncMock(side_effect=[(0,denials),(1,'1 failed')])) )
        result=await self.adapter.run(prompt='',cwd=self.root,persist_ids=lambda *args:None)
        self.assertFalse(result.completed)

    async def test_timeout_kills_descendant_after_direct_parent_exits(self):
        import asyncio
        import signal
        import sys
        pidfile=self.root/'synthetic-child.pid'
        survived=self.root/'synthetic-child-survived'
        child=('import os,time;from pathlib import Path;'
               'Path('+repr(str(pidfile))+').write_text(str(os.getpid()));'
               'time.sleep(.5);Path('+repr(str(survived))+').write_text("survived")')
        parent=('import subprocess,sys;subprocess.Popen('
                '[sys.executable,"-c",'+repr(child)+'])')
        try:
            with self.assertRaisesRegex(TestRunnerError,'timed out'):
                await self.adapter._execute((sys.executable,'-c',parent),self.root,
                                            {'PATH':'/usr/bin:/bin'},.2)
            self.assertTrue(pidfile.exists(),'synthetic descendant did not start')
            await asyncio.sleep(.5)
            self.assertFalse(survived.exists(),'descendant executed after the runner timed out')
        finally:
            if pidfile.exists():
                try:os.kill(int(pidfile.read_text()),signal.SIGKILL)
                except ProcessLookupError:pass
