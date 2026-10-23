from __future__ import annotations
import json, time
from typing import Callable, Optional
from .config import Settings

def _request(path:str, request_id:int, body:Optional[dict]=None)->str:
    payload="" if body is None else (body if isinstance(body,str) else json.dumps(body,separators=(",",":")))
    return f"{path}\n{request_id}\n\n{payload}"

def decode_frame(frame:str):
    if frame in {"o","h"}: return frame,[]
    if not frame or frame[0]!="a": return "unknown",[]
    return "array",json.loads(frame[1:])

def run_stream(settings:Settings,on_entity:Callable[[str,str,dict],None],on_snapshot:Callable[[dict],None],on_tick:Callable[[],None])->None:
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise RuntimeError("Install websockets>=12 to run the live relay") from exc
    backoff=1
    while True:
        try:
            with connect(settings.ws_url,open_timeout=10,close_timeout=5) as ws:
                if ws.recv()!="o": raise RuntimeError("Tradovate websocket did not open")
                ws.send(_request("authorize",1,settings.access_token))
                ws.send(_request("user/syncrequest",2,{"users":[settings.user_id],"accounts":sorted(settings.account_ids)}))
                backoff=1
                while True:
                    frame=ws.recv(); kind,items=decode_frame(frame)
                    if kind=="h":
                        ws.send("[]"); on_tick(); continue
                    for item in items:
                        if item.get("i")==2 and item.get("s")==200 and isinstance(item.get("d"),dict):
                            on_snapshot(item["d"]); continue
                        if item.get("e")!="props": continue
                        d=item.get("d") or {}; entity=d.get("entity")
                        if isinstance(entity,list):
                            for e in entity: on_entity(str(d.get("entityType")),str(d.get("eventType")),e)
                        elif isinstance(entity,dict):
                            on_entity(str(d.get("entityType")),str(d.get("eventType")),entity)
                    on_tick()
        except KeyboardInterrupt: raise
        except Exception:
            time.sleep(backoff); backoff=min(backoff*2,30)
