"""
Configuration management for the outreach tool.
Loads/saves config.json with defaults for profile, credentials, and settings.
"""

import copy
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "gmail_address": "",
    "gmail_app_password": "",
    "openai_api_key": "",
    "send_delay_seconds": 60,
    "tracking_base_url": "",
    "profile": {
        "name": "",
        "college": "",
        "branch": "",
        "year": "",
        "cgpa": "",
        "skills": "",
        "github": "",
        "linkedin": "",
        "bio": ""
    },
    "resume_path": "uploads/resume.pdf",
    "resume_parsed": {}
}

ENV_VAR_MAP = {
    "gmail_address": "GMAIL_ADDRESS",
    "gmail_app_password": "GMAIL_APP_PASSWORD",
    "openai_api_key": "OPENAI_API_KEY",
    "send_delay_seconds": "SEND_DELAY_SECONDS",
    "tracking_base_url": "TRACKING_BASE_URL",
}


def load_config():
    """Load config from disk. Create with defaults if missing."""
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        config = DEFAULT_CONFIG.copy()
    else:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)

    # Merge with defaults to handle missing keys after upgrades
    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged.update(config)
    if "profile" in config:
        merged_profile = DEFAULT_CONFIG["profile"].copy()
        merged_profile.update(config["profile"])
        merged["profile"] = merged_profile

    # Environment variables take precedence over the local config file.
    for config_key, env_name in ENV_VAR_MAP.items():
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            if config_key == "send_delay_seconds":
                try:
                    merged[config_key] = max(20, min(90, int(env_value)))
                except ValueError:
                    pass
            else:
                merged[config_key] = env_value

    return merged


def save_config(config):
    """Persist config to disk."""
    payload = copy.deepcopy(config)

    # Do not write secret values back to disk if Render env vars are set.
    for config_key, env_name in ENV_VAR_MAP.items():
        if os.getenv(env_name, "").strip():
            payload.pop(config_key, None)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def is_profile_complete(config):
    """Check if the minimum profile fields are filled."""
    profile = config.get("profile", {})
    required = ["name", "college", "branch", "year", "skills", "bio"]
    return all(profile.get(field, "").strip() for field in required)


def is_settings_complete(config):
    """Check if Gmail + OpenAI credentials are configured."""
    return bool(
        config.get("gmail_address", "").strip()
        and config.get("gmail_app_password", "").strip()
        and config.get("openai_api_key", "").strip()
    )
