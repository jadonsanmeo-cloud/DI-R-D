import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from data_intelligence_api.infrastructure.workflow.pipeline_factory import (
    _AxiomSandboxProvider,
)
from data_intelligence_sdk.runtime.config import ConfigManager


class _Sandbox:
    def __init__(self) -> None:
        self.capabilities = None
        self.ready_timeout: float | None = None

    def wait_until_ready(self, *, timeout: float) -> None:
        self.ready_timeout = timeout


class _SandboxClient:
    def __init__(self, sandbox: _Sandbox) -> None:
        self.sandbox = sandbox

    def create_sandbox(self, *_args, **_kwargs) -> _Sandbox:
        return self.sandbox


class _PoolLease:
    def __init__(self, sandbox: _Sandbox) -> None:
        self.lease_id = uuid4()
        self.sandbox = sandbox


class _PoolClient:
    def __init__(self, sandbox: _Sandbox) -> None:
        self.sandbox = sandbox
        self.lease_calls = 0
        self.create_calls = 0
        self.retired_lease_id = None

    def lease_pool_sandbox(self, _workspace_id):
        self.lease_calls += 1
        return _PoolLease(self.sandbox)

    def create_sandbox(self, *_args, **_kwargs) -> _Sandbox:
        self.create_calls += 1
        return self.sandbox

    def retire_pool_lease(self, lease_id, _workspace_id) -> None:
        self.retired_lease_id = lease_id


class AxiomSandboxProviderTests(unittest.TestCase):
    def test_uses_configured_ready_timeout(self) -> None:
        sandbox = _Sandbox()
        provider = _AxiomSandboxProvider(
            _SandboxClient(sandbox),
            workspace_id=uuid4(),
            cleanup=False,
            ready_timeout_seconds=90,
            pool_enabled=False,
        )

        with provider.open() as session:
            self.assertIsNotNone(session)

        self.assertEqual(sandbox.ready_timeout, 90)

    def test_leases_running_sandbox_and_retires_lease(self) -> None:
        sandbox = _Sandbox()
        client = _PoolClient(sandbox)
        provider = _AxiomSandboxProvider(
            client,
            workspace_id=uuid4(),
            cleanup=True,
            ready_timeout_seconds=90,
            pool_enabled=True,
        )

        with provider.open() as session:
            self.assertIs(session.sandbox, sandbox)

        self.assertEqual(client.lease_calls, 1)
        self.assertEqual(client.create_calls, 0)
        self.assertIsNotNone(client.retired_lease_id)

    def test_retires_leased_sandbox_when_pipeline_fails(self) -> None:
        client = _PoolClient(_Sandbox())
        provider = _AxiomSandboxProvider(
            client,
            workspace_id=uuid4(),
            cleanup=False,
            ready_timeout_seconds=90,
            pool_enabled=True,
        )

        with self.assertRaisesRegex(RuntimeError, "pipeline failed"):
            with provider.open():
                raise RuntimeError("pipeline failed")

        self.assertIsNotNone(client.retired_lease_id)

    def test_uses_request_create_when_pool_is_disabled(self) -> None:
        sandbox = _Sandbox()
        client = _PoolClient(sandbox)
        provider = _AxiomSandboxProvider(
            client,
            workspace_id=uuid4(),
            cleanup=False,
            ready_timeout_seconds=90,
            pool_enabled=False,
        )

        with provider.open():
            pass

        self.assertEqual(client.lease_calls, 0)
        self.assertEqual(client.create_calls, 1)

    def test_reads_ready_timeout_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"SANDBOX_READY_TIMEOUT_SECONDS": "90"},
            clear=False,
        ):
            settings = ConfigManager("missing-config.toml").sandbox_settings()

        self.assertEqual(settings.ready_timeout_seconds, 90)
        self.assertTrue(settings.pool_enabled)

    def test_reads_pool_enabled_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"SANDBOX_POOL_ENABLED": "false"},
            clear=False,
        ):
            settings = ConfigManager("missing-config.toml").sandbox_settings()

        self.assertFalse(settings.pool_enabled)
