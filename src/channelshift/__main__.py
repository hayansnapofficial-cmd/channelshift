"""Command line alternative to MCP; exports are printed, never executed."""
import argparse
import json
import sys
from pathlib import Path

from .core import create_schema, export_java, export_sql, list_templates, validate_schema
from .store import MAX_BYTES
from .delivery_profile import delivery_plan


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("templates")
    commands.add_parser("project-catalog", help="List proposed modules and presets without installing them")
    for name in ("project-plan", "dns-plan", "seo-metadata"):
        catalog_command = commands.add_parser(name, help="Validate input and print a non-executing plan")
        catalog_command.add_argument("--input", type=Path, required=True)
    delivery = commands.add_parser("delivery-plan", help="Show the draft website delivery sequence without executing it")
    delivery.add_argument("--input", type=Path)
    create = commands.add_parser("create")
    create.add_argument("template")
    create.add_argument("--project", required=True)
    create.add_argument("--database", default="postgresql", choices=("postgresql", "mysql", "sqlite"))
    for name in ("validate", "sql", "java"):
        sub = commands.add_parser(name)
        sub.add_argument("--input", type=Path, required=True)
        if name == "java":
            sub.add_argument("--package", default="com.example.app")
    args = parser.parse_args()
    try:
        if args.command == "templates":
            output = list_templates()
        elif args.command == "project-catalog":
            from .project_catalog import catalog, presets
            output = {"catalog": catalog(), "presets": presets()}
        elif args.command in {"project-plan", "dns-plan", "seo-metadata"}:
            from .project_catalog import build_project_plan, build_dns_change_plan, resolve_seo_metadata
            with args.input.open("rb") as stream:
                raw = stream.read(131073)
            if len(raw) > 131072:
                raise ValueError("payload_too_large")
            config = json.loads(raw)
            output = {"project-plan": build_project_plan, "dns-plan": build_dns_change_plan,
                      "seo-metadata": resolve_seo_metadata}[args.command](config)
        elif args.command == "delivery-plan":
            brief = None
            if args.input is not None:
                with args.input.open("rb") as stream:
                    raw = stream.read(65537)
                if len(raw) > 65536:
                    raise ValueError("payload_too_large")
                brief = json.loads(raw)
            output = delivery_plan(brief)
        elif args.command == "create":
            output = create_schema(args.template, args.project, args.database)
        else:
            if args.input.stat().st_size > MAX_BYTES:
                raise ValueError("payload_too_large")
            model = json.loads(args.input.read_bytes())
            if args.command == "validate":
                output = validate_schema(model)
            elif args.command == "java":
                output = export_java(model, args.package)
            else:
                print(export_sql(model))
                return 0
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 1 if isinstance(output, dict) and output.get("valid") is False else 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("Unable to process this input. Check the schema and command arguments.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
