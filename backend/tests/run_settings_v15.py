"""Run fake-upstream tests without loading credentials from local files."""
import os
from pathlib import Path
import sys
import tempfile
root = Path(__file__).resolve().parents[2]
os.chdir(root)
sys.path.insert(0, str(root))
for key in ('MCD_MCP_URL', 'MCD_MCP_TOKEN', 'MCD_MCP_CONTEXT', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'LLM_API_KEY'):
    os.environ[key] = ''
os.environ['LLM_PROVIDER'] = 'deepseek'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
original_exists = Path.exists
hidden = {root / '.env', root / 'backend' / '.env', root / 'backend' / 'data' / 'settings.local.json'}
Path.exists = lambda self: False if self in hidden else original_exists(self)
# Redirect both default runtime stores before importing the application.
# Existing saved user baskets/orders are never mutated by the test process.
(root / '.qa_tmp').mkdir(exist_ok=True)
runtime = Path(tempfile.mkdtemp(prefix='settings-v15-runtime-', dir=root / '.qa_tmp'))
import backend.config as config
config.DATA_DIR = runtime
from backend.services.order_repository import OrderRepository
OrderRepository.__init__.__defaults__ = (runtime / 'orders.sqlite3',)
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
