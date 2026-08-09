import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from app.main import app


def render() -> str:
    schema = json.loads(json.dumps(app.openapi()))
    return yaml.safe_dump(schema, allow_unicode=True, sort_keys=False, width=100)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the OpenAPI specification to a YAML file")
    parser.add_argument("output", help="Target path, for example docs/openapi.yaml")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write anything, exit with 1 if the file is out of date",
    )
    args = parser.parse_args()

    target = Path(args.output)
    rendered = render()

    if args.check:
        if not target.exists():
            print(f"{target} is missing, run: make openapi", file=sys.stderr)
            return 1
        if yaml.safe_load(target.read_text(encoding="utf-8")) != yaml.safe_load(rendered):
            print(f"{target} is out of date, run: make openapi", file=sys.stderr)
            return 1
        print(f"{target} is up to date")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    print(f"written {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
