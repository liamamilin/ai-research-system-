#!/usr/bin/env python3
"""Install the generic job dispatcher for the current project's Web settings."""
from pathlib import Path
import json
import os
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv(ROOT / '.env')
from core.schedule_host import install
from core.schedule_runner import Runtime
from web.settings import get_settings
if __name__ == '__main__':
    result = install(Runtime.from_settings(get_settings()))
    print(json.dumps({'online': result['online'], 'label': result['label'], 'detail': result['detail']}, ensure_ascii=False))
