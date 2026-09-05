"""Content stamping and isolated candidate construction (development only)."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import tomllib
import re
from generate_agent_profiles import check as check_agent_profiles

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT/'plugins/cortex'
BASE = '1.15.6'
def payload_digest(plugin):
    digest=hashlib.sha256()
    for path in sorted(plugin.rglob('*')):
        if path.is_symlink():
            raise ValueError('symlink in payload')
        if not path.is_file():
            continue
        if '__pycache__' in path.parts or path.suffix in {'.pyc','.pyo'}:
            raise ValueError('bytecode in payload')
        relative=path.relative_to(plugin).as_posix()
        body=path.read_bytes()
        if relative=='.codex-plugin/plugin.json':
            value=json.loads(body); value['version']=BASE
            body=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        digest.update(relative.encode()+b'\0'+str(len(body)).encode()+b'\0'+body)
    return digest.hexdigest()


def stamp():
    path=PLUGIN/'.codex-plugin/plugin.json'
    value=json.loads(path.read_text())
    value['version']=BASE+'+codex.sha256.'+payload_digest(PLUGIN)[:16]
    path.write_text(json.dumps(value,indent=2)+'\n')
    return value['version']


def validate(plugin=PLUGIN):
    check_agent_profiles(plugin)
    value=json.loads((plugin/'.codex-plugin/plugin.json').read_text())
    if value['name']!='cortex' or value['version']!=BASE+'+codex.sha256.'+payload_digest(plugin)[:16]:
        raise ValueError('invalid package identity or stale cache stamp')
    if 'hooks' in value or (plugin/'hooks').exists():
        raise ValueError('unexpected hooks')
    profiles=json.loads((plugin/'profiles.json').read_text())['profiles']
    agents=list((plugin/'agents').glob('*.toml'))
    if len(profiles)!=22 or len(agents)!=22 or len({p['name'] for p in profiles})!=22:
        raise ValueError('profile count')
    for profile in profiles:
        parsed=tomllib.loads((plugin/'agents'/profile['filename']).read_text())
        if parsed['name']!=profile['name'] or not parsed.get('developer_instructions'):
            raise ValueError('profile definition')
        instructions=parsed['developer_instructions']
        if '## Attached worker guidance' in instructions or '../skills/' in instructions or '.codex/plugins/' in instructions:
            raise ValueError('profiles must not contain copied skill bundles or installation paths')
        if 'cortex:context-compaction' not in instructions:
            raise ValueError('missing recovery skill requirement')
        if ('Call the live draft creator once' not in instructions or
                'only Cortex project file you may write' not in instructions or
                'Never inspect the Cortex database or final task files directly.' not in instructions):
            raise ValueError('missing project draft and final-task-file boundaries')
        if ('If the host exposes deferred\n   discovery only one tool at a time' not in instructions or
                'Do not use broad keyword searches, dump the whole' not in instructions):
            raise ValueError('missing deferred tool discovery requirement')
    declared=json.loads((plugin/'runtime-payload.json').read_text())['files']
    actual=sorted(p.relative_to(plugin).as_posix() for p in (plugin/'scripts').rglob('*.py'))
    if declared!=actual:
        raise ValueError('runtime payload differs from manifest')
    templates={path.stem for path in (plugin/'report-templates').glob('*.md')}
    if templates!={'general','planning','investigation','implementation','verification','documentation','synthesis','pipeline'}:
        raise ValueError('draft template set')
    mcp=json.loads((plugin/'.mcp.json').read_text())['mcpServers']['cortex']
    if mcp['command']!='python3' or mcp['args']!=['-B','./scripts/cortex.py']:
        raise ValueError('MCP entry point')
    return value['version']


def private_dir(path):
    current=Path(path.anchor)
    for part in path.parts[1:]:
        current/=part
        if current.is_symlink():
            raise ValueError('unsafe candidate directory')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not path.is_dir() or path.stat().st_uid!=os.getuid():
        raise ValueError('unsafe candidate directory')


def prepare():
    # Caller is the sole live-dev entry point, and points HOME at the isolated target.
    owner=Path(os.environ['CORTEX_DEV_OWNER_HOME'])
    dev=owner/'.cortex-dev'
    if not owner.is_absolute() or owner.resolve()!=owner or Path.home()!=dev or Path(os.environ['CODEX_HOME'])!=dev/'.codex':
        raise ValueError('candidate must use exact isolated HOME and CODEX_HOME')
    private_dir(dev); private_dir(dev/'.codex')
    version=stamp(); validate()
    candidates=dev/'candidates'; private_dir(candidates)
    target=candidates/version
    if target.exists():
        validate(target/'plugins/cortex')
        if payload_digest(target/'plugins/cortex')!=payload_digest(PLUGIN):
            raise ValueError('candidate content mismatch')
    else:
        temp=Path(tempfile.mkdtemp(prefix='.staging-',dir=candidates))
        try:
            shutil.copytree(PLUGIN,temp/'plugins/cortex')
            (temp/'.agents/plugins').mkdir(parents=True)
            shutil.copyfile(ROOT/'.agents/plugins/marketplace.json',temp/'.agents/plugins/marketplace.json')
            validate(temp/'plugins/cortex')
            os.replace(temp,target)
        finally:
            if temp.exists(): shutil.rmtree(temp)
    receipt=dict(version=version,digest=payload_digest(PLUGIN),candidate=str(target),plugin=str(target/'plugins/cortex'))
    file=dev/'.codex/cortex-candidate.json'
    fd,temp=tempfile.mkstemp(dir=file.parent)
    with os.fdopen(fd,'w') as stream:
        json.dump(receipt,stream);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,file)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['stamp','validate','prepare']);args=parser.parse_args()
    try:
        print(json.dumps(prepare()) if args.action=='prepare' else stamp() if args.action=='stamp' else validate())
    except (OSError,ValueError,KeyError):
        raise SystemExit('Cortex package operation failed; check package or isolated directory configuration.') from None
