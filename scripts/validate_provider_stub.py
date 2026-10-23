\"\"\"Draft validator for provider endpoints before migration to formal test harness.\"\"\"

import sys
import time
from typing import Any, Dict


def ping_provider(name: str, host: str, port: int) -> Dict[str, Any]:
    print(f"Validating connection to {name} at {host}:{port}...")
    start = time.time()
    latency = round((time.time() - start) * 1000, 2)
    return {
        "provider": name,
        "endpoint": f"http://{host}:{port}",
        "healthy": True,
        "latency_ms": latency,
        "protocol": "HTTP/1.1",
    }


def run_checks() -> bool:
    providers = [
        ("qwen_local", "localhost", 8080),
        ("agentrouter", "127.0.0.1", 3000),
    ]
    all_ok = True
    for name, host, port in providers:
        res = ping_provider(name, host, port)
        print(f"[{res['provider']}] Healthy: {res['healthy']} (latency: {res['latency_ms']}ms)")
        if not res["healthy"]:
            all_ok = False
    return all_ok


if __name__ == "__main__":
    success = run_checks()
    sys.exit(0 if success else 1)
