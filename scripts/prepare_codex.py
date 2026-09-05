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
# Marketplace installation is the complete preparation; no personal agents are registered.
print('Cortex isolated candidate installed and verified: '+receipt['version'])
