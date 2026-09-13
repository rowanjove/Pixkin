import unittest

from core.runtime.kernel import KernelState, PixkinKernel
from core.runtime.service_container import ServiceContainer


class KernelTests(unittest.TestCase):
    def test_kernel_lifecycle_is_idempotent_and_non_restartable(self):
        container = ServiceContainer()
        kernel = PixkinKernel(container)
        kernel.start()
        kernel.start()
        self.assertEqual(kernel.state, KernelState.RUNNING)
        kernel.stop()
        kernel.stop()
        self.assertEqual(kernel.state, KernelState.STOPPED)
        with self.assertRaises(RuntimeError):
            kernel.start()


if __name__ == "__main__":
    unittest.main()
