"""Independent schema validation, starter models, SQL and Java generation.

This module performs no filesystem, subprocess, database or network operations.
All user-provided descriptions and display names are data, never code fragments.
"""
from __future__ import annotations

import copy
import datetime as dt
import decimal
import hashlib
import json
import math
import re
import uuid

from .starters import STARTERS

FORMAT = "channelshift.schema/v1"
DATABASES = {"postgresql", "mysql", "sqlite"}
TYPES = {"uuid", "varchar", "text", "integer", "bigint", "decimal", "boolean", "date", "timestamp", "json"}
ID_TYPES = {"uuid", "varchar", "integer", "bigint"}
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}\Z", re.ASCII)
JAVA_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z", re.ASCII)
JAVA_KEYWORDS = set("abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null _ record sealed permits non-sealed var yield module open opens requires exports uses provides to with transitive when".split())
JAVA_TYPES = {"integer": "Integer", "bigint": "Long", "boolean": "Boolean", "varchar": "String", "text": "String", "decimal": "java.math.BigDecimal", "date": "java.time.LocalDate", "timestamp": "java.time.LocalDateTime"}
JAVA_RESERVED_CLASSES = {"String", "Long", "Integer", "Boolean", "Object", "Class", "Entity", "Table", "Column", "Id", "Index", "UniqueConstraint", "JpaRepository", "Serializable", "Record", "Enum"}
DELETE_ACTIONS = {"restrict": "RESTRICT", "cascade": "CASCADE", "set_null": "SET NULL", "no_action": "NO ACTION"}
MAX_BYTES = 2 * 1024 * 1024


