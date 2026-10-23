"""Check the configured model catalogue without sending any inference request."""

import argparse
import json
from pathlib import Path

from auditors_paradox.adapters.model_calls import ProviderBackend, provider_error
from auditors_paradox.config import StudyConfig, load_config


def check(cfg: StudyConfig) -> int:
    """Show only the HTTP status and whether the requested model is listed."""
    if cfg.agent.backend != "agentrouter" or cfg.agent.api_protocol != "chat":
        raise ValueError("This check requires the AgentRouter chat configuration")
    result = {"endpoint": cfg.agent.base_url, "requested_model": cfg.agent.model}
    try:
        catalogue = ProviderBackend(cfg.agent).client.models.list()
        visible = any(item.id == cfg.agent.model for item in catalogue.data)
    except Exception as exc:
        error = provider_error(exc)
        print(
            json.dumps(
                {
                    **result,
                    "status": "failed",
                    "http_status": error.status_code,
                    "error_kind": error.kind,
                }
            )
        )
        if error.kind == "client_not_authorized":
            print(
                "The gateway rejected this client. Request provider authorization for this Python application before retrying; changing models does not resolve client authorization."
            )
        else:
            print(
                "No inference requested. Verify account, endpoint and access; this result alone does not prove the token is invalid."
            )
        return 1
    print(json.dumps({**result, "status": "catalogue_accessible", "model_listed": visible}))
    print("No inference requested. Catalogue access does not prove chat completion support.")
    if not visible:
        print("The requested model is absent from the returned list; verify it with the provider.")
        return 2
    return 0


def main() -> int:
    """Read the selected config and use the token already in the process environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    return check(load_config(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
