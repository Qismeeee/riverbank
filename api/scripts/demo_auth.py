import argparse
import hashlib
import json
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3

import config
from services.underwriting.auth import validate_credentials


def generate(output: Path, days: int) -> None:
    if output.name != ".env.local" or not 1 <= days <= 30:
        raise ValueError("use an ignored .env.local file and a lifetime of 1 to 30 days")
    expires = (datetime.now(UTC) + timedelta(days=days)).isoformat()
    tokens = {
        subject: secrets.token_urlsafe(32) for subject in ("demo-a", "demo-b", "demo-c", "uw-demo")
    }
    document = {
        "schema_version": 1,
        "credentials": [
            {
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "subject": subject,
                "role": "underwriter" if subject == "uw-demo" else "seller",
                "expires_at": expires,
            }
            for subject, token in tokens.items()
        ],
    }
    validate_credentials(document)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump({"auth": document, "tokens": tokens}, stream, indent=2)
    output.chmod(0o600)
    print(f"Created private token file: {output}; expires_at={expires}")


def provision(document: dict) -> None:
    validate_credentials(document)
    if set(document) != {"schema_version", "credentials"}:
        raise ValueError("only credential hashes may be provisioned")
    client = boto3.client("secretsmanager", region_name=config.AWS_REGION)
    serialized = json.dumps(document, sort_keys=True)
    try:
        current = client.get_secret_value(SecretId=config.WORKFLOW_AUTH_SECRET_ID)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(Name=config.WORKFLOW_AUTH_SECRET_ID, SecretString=serialized)
    else:
        if json.loads(current["SecretString"]) != document:
            client.put_secret_value(
                SecretId=config.WORKFLOW_AUTH_SECRET_ID, SecretString=serialized
            )
    print("Demo credential hashes provisioned.")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--days", type=int, default=7)
    commands.add_parser("provision")
    args = parser.parse_args()
    try:
        if args.command == "generate":
            generate(args.output, args.days)
        else:
            provision(json.load(sys.stdin))
    except Exception:
        print(
            "Demo auth operation failed; check file, configuration and IAM permissions.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
