"""
项目配置文件 - 跨平台版本
"""
import os
from pathlib import Path

# ============ LLM & Embedding 配置 ============
GRAPHRAG_API_KEY = "sk-dDxQNxBFqkJmzcYEDCzk2J6DF8twJM23rffmgpwFRb2P2WhJ"
GRAPHRAG_API_BASE = "https://reelxai.com/v1"
GRAPHRAG_MODEL = "gpt-4.1-nano"
EMBEDDING_MODEL = "text-embedding-3-small"

# ============ MinerU OCR 配置 ============
MINERU_TOKEN = "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiIyNDUwNjM2OSIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc3NjM0NzI0MSwiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiMTc2ODk5ODM5ODUiLCJvcGVuSWQiOm51bGwsInV1aWQiOiIzZGJiMjFhZi0wM2M0LTQzZmMtYjMyMy02MzZlNDRiMzQzZGYiLCJlbWFpbCI6IiIsImV4cCI6MTc4NDEyMzI0MX0.TG4DVQuXJ2YGHACG9R4MDjXwx5jElYQjxnr19Yz2n1JoSI-R5ze2kfI0d3Yg75Ph3I9mPZnfjSgQJ-2K-_fJyw"
MINERU_API_BASE = "https://mineru.net/api/v4"

# ============ 目录配置（跨平台自适应） ============
# PROJECT_ROOT = src的父目录（即项目根目录）
# 使用 resolve() 确保是绝对路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
IMAGE_DIR = DATA_DIR / "images"
TABLE_DIR = DATA_DIR / "tables"
TEMP_DIR = DATA_DIR / "temp"
SUMMARY_DIR = DATA_DIR / "summary"
GRAPH_DIR = DATA_DIR / "graph"
TABLES_CSV_DIR = DATA_DIR / "tables_csv"

# 验证数据目录是否存在
def _check_data_dir():
    """检查数据目录，如果不存在给出提示"""
    if not DATA_DIR.exists():
        print(f"警告: 数据目录不存在: {DATA_DIR}")
        print(f"请确保将数据文件夹放在正确位置:")
        print(f"  {PROJECT_ROOT}/data/")
        return False
    return True

# 运行时检查
_check_data_dir()

# ============ 处理配置 ============
BATCH_SIZE = 50
MAX_WORKERS = 10
POLLING_INTERVAL = 10

# ============ Neo4j 配置 ============
NEO4J_URI = "neo4j://127.0.0.1:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "12345678"
