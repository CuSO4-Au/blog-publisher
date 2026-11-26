import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def state_path(source: Path) -> Path:
    source = source.resolve()
    return source.with_name(source.name + ".publisher.json")


def site_key(site: str) -> str:
    url = urlsplit(site)
    if not url.hostname:
        raise ValueError("The task record requires a WordPress site URL.")
    host = url.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if url.port and (url.scheme, url.port) not in {("https", 443), ("http", 80)}:
        host += f":{url.port}"
    return urlunsplit((url.scheme.lower(), host, url.path.rstrip("/"), "", ""))


def read_state(source: Path) -> dict:
    path = state_path(source)
    if not path.exists():
        return {"schema_version": 1, "sites": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"The local task record is invalid. Review {path} before uploading.") from error
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or not isinstance(data.get("sites"), dict)
    ):
        raise ValueError(f"Unsupported local task record: {path}")
    for site, entry in data["sites"].items():
        required = {"post_id", "creation_pending", "last_result", "updated_at"}
        if not isinstance(site, str) or not isinstance(entry, dict) or not required.issubset(entry):
            raise ValueError(f"Invalid site entry in {path}")
        post_id = entry.get("post_id")
        if post_id is not None and (type(post_id) is not int or post_id <= 0):
            raise ValueError(f"Invalid post ID in {path}")
        if type(entry.get("creation_pending", False)) is not bool:
            raise ValueError(f"Invalid creation state in {path}")
        if (post_id is None) != entry["creation_pending"]:
            raise ValueError(f"Incomplete draft identity in {path}")
        if not isinstance(entry["last_result"], str) or entry["last_result"] not in {"running", "success", "failed"}:
            raise ValueError(f"Invalid upload result in {path}")
        if not isinstance(entry["updated_at"], str):
            raise ValueError(f"Invalid operation time in {path}")
    return data


@contextmanager
def locked_state(source: Path, site: str):
    path = state_path(source)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            acquire = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            acquire = lambda: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(lock, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError:
            raise ValueError("Another upload is using this article. Wait for it to finish.") from None
        try:
            yield TaskRecord(source, site)
        finally:
            release()


class TaskRecord:
    def __init__(self, source: Path, site: str):
        self.path = state_path(source)
        self.site = site_key(site)
        self.data = read_state(source)
        self.entry = self.data["sites"].get(self.site, {})
        self.started = False

    def post_id(self, explicit_id: int | None) -> int | None:
        if explicit_id is not None:
            return explicit_id
        if self.entry.get("creation_pending"):
            raise ValueError(
                "A previous create request may have saved a draft. "
                "Check WordPress and retry with --post-id ID."
            )
        return self.entry.get("post_id")

    def begin(self, post_id: int | None):
        self.entry = {
            "post_id": post_id,
            "creation_pending": post_id is None,
            "last_result": "running",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if post_id is not None:
            self.entry["status"] = "draft"
        self.save()
        self.started = True

    def remember(self, post):
        self.entry.update(post_id=post["id"], status=post["status"], creation_pending=False)
        self.save()

    def finish(self, result: str):
        self.entry.update(last_result=result, updated_at=datetime.now(timezone.utc).isoformat())
        self.save()

    def save(self):
        self.data["sites"][self.site] = self.entry
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=self.path.name + ".",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
