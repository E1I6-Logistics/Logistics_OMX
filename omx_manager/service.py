"""Explicit user-service management. Install alone never starts a robot."""
from pathlib import Path
import subprocess
import time
from .config import ROOT,atomic_write,check_files,require_home,service_options

MARKER='# Generated OMX manager service;'

def quote(value):
    value=str(value)
    if any(c in value for c in ('\n','\r','\x00')):raise ValueError('Invalid service path')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'

def render(cfg,path,root=ROOT):
    options=service_options(cfg)
    if options['stop_timeout_s']<2*cfg['runtime']['shutdown_timeout_s']+6:
        raise ValueError('service.stop_timeout_s must allow 2*runtime.shutdown_timeout_s+6 seconds')
    command=' '.join([quote(root/'run_omx_mqtt.sh'),'--robot-id',cfg['device_id'],'--config',quote(Path(path).resolve()).replace('$','$$'),
                      '--confirm-motion','--important-only'])
    # Escape dollar expansion in the executable path too (systemd ExecStart grammar).
    command=command.replace(quote(root/'run_omx_mqtt.sh'),quote(root/'run_omx_mqtt.sh').replace('$','$$'),1)
    text=(ROOT/'deploy/omx-manager@.service').read_text()
    for key,value in {'@OMX_ROOT@':quote(root),'@OMX_COMMAND@':command,
                      '@RESTART_S@':str(options['restart_s']),'@STOP_TIMEOUT_S@':str(options['stop_timeout_s'])}.items():text=text.replace(key,value)
    return text

def manage(cfg,path,action,confirm_motion=False,follow=False,unit_dir=None):
    if action not in ('install','start','stop','status','logs','enable','disable','uninstall'):
        raise ValueError('service requires --action install/start/stop/status/logs/enable/disable/uninstall')
    unit='omx-manager@'+cfg['device_id']+'.service'
    directory=Path(unit_dir) if unit_dir is not None else Path.home()/'.config/systemd/user'
    file=directory/unit
    options=service_options(cfg)
    def systemctl(*args):return subprocess.run(['systemctl','--user',*args],check=True)
    if action=='install':
        if options['enable_on_boot'] and not confirm_motion:raise ValueError('Boot autostart requires --confirm-motion')
        text=render(cfg,path)
        if file.exists():
            previous=file.read_text()
            if not previous.startswith(MARKER):raise ValueError('Existing custom unit will not be overwritten: '+str(file))
            atomic_write(file.with_name(file.name+'.'+str(time.time_ns())+'.bak'),previous)
        atomic_write(file,text)
        systemctl('daemon-reload')
        systemctl('enable' if options['enable_on_boot'] else 'disable',unit)
        print('Installed '+str(file)+'; not started. Use service --action start --confirm-motion.')
        return
    if action=='status':return subprocess.run(['systemctl','--user','status',unit],check=False)
    if action=='logs':
        argv=['journalctl','--user','-u',unit,'-n','50','--no-pager']
        if follow:argv.append('--follow')
        return subprocess.run(argv,check=True)
    if action in ('start','enable'):
        if not confirm_motion:raise ValueError('Service start/autostart requires --confirm-motion')
        if not file.exists():raise ValueError('Install the service first')
        if file.read_text()!=render(cfg,path):raise ValueError('Service settings/path changed; reinstall first')
        check_files(cfg);require_home(cfg)
    if action=='uninstall':
        if not file.exists():print('Service unit not installed');return
        if not file.read_text().startswith(MARKER):raise ValueError('Refusing to remove a custom unit')
        systemctl('disable','--now',unit);file.unlink();systemctl('daemon-reload');return
    # A deliberate systemctl stop is not restarted despite Restart=always.
    return systemctl(action,unit)
