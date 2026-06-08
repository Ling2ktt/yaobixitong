"""
配置加载器 - 支持 YAML + 环境变量
"""

import os
import yaml
from typing import Dict, Any
from pathlib import Path
from dotenv import load_dotenv
from loguru import logger


def load_config(config_path: str = "config/system.yaml") -> Dict[str, Any]:
    """
    加载配置文件，支持环境变量替换
    
    替换规则：
    - ${VAR_NAME} -> 环境变量值
    - ${VAR_NAME:default} -> 环境变量值，不存在用默认值
    """
    # 加载 .env 文件
    env_path = Path('.env')
    if env_path.exists():
        load_dotenv(env_path)
        logger.debug("[Config] 已加载 .env 文件")
    
    # 读取YAML
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 递归替换环境变量
    config = _replace_env_vars(config)
    
    logger.info("[Config] 配置加载完成: {}", config_path)
    return config


def _replace_env_vars(obj: Any) -> Any:
    """递归替换环境变量"""
    if isinstance(obj, dict):
        return {k: _replace_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_replace_env_vars(item) for item in obj]
    elif isinstance(obj, str):
        return _replace_env_in_string(obj)
    else:
        return obj


def _replace_env_in_string(value: str) -> str:
    """替换字符串中的环境变量，支持 ${VAR} 和 ${VAR:default} 语法"""
    import re

    pattern = r'\$\{([^}]+)\}'

    def replace(match):
        content = match.group(1)
        if ':' in content:
            var_name, default = content.split(':', 1)
            return os.environ.get(var_name.strip(), default)
        else:
            return os.environ.get(content.strip(), '')

    return re.sub(pattern, replace, value)


def save_config(config: Dict[str, Any], path: str):
    """保存配置到文件"""
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)
    logger.info("[Config] 配置已保存: {}", path)
