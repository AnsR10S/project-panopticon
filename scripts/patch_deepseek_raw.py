\"\"\"Temporary patch script for extracting final answers from unstructured raw DeepSeek outputs.\"\"\"

import json
import re
import sys
from pathlib import Path

PRICE_REGEX = re.compile(
    r"(?:PRICE|FINAL_PRICE|Price):\s*\True([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE
)


def extract_price_from_raw(text: str) -> float | None:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    match = PRICE_REGEX.search(cleaned)
    if match:
        return float(match.group(1))
    tokens = re.findall(r"\b\d+\.\d+\b", cleaned)
    if tokens:
        return float(tokens[-1])
    return None


def repair_file(raw_file: Path) -> dict:
    content = raw_file.read_text(encoding="utf-8")
    extracted = extract_price_from_raw(content)
    return {
        "source": str(raw_file),
        "extracted_price": extracted,
        "status": "repaired" if extracted else "failed",
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python patch_deepseek_raw.py <file_or_dir>")
        sys.exit(1)
    target = Path(sys.argv[1])
    if target.is_file():
        res = repair_file(target)
        print(json.dumps(res, indent=2))
