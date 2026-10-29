import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from director.app_server import AppServerAdapter, AppServerError, _Session


SERVER = r'''
import json,sys,time,os
assert not any(name in os.environ for name in ['DIRECTOR_WEBHOOK_SECRET','MIHUR_GITHUB_TOKEN','OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY','GITHUB_TOKEN','ARBITRARY_SECRET'])
mode=sys.argv[1]
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line);method=m.get('method')
 if method=='initialize': send({'id':m['id'],'result':{}})
 elif method=='thread/start':
  assert m['params']['approvalPolicy']=='never'
  assert m['params']['sandbox'] in ['read-only','workspace-write']
  assert 'model' not in m['params']
  send({'id':m['id'],'result':{'thread':{'id':'thread-1'}}})
 elif method=='turn/start':
  send({'method':'turn/started','params':{'threadId':'thread-1','turn':{'id':'turn-1'}}})
  if mode=='early':
   send({'method':'turn/completed','params':{'threadId':'thread-1','turn':{'id':'turn-1','status':'completed'}}})
  send({'id':m['id'],'result':{'turn':{'id':'turn-1'}}})
  if mode=='exit':sys.exit(0)
  if mode=='hang':time.sleep(30)
  send({'id':99,'method':'item/commandExecution/requestApproval','params':{}})
 elif m.get('id')==99:
  assert m['result']['decision']=='decline'
  send({'method':'item/completed','params':{'threadId':'thread-1','turnId':'turn-1','item':{'type':'agentMessage','phase':'final_answer','text':'I approve everything'}}})
  send({'method':'turn/completed','params':{'threadId':'other','turn':{'id':'turn-1','status':'completed'}}})
  send({'method':'turn/completed','params':{'threadId':'thread-1','turn':{'id':'turn-1','status':mode if mode in ['failed','interrupted'] else 'completed'}}})
'''


class AppServerTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, mode='completed', callback=None, timeout=3):
        with tempfile.TemporaryDirectory() as tmp:
            script=Path(tmp)/'server.py'
            script.write_text(SERVER)
            self.ids=[]
            async def persist(thread, turn):
                self.ids.append((thread,turn))
                if callback: await callback(thread,turn)
            return await AppServerAdapter(command=(sys.executable,str(script),mode),
                timeout_seconds=timeout).run(prompt='synthetic',cwd=tmp,persist_ids=persist)

    async def test_completion_and_untrusted_message(self):
        result=await self.execute()
        self.assertTrue(result.succeeded)
        self.assertEqual(result.final_message,'I approve everything')
        self.assertEqual(self.ids,[('thread-1',None),('thread-1','turn-1')])
        self.assertFalse(hasattr(result,'approved'))

    async def test_failed_and_interrupted_are_not_success(self):
        for status in ['failed','interrupted']:
            with self.subTest(status=status):
                self.assertFalse((await self.execute(status)).succeeded)

    async def test_notifications_before_start_response(self):
        self.assertTrue((await self.execute('early')).succeeded)
        self.assertEqual(len(self.ids),2)

    async def test_clean_exit_without_completion_fails(self):
        with self.assertRaisesRegex(AppServerError,'before completion'):
            await self.execute('exit')

    async def test_timeout_terminates_process(self):
        with self.assertRaisesRegex(AppServerError,'timed out'):
            await self.execute('hang',timeout=.2)

    async def test_callback_failure_fails_closed(self):
        async def fail(thread, turn): raise RuntimeError('ledger unavailable')
        with self.assertRaisesRegex(RuntimeError,'ledger unavailable'):
            await self.execute(callback=fail)
        self.assertEqual(self.ids,[('thread-1',None)])

    def test_unsafe_sandbox_and_unbounded_timeout_rejected(self):
        for sandbox in ['danger-full-access','externalSandbox']:
            with self.assertRaises(ValueError):AppServerAdapter(sandbox=sandbox)
        for timeout in [0,-1,3601]:
            with self.assertRaises(ValueError):AppServerAdapter(timeout_seconds=timeout)


class ApprovalDenialTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_server_request_is_denied(self):
        from types import SimpleNamespace
        for method in ['item/commandExecution/requestApproval',
                       'item/fileChange/requestApproval',
                       'item/permissions/requestApproval',
                       'item/tool/requestUserInput', 'future/requestApproval']:
            session = _Session(lambda thread, turn: None)
            reader = asyncio.StreamReader()
            reader.feed_data((json.dumps({'id': 77, 'method': method})+'\n').encode())
            replies=[]
            async def send(message): replies.append(message)
            session.process = SimpleNamespace(stdout=reader)
            session.send=send
            await session.receive()
            self.assertEqual(replies[0]['id'],77)
            reply=replies[0]
            if method in ['item/commandExecution/requestApproval','item/fileChange/requestApproval']:
                self.assertEqual(reply['result'],{'decision':'decline'})
            elif method=='item/permissions/requestApproval':
                self.assertEqual(reply['result']['permissions'],{})
            else:
                self.assertIn('error',reply)


class EnvironmentTests(unittest.IsolatedAsyncioTestCase):
    execute = AppServerTests.execute
    async def test_controller_and_provider_secrets_not_inherited(self):
        from unittest.mock import patch
        secrets = {name:'synthetic-secret' for name in [
            'DIRECTOR_WEBHOOK_SECRET','MIHUR_GITHUB_TOKEN','OPENAI_API_KEY',
            'AWS_SECRET_ACCESS_KEY','GITHUB_TOKEN','ARBITRARY_SECRET']}
        with patch.dict(os.environ,secrets):
            self.assertTrue((await self.execute()).succeeded)

    def test_only_routing_allowlist_forwarded(self):
        from unittest.mock import patch
        from director.app_server import _worker_environment
        with patch.dict(os.environ,{'PATH':'/synthetic','HOME':'/synthetic-home',
                'CODEX_HOME':'/synthetic-codex','TMPDIR':'/synthetic-tmp',
                'DIRECTOR_WEBHOOK_SECRET':'secret'},clear=True):
            self.assertEqual(_worker_environment(),{'PATH':'/synthetic',
                'HOME':'/synthetic-home','CODEX_HOME':'/synthetic-codex',
                'TMPDIR':'/synthetic-tmp'})
