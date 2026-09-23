"""Detection and optional installation of local agent CLIs."""

import logging
import os
import shutil
import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional

from core import config

logger = logging.getLogger("CLIManager")


@dataclass(frozen=True)
class CliSpec:
    name: str
    executable: str
    npm_package: Optional[str]
    install_env: Optional[str]


SPECS = {
    "agy": CliSpec("agy", "agy", None, "AGY_INSTALL_COMMAND"),
    "codex": CliSpec("codex", "codex", "@openai/codex", None),
    "opencode": CliSpec("opencode", "opencode", "opencode-ai", None),
    "claude": CliSpec("claude", "claude", "@anthropic-ai/claude-code", None),
}


def _configured_executable(provider: str) -> str:
    return os.getenv(f"{provider.upper()}_BIN_PATH", SPECS[provider].executable).strip()


def ensure_cli(provider: str) -> str:
    """Return a usable executable, optionally installing it when explicitly enabled."""
    spec = SPECS.get(provider)
    if spec is None:
        raise ValueError(f"Unsupported CLI provider: {provider}")

    executable = _configured_executable(provider)
    resolved = shutil.which(executable)
    if resolved:
        return resolved
    if not config.AUTO_INSTALL_CLI:
        raise FileNotFoundError(
            f"{provider} CLI not found as '{executable}'. "
            "Set AUTO_INSTALL_CLI=true or configure its *_BIN_PATH."
        )

    if spec.npm_package:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        command = [npm, "install", "--global", spec.npm_package]
    else:
        install_command = os.getenv(spec.install_env or "", "").strip()
        if not install_command:
            raise FileNotFoundError(
                f"{provider} CLI is missing and no {spec.install_env} was configured"
            )
        command = shlex.split(install_command, posix=os.name != "nt")

    logger.info("Installing configured %s CLI", provider)
    result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to install {provider} CLI (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )
    resolved = shutil.which(executable)
    if not resolved:
        raise RuntimeError(f"{provider} CLI installation completed but '{executable}' is still unavailable")
    return resolved
