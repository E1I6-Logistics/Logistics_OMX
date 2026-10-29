"""Durable command identities. Interrupted jobs cannot be silently replayed."""
import json
import threading
from pathlib import Path
from .config import write_json

class JobJournal:
    def __init__(self,path): self.path=Path(path); self.lock=threading.RLock()
    def read(self):
        if not self.path.exists(): return {}
        data=json.loads(self.path.read_text())
        if not isinstance(data,dict): raise ValueError("Invalid job journal")
        return data
    @staticmethod
    def signature(items): return json.dumps(items,sort_keys=True,separators=(",",":"),allow_nan=False)
    def lookup(self,job_id,items):
        with self.lock:
            row=self.read().get(str(job_id))
            if row and row["signature"]!=self.signature(items): raise ValueError("Same job_id with different items")
            return row
    def begin(self,job_id,items):
        with self.lock:
            data=self.read(); key=str(job_id)
            if key in data: raise ValueError("Job already recorded; replay forbidden")
            data[key]={"signature":self.signature(items),"state":"RUNNING"}
            write_json(self.path,data)
    def finish(self,job_id,success,message):
        with self.lock:
            data=self.read(); data[str(job_id)].update(state="DONE",success=bool(success),message=message)
            write_json(self.path,data)
