#!/usr/bin/env bash
set -Eeuo pipefail
: "${VIRTUAL_ENV:?라즈베리 가상환경을 먼저 활성화하세요.}"
ROOT="${LEROBOT_ROOT:-$HOME/il_ws/src/lerobot}"
REPORT="${1:-$HOME/omx_pi_environment}"
mkdir -p "$REPORT"
python - "$ROOT" "$REPORT" <<'ENV'
import hashlib,importlib.metadata as m,json,pathlib,platform,subprocess,sys
root=pathlib.Path(sys.argv[1]);out=pathlib.Path(sys.argv[2])
def git(*args):
 p=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True);return p.stdout.strip() if p.returncode==0 else 'unavailable'
packages={d.metadata['Name']:d.version for d in m.distributions() if d.metadata.get('Name')}
files=list((root/'src/lerobot').rglob('*.py'))+list(root.glob('omx_*'))+list(root.glob('run_ab_orders.sh'))
hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
import lerobot
report={'platform':platform.platform(),'machine':platform.machine(),'python':sys.version,'python_executable':sys.executable,'lerobot_import_path':lerobot.__file__,'git_head':git('rev-parse','HEAD'),'git_status':git('status','--short'),'packages':packages,'source_sha256':hashes}
(out/'environment.json').write_text(json.dumps(report,indent=2)+'\n')
(out/'installed_versions.txt').write_text('\n'.join(f'{k}=={v}' for k,v in sorted(packages.items()))+'\n')
print('보고서:',out)
ENV
