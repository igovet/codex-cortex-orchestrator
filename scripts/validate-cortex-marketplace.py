#!/usr/bin/env python3
"""Validate the current seven-operation package."""
import json
import sys
from cortex_package import PLUGIN, ROOT, validate

version=validate()
sys.path.insert(0,str(PLUGIN/'scripts'))
from cortex_runtime.contracts import TOOLS
assert {t['name'] for t in TOOLS}=={'create_task','set_governance','create_draft','read_draft','write_report','list_reports','read_report'}
market=json.loads((ROOT/'.agents/plugins/marketplace.json').read_text())
assert market['name']=='cortex'
assert market['plugins'][0]['source']['path']=='./plugins/cortex'
print('Package validated: '+version+'; 7 tools; 22 profiles')
