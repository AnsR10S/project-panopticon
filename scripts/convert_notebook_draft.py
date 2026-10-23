\"\"\"Draft helper to convert execution parameters between YAML configs and notebook cells.\"\"\"

import argparse
import json
from pathlib import Path
import yaml


def extract_parameters(config_file: Path) -> dict:
    with open(config_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    study_cfg = data.get("study", {})
    return {
        "study_id": study_cfg.get("id", "untitled"),
        "rounds": study_cfg.get("rounds", 50),
        "arms": data.get("arms", []),
        "tolerance": data.get("convergence_tolerance", 0.01),
    }


def inject_into_notebook(nb_path: Path, params: dict) -> None:
    with open(nb_path, "r", encoding="utf-8") as f:
        nb = json.load(f)
    print(f"Injecting parameters into notebook {nb_path.name}: {params}")
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            cell["source"] = [
                "# Injected parameters\n",
                f"CONFIG = {json.dumps(params, indent=2)}\n",
            ]
            break
    with open(nb_path, "w", encoding="utf-8") as f:
        json.dump(nb, f, indent=1)
    print("Notebook updated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--notebook", type=Path, required=True)
    args = parser.parse_args()
    p = extract_parameters(args.config)
    inject_into_notebook(args.notebook, p)
