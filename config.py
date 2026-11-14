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
VISION_MODEL = os.environ.get("VISION_MODEL") or LLM_MODEL
LIGHT_MODEL = os.environ.get("LIGHT_MODEL", "anthropic:claude-haiku-5-5")  # cheap subagent (stakes lookup)  # reads Matterport interior images
# Matterport Basic-auth pair: accepts either naming (the provided .env uses API_KEY / API_TOKEN).
MATTERPORT_ID = os.environ.get("MATTERPORT_TOKEN_ID") or os.environ.get("MATTERPORT_API_KEY", "")
MATTERPORT_SECRET = os.environ.get("MATTERPORT_TOKEN_SECRET") or os.environ.get("MATTERPORT_API_TOKEN", "")
# Vexcel runs MOCK unless a key is set and MOCK isn't forced.
MOCK = bool(os.environ.get("MOCK")) or not VEXCEL_KEY
