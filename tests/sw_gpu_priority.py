"""GPU yield policy fixtures; never initializes or queries a GPU."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.gpu_priority import yield_reason, descendants, PriorityConfirmation


class PriorityTests(unittest.TestCase):
    def sample(self, rows=(), used=27540, free=5000):
        return {'processes': list(rows), 'used_mib': used, 'free_mib': free}
    def process(self, pid, name='python.exe', memory=0, engine=0):
        return {'pid': pid, 'name': name, 'dedicated_bytes': memory * 1048576, 'engine_percent': engine}
    def test_own_runner_and_desktop_do_not_pause(self):
        rows = [self.process(1, memory=27500, engine=90), self.process(2, 'dwm.exe', 80, 3),
                self.process(3, 'LockApp.exe', 18, 2)]
        self.assertIsNone(yield_reason(self.sample(rows), {1}, baseline_mib=27540))
    def test_foreign_allocation_or_compute_yields(self):
        for row in (self.process(3, memory=64), self.process(4, engine=1)):
            self.assertEqual(yield_reason(self.sample([row]), {1})['reason'], 'foreign_5090_process')
    def test_hidden_wsl_allocation_is_observed_by_total_memory(self):
        self.assertEqual(yield_reason(self.sample(used=28000), {1}, baseline_mib=27540)['reason'], 'additional_5090_memory_use')
    def test_not_enough_free_space_before_loading_yields(self):
        self.assertEqual(yield_reason(self.sample(free=20000), require_free=True)['reason'], '5090_not_available')
    def test_only_owned_process_tree_is_exempted(self):
        rows = [{'pid': 1, 'parent_pid': 99}, {'pid': 2, 'parent_pid': 1}, {'pid': 3, 'parent_pid': 2}, {'pid': 4, 'parent_pid': 99}]
        self.assertEqual(descendants(rows, 1), {1, 2, 3})
    def test_transient_low_memory_activity_is_not_a_real_reservation(self):
        gate = PriorityConfirmation()
        reason = {'reason': 'foreign_5090_process', 'pid': 2, 'dedicated_mib': 26, 'engine_percent': 1}
        self.assertIsNone(gate.consider(reason)); self.assertIsNone(gate.consider(None))
        self.assertIsNone(gate.consider(reason)); self.assertEqual(gate.consider(reason), reason)
    def test_real_memory_allocation_never_waits_for_confirmation(self):
        reason = {'reason': 'foreign_5090_process', 'pid': 2, 'dedicated_mib': 100, 'engine_percent': 0}
        self.assertEqual(PriorityConfirmation().consider(reason), reason)


if __name__ == '__main__':
    unittest.main()
