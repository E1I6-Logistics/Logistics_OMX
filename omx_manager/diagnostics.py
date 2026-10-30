"""Read-only performance monitor. No torch, camera SDK, serial or MQTT imports."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from .config import diagnostic_options
from .runtime import state_dir

def read_json(path):
    try:
        data=json.loads(Path(path).read_text());return data if isinstance(data,dict) else {}
    except (OSError,ValueError): return {}

def read_process(pid, proc=Path('/proc')):
    try:
        base=proc/str(pid)
        text=(base/'stat').read_text();fields=text[text.rfind(')')+2:].split()
        status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in (base/'status').read_text().splitlines() if ':' in line}
        command=(base/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
        if 'lerobot.scripts.lerobot_rollout' not in command and 'lerobot-rollout' not in command: return None
        return {'pid':pid,'start_ticks':int(fields[19]),'cpu_ticks':int(fields[11])+int(fields[12]),
                'rss_mb':int(status.get('VmRSS','0 kB').split()[0])/1024,
                'threads':int(status.get('Threads','0'))}
    except (OSError,ValueError,IndexError): return None

def read_system(proc=Path('/proc'), sysroot=Path('/sys')):
    result={}
    try:
        cpu=list(map(int,(proc/'stat').read_text().splitlines()[0].split()[1:]))
        result.update(cpu_total=sum(cpu[:8]),cpu_idle=cpu[3]+cpu[4])
        memory={line.split(':')[0]:int(line.split()[1]) for line in (proc/'meminfo').read_text().splitlines() if ':' in line}
        result.update(memory_total_mb=memory['MemTotal']/1024,memory_available_mb=memory['MemAvailable']/1024)
    except (OSError,ValueError,IndexError,KeyError): pass
    for key,path,scale in [('temperature_c','class/thermal/thermal_zone0/temp',1000),('cpu_mhz','devices/system/cpu/cpu0/cpufreq/scaling_cur_freq',1000)]:
        try: result[key]=float((sysroot/path).read_text())/scale
        except (OSError,ValueError):result[key]=None
    return result

def finite(value):
    return type(value) in (int,float) and math.isfinite(value)

def valid_metrics(data):
    if not isinstance(data.get('events'),dict) or not isinstance(data.get('timings'),dict) or not isinstance(data.get('cameras'),dict):return False
    if not finite(data.get('target_hz')) or data['target_hz']<=0:return False
    for row in data['events'].values():
        if not isinstance(row,(list,tuple)) or len(row)!=2 or type(row[0]) is not int or row[0]<0:return False
        if row[1] is not None and (not finite(row[1]) or row[1]<0):return False
    for row in data['timings'].values():
        if not isinstance(row,(list,tuple)) or len(row)!=3 or type(row[0]) is not int or row[0]<0:return False
        if any(not finite(v) or v<0 for v in row[1:]):return False
    for row in data['cameras'].values():
        if not isinstance(row,dict):return False
        if any(row.get(key) is not None and (not finite(row[key]) or row[key]<=0) for key in ('configured_fps','processing_fps')):return False
    return True

class Sampler:
    def __init__(self,cfg):
        self.cfg=cfg;self.options=diagnostic_options(cfg);self.previous=None;self.last_metrics=None;self.throttle=None;self.next_throttle=0
    def sample(self,now=None):
        now=time.monotonic() if now is None else now
        directory=state_dir(self.cfg);heartbeat=read_json(directory/'heartbeat.json')
        pid=heartbeat.get('pid');process=read_process(pid) if type(pid) is int and pid>0 else None
        system=read_system();result={'at':time.time(),'device':self.cfg['device_id'],'process':process,
            'system':system,'control_cpu_pct':None,'system_cpu_pct':None,'metrics':None,'metrics_status':'UNAVAILABLE','heartbeat_age_s':None}
        if self.previous:
            last,old_process,old_system=self.previous;elapsed=now-last
            if elapsed>0:
                if process and old_process and (process['pid'],process['start_ticks'])==(old_process['pid'],old_process['start_ticks']):
                    result['control_cpu_pct']=max(0.,(process['cpu_ticks']-old_process['cpu_ticks'])/os.sysconf('SC_CLK_TCK')/elapsed*100)
                if 'cpu_total' in system and 'cpu_total' in old_system:
                    total=system['cpu_total']-old_system['cpu_total'];idle=system['cpu_idle']-old_system['cpu_idle']
                    if total>0: result['system_cpu_pct']=max(0.,min(100.,100*(total-idle)/total))
        self.previous=(now,process,system)
        metrics=read_json(directory/'metrics.json')
        stamp=metrics.get('monotonic')
        fresh=process and finite(stamp) and 0<=now-stamp<=self.options['stale_s'] and metrics.get('pid')==process['pid'] and metrics.get('process_start_ticks')==process['start_ticks']
        if metrics:result['metrics_status']='STALE_OR_OTHER_PROCESS'
        if fresh and not valid_metrics(metrics):
            result['metrics_status']='INVALID';fresh=False
        if fresh:
            previous=self.last_metrics
            same_process=previous and (previous.get('pid'),previous.get('process_start_ticks'))==(metrics['pid'],metrics['process_start_ticks'])
            dt=stamp-previous.get('monotonic',stamp) if same_process else 0
            if not same_process:previous=None
            result['metrics_status']='WARMUP' if dt<=0 else 'OK'
            def rate(key):
                if dt<=0:return None
                new=metrics.get('events',{}).get(key,[0,None])[0];old=previous.get('events',{}).get(key,[0,None])[0]
                return (new-old)/dt if new>=old else None
            cameras={}
            for name,config in metrics.get('cameras',{}).items():
                last=metrics.get('events',{}).get('camera/'+name+'/published',[0,None])[1]
                cameras[name]=dict(config,capture_fps=rate('camera/'+name+'/captured'),published_fps=rate('camera/'+name+'/published'),frame_age_ms=(now-last)*1000 if last is not None else None)
            n,total,worst=metrics.get('timings',{}).get('act_chunk',[0,0,0]);mean=None
            old=previous.get('timings',{}).get('act_chunk',[0,0,0]) if previous else [0,0,0]
            if dt>0 and n>old[0]:mean=(total-old[1])/(n-old[0])*1000
            result['metrics']={'target_hz':metrics.get('target_hz'),'loop_hz':rate('loop'),'action_send_hz':rate('action_sent'),
                'act_chunk_ms':mean,'act_chunk_worst_ms':worst*1000 if n else None,'act_chunk_samples':n,'cameras':cameras}
            if dt>0 or not previous:self.last_metrics=metrics
        else:
            self.last_metrics=None
        workstation=read_json(directory/'workstation_status.json');inventory=read_json(directory/'inventory.json')
        phase='NOT_RUNNING'
        if process:
            phase='STARTING'
            if self.cfg['role']=='unload' and workstation.get('pid')==process['pid']:
                phase=str(workstation.get('phase','UNKNOWN'))
            elif self.cfg['role']=='pickup':
                remaining=inventory.get('remaining')
                if type(remaining) is int and remaining>=0:phase='WAITING_ORDER' if remaining==0 else 'PICKING'
                elif inventory:phase='STATE_INVALID'
            heartbeat_stamp=heartbeat.get('monotonic')
            if finite(heartbeat_stamp):
                result['heartbeat_age_s']=max(0.,now-heartbeat_stamp)
                if now-heartbeat_stamp>self.cfg['runtime']['heartbeat_timeout_s']:phase='HEARTBEAT_STALE'
        waiting=read_json(directory/'recovery_wait.json')
        if not process and waiting.get('phase')=='RECOVERY_WAIT' and finite(waiting.get('at')) and 0<=time.time()-waiting['at']<=max(15,self.cfg['recovery'].get('poll_s',5)*3):
            phase='RECOVERY_WAIT';result['recovery_reason']=waiting.get('reason')
        if self.cfg['role']=='unload' and process and workstation.get('pid')==process['pid']:
            result['unload']={k:workstation.get(k) for k in ('attempts','completed_cycles','estimated_transfers','expected_count','basket_empty_verified')}
        result['phase']=phase
        if now>=self.next_throttle:
            self.next_throttle=now+10
            if shutil.which('vcgencmd'):
                try:self.throttle=subprocess.check_output(['vcgencmd','get_throttled'],timeout=.5,text=True,stderr=subprocess.DEVNULL).strip()
                except (OSError,subprocess.SubprocessError):self.throttle=None
        result['pi_throttled']=self.throttle
        return result

def number(value,suffix=''):
    return '--' if value is None else f'{value:.1f}{suffix}'

def render(data):
    p=data['process'] or {};s=data['system'];m=data['metrics']
    lines=[f"{data['device']} {data['phase']} PID={p.get('pid','--')} CPU={number(data['control_cpu_pct'],'%')} (100%=1 core) totalCPU={number(data['system_cpu_pct'],'%')} RSS={number(p.get('rss_mb'),'MB')} RAM_available={number(s.get('memory_available_mb'),'MB')} temp={number(s.get('temperature_c'),'C')} clock={number(s.get('cpu_mhz'),'MHz')}"]
    if m:
        lines.append(f"  loop={number(m['loop_hz'],'Hz')} target={number(m['target_hz'],'Hz')} model_send={number(m['action_send_hz'],'Hz')} ACT_chunk={number(m['act_chunk_ms'],'ms')} worst={number(m['act_chunk_worst_ms'],'ms')} samples={m['act_chunk_samples']}")
        for name,c in m['cameras'].items():lines.append(f"  camera {name}: capture={number(c['capture_fps'],'fps')} published={number(c['published_fps'],'fps')} configured={number(c.get('configured_fps'),'fps')} age={number(c['frame_age_ms'],'ms')}")
    else:lines.append('  Detailed metrics unavailable: '+data.get('metrics_status','UNAVAILABLE')+'. Start run with --diagnostics; allow two snapshots.')
    if data.get('recovery_reason'):lines.append('  RECOVERY_WAIT: '+str(data['recovery_reason']))
    if data.get('unload'):
        u=data['unload'];lines.append(f"  unload attempts={u.get('attempts')} cycles={u.get('completed_cycles')} estimated_transfers={u.get('estimated_transfers')} expected={u.get('expected_count')} empty={u.get('basket_empty_verified')}")
    if data['pi_throttled']:lines.append('  '+data['pi_throttled'])
    return '\n'.join(lines)

def monitor(cfg,interval=None,samples=0,json_output=False):
    if interval is None:interval=diagnostic_options(cfg)["interval_s"]
    if not math.isfinite(interval) or interval<1:raise ValueError('interval must be finite and >=1 second')
    if type(samples) is not int or samples<0:raise ValueError('samples must be >=0; 0 means continuous')
    sampler=Sampler(cfg);count=0
    while samples==0 or count<samples:
        data=sampler.sample();print(json.dumps(data,ensure_ascii=False) if json_output else render(data),flush=True)
        count+=1
        if samples==0 or count<samples:time.sleep(interval)
