import os
import secrets
from pathlib import Path


def data_directory() -> Path:
    root = Path(os.environ.get("TOKENSCOPE_DATA_DIR", Path.home() / ".tokenscope")).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def api_token(root: Path) -> str:
    if os.environ.get("TOKENSCOPE_TOKEN"):
        return os.environ["TOKENSCOPE_TOKEN"]
    target = root / "token"
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return target.read_text().strip()
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as stream:
        stream.write(token)
    return token
