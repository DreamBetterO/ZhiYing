from __future__ import annotations

import threading
import time
import unittest

from zhiying.execution.resource_leases import ResourceLeaseManager


class V6ResourceLeaseTests(unittest.TestCase):
    def test_gpu_lease_is_globally_serial(self) -> None:
        active = 0
        maximum = 0
        guard = threading.Lock()

        def worker() -> None:
            nonlocal active, maximum
            with ResourceLeaseManager.acquire("gpu-test"):
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.02)
                with guard:
                    active -= 1

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
