"""MCP Dev Entry — 供 fastmcp dev 测试使用。"""

import sys
sys.path.insert(0, '.')

from src.config import get_config
from src.auth import AuthManager
from src.storage import Storage
from src.memory import MemoryStore
from src.main import _get_user_id
from src.mcp_server import create_server

config = get_config()
auth = AuthManager(config)
storage = Storage(config)
memory = MemoryStore(config.memory_dir, db_getter=lambda: storage.db)
user_id = _get_user_id(auth)

mcp = create_server(config, auth, storage, memory, user_id)
