import sys
import contextlib
import io
import json
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import JsonLineServer


class DeadlineTests(unittest.TestCase):
    def test_error_states_explicitly_remain_unknown(self):
        server = JsonLineServer.__new__(JsonLineServer)
        for status in ("TIMEOUT", "ERROR", "UNAVAILABLE", "OUT_OF_DOMAIN", "LOADING"):
            result = server._error("synthetic", status, "SYNTHETIC_ERROR")
            self.assertEqual(result["level"], "UNKNOWN")
            self.assertFalse(result["calibrated"])
            self.assertNotIn("candidate", result)

    def test_warmup_redirection_cannot_swallow_protocol_reply(self):
        server = JsonLineServer.__new__(JsonLineServer)
        server._write_lock = threading.Lock()
        protocol = io.StringIO()
        diagnostics = io.StringIO()
        server._protocol_stdout = protocol
        with contextlib.redirect_stdout(diagnostics):
            server._write({'id': 'synthetic', 'status': 'LOADING'})
        self.assertEqual(json.loads(protocol.getvalue())['status'], 'LOADING')
        self.assertEqual(diagnostics.getvalue(), '')

    def test_completion_timeout_race_emits_exactly_once(self):
        for _ in range(40):
            server = JsonLineServer.__new__(JsonLineServer)
            server._state_lock = threading.Lock()
            timer = threading.Timer(60, lambda: None)
            future = Future()
            future.set_result({'id': 'synthetic', 'status': 'OK'})
            server._pending = {'synthetic': {'timer': timer, 'future': future}}
            output = []
            server._write = output.append
            barrier = threading.Barrier(3)
            def complete():
                barrier.wait()
                server._complete('synthetic', future)
            def timeout():
                barrier.wait()
                server._timeout('synthetic')
            threads = [threading.Thread(target=complete), threading.Thread(target=timeout)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()
            self.assertEqual(len(output), 1)
            self.assertIn(output[0]['status'], ('OK', 'TIMEOUT'))
            self.assertFalse(server._pending)

    def test_timed_out_queued_work_is_cancelled_and_late_result_ignored(self):
        server = JsonLineServer.__new__(JsonLineServer)
        server._state_lock = threading.Lock()
        future = Future()
        server._pending = {'synthetic': {'timer': threading.Timer(60, lambda: None), 'future': future}}
        output = []
        server._write = output.append
        server._timeout('synthetic')
        self.assertTrue(future.cancelled())
        server._complete('synthetic', future)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['status'], 'TIMEOUT')
