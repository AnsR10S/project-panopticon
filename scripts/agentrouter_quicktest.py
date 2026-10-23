\"\"\"Quick connectivity smoke test for local AgentRouter reverse proxy.\"\"\"

import json
import sys
import urllib.error
import urllib.request


def test_endpoint(base_url: str = "http://localhost:8080/v1") -> bool:
    health_url = f"{base_url}/health"
    print(f"Testing AgentRouter connectivity at {health_url}...")
    try:
        req = urllib.request.Request(health_url, headers={"User-Agent": "AuditorsParadox/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            status = response.getcode()
            body = response.read().decode("utf-8")
            print(f"Response ({status}): {body}")
            if status == 200:
                print("AgentRouter endpoint is reachable and responsive.")
                return True
    except urllib.error.URLError as err:
        print(f"AgentRouter connectivity check failed: {err}")
        return False
    except Exception as exc:
        print(f"Unexpected error: {exc}")
        return False
    return False


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/v1"
    success = test_endpoint(url)
    sys.exit(0 if success else 1)
