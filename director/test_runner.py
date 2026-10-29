"""Fixed-command macOS Seatbelt pytest executor; never a Codex or approval identity.

Only operator-approved tests/runtime paths may be configured. Product code executes
only after actual sandbox read/write/network denial probes succeed. No fallback to
unconfined execution exists. The trusted virtualenv must contain only synthetic test
dependencies and no credentials. Existing conftest/plugin autoload is disabled.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
import errno
import inspect
import json
import os
from pathlib import Path
import signal
import re
import subprocess
import sys
import tempfile
import uuid


class TestRunnerError(RuntimeError):
    def __init__(self,message,*,tests_executed=0):
        super().__init__(message)
        self.tests_executed=tests_executed


@dataclass(frozen=True)
class PytestConfig:
    python: Path
    runtime_roots: tuple[Path, ...]
    approved_tests: tuple[str, ...]
    timeout_seconds: float = 120
    sandbox_executable: Path = Path('/usr/bin/sandbox-exec')


@dataclass(frozen=True)
class TestResult:
    thread_id: str
    turn_id: str
    status: str
    final_message: str
    executor: str = 'os-confined-pytest'
    head_sha: str = ''

    @property
    def succeeded(self): return self.status == 'completed'

    @property
    def completed(self): return self.succeeded


def _quote(value): return json.dumps(str(value))


def sandbox_profile(config: PytestConfig, cwd: Path, temporary: Path) -> str:
    reads = {cwd, *config.runtime_roots, config.python.resolve(),
             Path('/usr/lib'), Path('/System/Library'), Path('/dev/null'),
             Path('/dev/random'), Path('/dev/urandom')}
    # Metadata on exact ancestors permits path resolution without listing broad roots.
    metadata = {parent for path in reads | {temporary} for parent in path.parents}
    rules = ['(version 1)', '(deny default)', '(deny network*)',
             '(allow process-fork)', '(allow sysctl-read)',
             '(allow file-read* (literal "/"))',
             '(allow file-write-data (literal "/dev/null"))',
             '(allow process-exec '+ ' '.join('(subpath '+_quote(root)+')' for root in config.runtime_roots) + ')']
    for path in sorted(reads):
        rules.append('(allow file-read* (subpath '+_quote(path)+'))')
    for path in sorted(metadata):
        rules.append('(allow file-read-metadata (literal '+_quote(path)+'))')
    rules.append('(allow file-read* file-write* (subpath '+_quote(temporary)+'))')
    return '\n'.join(rules)


PROBE = '''import errno,json,os,socket,sys
out={}
for name,action in [('credential_read',lambda:open(sys.argv[1],'rb').read()),('outside_write',lambda:open(sys.argv[1],'ab').write(b'x'))]:
 try: action();out[name]=False
 except OSError as e: out[name]=e.errno in (errno.EPERM,errno.EACCES)
try:
 s=socket.socket();s.settimeout(1);s.connect(('127.0.0.1',9));out['network']=False
except OSError as e: out['network']=e.errno in (errno.EPERM,errno.EACCES)
print(json.dumps(out))
sys.exit(0 if all(out.values()) else 4)
'''


class ConfinedPytestAdapter:
    def __init__(self, config: PytestConfig): self.config=config

    def _validate(self, cwd):
        config=self.config
        if sys.platform != 'darwin' or not config.sandbox_executable.is_file():
            raise TestRunnerError('macOS sandbox-exec unavailable; no unconfined fallback')
        if not 0 < config.timeout_seconds <= 3600:
            raise TestRunnerError('test timeout must be bounded')
        if not config.python.is_absolute() or not config.python.is_file() or not config.runtime_roots:
            raise TestRunnerError('explicit trusted virtualenv interpreter/runtime roots required')
        if not (config.python.parent.parent/'pyvenv.cfg').is_file():
            raise TestRunnerError('trusted interpreter must be from an explicit virtualenv')
        if not config.approved_tests:
            raise TestRunnerError('static approved test allowlist required')
        forbidden = {Path.home().resolve(), Path(__file__).resolve().parent.parent}
        for root in config.runtime_roots:
            resolved_root = root.resolve()
            if (not root.is_absolute() or not root.is_dir()
                    or any(resolved_root == path or resolved_root in path.parents for path in forbidden)
                    or resolved_root in {Path('/'),Path('/private'),Path('/tmp'),Path('/private/tmp')}):
                raise TestRunnerError('runtime read roots must exclude controller storage and broad host paths')
        if not any(root.resolve() in config.python.resolve().parents for root in config.runtime_roots):
            raise TestRunnerError('interpreter must be inside an approved runtime root')
        for value in config.approved_tests:
            path=Path(value)
            if path.is_absolute() or '..' in path.parts or not value.startswith('tests/') or not value.endswith('.py'):
                raise TestRunnerError('test paths must be explicit relative test files')
            resolved=(cwd/path).resolve()
            if cwd not in resolved.parents or not resolved.is_file():
                raise TestRunnerError('approved test path escapes checkout or does not exist')

    async def run(self, *, prompt: str, cwd: str | Path, persist_ids):
        del prompt  # Model/user text never selects executable or test arguments.
        cwd=Path(cwd).resolve()
        self._validate(cwd)
        head_before = self._head(cwd)
        identity='os-confined-pytest:'+str(uuid.uuid4())
        turn='process:'+str(uuid.uuid4())
        for value in [None,turn]:
            saved=persist_ids(identity,value)
            if inspect.isawaitable(saved):await saved
        with tempfile.TemporaryDirectory(prefix='director-confined-tests-') as writable, tempfile.TemporaryDirectory(prefix='director-denial-canary-') as excluded:
            temporary=Path(writable).resolve()
            canary=Path(excluded).resolve()/'synthetic-credential'
            canary.write_text('SYNTHETIC_CANARY_ONLY')
            (temporary/'home').mkdir()
            environment={'PATH':'/usr/bin:/bin','HOME':str(temporary/'home'),
                'TMPDIR':str(temporary),'TMP':str(temporary),'TEMP':str(temporary),
                'DATABASE_URL':'sqlite:///:memory:', 'PYTHONDONTWRITEBYTECODE':'1',
                'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONNOUSERSITE':'1','LANG':'C.UTF-8','TERM':'dumb'}
            profile=sandbox_profile(self.config,cwd,temporary)
            command=(str(self.config.sandbox_executable),'-p',profile,str(self.config.python))
            code,output=await self._execute((*command,'-I','-S','-c',PROBE,str(canary)),cwd,environment,min(15,self.config.timeout_seconds))
            try:denials=json.loads(output.strip())
            except (ValueError,TypeError):denials={}
            if code != 0 or denials != {'credential_read':True,'outside_write':True,'network':True}:
                raise TestRunnerError('OS confinement denial probes failed; product tests were not executed')
            try:
                code,output=await self._execute((*command,'-I','-m','pytest','--noconftest','-p','no:cacheprovider',
                    '-o','addopts=','--override-ini','pythonpath=.',*self.config.approved_tests),cwd,environment,self.config.timeout_seconds)
                head_after = self._head(cwd)
            except TestRunnerError as exc:
                raise TestRunnerError(str(exc),tests_executed=None) from None
            if head_before != head_after:
                raise TestRunnerError('checkout HEAD changed during confined tests',tests_executed=None)
            counts = {name: int(value) for value, name in re.findall(r'(\d+) (passed|failed|skipped|error|errors|deselected)', output)}
            report={'head_sha':head_after,'counts':counts,'executor':'os-confined-pytest','exit_code':code,'denial_probes':denials,
                'approved_tests':list(self.config.approved_tests),'output':output,
                'authenticated_approval':False}
            return TestResult(identity,turn,'completed' if code==0 else 'failed',json.dumps(report),head_sha=head_after)

    @staticmethod
    def _head(cwd):
        try:
            result=subprocess.run(['/usr/bin/git','--no-optional-locks','-c','core.fsmonitor=false',
                '-C',str(cwd),'rev-parse','HEAD'],env={'PATH':'/usr/bin:/bin'},
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                text=True,timeout=5,check=True)
        except (OSError,subprocess.SubprocessError):
            raise TestRunnerError('trusted checkout HEAD read failed') from None
        head=result.stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{40}',head):raise TestRunnerError('invalid checkout HEAD')
        return head

    async def _execute(self, command, cwd, environment, timeout):
        process=None
        try:
            process=await asyncio.create_subprocess_exec(*command,cwd=str(cwd),env=environment,
                stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,start_new_session=True)
            async def drain():
                chunks=[];remaining=512*1024
                while chunk:=await process.stdout.read(65536):
                    if remaining>0:chunks.append(chunk[:remaining]);remaining-=len(chunk[:remaining])
                return b''.join(chunks).decode('utf-8',errors='replace')
            async with asyncio.timeout(timeout):
                output=await drain();await process.wait()
                return process.returncode,output
        except TimeoutError:
            raise TestRunnerError('confined process timed out') from None
        except OSError:
            raise TestRunnerError('confined process launch unavailable') from None
        finally:
            if process is not None:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()
