"""Read only the AgentRouter credential from a private local file."""

import os
from pathlib import Path


def load_tokens(root: Path, mode: str = "shared") -> None:
    """Validate every required key before loading them, without evaluating or printing values."""
    if mode not in {"shared", "per_firm"}:
        raise ValueError("Credential mode must be shared or per_firm")
    names = (
        [f"AGENTROUTER_API_KEY_FIRM_{firm}" for firm in range(3)]
        if mode == "per_firm"
        else ["AGENTROUTER_API_KEY"]
    )
    path = root / ".env"
    content = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    selected: dict[str, str] = {}
    for name in names:
        if os.environ.get(name):
            selected[name] = os.environ[name]
            continue
        values = []
        for line in content.splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == name:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values.append(value)
        if len(values) != 1 or not values[0] or values[0].startswith(("YOUR_", "PASTE_")):
            raise ValueError(f"Set exactly one nonempty {name} in the project's ignored .env file")
        selected[name] = values[0]
    os.environ.update(selected)


def load_token(root: Path) -> None:
    """Keep existing single-key launchers compatible."""
    load_tokens(root)
