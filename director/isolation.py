"""Fail-closed protected-controller launch policy, not a scheduler or service installer.

Operator provisioning is mandatory: a privileged Linux controller, a distinct worker
account, root-owned bubblewrap, private controller storage, and worker-owned dedicated
worktree/temp directories. Worker HOME and CODEX_HOME must live inside worker temp;
provision separate worker sign-in explicitly. Never copy controller authentication.
UID separation alone does not constrain writes, so the mount namespace is required.
No live isolation is established by constructing this configuration or passing tests.
Network is unshared by default. Provider routing requires an operator-provisioned
restricted gateway/network namespace and a separately reviewed launch policy;
this helper never silently enables host networking. Without that provisioning,
provider execution is unavailable and must remain blocked.

Concrete operator configuration example (not provisioning commands)::

    IsolationConfig(
        controller_uid=0, worker_uid=2001, worker_gid=2001,
        protected_paths=(Path('/var/lib/director'),
                         Path('/etc/director')),
        worktree=Path('/srv/director-workers/job'),
        temp=Path('/srv/director-workers/temp'),
        worker_home=Path('/srv/director-workers/temp/home'),
        worker_codex_home=Path('/srv/director-workers/temp/home/.codex'),
        sandbox_executable=Path('/usr/bin/bwrap'))

Required external setup: a Linux root controller service identity and dedicated
unprivileged worker UID/GID; root-owned non-group/world-writable ancestors;
controller directories mode 0700 and ledger/secret/config files mode 0600;
root-owned /srv/director-workers mode 0755; job/temp/home/.codex directories owned
by worker 2001 mode 0700. Install a root-owned non-writable bubblewrap executable
and enable its required namespace support. Provision worker-specific sign-in
independently in worker CODEX_HOME, never by copying controller auth. Configure
restricted provider egress through a gateway before requesting live model work.
Account creation, service installation, sign-in and gateway setup are deliberately
not performed here. Existing macOS/shared-UID controllers are unsupported.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import subprocess
import sys


class IsolationError(RuntimeError):
    """Operator provisioning is missing or unsafe; execution must stop."""


@dataclass(frozen=True)
class IsolationConfig:
    controller_uid: int
    worker_uid: int
    worker_gid: int
    protected_paths: tuple[Path, ...]
    worktree: Path
    temp: Path
    worker_home: Path
    worker_codex_home: Path
    sandbox_executable: Path = Path('/usr/bin/bwrap')


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _checked_path(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise IsolationError('all isolation paths must be absolute and normalized')
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise IsolationError('symlink in isolation path')
    return path


def _private_directory(path: Path, owner: int):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner:
        raise IsolationError('directory owner or type does not match configured identity')
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise IsolationError('private directories require mode 0700')


def preflight(config: IsolationConfig) -> None:
    """Validate before every launch; no account, permission, or auth mutations."""
    try:
        _preflight(config)
    except OSError:
        raise IsolationError('required isolation path or permission is unavailable') from None


def _preflight(config: IsolationConfig) -> None:
    if config.controller_uid == config.worker_uid or config.worker_uid <= 0 or config.worker_gid <= 0:
        raise IsolationError('controller and non-root worker identities must be distinct')
    if os.name != 'posix' or sys.platform != 'linux':
        raise IsolationError('protected execution requires provisioned Linux mount namespace confinement')
    if os.geteuid() != 0 or config.controller_uid != os.geteuid():
        raise IsolationError('configured privileged controller identity is unavailable')
    if not config.protected_paths:
        raise IsolationError('controller ledger, secret, and config paths must be explicitly protected')
    roots = [_checked_path(config.worktree), _checked_path(config.temp)]
    if _inside(roots[0], roots[1]) or _inside(roots[1], roots[0]):
        raise IsolationError('worker worktree and temp must be separate dedicated roots')
    for root in roots:
        _private_directory(root, config.worker_uid)
        # Root directory rename/replacement must be controlled by the controller.
        parent = root.parent.lstat()
        if parent.st_uid != config.controller_uid or stat.S_IMODE(parent.st_mode) & 0o022:
            raise IsolationError('worker roots require controller-owned non-writable parent')
    for home in [config.worker_home, config.worker_codex_home]:
        home = _checked_path(home)
        if not _inside(home, config.temp):
            raise IsolationError('worker HOME and CODEX_HOME must be inside dedicated temp')
        _private_directory(home, config.worker_uid)
    protected = [_checked_path(path) for path in config.protected_paths]
    for path in protected:
        info = path.lstat()
        if info.st_uid != config.controller_uid or stat.S_IMODE(info.st_mode) & 0o077:
            raise IsolationError('controller paths must be controller-owned and inaccessible to group/other')
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise IsolationError('controller protected path must be a directory or regular file')
        for root in roots:
            if _inside(path, root) or _inside(root, path):
                raise IsolationError('controller paths must not overlap worker writable roots')
        for parent in path.parents:
            parent_info = parent.lstat()
            if parent_info.st_uid != config.controller_uid or stat.S_IMODE(parent_info.st_mode) & 0o022:
                raise IsolationError('controller path ancestors must prevent replacement')
    binary = _checked_path(config.sandbox_executable)
    info = binary.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or not info.st_mode & 0o111:
        raise IsolationError('sandbox executable must be trusted root-owned executable')
    for parent in binary.parents:
        info = parent.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise IsolationError('sandbox executable ancestors must be trusted')
    # Check actual access with dropped credentials, detecting ACL grants as well as DAC.
    # No secret contents are read. This process inherits no controller environment.
    probe = 'import os,sys;sys.exit(any(os.access(p,os.R_OK|os.W_OK|os.X_OK) or os.access(p,os.R_OK) or os.access(p,os.W_OK) or os.access(p,os.X_OK) for p in sys.argv[1:]))'
    try:
        result = subprocess.run([sys.executable, '-I', '-S', '-c', probe, *map(str, protected)],
            cwd='/', env={}, user=config.worker_uid, group=config.worker_gid,
            extra_groups=[], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        raise IsolationError('worker credential/access probe unavailable') from None
    if result.returncode != 0:
        raise IsolationError('worker can access protected controller paths or access probe failed')


def require_coverage(config: IsolationConfig, paths) -> None:
    """Bind the policy to the actual controller files, not unrelated private paths."""
    for value in paths:
        path=_checked_path(Path(value))
        if _inside(path,config.worktree) or _inside(path,config.temp):
            raise IsolationError('actual controller path overlaps worker writable root')
        if not any(_inside(path,Path(root)) for root in config.protected_paths):
            raise IsolationError('actual controller ledger/config/control path is not protected')


def launch_spec(config: IsolationConfig, command: tuple[str, ...], *, cwd: Path) -> tuple[tuple[str, ...], dict]:
    """Return argv and asyncio.create_subprocess_exec kwargs after fresh preflight.

    Python POSIX subprocess drops supplementary groups, GID and UID before exec;
    bubblewrap then creates a read-only host mount with only two writable roots.
    Missing user namespace/privilege support makes the launcher exit, never fallback.
    Caller supplies its own PIPE/DEVNULL, timeout and start_new_session settings.
    """
    preflight(config)
    cwd = _checked_path(cwd)
    if not _inside(cwd, config.worktree):
        raise IsolationError('worker cwd must be inside dedicated worktree')
    if not command or not Path(command[0]).is_absolute():
        raise IsolationError('protected worker command must use an absolute executable')
    argv = [str(config.sandbox_executable), '--die-with-parent', '--new-session',
            '--unshare-all', '--ro-bind', '/', '/', '--proc', '/proc', '--dev', '/dev',
            '--bind', str(config.worktree), str(config.worktree),
            '--bind', str(config.temp), str(config.temp)]
    for path in config.protected_paths:
        argv.extend(['--tmpfs', str(path)] if Path(path).is_dir()
                    else ['--ro-bind', '/dev/null', str(path)])
    argv.extend(['--chdir', str(cwd), '--', *command])
    environment = {'PATH': '/usr/bin:/bin', 'HOME': str(config.worker_home),
                   'CODEX_HOME': str(config.worker_codex_home), 'TMPDIR': str(config.temp),
                   'TMP': str(config.temp), 'TEMP': str(config.temp), 'LANG': 'C.UTF-8'}
    return tuple(argv), {'cwd': str(cwd), 'env': environment,
                         'user': config.worker_uid, 'group': config.worker_gid,
                         'extra_groups': []}
