# -*- coding: utf-8 -*-
"""提示词加载器：从 prompts.toml 读取文案（Python 3.11 内置 tomllib，无需第三方依赖）"""
import tomllib
from pathlib import Path

_PROMPT_FILE = Path(__file__).with_name("prompts.toml")


def load_prompts() -> dict[str, str]:
    """读取并校验必填的提示词键，缺键直接在启动时暴露，而不是运行到一半才报错"""
    with open(_PROMPT_FILE, "rb") as f:
        data = tomllib.load(f)
    required = {"supervisor", "chat", "agent", "knowledge"}
    missing = required - data.keys()
    if missing:
        raise RuntimeError(f"prompts.toml 缺少提示词：{sorted(missing)}")
    return data


PROMPTS = load_prompts()
