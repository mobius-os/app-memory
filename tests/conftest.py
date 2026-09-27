"""Keep every Memory test away from a live Möbius instance.

The modules read DATA_DIR, APP_JOB_STATE_DIR, and API_BASE_URL at import, and
pytest imports this file before any test module. Pointing them at a throwaway
directory and an unreachable address means a test that forgets a stub fails
here the same way it fails in CI, instead of silently reading or writing the
owner's real Memory state or calling the running server.
"""

import os
import tempfile

_ROOT = tempfile.mkdtemp(prefix="memory-tests-")
os.environ["DATA_DIR"] = os.path.join(_ROOT, "data")
os.environ["APP_JOB_STATE_DIR"] = os.path.join(_ROOT, "job-state")
os.environ["API_BASE_URL"] = "http://127.0.0.1:9"
