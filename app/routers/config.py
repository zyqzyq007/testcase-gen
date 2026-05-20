"""
Runtime LLM configuration management.

GET  /api/config/llm  → return the live config
PUT  /api/config/llm  → partial update; persists changed fields to .env so they
                        survive process restarts
POST /api/config/llm/test → fire a small chat completion against the current
                            config to validate connectivity
"""
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dotenv import find_dotenv

from app.services import llm_service

router = APIRouter(prefix="/api/config", tags=["config"])


class LLMConfigResponse(BaseModel):
    api_key: str
    base_url: str
    model: str
    timeout: float


class LLMConfigUpdate(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    timeout: Optional[float] = None


# Map runtime config keys → .env variable names
_ENV_KEY_MAP = {
    "api_key": "OPENAI_API_KEY",
    "base_url": "OPENAI_BASE_URL",
    "model": "OPENAI_MODEL",
    "timeout": "OPENAI_TIMEOUT",
}


def _persist_to_env(changes: dict) -> Optional[str]:
    """
    Write the given config changes into .env using an in-place rewrite
    (no temp file + rename), so it works when .env is bind-mounted as a single
    file in Docker. Returns the path that was written, or None if .env could
    not be located or created.
    """
    dotenv_path = find_dotenv(usecwd=True)
    if not dotenv_path:
        dotenv_path = os.path.join(os.getcwd(), ".env")
        if not os.path.exists(dotenv_path):
            try:
                open(dotenv_path, "a").close()
            except OSError:
                return None

    updates: dict[str, str] = {}
    for k, v in changes.items():
        env_name = _ENV_KEY_MAP.get(k)
        if env_name is None or v is None:
            continue
        updates[env_name] = str(v)
        os.environ[env_name] = str(v)
    if not updates:
        return dotenv_path

    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    new_lines = []
    seen = set()
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            seen.add(key)
        else:
            new_lines.append(line)

    # Append keys that were not previously present
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
    for key, val in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={val}\n")

    with open(dotenv_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    return dotenv_path


@router.get("/llm", response_model=LLMConfigResponse)
async def get_llm_config():
    cfg = llm_service.get_config()
    return LLMConfigResponse(**cfg)


@router.put("/llm", response_model=LLMConfigResponse)
async def update_llm_config(payload: LLMConfigUpdate):
    changes = payload.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")

    cfg = llm_service.update_config(**changes)
    _persist_to_env(changes)
    return LLMConfigResponse(**cfg)


@router.post("/llm/test")
async def test_llm_config():
    """Send a minimal probe request to verify the current config works."""
    if llm_service._is_mock_mode():
        raise HTTPException(
            status_code=400,
            detail="API key not set (currently in mock mode)",
        )
    try:
        client = llm_service._make_client()
        cfg = llm_service.get_config()
        resp = await client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=4,
            temperature=0,
            stream=False,
        )
        content = resp.choices[0].message.content if resp.choices else ""
        return {
            "ok": True,
            "model": cfg["model"],
            "base_url": cfg["base_url"],
            "reply": content,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
