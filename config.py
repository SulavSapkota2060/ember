import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "out"
OUT_DIR.mkdir(exist_ok=True)
KIT_DIR = ROOT / os.environ.get("COSTAR_KIT_DIR", "../data/costar-hackathon-kit")
VEXCEL_KEY = os.environ.get("VEXCEL_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
# Vexcel runs MOCK unless a key is set and MOCK isn't forced.
MOCK = bool(os.environ.get("MOCK")) or not VEXCEL_KEY
