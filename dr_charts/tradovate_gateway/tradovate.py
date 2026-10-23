from __future__ import annotations

import json
import time
from typing import Callable, Optional

from .config import Settings


def _request(path: str, request_id: int, body=None) -> str:
    if body is None:
        payload = ""
    elif isinstance(body, str):
        payload = body
    else:
        payload = json.dumps(body, separators=(",", ":"))
    return f"{path}\n{request_id}\n\n{payload}"


def decode_frame(frame: str):
    if frame in {"o", "h"}:
        return frame, []
    if not frame:
        return "unknown", []
    if frame[0] == "a":
        return "array", json.loads(frame[1:])
    if frame[0] == "c":
        return "close", json.loads(frame[1:]) if len(frame) > 1 else []
    return "unknown", []


def _receive_until_response(ws, request_id: int, on_tick: Callable[[], None]):
    buffered = []
    while True:
        frame = ws.recv()
        kind, items = decode_frame(frame)
        if kind == "h":
            ws.send("[]")
            on_tick()
            continue
        if kind == "close":
            raise ConnectionError(f"Tradovate closed socket: {items}")
        for item in items:
            if item.get("i") == request_id:
                return item, buffered
            buffered.append(item)
        on_tick()


def _dispatch_events(items, on_entity: Callable[[str, str, dict], None]) -> None:
    for item in items:
        if item.get("e") != "props":
            continue
        d = item.get("d") or {}
        entity = d.get("entity")
        if isinstance(entity, list):
            for e in entity:
                on_entity(str(d.get("entityType")), str(d.get("eventType")), e)
        elif isinstance(entity, dict):
            on_entity(str(d.get("entityType")), str(d.get("eventType")), entity)


def run_stream(
    settings: Settings,
    on_entity: Callable[[str, str, dict], None],
    on_snapshot: Callable[[dict], None],
    on_tick: Callable[[], None],
) -> None:
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise RuntimeError("Install websockets>=12 to run the live gateway") from exc

    backoff = 1
    while True:
        try:
            with connect(settings.ws_url, open_timeout=10, close_timeout=5) as ws:
                if ws.recv() != "o":
                    raise ConnectionError("Tradovate websocket did not open")

                ws.send(_request("authorize", 1, settings.access_token))
                auth, auth_buffer = _receive_until_response(ws, 1, on_tick)
                if auth.get("s") != 200:
                    raise PermissionError(f"Tradovate authorization failed with status {auth.get('s')}")
                if any(item.get("e") == "props" for item in auth_buffer):
                    raise RuntimeError("received user events before authorization completed")

                ws.send(_request("user/syncrequest", 2, {"users": [settings.user_id], "accounts": sorted(settings.account_ids)}))
                sync, sync_buffer = _receive_until_response(ws, 2, on_tick)
                if sync.get("s") != 200 or not isinstance(sync.get("d"), dict):
                    raise RuntimeError(f"Tradovate sync failed with status {sync.get('s')}")
                on_snapshot(sync["d"])
                # Events that raced the sync response are applied after the snapshot.
                # Snapshot duplicates are harmless because fill ids are idempotent.
                _dispatch_events(sync_buffer, on_entity)
                on_tick()
                backoff = 1

                while True:
                    frame = ws.recv()
                    kind, items = decode_frame(frame)
                    if kind == "h":
                        ws.send("[]")
                        on_tick()
                        continue
                    if kind == "close":
                        raise ConnectionError(f"Tradovate closed socket: {items}")
                    _dispatch_events(items, on_entity)
                    on_tick()
        except KeyboardInterrupt:
            raise
        except PermissionError:
            # Bad or expired credentials should not hammer the API forever.
            raise
        except Exception:
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
