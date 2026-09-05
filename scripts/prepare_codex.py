"""Register the prepared candidate exclusively in the isolated Codex profile."""
import json
import os
from pathlib import Path
import subprocess
from cortex_package import private_dir, validate, payload_digest

owner=Path(os.environ['CORTEX_DEV_OWNER_HOME'])
dev=owner/'.cortex-dev'
if Path.home()!=dev or Path(os.environ['CODEX_HOME'])!=dev/'.codex' or owner.resolve()!=owner:
    raise SystemExit('Refusing a non-isolated Codex profile.')
private_dir(dev);private_dir(dev/'.codex')
receipt_path=dev/'.codex/cortex-candidate.json'
if receipt_path.is_symlink(): raise SystemExit('Unsafe candidate receipt.')
receipt=json.loads(receipt_path.read_text())
root=Path(receipt['candidate'])
if root.parent!=dev/'candidates' or root.name!=receipt['version']:
    raise SystemExit('Unexpected candidate location.')
validate(root/'plugins/cortex')

def run(args, allow_failure=False):
    result=subprocess.run(['codex',*args],capture_output=True,text=True)
    if result.returncode and not allow_failure:
        raise SystemExit('Isolated Codex registration failed: '+' '.join(args[:3]))
    return result

# Remove only this plugin and its marketplace in the isolated profile.
run(['plugin','remove','cortex@cortex','--json'],True)
run(['plugin','marketplace','remove','cortex','--json'],True)
run(['plugin','marketplace','add',str(root),'--json'])
run(['plugin','add','cortex@cortex','--json'])
# Verify actual installed bytes, not just the staging copy.
cache=dev/'.codex/plugins/cache/cortex/cortex'/receipt['version']
validate(cache)
if payload_digest(cache)!=receipt['digest']:
    raise SystemExit('Installed candidate differs from source.')
# Native Agent v2 discovers standalone profiles here and attaches their developer
# instructions at spawn. The plugin cache alone is not a native role registry.
# This preparation is explicitly restricted above to the disposable dev home.
agent_dir=dev/'.codex/agents'
private_dir(agent_dir)
for source in (cache/'agents').glob('*.toml'):
    target=agent_dir/source.name
    if target.is_symlink():
        raise SystemExit('Unsafe isolated agent profile target.')
    target.write_bytes(source.read_bytes())
    target.chmod(0o600)
print('Cortex isolated candidate installed and verified: '+receipt['version'])
