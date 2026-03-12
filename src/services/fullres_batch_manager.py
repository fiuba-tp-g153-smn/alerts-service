"""Threaded manager for batching full-resolution intersection subprocesses."""

import json
import os
import subprocess
import sys
import threading
import uuid
import queue
from logging import Logger

_WORKER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "fullres_worker.py")
)


class FullresBatchManager:
    """Manages batching of full-resolution requests to prevent RAM blowups."""

    def __init__(self, logger: Logger):
        """Initialize the batch manager with a background thread."""
        self.logger = logger
        self.queue = queue.Queue()
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()

    def submit(self, task: str, geometry_wkb_hex: str, layer_path: str) -> dict:
        """Submit a task to the background worker and wait for the result."""
        req_id = uuid.uuid4().hex
        event = threading.Event()

        context = {
            "result": None,
            "error": None,
            "event": event,
        }

        payload = {
            "id": req_id,
            "task": task,
            "geometry_wkb_hex": geometry_wkb_hex,
            "layer_path": layer_path,
            "context": context,
        }

        self.queue.put(payload)

        # Block until the background thread sets the event
        event.wait()

        if context["error"]:
            raise RuntimeError(f"fullres_worker failed: {context['error']}")

        return context["result"]

    def _worker_loop(self):
        """Continuously process batches of requests from the queue."""
        while self._running:
            try:
                # Block for up to 1 second waiting for the first item
                item = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue

            batch = [item]

            # Drain the queue of any currently waiting items to form a batch
            while not self.queue.empty():
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break

            self.logger.info(
                f"FullresBatchManager processing batch of size {len(batch)}"
            )

            payload_for_worker = [
                {
                    "id": req["id"],
                    "task": req["task"],
                    "geometry_wkb_hex": req["geometry_wkb_hex"],
                    "layer_path": req["layer_path"],
                }
                for req in batch
            ]

            try:
                result = subprocess.run(
                    [sys.executable, _WORKER_PATH],
                    input=json.dumps(payload_for_worker),
                    capture_output=True,
                    text=True,
                    check=False,
                )

                if result.returncode != 0:
                    self.logger.error(
                        f"Worker subprocess failed with exit code {result.returncode}: {result.stderr}"
                    )
                    # If the whole subprocess failed, fail all requests in the batch
                    for req in batch:
                        req["context"][
                            "error"
                        ] = f"Subprocess failed (exit {result.returncode}): {result.stderr}"
                        req["context"]["event"].set()
                    continue

                try:
                    output = json.loads(result.stdout)
                except json.JSONDecodeError:
                    self.logger.error(
                        f"Failed to decode worker output: {result.stdout}"
                    )
                    for req in batch:
                        req["context"][
                            "error"
                        ] = "Invalid JSON returned from worker subprocess"
                        req["context"]["event"].set()
                    continue

                results = output.get("results", {})
                errors = output.get("errors", {})

                for req in batch:
                    req_id = req["id"]
                    if req_id in errors:
                        req["context"]["error"] = errors[req_id]
                    elif req_id in results:
                        req["context"]["result"] = results[req_id]
                    else:
                        req["context"][
                            "error"
                        ] = "Worker did not return a result for this request"

                    req["context"]["event"].set()

            except Exception as e:
                self.logger.error(f"Batch processing error: {e}")
                for req in batch:
                    req["context"]["error"] = str(e)
                    req["context"]["event"].set()

    def stop(self):
        """Stop the background worker thread cleanly."""
        self._running = False
        self._thread.join(timeout=2.0)
