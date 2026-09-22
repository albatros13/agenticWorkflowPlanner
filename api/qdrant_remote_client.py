import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient
import logging
try:
    from importlib.metadata import version
    print("Qdrant client version:", version("qdrant-client"))
except Exception:
    print("Qdrant client version: unknown")

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

QDRANT_URL_KG = os.getenv("QDRANT_URL_KG")
QDRANT_API_KEY_KG = os.getenv("QDRANT_API_KEY_KG")

if not (QDRANT_URL and QDRANT_API_KEY):
    logger.error("❌ Configuration error: QDRANT_URL or QDRANT_API_KEY not found in environment or .env file.")
    raise RuntimeError(
        "Configuration error: QDRANT_URL or QDRANT_API_KEY not found. "
        "Please set it as an environment variable or in your .env file."
    )

if not (QDRANT_URL_KG and QDRANT_API_KEY_KG):
    logger.warning("⚠️ QDRANT_URL_KG or QDRANT_API_KEY_KG not found. Knowledge Graph features might fail.")

_client = None
_client_kg = None

def get_remote_client():
    global _client
    if _client is None:
        _client = QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY,
            https=True
        )
        logger.info("✅ Qdrant client created (main)")
    return _client

def get_kg_client():
    global _client_kg
    if _client_kg is None:
        if not (QDRANT_URL_KG and QDRANT_API_KEY_KG):
            raise RuntimeError("QDRANT_URL_KG or QDRANT_API_KEY_KG not configured.")
        _client_kg = QdrantClient(
            url=QDRANT_URL_KG,
            api_key=QDRANT_API_KEY_KG,
            https=True
        )
        logger.info("✅ Qdrant KG client created")
    return _client_kg
