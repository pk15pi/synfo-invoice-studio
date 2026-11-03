"""SECRET_KEY: env QB_SECRET_KEY, else a random key kept in a git-ignored local file, else an in-memory key.
Never raises: a failure only means signed cookies (flash messages) reset on restart."""
import os
import secrets
from pathlib import Path

FALLBACK_NOTE = 'QB_SECRET_KEY not set and the key file could not be read/written; using a per-process key.'


def load_secret_key(key_file):
    env = (os.environ.get('QB_SECRET_KEY') or '').strip()
    if env:
        return env
    try:
        p = Path(key_file)
        if p.is_file():
            k = p.read_text(encoding='utf-8').strip()
            if len(k) >= 32:
                return k
        p.parent.mkdir(parents=True, exist_ok=True)
        k = secrets.token_urlsafe(50)
        p.write_text(k, encoding='utf-8')
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return k
    except Exception:  # noqa: BLE001 - startup must never break because of the key file
        return secrets.token_urlsafe(50)
