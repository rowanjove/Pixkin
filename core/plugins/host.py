"""JSON-RPC-like process host for explicitly trusted plugin code.

The host intentionally exposes only a line-delimited control channel and
scrubs credential-shaped environment variables. Process/job containment keeps
plugin failures from taking down Pixkin, but it is not a filesystem or network
sandbox: plugins still run under the current user's token and must therefore
be explicitly trusted before they are started.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from core.plugins.manifest import PluginManifest, PluginManifestError
from core.runtime.permissions import (
    ContextPermissionService,
    PermissionOperation,
    PermissionResource,
    PermissionState,
)


LOGGER = logging.getLogger("desktop_pet.plugins.host")
MAX_MESSAGE_BYTES = 256 * 1024


def _sanitized_plugin_environment() -> dict[str, str]:
    """Do not inherit common credential-shaped environment variables."""
    blocked_markers = (
        "API_KEY",
        "APIKEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "CREDENTIAL",
        "AUTHORIZATION",
    )
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in blocked_markers)
    }


class PluginProcessError(RuntimeError):
    pass


class PluginProcess:
    def __init__(self, manifest: PluginManifest, *, cwd: str | Path | None = None):
        self.manifest = manifest
        self.cwd = str(Path(cwd).resolve()) if cwd is not None else None
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        # Bound unsolicited responses so a hostile plugin cannot exhaust the
        # host process by flooding the JSON-RPC channel.
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
        self._write_lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._next_id = 0
        self._job = None

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def start(self, *, timeout: float = 5.0) -> dict[str, Any]:
        if self.running:
            raise PluginProcessError("插件已经启动")
        try:
            creation_flags = 0
            if os.name == "nt":
                creation_flags = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            self._process = subprocess.Popen(
                list(self.manifest.command),
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
                env=_sanitized_plugin_environment(),
            )
            self._attach_process_group()
        except (OSError, ValueError, PluginProcessError) as exc:
            if self._process is not None:
                try:
                    self._process.kill()
                except OSError:
                    pass
            self._process = None
            raise PluginProcessError("插件进程无法启动") from exc
        self._stop_event.clear()
        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"pixkin-plugin-{self.manifest.id}",
            daemon=True,
        )
        self._reader.start()
        try:
            response = self.call(
                "initialize",
                {"api_version": self.manifest.api_version},
                timeout=timeout,
            )
            if not isinstance(response, dict) or response.get("api_version") != self.manifest.api_version:
                raise PluginProcessError("插件初始化协商失败")
            return response
        except Exception:
            # Initialization can fail before ``call`` gets a chance to stop the
            # process (for example malformed JSON, EOF, or a plugin exception).
            # Never leave an orphaned child behind when negotiation fails.
            self.stop(force=True)
            raise

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 10.0) -> dict[str, Any]:
        if not self.running:
            raise PluginProcessError("插件进程未运行")
        process = self._process
        assert process is not None and process.stdin is not None
        with self._call_lock:
            self._next_id += 1
            request_id = self._next_id
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": str(method),
                "params": params or {},
            }
            encoded = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
            if len(encoded) > MAX_MESSAGE_BYTES:
                raise PluginProcessError("插件请求超过消息大小上限")
            try:
                with self._write_lock:
                    process.stdin.write(encoded)
                    process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise PluginProcessError("插件进程写入失败") from exc
            deadline = time.monotonic() + max(0.05, float(timeout))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.stop(force=True)
                    raise PluginProcessError(f"插件调用超时：{method}")
                try:
                    response = self._responses.get(timeout=remaining)
                except queue.Empty as exc:
                    self.stop(force=True)
                    raise PluginProcessError(f"插件调用超时：{method}") from exc
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise PluginProcessError(str(response["error"]))
                result = response.get("result", {})
                if not isinstance(result, dict):
                    raise PluginProcessError("插件返回结果必须是对象")
                return result

    def stop(self, *, force: bool = False) -> None:
        process = self._process
        if process is None:
            return
        if not force and process.poll() is None:
            try:
                self.call("shutdown", {}, timeout=1.0)
            except PluginProcessError:
                pass
        self._stop_event.set()
        job = self._job
        if force and job is not None:
            try:
                win32job: Any = __import__("win32job")

                win32job.TerminateJobObject(job, 1)
            except Exception:
                LOGGER.debug("终止插件 Job Object 失败", exc_info=True)
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._close_process_group()
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.0)
        self._process = None
        self._reader = None

    def _attach_process_group(self) -> None:
        """Contain plugin descendants so timeout/shutdown cannot orphan them."""
        process = self._process
        if process is None:
            return
        if os.name == "nt":
            try:
                win32job: Any = __import__("win32job")

                job = win32job.CreateJobObject(None, "")
                info = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation
                )
                info["BasicLimitInformation"]["LimitFlags"] |= (
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                )
                win32job.SetInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation, info
                )
                process_handle = getattr(process, "_handle", None)
                if process_handle is None:
                    raise PluginProcessError("插件进程句柄不可用")
                win32job.AssignProcessToJobObject(job, process_handle)
                self._job = job
            except Exception as exc:
                raise PluginProcessError("插件进程组隔离不可用") from exc

    def _close_process_group(self) -> None:
        job = self._job
        self._job = None
        if job is not None:
            try:
                job.Close()
            except Exception:
                LOGGER.debug("关闭插件 Job Object 失败", exc_info=True)

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while not self._stop_event.is_set():
            try:
                # ``BufferedReader.readline(size)`` bounds the amount of data
                # materialized before the protocol-size check.  A hostile
                # plugin cannot force an unbounded allocation with one line.
                line = process.stdout.readline(MAX_MESSAGE_BYTES + 1)
            except OSError:
                return
            if not line:
                return
            if len(line) > MAX_MESSAGE_BYTES:
                LOGGER.warning("plugin message too large id=%s", self.manifest.id)
                self._stop_event.set()
                return
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                LOGGER.warning("invalid plugin message id=%s", self.manifest.id)
                continue
            if isinstance(message, dict) and "id" in message:
                try:
                    self._responses.put_nowait(message)
                except queue.Full:
                    LOGGER.warning(
                        "plugin response queue full id=%s", self.manifest.id
                    )
                    self._stop_event.set()
                    return


class PluginHost:
    """Discover and explicitly start process-isolated plugin manifests."""

    def __init__(
        self,
        root: str | Path,
        *,
        safe_mode: bool = False,
        permission_service: ContextPermissionService | None = None,
    ):
        self.root = Path(root).resolve()
        self.safe_mode = bool(safe_mode)
        self.permission_service = permission_service
        self._processes: dict[str, PluginProcess] = {}

    def discover(self) -> tuple[PluginManifest, ...]:
        if self.safe_mode or not self.root.is_dir():
            return ()
        manifests = []
        for path in sorted(self.root.glob("*.json")):
            try:
                manifests.append(PluginManifest.load(path))
            except PluginManifestError:
                continue
        return tuple(manifests)

    def start(self, manifest: PluginManifest | None = None, *, timeout: float = 5.0):
        # ServiceContainer invokes ``start()`` without arguments during app
        # boot. Discovery is explicit and safe; executable plugins still need
        # a manifest passed by the permission-reviewed install flow.
        if manifest is None:
            return self.discover()
        if self.safe_mode:
            raise PluginProcessError("safe mode 禁止启动插件")
        # A host without the central permission service cannot establish the
        # user's trust decision.  Fail closed even for manifests that declare
        # no additional resources; an empty declaration is not authorization.
        if self.permission_service is None:
            raise PluginProcessError("插件权限默认拒绝")
        if (
            self.permission_service.state(PermissionResource.PLUGIN)
            is PermissionState.ASK
        ):
            raise PluginProcessError("启动常驻插件需要会话级插件授权")
        decision = self.permission_service.decide(
            PermissionResource.PLUGIN,
            PermissionOperation.EXECUTE,
        )
        if not decision.allowed:
            raise PluginProcessError(f"插件权限未允许：{decision.reason}")
        if manifest.permissions:
            for raw_resource, operations in manifest.permissions.items():
                resource = PermissionResource(raw_resource)
                for raw_operation in operations:
                    operation = PermissionOperation(raw_operation)
                    decision = self.permission_service.decide(resource, operation)
                    if not decision.allowed:
                        raise PluginProcessError(
                            f"插件声明权限未获允许：{resource.value}/{operation.value}"
                        )
        if manifest.path is not None and self.root not in manifest.path.parents:
            raise PluginProcessError("插件清单不在受信插件目录")
        if manifest.id in self._processes:
            raise PluginProcessError("插件已经启动")
        process = PluginProcess(manifest, cwd=manifest.path.parent if manifest.path else self.root)
        process.start(timeout=timeout)
        self._processes[manifest.id] = process
        return process

    def stop(self, plugin_id: str | None = None) -> bool | None:
        """Stop one plugin, or all plugins when called as a lifecycle hook.

        ``ServiceContainer`` invokes ``stop()`` without arguments during
        kernel shutdown, while the install/uninstall flow still needs the
        targeted ``stop(plugin_id)`` form.  Keeping both forms here avoids a
        shutdown-time ``TypeError`` that would otherwise leave child plugin
        processes alive.
        """
        if plugin_id is None:
            self.stop_all()
            return None
        process = self._processes.pop(str(plugin_id), None)
        if process is None:
            return False
        process.stop()
        return True

    def stop_all(self) -> None:
        for plugin_id in tuple(self._processes):
            self.stop(plugin_id)

    def close(self) -> None:
        """ServiceContainer-compatible lifecycle hook."""
        self.stop_all()

    def running_ids(self) -> tuple[str, ...]:
        return tuple(self._processes)
