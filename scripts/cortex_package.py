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
import stat
from generate_agent_profiles import check as check_agent_profiles
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugins.cortex.scripts.cortex_runtime.runtime.state import dependency_identity

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT/'plugins/cortex'
BASE = '1.15.7'


def _assert_no_symlink_tree(path, *, label='candidate'):
    """Reject symlinks in every existing path component and descendant."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError(f'{label} path must be absolute')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f'unsafe symlink in {label} path')
        if current.exists() and not current.is_dir() and current != path:
            raise ValueError(f'unsafe non-directory in {label} path')
    if not path.exists():
        return
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f'{label} root must be a real directory')
    for parent, directories, files in os.walk(path, topdown=True, followlinks=False):
        for name in [*directories, *files]:
            entry = Path(parent) / name
            if entry.is_symlink():
                raise ValueError(f'unsafe symlink in {label} tree')


def payload_digest(plugin):
    digest=hashlib.sha256()
    _assert_no_symlink_tree(plugin, label='payload')
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


def dependency_lock_entries(plugin=PLUGIN):
    lock = plugin/'requirements.lock'
    if not lock.is_file():
        raise ValueError('missing hash-locked gateway dependency manifest')
    entries=[]
    for raw in lock.read_text().splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or line.startswith('--'):
            continue
        fields=line.split()
        if not fields or '==' not in fields[0]:
            raise ValueError('invalid hash-locked dependency entry')
        if len(fields)<2:
            raise ValueError('dependency entry must contain valid SHA-256 hashes')
        hashes=[field.split(':',1)[1] for field in fields[1:] if field.startswith('--hash=sha256:')]
        if len(hashes)<1 or any(len(value)!=64 or any(ch not in '0123456789abcdef' for ch in value) for value in hashes):
            raise ValueError('dependency entry must contain valid SHA-256 hashes')
        if any(not field.startswith('--hash=sha256:') for field in fields[1:]):
            raise ValueError('dependency entry contains an unsupported option')
        name, version=fields[0].split('==',1)
        entries.append((name, version, tuple(hashes)))
    if len({name for name, _, _ in entries}) != len(entries):
        raise ValueError('hash-locked dependency manifest contains duplicate names')
    return entries


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
    requirements = plugin/'requirements.txt'
    if not requirements.is_file():
        raise ValueError('missing gateway dependency manifest')
    dependency_lines = [line.strip() for line in requirements.read_text().splitlines()
                        if line.strip() and not line.lstrip().startswith('#')]
    if not dependency_lines or any('==' not in line for line in dependency_lines):
        raise ValueError('gateway dependencies must be exactly pinned')
    dependencies = dict(line.split('==', 1) for line in dependency_lines)
    if dependencies.get('aiohttp') != '3.14.3' or dependencies.get('zstandard') != '0.25.0':
        raise ValueError('gateway direct dependencies changed without a package review')
    if len(dependencies) != len(dependency_lines):
        raise ValueError('gateway dependency manifest contains duplicate names')
    locked = dependency_lock_entries(plugin)
    if {name: version for name, version, _ in locked} != dependencies:
        raise ValueError('hash-locked dependencies differ from the pinned manifest')
    hook_file=plugin/'hooks/hooks.json'
    if hook_file.exists():
        hook_config=json.loads(hook_file.read_text())
        events=hook_config.get('hooks',{})
        allowed={'UserPromptSubmit','SessionStart','SubagentStart','PreCompact',
                 'PostCompact','PostToolUse','PreToolUse','SubagentStop','Stop',
                 'Interrupt','SessionEnd'}
        if set(events)!=allowed:
            raise ValueError('unexpected lifecycle hook catalogue')
        for groups in events.values():
            if not isinstance(groups,list) or not groups:
                raise ValueError('invalid lifecycle hook groups')
            for group in groups:
                handlers=group.get('hooks',[])
                if not handlers:
                    raise ValueError('empty lifecycle hook group')
                for handler in handlers:
                    if (handler.get('type')!='command' or
                            handler.get('command')!='python3 -B "${PLUGIN_ROOT}/scripts/cortex_hook.py"' or
                            not 0<handler.get('timeout',0)<=3 or
                            handler.get('async',False)):
                        raise ValueError('hooks must use the packaged Python handler')
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
        # Generated byte equality is checked above. Validate the shared role
        # structure, not incidental wording or a repeated tool bootstrap.
        if not all(section in instructions for section in (
                '## Role and responsibility','## Specialist workflow',
                '## Report class selection')):
            raise ValueError('incomplete specialist structure')
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
    if set(mcp.get('env_vars', [])) != {'CORTEX_OBSERVATION_DIR', 'CORTEX_DEPENDENCY_DIR'}:
        raise ValueError('MCP environment contract')
    return value['version']


def private_dir(path):
    current=Path(path.anchor)
    for part in path.parts[1:]:
        current/=part
        if current.is_symlink():
            raise ValueError('unsafe candidate directory')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()):
        raise ValueError('unsafe candidate directory')
    # mkdir(mode=...) does not tighten an existing directory.  These are
    # isolated candidate roots, so repair a permissive mode before any
    # receipt, dependency, or Marketplace state is written beneath them.
    os.chmod(path, 0o700)
    info = path.lstat()
    if info.st_mode & 0o077:
        raise ValueError('candidate directory must be owner-only')


def prepare():
    # Caller is the sole live-dev entry point, and points HOME at the isolated target.
    owner=Path(os.environ['CORTEX_DEV_OWNER_HOME'])
    dev=owner/'.cortex-dev'
    if not owner.is_absolute() or owner.resolve()!=owner or Path.home()!=dev or Path(os.environ['CODEX_HOME'])!=dev/'.codex':
        raise ValueError('candidate must use exact isolated HOME and CODEX_HOME')
    private_dir(dev); private_dir(dev/'.codex')
    version=stamp(); validate()
    dependency_target=Path(os.environ.get('CORTEX_DEPENDENCY_DIR',''))
    manifest_digest, dependency_bytes_digest, dependency_digest = dependency_identity(PLUGIN, dependency_target, required=True)
    candidates=dev/'candidates'; private_dir(candidates)
    target=candidates/version
    if target.exists():
        _assert_no_symlink_tree(target, label='candidate')
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
    receipt=dict(version=version,digest=payload_digest(PLUGIN),candidate=str(target),plugin=str(target/'plugins/cortex'),dependency_manifest_digest=manifest_digest,dependency_bytes_digest=dependency_bytes_digest,dependency_digest=dependency_digest)
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
