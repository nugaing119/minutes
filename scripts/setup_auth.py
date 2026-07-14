from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from scripts.keychain import set_secret


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Store meeting-minutes credentials in macOS Keychain."
    )
    parser.add_argument(
        "--model",
        help="OpenAI model name to store. If omitted, you will be prompted.",
    )
    args = parser.parse_args()

    api_key = getpass.getpass("OpenAI API key: ").strip()
    if not api_key:
        raise SystemExit("error: OpenAI API key is required")

    model = args.model or input("OpenAI model: ").strip()
    if not model:
        raise SystemExit("error: OpenAI model is required")

    set_secret("OPENAI_API_KEY", api_key)
    set_secret("OPENAI_MODEL", model)
    print("saved OpenAI credentials to macOS Keychain")


if __name__ == "__main__":
    main()
