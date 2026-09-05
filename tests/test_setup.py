"""Native profile setup must cover ordinary installs, not dev-only preparation."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
PLUGIN=ROOT/'plugins/cortex'
spec=importlib.util.spec_from_file_location('cortex_setup',PLUGIN/'scripts/cortex_setup.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)


def test_plain_plugin_install_is_missing_profiles_until_explicit_setup(tmp_path):
    home=tmp_path/'codex'
    before=setup.register(PLUGIN,home)
    assert len(before['missing'])==22 and not home.exists()
    assert setup.register(PLUGIN,home,True)['installed']
    result=setup.register(PLUGIN,home)
    assert result['missing']==result['stale']==result['conflicts']==[]
    for source in (PLUGIN/'agents').glob('*.toml'):
        target=home/'agents'/source.name
        assert target.read_bytes()==source.read_bytes()
        assert target.stat().st_mode&0o077==0


def test_conflicting_user_profile_prevents_all_changes(tmp_path):
    home=tmp_path/'codex';agents=home/'agents';agents.mkdir(parents=True)
    target=agents/'backend-dev.toml';target.write_text('user profile')
    result=setup.register(PLUGIN,home,True)
    assert result['conflicts']==['backend-dev.toml'] and not result['installed']
    assert list(agents.iterdir())==[target] and target.read_text()=='user profile'
    assert not (home/'cortex-native-profiles.json').exists()


def test_managed_profiles_update_without_touching_unrelated_files(tmp_path):
    home=tmp_path/'codex';setup.register(PLUGIN,home,True)
    target=home/'agents/backend-dev.toml';target.write_text('previous managed version')
    receipt=home/'cortex-native-profiles.json';data=json.loads(receipt.read_text())
    data[target.name]=setup.sha(target.read_bytes());receipt.write_text(json.dumps(data))
    other=home/'agents/user.toml';other.write_text('user-owned')
    assert setup.register(PLUGIN,home)['stale']==['backend-dev.toml']
    assert setup.register(PLUGIN,home,True)['installed']
    assert other.read_text()=='user-owned'
    assert target.read_bytes()==(PLUGIN/'agents/backend-dev.toml').read_bytes()


def test_symlinked_registry_is_rejected(tmp_path):
    home=tmp_path/'codex';home.mkdir()
    outside=tmp_path/'outside';outside.mkdir()
    (home/'agents').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):
        setup.register(PLUGIN,home,True)
    assert not list(outside.iterdir())
