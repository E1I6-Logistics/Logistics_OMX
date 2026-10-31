"""Bounded, optional child log capture. Slow disks never block the drain threads."""
from collections import deque
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import re
import threading
import time

HEARTBEAT = re.compile(r"\[SEND\].*/status:.*['\"]state['\"]: ['\"](?:IDLE|WORKING)['\"]")
IMPORTANT = re.compile(r"error|warn|exception|traceback|safety|recovery|diagnostic|\[HOME\]|\[ORDER\]|\[UNLOAD\]|\[CPU\]|\[EXPOSURE\]|calibration|COMMAND RECEIVED|/ack:|/progress:|/result:|WORKSTATION|registered|connected|disconnect|camera.*(?:failed|reconnect)|inventory|cycle completed|order.*(?:complete|progress|counter|failed)|Base strategy ready|policy loaded", re.I)

class LogFilter:
    def __init__(self,cadence_interval=10.): self.traceback = False; self.last_cadence = float("-inf"); self.cadence_interval=cadence_interval
    def accepts(self, line):
        if HEARTBEAT.search(line): return False
        if "Control loop is running slower" in line:
            now=time.monotonic()
            if now-self.last_cadence<self.cadence_interval: return False
            self.last_cadence=now
            return True
        if 'Traceback (most recent call last)' in line:
            self.traceback = True
            return True
        if self.traceback:
            if line.startswith((' ', '\t')) or re.match(r'^[\w.]+(?:Error|Exception|Interrupt)(?::|$)', line):
                return True
            self.traceback = False
        return bool(IMPORTANT.search(line))

class Capture:
    def __init__(self, directory, important_only=False, options=None):
        from .config import diagnostic_options
        self.options=diagnostic_options({}) if options is None else options
        self.queue = queue.Queue(maxsize=self.options["queue_lines"])
        self.stop = threading.Event()
        self.readers = []
        self.dropped = 0
        self.important_only = important_only
        self.handlers = []
        self.filters = {}
        try:
            directory = Path(directory);directory.mkdir(parents=True,exist_ok=True)
            for name,size in [('full.log',int(self.options['full_log_mb']*1024*1024)),('important.log',int(self.options['important_log_mb']*1024*1024))]:
                handler=RotatingFileHandler(directory/name,maxBytes=size,backupCount=self.options["backups"],encoding='utf-8')
                handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
                self.handlers.append(handler)
        except OSError as exc:
            print('[DIAGNOSTICS] File logging unavailable: '+str(exc),flush=True)
            for handler in self.handlers: handler.close()
            self.handlers=[]
        self.writer=threading.Thread(target=self._write,name='omx-log-writer',daemon=True)
        self.writer.start()
    def emit(self, source, line):
        try: self.queue.put_nowait((source,line.rstrip('\r\n')[:16384]))
        except queue.Full: self.dropped+=1
    def attach(self, proc, source):
        def drain():
            try:
                for line in iter(lambda: proc.stdout.readline(16384), ''): self.emit(source,line)
            except (OSError,ValueError): pass
            finally:
                try: proc.stdout.close()
                except OSError: pass
        thread=threading.Thread(target=drain,name='omx-log-'+source,daemon=True)
        self.readers[:]=[reader for reader in self.readers if reader.is_alive()]
        self.readers.append(thread);thread.start()
    def _write(self):
        while not self.stop.is_set() or not self.queue.empty():
            try: source,line=self.queue.get(timeout=.1)
            except queue.Empty: continue
            important=self.filters.setdefault(source,LogFilter(self.options["slow_warning_interval_s"])).accepts(line)
            record=logging.LogRecord('omx',logging.INFO,'',0,'['+source+'] '+line,(),None)
            for index,handler in enumerate(self.handlers):
                if index==0 or important:
                    try: handler.emit(record)
                    except Exception: pass
            if important or not self.important_only:
                try: print('['+source+'] '+line,flush=True)
                except (BrokenPipeError,OSError): pass
    def close(self):
        for reader in self.readers: reader.join(timeout=1)
        if self.dropped: self.emit('manager','[DIAGNOSTICS] Log queue dropped '+str(self.dropped)+' lines; control was not blocked')
        self.stop.set();self.writer.join(timeout=2)
        if not self.writer.is_alive():
            for handler in self.handlers: handler.close()

def view(path, follow=False, lines=50):
    path=Path(path)
    if not 1<=lines<=10000: raise ValueError('lines must be 1..10000')
    position=0;identity=None;opened=False
    while True:
        try:
            stat=path.stat();current=(stat.st_dev,stat.st_ino)
            if current!=identity or stat.st_size<position:
                position=0;identity=current
                if not opened:
                    with path.open(errors='replace') as stream:
                        for line in deque(stream,maxlen=lines): print(line,end='',flush=True)
                        position=stream.tell()
                    opened=True
            with path.open(errors='replace') as stream:
                stream.seek(position)
                for line in stream: print(line,end='',flush=True)
                position=stream.tell()
        except FileNotFoundError:
            if not follow:
                print('No captured log yet. Start run with --important-only or --diagnostics.');return
        if not follow: return
        time.sleep(.3)
