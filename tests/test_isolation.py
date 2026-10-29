"""Synthetic permission/privilege contracts; no host accounts or services provisioned."""
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from director.isolation import IsolationConfig, IsolationError, launch_spec, preflight


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.config = IsolationConfig(0, 2001, 2001, (Path('/controller'),),
            Path('/workers/job'), Path('/workers/temp'),
            Path('/workers/temp/home'), Path('/workers/temp/home/codex'))
        self.metadata = {}
        def info(path):
            path = Path(path)
            if path in self.metadata: return self.metadata[path]
            if path == Path('/usr/bin/bwrap'):
                return SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o755)
            worker = path in [self.config.worktree,self.config.temp,
                              self.config.worker_home,self.config.worker_codex_home]
            return SimpleNamespace(st_uid=2001 if worker else 0,
                st_mode=stat.S_IFDIR | (0o700 if worker or path==Path('/controller') else 0o755))
        self.enterContext(patch('director.isolation.os.geteuid',return_value=0))
        self.enterContext(patch('director.isolation.sys.platform','linux'))
        self.enterContext(patch.object(Path,'lstat',autospec=True,side_effect=info))
        self.probe = self.enterContext(patch('director.isolation.subprocess.run',
            return_value=SimpleNamespace(returncode=0)))

    def test_preflight_dropped_access_probe_has_no_environment(self):
        preflight(self.config)
        kwargs=self.probe.call_args.kwargs
        self.assertEqual(kwargs['env'],{})
        self.assertEqual((kwargs['user'],kwargs['group'],kwargs['extra_groups']),(2001,2001,[]))
        self.assertEqual(kwargs['timeout'],5)

    def test_same_uid_is_rejected_even_when_root(self):
        from dataclasses import replace
        with self.assertRaisesRegex(IsolationError,'distinct'):
            preflight(replace(self.config,worker_uid=0))

    def test_missing_privilege_fails(self):
        with patch('director.isolation.os.geteuid',return_value=501):
            with self.assertRaisesRegex(IsolationError,'privileged'):
                preflight(self.config)

    def test_unconfigured_root_and_platform_fail(self):
        from dataclasses import replace
        with self.assertRaises(IsolationError):preflight(replace(self.config,protected_paths=()))
        with patch('director.isolation.sys.platform','darwin'):
            with self.assertRaisesRegex(IsolationError,'Linux'):preflight(self.config)

    def test_group_readable_or_wrong_owner_controller_rejected(self):
        for owner,mode in [(0,0o750),(2001,0o700)]:
            self.metadata[Path('/controller')]=SimpleNamespace(st_uid=owner,st_mode=stat.S_IFDIR|mode)
            with self.assertRaisesRegex(IsolationError,'controller-owned'):preflight(self.config)

    def test_symlink_and_writable_ancestor_rejected(self):
        self.metadata[Path('/workers')]=SimpleNamespace(st_uid=0,st_mode=stat.S_IFLNK|0o777)
        with self.assertRaisesRegex(IsolationError,'symlink'):preflight(self.config)
        self.metadata[Path('/workers')]=SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|0o777)
        with self.assertRaisesRegex(IsolationError,'parent'):preflight(self.config)

    def test_acl_access_or_probe_failure_rejected(self):
        self.probe.return_value=SimpleNamespace(returncode=1)
        with self.assertRaisesRegex(IsolationError,'access'):preflight(self.config)
        self.probe.side_effect=PermissionError()
        with self.assertRaisesRegex(IsolationError,'unavailable'):preflight(self.config)

    def test_worker_home_must_be_private_separate_temp(self):
        from dataclasses import replace
        with self.assertRaisesRegex(IsolationError,'inside dedicated temp'):
            preflight(replace(self.config,worker_home=Path('/home/controller')))

    def test_launch_identity_environment_and_mount_contract(self):
        with patch.object(Path,'is_dir',return_value=True),patch.dict(os.environ,{
                'DIRECTOR_WEBHOOK_SECRET':'synthetic','MIHUR_GITHUB_TOKEN':'synthetic',
                'OPENAI_API_KEY':'synthetic','HOME':'/controller'}):
            argv,kwargs=launch_spec(self.config,('/usr/bin/codex','app-server'),cwd=self.config.worktree)
        self.assertEqual(kwargs['user'],2001)
        self.assertEqual(kwargs['group'],2001)
        self.assertEqual(kwargs['extra_groups'],[])
        self.assertEqual(kwargs['env']['HOME'],str(self.config.worker_home))
        self.assertEqual(kwargs['env']['CODEX_HOME'],str(self.config.worker_codex_home))
        self.assertNotIn('DIRECTOR_WEBHOOK_SECRET',kwargs['env'])
        self.assertNotIn('MIHUR_GITHUB_TOKEN',kwargs['env'])
        self.assertNotIn('OPENAI_API_KEY',kwargs['env'])
        self.assertIn('--unshare-all',argv)
        self.assertIn('--ro-bind',argv)
        self.assertIn('--tmpfs',argv)
        self.assertEqual(argv.count('--bind'),2)

    def test_cwd_escape_and_relative_command_rejected(self):
        for command,cwd in [(('codex',),self.config.worktree),(('/usr/bin/codex',),Path('/outside'))]:
            with self.assertRaises(IsolationError):launch_spec(self.config,command,cwd=cwd)

    def test_explicit_environment_real_unprivileged_subprocess(self):
        # Actual subprocess environment check, not a claim of live UID isolation.
        with patch.object(Path,'is_dir',return_value=True):
            _,kwargs=launch_spec(self.config,('/usr/bin/codex',),cwd=self.config.worktree)
        environment=kwargs['env']
        with patch.dict(os.environ,{'DIRECTOR_WEBHOOK_SECRET':'synthetic'}):
            # Run the stdlib function captured outside the privilege-mocked module.
            process=subprocess.Popen([sys.executable,'-I','-S','-c',
                'import os; assert "DIRECTOR_WEBHOOK_SECRET" not in os.environ; assert os.environ["HOME"]=="/workers/temp/home"'],
                env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            _,stderr=process.communicate(timeout=5)
            self.assertEqual(process.returncode,0,stderr)
