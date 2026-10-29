"""Bounded Codex app-server stdio client; model text is never review authority.

Protocol: https://learn.chatgpt.com/docs/app-server (installed CLI schema).
The caller must persist a dispatch intent before invoking run. Thread IDs are saved
before turn/start; the server allocates turn IDs when starting execution, so a crash
before their callback completes is ambiguous and must never cause automatic replay.
Sandbox modes and environment filtering are defense in depth, not an OS credential
boundary. Existing Codex user configuration may expose MCP servers or configured
credentials. A separately protected controller and disposable execution environment
remain required; HOME/CODEX_HOME are retained only for existing sign-in routing.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import os
from pathlib import Path
import signal
from typing import Awaitable, Callable


class AppServerError(RuntimeError):
    """Sanitized transport/protocol failure (never includes server payloads)."""


@dataclass(frozen=True)
class AppServerResult:
    thread_id: str
    turn_id: str
    status: str
    final_message: str

    @property
    def succeeded(self) -> bool:
        return self.status == "completed"


PersistIDs = Callable[[str, str | None], Awaitable[None] | None]


class AppServerAdapter:
    def __init__(self, *, sandbox: str = "read-only", timeout_seconds: float = 120,
                 command: tuple[str, ...] = ("codex", "app-server", "--listen", "stdio://"),
                 launch_policy=None):
        if sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("unsupported sandbox")
        if not 0 < timeout_seconds <= 3600:
            raise ValueError("timeout must be bounded to one hour")
        self.sandbox = sandbox
        self.timeout_seconds = timeout_seconds
        self.command = command
        self.launch_policy=launch_policy

    async def run(self, *, prompt: str, cwd: str | Path,
                  persist_ids: PersistIDs) -> AppServerResult:
        """Execute one turn; persist callbacks must fail closed on ledger errors.

        Server stderr is discarded and stdout is protocol-only, never logged.
        Returned final_message is untrusted evidence, including any approval claim.
        """
        session = _Session(persist_ids)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                argv=self.command
                launch={'cwd':str(Path(cwd).resolve()),'env':_worker_environment()}
                if self.launch_policy is not None:
                    import shutil
                    from .isolation import launch_spec
                    executable=shutil.which(argv[0]) or argv[0]
                    argv,launch=await asyncio.to_thread(launch_spec,self.launch_policy,(executable,*argv[1:]),cwd=Path(cwd).resolve())
                session.process = await asyncio.create_subprocess_exec(
                    *argv, **launch,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
                    limit=2 * 1024 * 1024)
                await session.request("initialize", {
                    "clientInfo": {"name": "engineering-director", "version": "1"}})
                await session.send({"method": "initialized", "params": {}})
                response = await session.request("thread/start", {
                    "cwd": str(Path(cwd).resolve()), "sandbox": self.sandbox,
                    "approvalPolicy": "never", "approvalsReviewer": "user"})
                session.thread_id = _identifier(response, "thread")
                await session.persist(None)
                response = await session.request("turn/start", {
                    "threadId": session.thread_id,
                    "input": [{"type": "text", "text": prompt, "text_elements": []}]})
                turn_id = _identifier(response, "turn")
                await session.bind_turn(turn_id)
                while session.completed is None:
                    await session.receive()
                return AppServerResult(session.thread_id, session.turn_id,
                                       session.completed, session.final_message)
        except TimeoutError:
            raise AppServerError("app-server execution timed out; reconcile persisted IDs before retry") from None
        except (OSError, ValueError, KeyError, TypeError, asyncio.IncompleteReadError):
            raise AppServerError("app-server transport or protocol failed") from None
        finally:
            await session.close()


def _worker_environment() -> dict[str, str]:
    """Forward routing essentials only, never ambient controller/provider secrets."""
    allowed = {"PATH", "HOME", "CODEX_HOME", "TMPDIR", "TMP", "TEMP",
               "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}
    return {name: value for name, value in os.environ.items() if name in allowed}


def _identifier(response: dict, key: str) -> str:
    value = response[key]["id"]
    if not isinstance(value, str) or not value:
        raise AppServerError("app-server returned invalid identifier")
    return value


class _Session:
    def __init__(self, persist_ids: PersistIDs):
        self.persist_ids = persist_ids
        self.process = None
        self.sequence = 0
        self.thread_id = ""
        self.turn_id = ""
        self.completed = None
        self.final_message = ""
        self.pending_events = []

    async def persist(self, turn_id):
        result = self.persist_ids(self.thread_id, turn_id)
        if inspect.isawaitable(result):
            await result

    async def bind_turn(self, turn_id):
        if self.turn_id and self.turn_id != turn_id:
            raise AppServerError("app-server turn identifier changed")
        if not self.turn_id:
            self.turn_id = turn_id
            await self.persist(turn_id)
            for method, params in self.pending_events:
                await self.event(method, params)
            self.pending_events.clear()

    async def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        await self.send({"id": request_id, "method": method, "params": params})
        while True:
            response = await self.receive()
            if response and response.get("id") == request_id:
                if "error" in response:
                    raise AppServerError("app-server request rejected: " + method)
                if not isinstance(response.get("result"), dict):
                    raise AppServerError("app-server returned invalid response")
                return response["result"]

    async def receive(self):
        line = await self.process.stdout.readline()
        if not line:
            raise AppServerError("app-server exited before completion")
        message = json.loads(line)
        if not isinstance(message, dict):
            raise AppServerError("invalid app-server message")
        method = message.get("method")
        if method and "id" in message:
            # Reject every server request. Known approval methods get typed denials;
            # unknown requests get an error, never an optimistic empty response.
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                reply = {"result": {"decision": "decline"}}
            elif method == "item/permissions/requestApproval":
                reply = {"result": {"permissions": {}, "scope": "turn"}}
            else:
                reply = {"error": {"code": -32601, "message": "Server requests disabled"}}
            await self.send({"id": message["id"], **reply})
        elif method:
            await self.event(method, message.get("params", {}))
        else:
            return message

    async def event(self, method, params):
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id:
            return
        if method == "turn/started":
            await self.bind_turn(_identifier(params, "turn"))
        elif method in {"item/completed", "turn/completed"}:
            if not self.turn_id:
                if len(self.pending_events) >= 1000:
                    raise AppServerError("too many app-server events before turn binding")
                self.pending_events.append((method, params))
                return
            turn_id = params.get("turnId") if method == "item/completed" else _identifier(params, "turn")
            if turn_id != self.turn_id:
                return
            if method == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage" and item.get("phase") in {None, "final_answer"}:
                    text = item.get("text")
                    if isinstance(text, str):
                        self.final_message = text
            else:
                status = params["turn"].get("status")
                if status not in {"completed", "failed", "interrupted"}:
                    raise AppServerError("invalid terminal turn status")
                self.completed = status

    async def close(self):
        if self.process is None:
            return
        if self.process.returncode is None:
            if self.thread_id and self.turn_id and self.completed is None:
                try:
                    await asyncio.wait_for(self.send({"id": self.sequence + 1,
                        "method": "turn/interrupt", "params": {
                            "threadId": self.thread_id, "turnId": self.turn_id}}), .5)
                except (OSError, asyncio.TimeoutError):
                    pass
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.process.wait()

        # Descendants can survive an already-exited parent and retain pipe handles.
        # Every session owns a fresh process group; always clean that group.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