def _identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def _number(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _shape(value, allowed, required, path, issue):
    if not isinstance(value, dict):
        issue(path, "invalid_object", "An object is required.")
        return False
    if any(key not in allowed for key in value):
        issue(path, "unknown_property", "Unknown properties are not allowed.")
    for key in sorted(required - value.keys()):
        issue(path + "." + key, "required", "A required property is missing.")
    return True


def _preflight(schema, issue):
    """Bound JSON traversal before model operations or serializing input."""
    active = set()
    nodes = 0

    def walk(value, path, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 100000 or depth > 16:
            issue(path, "input_limit", "Input exceeds the supported structure limit.")
            return False
        if isinstance(value, str):
            if len(value) > 10000 or "\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                issue(path, "invalid_string", "A string exceeds a limit or contains unsupported characters.")
                return False
            return True
        if value is None or type(value) is bool:
            return True
        if type(value) is int:
            if abs(value) > 9007199254740991:
                issue(path, "unsafe_integer", "Integer literals must stay within the browser-safe range.")
                return False
            return True
        if type(value) is float:
            if not math.isfinite(value):
                issue(path, "nonfinite_number", "Numbers must be finite.")
                return False
            if value.is_integer() and abs(value) > 9007199254740991:
                issue(path, "unsafe_integer", "Integer literals must stay within the browser-safe range.")
                return False
            return True
        if not isinstance(value, (dict, list)) or id(value) in active:
            issue(path, "invalid_json", "Input must be an acyclic JSON value.")
            return False
        active.add(id(value))
        try:
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str) or not walk(key, path, depth + 1):
                        issue(path, "invalid_json", "Object keys must be bounded strings.")
                        return False
                    if not walk(child, path, depth + 1):
                        return False
            else:
                for child in value:
                    if not walk(child, path, depth + 1):
                        return False
        finally:
            active.remove(id(value))
        return True

    if not walk(schema, "$", 0):
        return False
    try:
        encoded = json.dumps(schema, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        issue("$", "invalid_json", "Input must be a bounded JSON value.")
        return False
    if len(encoded) > MAX_BYTES:
        issue("$", "input_too_large", "Schema exceeds the 2 MiB size limit.")
        return False
    return True


def _default_valid(attr):
    value = attr["default"]
    kind = attr.get("type")
    if value is None:
        return attr.get("nullable") is True
    if isinstance(value, dict):
        return kind == "timestamp" and value == {"function": "current_timestamp"}
    if kind in ("integer", "bigint"):
        bits = 32 if kind == "integer" else 64
        return _number(value, -(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
    if kind == "boolean":
        return type(value) is bool
    if kind == "decimal":
        if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
            return False
        number = decimal.Decimal(str(value))
        precision, scale = attr.get("precision", 19), attr.get("scale", 2)
        if not _number(precision, 1, 38) or not _number(scale, 0, precision):
            return False
        with decimal.localcontext() as context:
            context.prec = 100
            return abs(number) < decimal.Decimal(10) ** (precision - scale) and number == number.quantize(decimal.Decimal(10) ** -scale)
    if not isinstance(value, str):
        return False
    try:
        if kind == "uuid":
            return str(uuid.UUID(value)) == value.lower()
        if kind == "varchar":
            length = attr.get("length", 255)
            return _number(length, 1, 65535) and len(value) <= length
        if kind == "text":
            return True
        if kind == "date":
            return re.fullmatch(r"\d{4}-\d{2}-\d{2}", value, re.ASCII) is not None and dt.date.fromisoformat(value) is not None
        if kind == "timestamp":
            return re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", value, re.ASCII) is not None and dt.datetime.fromisoformat(value).tzinfo is None
        if kind == "json":
            def reject_constant(_):
                raise ValueError("invalid_json")
            json.loads(value, parse_constant=reject_constant)
            return True
    except (ValueError, OverflowError, decimal.InvalidOperation, RecursionError):
        return False
    return False


def _type_signature(attr):
    kind = attr["type"]
    if kind == "varchar":
        return kind, attr.get("length", 255)
    if kind == "decimal":
        return kind, attr.get("precision", 19), attr.get("scale", 2)
    return (kind,)


def validate_schema(schema) -> dict:
    """Return bounded structured issues. Never echo arbitrary input contents."""
    issues = []

    def issue(path, code, message):
        if len(issues) < 100:
            issues.append({"path": path, "code": code, "message": message})

    if not _preflight(schema, issue):
        return {"valid": False, "issues": issues}
    if not _shape(schema, {"format", "name", "database", "entities", "relations"}, {"format", "name", "database", "entities", "relations"}, "$", issue):
        return {"valid": False, "issues": issues}
    if schema.get("format") != FORMAT:
        issue("$.format", "invalid_format", "Use channelshift.schema/v1.")
    name = schema.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name):
        issue("$.name", "invalid_project_name", "Project name must contain 1 to 200 display characters.")
    if not isinstance(schema.get("database"), str) or schema["database"] not in DATABASES:
        issue("$.database", "invalid_database", "Choose postgresql, mysql or sqlite.")
    entities = schema.get("entities")
    relations = schema.get("relations")
    if not isinstance(entities, list) or len(entities) > 100:
        issue("$.entities", "entity_limit", "Entities must be a list of at most 100 items.")
        entities = []
    if not isinstance(relations, list) or len(relations) > 2000:
        issue("$.relations", "relation_limit", "Relations must be a list of at most 2000 items.")
        relations = []
    entity_map = {}
    index_names = set()
    attribute_count = 0
    for ei, entity in enumerate(entities):
        ep = f"$.entities[{ei}]"
        if not _shape(entity, {"name", "description", "attributes", "indexes"}, {"name", "attributes"}, ep, issue):
            continue
        entity_name = entity.get("name")
        if not _identifier(entity_name):
            issue(ep + ".name", "invalid_identifier", "Use a lowercase SQL identifier of 1 to 63 characters.")
        elif entity_name in entity_map:
            issue(ep + ".name", "duplicate_identifier", "Entity names must be unique.")
        else:
            entity_map[entity_name] = {"entity": entity, "attributes": {}, "unique_keys": set()}
        if schema.get("database") == "sqlite" and _identifier(entity_name) and entity_name.startswith("sqlite_"):
            issue(ep + ".name", "reserved_identifier", "SQLite reserves the sqlite_ prefix for internal table and index names.")
        if "description" in entity and not isinstance(entity["description"], str):
            issue(ep + ".description", "invalid_string", "Description must be text.")
        attrs = entity.get("attributes")
        if not isinstance(attrs, list) or not attrs:
            issue(ep + ".attributes", "incomplete_entity", "An entity needs attributes and one primary key.")
            attrs = []
        attribute_count += len(attrs)
        attr_map = {}
        unique_keys = set()
        pk_count = 0
        for ai, attr in enumerate(attrs):
            ap = f"{ep}.attributes[{ai}]"
            if not _shape(attr, {"name", "type", "nullable", "primary_key", "unique", "length", "precision", "scale", "default"}, {"name", "type", "nullable"}, ap, issue):
                continue
            attr_name = attr.get("name")
            if not _identifier(attr_name):
                issue(ap + ".name", "invalid_identifier", "Use a lowercase SQL identifier of 1 to 63 characters.")
            elif attr_name in attr_map:
                issue(ap + ".name", "duplicate_identifier", "Attribute names must be unique in an entity.")
            else:
                attr_map[attr_name] = attr
            kind = attr.get("type")
            if not isinstance(kind, str) or kind not in TYPES:
                issue(ap + ".type", "invalid_type", "Choose a supported logical attribute type.")
                kind = None
            for flag in ("nullable", "primary_key", "unique"):
                if flag in attr and type(attr[flag]) is not bool:
                    issue(ap + "." + flag, "invalid_boolean", "Flags must be true or false.")
            if attr.get("primary_key") is True:
                pk_count += 1
                if attr.get("nullable") is not False or kind not in ID_TYPES:
                    issue(ap, "invalid_primary_key", "Primary keys must be non-null uuid, varchar, integer or bigint values.")
            if _identifier(attr_name) and (attr.get("unique") is True or attr.get("primary_key") is True):
                unique_keys.add((attr_name,))
            if "length" in attr and (kind != "varchar" or not _number(attr["length"], 1, 65535)):
                issue(ap + ".length", "invalid_length", "Only varchar supports length, from 1 to 65535.")
            if "precision" in attr and (kind != "decimal" or not _number(attr["precision"], 1, 38)):
                issue(ap + ".precision", "invalid_precision", "Only decimal supports precision, from 1 to 38.")
            precision = attr.get("precision", 19)
            if "scale" in attr and (kind != "decimal" or not _number(precision, 1, 38) or not _number(attr["scale"], 0, precision)):
                issue(ap + ".scale", "invalid_scale", "Decimal scale must be from zero to precision.")
            if kind == "decimal" and _number(precision, 1, 38) and type(attr.get("scale", 2)) is int and attr.get("scale", 2) > precision:
                issue(ap, "invalid_scale", "Default decimal scale is 2; specify a smaller scale for lower precision.")
            if "default" in attr and not _default_valid(attr):
                issue(ap + ".default", "invalid_default", "Default must be a compatible scalar literal or supported timestamp function.")
        if pk_count != 1:
            issue(ep + ".attributes", "primary_key_count", "Each entity must have exactly one primary key.")
        indexes = entity.get("indexes", [])
        if not isinstance(indexes, list) or len(indexes) > 2000:
            issue(ep + ".indexes", "invalid_indexes", "Indexes must be a bounded list.")
            indexes = []
        for ii, index in enumerate(indexes):
            ip = f"{ep}.indexes[{ii}]"
            if not _shape(index, {"name", "columns", "unique"}, {"name", "columns"}, ip, issue):
                continue
            index_name = index.get("name")
            if not _identifier(index_name):
                issue(ip + ".name", "invalid_identifier", "Use a lowercase SQL identifier of 1 to 63 characters.")
            elif index_name in index_names:
                issue(ip + ".name", "duplicate_identifier", "Index names must be unique across the schema.")
            else:
                index_names.add(index_name)
            if schema.get("database") == "sqlite" and _identifier(index_name) and index_name.startswith("sqlite_"):
                issue(ip + ".name", "reserved_identifier", "SQLite reserves the sqlite_ prefix for internal table and index names.")
            columns = index.get("columns")
            if not isinstance(columns, list) or not columns or not all(_identifier(c) for c in columns) or len(set(columns)) != len(columns) or any(c not in attr_map for c in columns):
                issue(ip + ".columns", "invalid_index_columns", "Index columns must reference distinct existing attributes.")
            elif index.get("unique") is True:
                unique_keys.add(tuple(columns))
            if "unique" in index and type(index["unique"]) is not bool:
                issue(ip + ".unique", "invalid_boolean", "Unique must be true or false.")
        if _identifier(entity_name) and entity_name in entity_map:
            entity_map[entity_name]["attributes"] = attr_map
            entity_map[entity_name]["unique_keys"] = unique_keys
    if attribute_count > 2000:
        issue("$.entities", "attribute_limit", "The schema may contain at most 2000 attributes.")
    if index_names.intersection(entity_map):
        issue("$.entities", "duplicate_identifier", "Index names must not collide with table names.")
    relation_names = set()
    for ri, relation in enumerate(relations):
        rp = f"$.relations[{ri}]"
        if not _shape(relation, {"name", "from", "to", "on_delete"}, {"name", "from", "to", "on_delete"}, rp, issue):
            continue
        relation_name = relation.get("name")
        if not _identifier(relation_name):
            issue(rp + ".name", "invalid_identifier", "Use a lowercase SQL identifier of 1 to 63 characters.")
        elif relation_name in relation_names:
            issue(rp + ".name", "duplicate_identifier", "Relation names must be unique.")
        else:
            relation_names.add(relation_name)
        action = relation.get("on_delete")
        if not isinstance(action, str) or action not in DELETE_ACTIONS:
            issue(rp + ".on_delete", "invalid_delete_action", "Choose restrict, cascade, set_null or no_action.")
        endpoints = []
        for side in ("from", "to"):
            end = relation.get(side)
            sp = rp + "." + side
            if not _shape(end, {"entity", "columns"}, {"entity", "columns"}, sp, issue):
                endpoints.append(None)
                continue
            ref = entity_map.get(end.get("entity")) if _identifier(end.get("entity")) else None
            cols = end.get("columns")
            if ref is None or not isinstance(cols, list) or not cols or not all(_identifier(c) for c in cols) or len(set(cols)) != len(cols) or any(c not in ref["attributes"] for c in cols):
                issue(sp, "unresolved_reference", "Relation endpoint must reference distinct existing attributes.")
                endpoints.append(None)
            else:
                endpoints.append((ref, cols))
        if all(endpoint is not None for endpoint in endpoints):
            source, target = endpoints
            if len(source[1]) != len(target[1]):
                issue(rp, "foreign_key_arity", "Foreign-key endpoints need equal column counts.")
            else:
                for source_col, target_col in zip(source[1], target[1]):
                    source_attr, target_attr = source[0]["attributes"][source_col], target[0]["attributes"][target_col]
                    if isinstance(source_attr.get("type"), str) and isinstance(target_attr.get("type"), str) and source_attr["type"] in TYPES and target_attr["type"] in TYPES and _type_signature(source_attr) != _type_signature(target_attr):
                        issue(rp, "foreign_key_type", "Foreign-key types and type parameters must match.")
                    if action == "set_null" and source_attr.get("nullable") is not True:
                        issue(rp, "foreign_key_nullable", "SET NULL requires nullable source attributes.")
            if tuple(target[1]) not in target[0]["unique_keys"]:
                issue(rp + ".to", "foreign_key_unique", "Target columns must form a primary or unique key.")
    return {"valid": not issues, "issues": issues}


def list_templates() -> list[dict]:
    return [{"id": key, "title": value["title"], "description": value["description"], "tableCount": len(value["entities"])} for key, value in STARTERS.items()]


def create_schema(template_id, project, database="postgresql") -> dict:
    if not isinstance(template_id, str) or template_id not in STARTERS:
        raise ValueError("invalid_template")
    template = STARTERS[template_id]
    schema = {"format": FORMAT, "name": project, "database": database,
              "entities": copy.deepcopy(template["entities"]), "relations": copy.deepcopy(template["relations"])}
    if not validate_schema(schema)["valid"]:
        raise ValueError("invalid_schema")
    return schema


def _validated(schema):
    if not validate_schema(schema)["valid"]:
        raise ValueError("invalid_schema")


def _quote(name, database):
    delimiter = "`" if database == "mysql" else '"'
    return delimiter + name + delimiter


def _sql_type(attr, database):
    kind = attr["type"]
    if kind == "varchar":
        return f"VARCHAR({attr.get('length', 255)})"
    if kind == "decimal":
        return f"DECIMAL({attr.get('precision', 19)}, {attr.get('scale', 2)})"
    if database == "postgresql":
        return {"uuid": "UUID", "text": "TEXT", "integer": "INTEGER", "bigint": "BIGINT", "boolean": "BOOLEAN", "date": "DATE", "timestamp": "TIMESTAMP WITHOUT TIME ZONE", "json": "JSONB"}[kind]
    if database == "mysql":
        return {"uuid": "CHAR(36)", "text": "TEXT", "integer": "INT", "bigint": "BIGINT", "boolean": "BOOLEAN", "date": "DATE", "timestamp": "DATETIME(6)", "json": "JSON"}[kind]
    # INT retains integer affinity without SQLite's implicit INTEGER PK rowid.
    return {"uuid": "TEXT", "text": "TEXT", "integer": "INT", "bigint": "BIGINT", "boolean": "INTEGER", "date": "TEXT", "timestamp": "TEXT", "json": "TEXT"}[kind]


def _sql_default(attr, database):
    value = attr["default"]
    if value is None:
        return "NULL"
    if isinstance(value, dict):
        return "CURRENT_TIMESTAMP"
    if type(value) is bool:
        return ("TRUE" if value else "FALSE") if database != "sqlite" else ("1" if value else "0")
    if type(value) in (int, float):
        return str(value)
    if attr["type"] == "timestamp":
        value = value.replace("T", " ")
    if database == "mysql":
        if attr["type"] in ("text", "json"):
            raise ValueError("unsupported_default")
        encoded = value.encode("utf-8").hex()
        return f"(CONVERT(X'{encoded}' USING utf8mb4))"
    if database == "postgresql":
        # E strings double both slashes and quotes independent of the session's
        # standard_conforming_strings value; embedded NUL was rejected earlier.
        return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    return "'" + value.replace("'", "''") + "'"


def _constraint_name(prefix, entity, column=""):
    digest = hashlib.sha256((entity + ":" + column).encode("ascii")).hexdigest()[:16]
    return "_cs_" + prefix + "_" + digest


def _mysql_support(schema):
    for entity in schema["entities"]:
        attrs = {a["name"]: a for a in entity["attributes"]}
        for attr in attrs.values():
            if attr["type"] == "varchar" and attr.get("length", 255) > 16383:
                raise ValueError("unsupported_mysql_type")
            if attr["type"] in ("date", "timestamp") and isinstance(attr.get("default"), str) and int(attr["default"][:4]) < 1000:
                raise ValueError("unsupported_default")
        keys = [[a["name"]] for a in attrs.values() if a.get("primary_key") or a.get("unique")]
        keys += [idx["columns"] for idx in entity.get("indexes", [])]
        keys += [r[side]["columns"] for r in schema["relations"] for side in ("from", "to") if r[side]["entity"] == entity["name"]]
        for key in keys:
            if any(attrs[col]["type"] in ("text", "json") for col in key):
                raise ValueError("unsupported_mysql_index")
            # Conservative UTF-8 upper bound for InnoDB's 3072-byte key limit.
            size = sum(attrs[col].get("length", 255) * 4 if attrs[col]["type"] == "varchar" else 144 if attrs[col]["type"] == "uuid" else 16 for col in key)
            if size > 3072:
                raise ValueError("unsupported_mysql_index")


def export_sql(schema) -> str:
    _validated(schema)
    database = schema["database"]
    if database == "mysql":
        _mysql_support(schema)
    quote = lambda name: _quote(name, database)
    lines = ["-- Generated by ChannelShift. Review before applying to an existing database.",
             "-- IDs are application-assigned; no identity/sequence is implied.",
             "-- Timestamps are timezone-free UTC by convention; configure the database session to UTC."]
    if database == "mysql":
        lines.append("-- Target: MySQL 8.0.16+ with InnoDB, utf8mb4 and a 16 KiB page size.")
        lines.append("-- UUID is CHAR(36); BOOLEAN is MySQL's TINYINT alias. Choose collation explicitly for your app.")
    if database == "sqlite":
        lines.extend(["-- SQLite uses dynamic typing; varchar lengths, decimal precision/scale and UUID/JSON formats are not enforced.",
                      "-- DECIMAL has numeric affinity and may lose decimal precision; use integer minor units when exact money is required.",
                      "-- Dates/timestamps use ISO text; JSON uses text; Boolean values are stored as 0/1.",
                      "-- Enable PRAGMA foreign_keys=ON on every application connection before a transaction."])
    lines.append("")

    def foreign_key(relation):
        source, target = relation["from"], relation["to"]
        source_columns = ", ".join(quote(c) for c in source["columns"])
        target_columns = ", ".join(quote(c) for c in target["columns"])
        return (f"CONSTRAINT {quote(relation['name'])} FOREIGN KEY ({source_columns}) "
                f"REFERENCES {quote(target['entity'])} ({target_columns}) ON DELETE {DELETE_ACTIONS[relation['on_delete']]}")

    for entity in schema["entities"]:
        definitions = []
        for attr in entity["attributes"]:
            definition = quote(attr["name"]) + " " + _sql_type(attr, database)
            if not attr["nullable"]:
                definition += " NOT NULL"
            if "default" in attr:
                definition += " DEFAULT " + _sql_default(attr, database)
            definitions.append(definition)
        for attr in entity["attributes"]:
            if attr.get("primary_key"):
                definitions.append(f"CONSTRAINT {quote(_constraint_name('pk', entity['name']))} PRIMARY KEY ({quote(attr['name'])})")
            elif attr.get("unique"):
                definitions.append(f"CONSTRAINT {quote(_constraint_name('uq', entity['name'], attr['name']))} UNIQUE ({quote(attr['name'])})")
        if database == "sqlite":
            definitions.extend(foreign_key(r) for r in schema["relations"] if r["from"]["entity"] == entity["name"])
        suffix = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4" if database == "mysql" else ""
        lines.append(f"CREATE TABLE {quote(entity['name'])} (\n    " + ",\n    ".join(definitions) + "\n)" + suffix + ";")
        lines.append("")
    for entity in schema["entities"]:
        for index in entity.get("indexes", []):
            unique = "UNIQUE " if index.get("unique") else ""
            cols = ", ".join(quote(c) for c in index["columns"])
            lines.append(f"CREATE {unique}INDEX {quote(index['name'])} ON {quote(entity['name'])} ({cols});")
    if database != "sqlite":
        for relation in schema["relations"]:
            lines.append(f"ALTER TABLE {quote(relation['from']['entity'])} ADD {foreign_key(relation)};")
    return "\n".join(lines).rstrip() + "\n"


def _pascal(name):
    return "".join(part[0].upper() + part[1:] for part in name.split("_") if part)


def _java_class(name):
    result = _pascal(name)
    return result + "Entity" if result in JAVA_RESERVED_CLASSES else result


def _java_field(name):
    pascal = _pascal(name)
    result = pascal[0].lower() + pascal[1:]
    return result + "Value" if result in JAVA_KEYWORDS or result == "java" else result


def _java_type(attr, database):
    if attr["type"] == "uuid":
        return "java.util.UUID" if database == "postgresql" else "String"
    return JAVA_TYPES[attr["type"]]


def _java_literal(attr, database):
    value = attr["default"]
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "java.time.LocalDateTime.now(java.time.Clock.systemUTC())"
    if attr["type"] == "boolean":
        return "true" if value else "false"
    if attr["type"] == "integer":
        return str(value)
    if attr["type"] == "bigint":
        return str(value) + "L"
    quoted = json.dumps(str(value), ensure_ascii=True)
    if attr["type"] == "decimal":
        return "new java.math.BigDecimal(" + quoted + ")"
    if attr["type"] == "uuid" and database == "postgresql":
        return "java.util.UUID.fromString(" + quoted + ")"
    if attr["type"] == "date":
        return "java.time.LocalDate.parse(" + quoted + ")"
    if attr["type"] == "timestamp":
        return "java.time.LocalDateTime.parse(" + json.dumps(value.replace(" ", "T")) + ")"
    return quoted


def export_java(schema, package="com.example.app") -> list[dict]:
    """Generate Java 17+/Jakarta Persistence 3.1 and Spring Data JPA 3.x sources.

    No build file or dependency version is imposed on an existing application.
    Generated entities use assigned IDs, field access and scalar FK properties.
    """
    _validated(schema)
    if not isinstance(package, str) or len(package) > 200 or not package or any(not JAVA_IDENTIFIER.fullmatch(part) or part in JAVA_KEYWORDS for part in package.split(".")) or package.split(".")[0] == "java":
        raise ValueError("invalid_java_package")
    classes = {}
    files = []
    names_seen = set()
    for entity in schema["entities"]:
        class_name = _java_class(entity["name"])
        for generated_name in (class_name, class_name + "Repository"):
            # Case-insensitive collisions also break exports on Windows/macOS.
            if generated_name.casefold() in names_seen:
                raise ValueError("java_name_collision")
            names_seen.add(generated_name.casefold())
        classes[entity["name"]] = class_name
        fields = set()
        methods = set()
        for attr in entity["attributes"]:
            if attr["type"] == "json":
                raise ValueError("java_json_mapping_unsupported")
            field = _java_field(attr["name"])
            suffix = field[0].upper() + field[1:]
            if field in fields or suffix in methods or suffix == "Class":
                raise ValueError("java_name_collision")
            fields.add(field)
            methods.add(suffix)
    directory = "src/main/java/" + package.replace(".", "/") + "/"
    for entity in schema["entities"]:
        class_name = classes[entity["name"]]
        source = [f"package {package};", "", "import jakarta.persistence.Column;", "import jakarta.persistence.Entity;", "import jakarta.persistence.Id;", "import jakarta.persistence.Table;", "", "@Entity", f'@Table(name = "{entity["name"]}")', f"public class {class_name} {{"]
        for attr in entity["attributes"]:
            if attr.get("primary_key"):
                source.append("    @Id")
            annotations = [f'name = "{attr["name"]}"', "nullable = " + str(attr["nullable"]).lower()]
            if attr.get("unique"):
                annotations.append("unique = true")
            if attr["type"] == "varchar":
                annotations.append("length = " + str(attr.get("length", 255)))
            elif attr["type"] == "uuid" and schema["database"] != "postgresql":
                annotations.append("length = 36")
                if schema["database"] == "mysql":
                    annotations.append('columnDefinition = "CHAR(36)"')
            elif attr["type"] == "decimal":
                annotations.extend(["precision = " + str(attr.get("precision", 19)), "scale = " + str(attr.get("scale", 2))])
            elif attr["type"] == "text":
                annotations.append('columnDefinition = "TEXT"')
            source.append("    @Column(" + ", ".join(annotations) + ")")
            default = " = " + _java_literal(attr, schema["database"]) if "default" in attr else ""
            source.append(f"    private {_java_type(attr, schema['database'])} {_java_field(attr['name'])}{default};")
            source.append("")
        source.extend([f"    public {class_name}() {{", "    }", ""])
        for attr in entity["attributes"]:
            field = _java_field(attr["name"])
            suffix = field[0].upper() + field[1:]
            java_type = _java_type(attr, schema["database"])
            source.extend([f"    public {java_type} get{suffix}() {{", f"        return {field};", "    }", "",
                           f"    public void set{suffix}({java_type} {field}) {{", f"        this.{field} = {field};", "    }", ""])
        source.append("}")
        files.append({"path": directory + class_name + ".java", "content": "\n".join(source) + "\n"})
        pk = next(attr for attr in entity["attributes"] if attr.get("primary_key"))
        repository = (f"package {package};\n\nimport org.springframework.data.jpa.repository.JpaRepository;\n\n"
                      f"public interface {class_name}Repository extends JpaRepository<{class_name}, {_java_type(pk, schema['database'])}> {{\n}}\n")
        files.append({"path": directory + class_name + "Repository.java", "content": repository})
    files.append({"path": "JAVA_STARTER.md", "content": _JAVA_GUIDE})
    return files


_JAVA_GUIDE = """# Java starter integration

Baseline: Java 17+, Jakarta Persistence 3.1 and Spring Data JPA 3.x (for example,
Spring Boot 3.x). Check the existing project's Java, framework, provider, ORM,
database, package and migration conventions before merging these files. No build
file or dependency version is supplied or installed automatically.

Apply the separately generated SQL through the application's migration workflow.
Do not use automatic schema creation/update from these JPA classes. The SQL owns
indexes, composite unique keys, foreign keys and delete behavior; JPA intentionally
keeps foreign keys as scalar fields without associations or automatic cascades.
IDs are assigned by the application; there is no generated-value strategy.

Table and column names are preserved. When using Hibernate, enable
spring.jpa.properties.hibernate.globally_quoted_identifiers=true and
spring.jpa.properties.hibernate.globally_quoted_identifiers_skip_column_definitions=true
so SQL keyword names remain usable without quoting native type declarations.
Use spring.jpa.hibernate.ddl-auto=none; use migration validation appropriate to the
existing project. SQLite requires a compatible JPA provider/dialect and
PRAGMA foreign_keys=ON for each connection; this starter does not install one.

Timestamp is timezone-free LocalDateTime. Use UTC by application convention and
configure database sessions to UTC. PostgreSQL CURRENT_TIMESTAMP and MySQL
CURRENT_TIMESTAMP use the session timezone; SQLite CURRENT_TIMESTAMP is UTC.
Literal timestamps reject offsets to prevent silent timezone conversion. Java
current_timestamp defaults initialize at object construction using the UTC clock;
SQL defaults initialize on INSERT. Set values explicitly when that timing matters.

UUID is java.util.UUID for PostgreSQL and String for MySQL/SQLite text storage.
JSON export is refused because portable Jakarta Persistence has no JSON mapping;
choose an explicit provider-specific mapping in your existing project first.
SQLite DECIMAL has numeric affinity and does not guarantee exact decimal storage.
Use integer minor units for exact money on SQLite. Review precision, currency,
collation, length checks, validation, authentication, concurrency and lifecycle
rules for your application's requirements. No credentials or record data are
included. String/number/default literals are generated from typed values only.
"""
