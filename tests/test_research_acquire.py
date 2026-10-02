"""Input integrity regressions; use local byte streams, never network fixtures."""
import hashlib
import io

import pytest

from astra_research import acquire

REVISION = "a" * 40
CONTENT = b"timestamp,open\n2024-01-01,1.123456789\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


def test_immutable_download_and_reuse(tmp_path, monkeypatch):
    calls = []

    def download(request, timeout):
        calls.append((request.full_url, timeout))
        return io.BytesIO(CONTENT)

    monkeypatch.setattr(acquire, "urlopen", download)
    record = acquire.acquire_one(tmp_path, "nq.csv", REVISION, DIGEST)
    assert (tmp_path / "nq.csv").read_bytes() == CONTENT  # Never round off-tick input.
    assert record["sha256"] == DIGEST and not record["reused"]
    assert calls == [(f"{acquire.BASE_URL}/{REVISION}/data/nq.csv", 180)]
    assert acquire.acquire_one(tmp_path, "nq.csv", REVISION, DIGEST)["reused"]
    assert len(calls) == 1


def test_bad_download_never_publishes(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire, "urlopen", lambda *args, **kwargs: io.BytesIO(b"changed upstream bytes"))
    with pytest.raises(ValueError, match="SHA256 mismatch for downloaded"):
        acquire.acquire_one(tmp_path, "nq.csv", REVISION, DIGEST)
    assert list(tmp_path.iterdir()) == []


def test_corrupt_existing_evidence_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / "nq.csv"
    path.write_bytes(b"corrupt")
    monkeypatch.setattr(acquire, "urlopen", lambda *args, **kwargs: pytest.fail("must not download over corrupt evidence"))
    with pytest.raises(ValueError, match="SHA256 mismatch for existing"):
        acquire.acquire_one(tmp_path, "nq.csv", REVISION, DIGEST)
    assert path.read_bytes() == b"corrupt"


def test_interrupted_transfer_cleans_partial_file(tmp_path, monkeypatch):
    def interrupt(*args, **kwargs):
        raise OSError("interrupted transfer")
    monkeypatch.setattr(acquire, "urlopen", interrupt)
    with pytest.raises(OSError, match="interrupted transfer"):
        acquire.acquire_one(tmp_path, "nq.csv", REVISION, DIGEST)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name,revision,digest", [
    ("../escape.csv", REVISION, DIGEST),
    ("nq.csv", "main", DIGEST),
    ("nq.csv", REVISION, ""),
])
def test_unfrozen_or_unsafe_input_rejected(tmp_path, name, revision, digest):
    with pytest.raises(ValueError):
        acquire.acquire_one(tmp_path, name, revision, digest)
    assert list(tmp_path.iterdir()) == []
