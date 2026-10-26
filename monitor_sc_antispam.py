"""Delivery batch telemetry: UTC timestamps only; no message or recipient data."""
import json
import os
import pathlib
import tempfile
from datetime import datetime, timezone

PATH = pathlib.Path.home() / '.local/state/monitor-sc/antispam.json'


def record_batch(succeeded, failed, path=None):
    # Optional integration. No writes to a user's home unless explicitly enabled.
    if path is None and os.getenv('MONITOR_SC_ENABLED', 'false').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        return False
    temporary = None
    try:
        if not succeeded and not failed:
            return False
        path = pathlib.Path(path) if path is not None else PATH
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            state = {}
        state = state if isinstance(state, dict) else {}
        state = {key: state.get(key) for key in ('last_success_at', 'last_failure_at')}
        stamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        if succeeded:
            state['last_success_at'] = stamp
        if failed:
            state['last_failure_at'] = stamp
        descriptor, temporary = tempfile.mkstemp(prefix='.antispam-', dir=path.parent)
        with os.fdopen(descriptor, 'w') as stream:
            if hasattr(os, 'fchmod'):
                os.fchmod(stream.fileno(), 0o600)
            json.dump(state, stream)
        os.replace(temporary, path)
        return True
    except Exception:
        return False  # Monitoring cannot break the existing spam filter.
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
