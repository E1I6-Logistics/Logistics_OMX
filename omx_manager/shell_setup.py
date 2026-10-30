"""Install Bash shortcuts only. No hardware, package installs or service starts."""
import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import sys
import time
from .config import ROOT, atomic_write, load

BEGIN = '# >>> Logistics_OMX shortcuts >>>'
END = '# <<< Logistics_OMX shortcuts <<<'
ENV_MARKER = '# Generated Logistics_OMX shortcuts;'


def robot_from_hostname(hostname):
    match = re.fullmatch(r'omx0?([1-4])', hostname.split('.')[0].lower())
    return 'omx' + match[1] if match else None


def read_env(path):
    result = {}
    if not path.exists(): return result
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix('export ')
        key, sep, value = line.partition('=')
        if sep and key in ('OMX_ROBOT_ID', 'OMX_CONFIG_PATH', 'OMX_PYTHON', 'OMX_PYTHON_ENV_PATH'):
            words = shlex.split(value, comments=True)
            if len(words) != 1: raise ValueError('Invalid OMX registration: ' + str(path))
            result[key] = words[0]
    return result


def absolute(value, base):
    path = Path(os.path.expandvars(str(value))).expanduser()
    if any(char in str(path) for char in ('\n', '\r', '\0')):
        raise ValueError('Paths must not contain control characters')
    return Path(os.path.abspath(path if path.is_absolute() else base / path))


def replace_with_backup(path, text):
    if path.is_symlink(): path = path.resolve()
    if path.exists() and path.read_text() == text: return
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + '.omx-' + str(time.time_ns()) + '.bak'))
    atomic_write(path, text)
    path.chmod(mode)


def install(robot_id=None, config_path=None, python=None, *, root=ROOT, home=None, hostname=None):
    home = Path.home() if home is None else Path(home)
    root = absolute(root, Path.cwd())
    for name in ('omx_control', 'scripts/omx_shell.sh'):
        if not (root / name).is_file(): raise ValueError('Missing OMX file: ' + str(root / name))
    registration = home / '.config/logistics_omx/shell.env'
    if registration.exists() and not registration.read_text().startswith(ENV_MARKER):
        raise ValueError('Custom shell.env preserved; review before registration: ' + str(registration))
    saved = read_env(registration)
    legacy = read_env(home / '.config/logistics_omx/device.env')
    detected = robot_from_hostname(hostname or socket.gethostname())
    explicit = robot_id not in (None, 'auto')
    if not explicit:
        registered = saved.get('OMX_ROBOT_ID') or legacy.get('OMX_ROBOT_ID')
        if detected and registered and detected != registered:
            raise ValueError('Hostname/device registration mismatch; choose --robot-id omxN explicitly')
        robot_id = registered or detected
    if robot_id not in ('omx1', 'omx2', 'omx3', 'omx4'):
        raise ValueError('Cannot identify this Pi; use --robot-id omx1..omx4')
    old_config = saved.get('OMX_CONFIG_PATH') if saved.get('OMX_ROBOT_ID') == robot_id else None
    selected = config_path or old_config
    config = absolute(selected, root) if selected else root / 'state/config' / (robot_id + '.yaml')
    template = root / 'params' / (robot_id + '.yaml')
    if selected and not config.is_file(): raise ValueError('Registered config missing: ' + str(config))
    cfg = load(config if config.is_file() else template)
    if cfg['device_id'] != robot_id: raise ValueError('YAML device_id does not match selected OMX')
    preferred = python or saved.get('OMX_PYTHON') or os.environ.get('OMX_PYTHON')
    if not preferred:
        environment = legacy.get('OMX_PYTHON_ENV_PATH') or os.environ.get('OMX_PYTHON_ENV_PATH')
        preferred = str(Path(os.path.expandvars(environment)).expanduser() / 'bin/python') if environment else str(home / 'venv/.venv/bin/python')
        if not Path(preferred).is_file(): preferred = sys.executable
    if '/' not in str(preferred): preferred = shutil.which(str(preferred)) or str(preferred)
    executable = absolute(preferred, root)  # Do not resolve venv/bin/python to the system interpreter.
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError('Python executable missing: ' + str(executable))
    # Validate everything before writing; an existing commissioned YAML is never overwritten.
    if not config.exists():
        config.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, config)
    text = ENV_MARKER + ' no robot starts when sourced.\n'
    for key, value in (('OMX_ROBOT_ID', robot_id), ('OMX_CONFIG_PATH', str(config)), ('OMX_PYTHON', str(executable))):
        text += key + '=' + shlex.quote(value) + '\n'
    replace_with_backup(registration, text)
    registration.chmod(0o600)
    bashrc = home / '.bashrc'
    previous = bashrc.read_text() if bashrc.exists() else ''
    previous = re.sub(re.escape(BEGIN) + r'.*?' + re.escape(END) + r'\n?', '', previous, flags=re.S)
    previous = ''.join(line for line in previous.splitlines(keepends=True)
                       if not ('Logistics_OMX shell integration' in line and 'omx_shell.sh' in line and line.lstrip().startswith(('source ', '. '))))
    script = shlex.quote(str(root / 'scripts/omx_shell.sh'))
    block = BEGIN + '\nif [ -f ' + script + ' ]; then source ' + script + '; fi\n' + END + '\n'
    replace_with_backup(bashrc, previous.rstrip('\n') + '\n\n' + block)
    print('Registered:', robot_id)
    print('YAML:', config)
    print('Python:', executable)
    print('No robot/service started. Run: source ~/.bashrc')
    return {'robot_id': robot_id, 'config': config, 'python': executable, 'registration': registration}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--robot-id', default='auto', choices=('auto', 'omx1', 'omx2', 'omx3', 'omx4'))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--python')
    args = parser.parse_args(argv)
    try: install(args.robot_id, args.config, args.python)
    except (OSError, ValueError) as exc:
        print('[ERROR]', exc, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__': raise SystemExit(main())
