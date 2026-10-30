import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from telnyx_adapter import AdapterConfig, TelnyxAdapter
from telnyx_adapter.config import Approval


class TestAtomicConcurrency(unittest.TestCase):
    def test_parallel_claims_allow_only_one_and_unknown_blocks_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            target='+12025550101'
            adapter=TelnyxAdapter(Path(tmp)/'state.db','unused',
                                 AdapterConfig(authorized_destinations=(target,)))
            attempts=[]
            for _ in range(8):
                job,attempt=str(uuid4()),str(uuid4())
                adapter.prepare(job_id=job,connection_id='conn',from_number='+12025550100',
                    to_number=target,attempt_id=attempt,max_minutes=1,
                    approval=Approval(job_id=job,destination=target,max_minutes=1,estimated_cost_cents=1))
                attempts.append(attempt)
            def claim(attempt):
                try:
                    adapter.claim_submission(attempt)
                    return True
                except ValueError:
                    return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(claim,attempts))
            self.assertEqual(sum(results),1)
            states=[adapter.get_attempt(a)['state'] for a in attempts]
            self.assertEqual(states.count('submit_unknown'),1)
            self.assertEqual(states.count('prepared'),7)
            for attempt,state in zip(attempts,states):
                if state=='prepared':
                    self.assertFalse(claim(attempt))
