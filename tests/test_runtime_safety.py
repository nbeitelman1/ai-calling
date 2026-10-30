import asyncio
import os
import unittest
from unittest.mock import patch

from runtime_safety import cancel_tasks_bounded, timeout_setting, wait_pipeline_started


class TestRuntimeSafety(unittest.IsolatedAsyncioTestCase):
    async def make_waiters(self):
        return [asyncio.create_task(asyncio.Event().wait()) for _ in range(2)]

    async def test_start_success(self):
        ready=asyncio.Event(); ready.set()
        waiters=await self.make_waiters()
        try:
            await wait_pipeline_started(ready,*waiters,timeout=.05)
        finally:
            await cancel_tasks_bounded(waiters)

    async def test_start_timeout_is_absolute(self):
        waiters=await self.make_waiters()
        try:
            with self.assertRaises(TimeoutError):
                await wait_pipeline_started(asyncio.Event(),*waiters,timeout=.03)
        finally:
            await cancel_tasks_bounded(waiters)

    async def test_early_runner_failure_wins(self):
        async def fail():
            raise ValueError('synthetic provider failure')
        runner=asyncio.create_task(fail())
        other=asyncio.create_task(asyncio.Event().wait())
        try:
            with self.assertRaises(ValueError):
                await wait_pipeline_started(asyncio.Event(),runner,other,timeout=.05)
        finally:
            await cancel_tasks_bounded([runner,other])

    async def test_disconnect_wins_even_if_ready(self):
        ready=asyncio.Event(); ready.set()
        runner=asyncio.create_task(asyncio.Event().wait())
        async def disconnect():
            return 'websocket_disconnected'
        peer=asyncio.create_task(disconnect())
        await asyncio.sleep(0)
        try:
            with self.assertRaises(ConnectionError):
                await wait_pipeline_started(ready,runner,peer,timeout=.05)
        finally:
            await cancel_tasks_bounded([runner,peer])

    async def test_cleanup_does_not_wait_forever_for_resistant_task(self):
        finish=asyncio.Event()
        active=asyncio.Event()
        async def resistant():
            active.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await finish.wait()
        task=asyncio.create_task(resistant())
        await active.wait()
        try:
            pending=await asyncio.wait_for(cancel_tasks_bounded([task],timeout=.02),.2)
            self.assertEqual(pending,1)
        finally:
            finish.set()
            await task

    def test_timeouts_reject_nonfinite_and_out_of_bounds(self):
        for value in ['nan','inf','-1','0','121']:
            with self.subTest(value=value), patch.dict(os.environ,{'TEST_TIMEOUT':value}):
                with self.assertRaises(ValueError):
                    timeout_setting('TEST_TIMEOUT',5)
