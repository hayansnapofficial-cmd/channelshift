"""Behavior tests for independent schema generation and export."""
import copy
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from channelshift import core


def attribute(name, kind="varchar", **kwargs):
    return {"name": name, "type": kind, "nullable": False, **kwargs}


def sample(database="sqlite"):
    return {
        "format": "channelshift.schema/v1", "name": "Example", "database": database,
        "entities": [{"name": "items", "attributes": [
            attribute("id", "integer", primary_key=True),
            attribute("label", length=100),
        ], "indexes": []}], "relations": [],
    }


class TemplateTests(unittest.TestCase):
    def test_templates_are_complete_and_fresh(self):
        expected = {"membership": 3, "content": 3, "booking": 3, "commerce": 4}
        self.assertEqual({x["id"]: x["tableCount"] for x in core.list_templates()}, expected)
        for name, count in expected.items():
            for dialect in ("postgresql", "mysql", "sqlite"):
                with self.subTest(template=name, dialect=dialect):
                    schema = core.create_schema(name, "New project", dialect)
                    self.assertTrue(core.validate_schema(schema)["valid"])
                    self.assertEqual(len(schema["entities"]), count)
                    self.assertTrue(core.export_sql(schema).strip())
            schema["entities"].clear()
            self.assertEqual(len(core.create_schema(name, "Again")["entities"]), count)

    def test_invalid_creation_is_safe(self):
        for args in [("../secret", "Demo"), ("booking", ""),
                     ("booking", "Demo", "oracle"), ([], "Demo")]:
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "^[a-z_]+$"):
                core.create_schema(*args)

    def test_sqlite_all_templates_enforce_references(self):
        fixtures = {
            "membership": [
                ("INSERT INTO users(id,email,display_name) VALUES (1,'a@b.c','A')", ()),
                ("INSERT INTO roles(id,name) VALUES (1,'editor')", ()),
                ("INSERT INTO user_roles(id,user_id,role_id) VALUES (1,1,1)", ()),
                ("INSERT INTO user_roles(id,user_id,role_id) VALUES (2,999,1)", "fk"),
            ],
            "content": [
                ("INSERT INTO authors(id,display_name) VALUES (1,'A')", ()),
                ("INSERT INTO categories(id,name,slug) VALUES (1,'News','news')", ()),
                ("INSERT INTO posts(id,author_id,category_id,title,slug,body) VALUES (1,1,1,'T','t','Body')", ()),
                ("INSERT INTO posts(id,author_id,title,slug,body) VALUES (2,999,'U','u','Body')", "fk"),
            ],
            "booking": [
                ("INSERT INTO customers(id,display_name,email) VALUES (1,'A','a@b.c')", ()),
                ("INSERT INTO services(id,name,duration_minutes,price) VALUES (1,'Session',60,10)", ()),
                ("INSERT INTO bookings(id,customer_id,service_id,starts_at) VALUES (1,1,1,'2026-09-29 10:00:00')", ()),
                ("INSERT INTO bookings(id,customer_id,service_id,starts_at) VALUES (2,999,1,'2026-09-29 11:00:00')", "fk"),
            ],
            "commerce": [
                ("INSERT INTO customers(id,display_name,email) VALUES (1,'A','a@b.c')", ()),
                ("INSERT INTO products(id,sku,name,unit_price) VALUES (1,'ABC','Book',10)", ()),
                ("INSERT INTO orders(id,customer_id) VALUES (1,1)", ()),
                ("INSERT INTO order_items(id,order_id,product_id,quantity,unit_price) VALUES (1,1,1,2,10)", ()),
                ("INSERT INTO order_items(id,order_id,product_id,quantity,unit_price) VALUES (2,999,1,2,10)", "fk"),
            ],
        }
        for template, statements in fixtures.items():
            with self.subTest(template=template), sqlite3.connect(":memory:") as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript(core.export_sql(core.create_schema(template, "Demo", "sqlite")))
                for sql, expectation in statements:
                    if expectation == "fk":
                        with self.assertRaises(sqlite3.IntegrityError):
                            db.execute(sql)
                    else:
                        db.execute(sql)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])


class ValidationTests(unittest.TestCase):
    def assertInvalid(self, schema):
        result = core.validate_schema(schema)
        self.assertFalse(result["valid"])
        self.assertTrue(result["issues"])
        self.assertTrue(all(set(i) == {"path", "code", "message"} for i in result["issues"]))
        for export in (core.export_sql, core.export_java):
            with self.assertRaisesRegex(ValueError, "^[a-z_]+$"):
                export(schema)

    def test_empty_schema_allowed(self):
        schema = sample()
        schema["entities"] = []
        self.assertTrue(core.validate_schema(schema)["valid"])

    def test_rejects_unknown_properties_everywhere(self):
        paths = [[], ["entities", 0], ["entities", 0, "attributes", 0]]
        for path in paths:
            schema = sample()
            target = schema
            for part in path:
                target = target[part]
            target["raw_sql"] = "secret payload"
            self.assertInvalid(schema)
            self.assertNotIn("secret payload", json.dumps(core.validate_schema(schema)))

    def test_rejects_bad_types_and_nonfinite_values(self):
        for value in [None, [], "secret", 12, {"format": "secret"}]:
            self.assertInvalid(value)
        for value in [math.nan, math.inf, -math.inf, True, "1.0"]:
            schema = sample()
            schema["entities"][0]["attributes"][1] = attribute("amount", "decimal", default=value)
            self.assertInvalid(schema)

    def test_rejects_duplicate_or_invalid_names(self):
        for name in ["Upper", "9name", "a-b", "x" * 64, "id\"; DROP TABLE items;"]:
            schema = sample()
            schema["entities"][0]["attributes"][1]["name"] = name
            self.assertInvalid(schema)
        schema = sample()
        schema["entities"][0]["attributes"][1]["name"] = "id"
        self.assertInvalid(schema)
        schema = sample()
        schema["entities"].append(copy.deepcopy(schema["entities"][0]))
        self.assertInvalid(schema)

    def test_primary_key_must_be_single_nonnullable_supported_scalar(self):
        changes = [{"primary_key": False}, {"nullable": True}, {"type": "json"},
                   {"type": "boolean"}, {"type": "decimal"}]
        for changeset in changes:
            schema = sample()
            schema["entities"][0]["attributes"][0].update(changeset)
            self.assertInvalid(schema)
        schema = sample()
        schema["entities"][0]["attributes"][1]["primary_key"] = True
        self.assertInvalid(schema)

    def test_type_parameters_and_defaults_are_checked(self):
        invalid = [attribute("label", length=0), attribute("label", length=True),
                   attribute("label", length=65536), attribute("n", "decimal", precision=2, scale=3),
                   attribute("n", "integer", length=12), attribute("n", "integer", default=True),
                   attribute("n", "integer", default=2147483648),
                   attribute("n", "boolean", default=1), attribute("label", default=None),
                   attribute("label", default={"function": "now()"}),
                   attribute("label", "timestamp", default={"function": "current_timestamp", "sql": "x"}),
                   attribute("label", "date", default="2026-02-30"),
                   attribute("label", "timestamp", default="2026-01-01T00:00:00+01:00"),
                   attribute("label", "uuid", default="bad"), attribute("label", "json", default="{bad}")]
        for attr in invalid:
            with self.subTest(attr=attr):
                schema = sample()
                schema["entities"][0]["attributes"][1] = attr
                self.assertInvalid(schema)

    def test_rejects_limits_cycles_and_controls_without_throwing(self):
        schema = sample()
        schema["name"] = "x" * 201
        self.assertInvalid(schema)
        schema = sample()
        schema["entities"][0]["description"] = "x" * 10001
        self.assertInvalid(schema)
        schema = sample()
        schema["relations"].append(schema)
        self.assertInvalid(schema)
        schema = sample()
        schema["entities"] *= 101
        self.assertInvalid(schema)
        schema = sample()
        schema["entities"][0]["attributes"][1]["default"] = "a\0b"
        self.assertInvalid(schema)

    def test_relations_require_matching_unique_keys_and_nullable_set_null(self):
        schema = core.create_schema("booking", "Test", "sqlite")
        scenarios = []
        for side, key, value in [("from", "entity", "missing"), ("to", "columns", ["missing"]),
                                 ("to", "columns", ["display_name"]), ("from", "columns", [])]:
            bad = copy.deepcopy(schema)
            bad["relations"][0][side][key] = value
            scenarios.append(bad)
        bad = copy.deepcopy(schema)
        bad["relations"][0]["on_delete"] = "set_null"
        scenarios.append(bad)
        bad = copy.deepcopy(schema)
        bad["entities"][2]["attributes"][1]["type"] = "varchar"
        scenarios.append(bad)
        for bad in scenarios:
            self.assertInvalid(bad)

    def test_indexes_validate_columns_and_unique_name_scope(self):
        schema = sample()
        for idx in [{"name": "idx_items", "columns": ["missing"]},
                    {"name": "idx_items", "columns": ["label", "label"]},
                    {"name": "idx_items", "columns": []},
                    {"name": "idx_items", "columns": ["label"], "unique": "true"}]:
            schema["entities"][0]["indexes"] = [idx]
            self.assertInvalid(schema)

    def test_sqlite_reserved_table_and_index_prefixes_are_rejected(self):
        for location in ("table", "index"):
            with self.subTest(location=location):
                schema = sample("sqlite")
                if location == "table":
                    schema["entities"][0]["name"] = "sqlite_items"
                else:
                    schema["entities"][0]["indexes"] = [{"name": "sqlite_items_label", "columns": ["label"]}]
                self.assertInvalid(schema)
                self.assertIn("reserved_identifier", {issue["code"] for issue in core.validate_schema(schema)["issues"]})
                for dialect in ("postgresql", "mysql"):
                    schema["database"] = dialect
                    self.assertTrue(core.validate_schema(schema)["valid"])

    def test_sqlite_prefix_remains_valid_for_column_names(self):
        schema = sample("sqlite")
        schema["entities"][0]["attributes"][1]["name"] = "sqlite_label"
        with sqlite3.connect(":memory:") as db:
            db.executescript(core.export_sql(schema))
            db.execute("INSERT INTO items(id,sqlite_label) VALUES (1,'ok')")
            self.assertEqual(db.execute("SELECT sqlite_label FROM items").fetchone()[0], "ok")

    def test_all_field_type_mutations_return_issues_without_throwing(self):
        schema = core.create_schema("booking", "Test")
        schema["entities"][1]["attributes"][3]["default"] = 1
        mutations = [None, [], {}, ["bad"], {"bad": 1}, True, 1, 1.5, "bad", 10 ** 500]
        paths = []

        def paths_in(value, path=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    paths.append(path + (key,))
                    paths_in(child, path + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    paths.append(path + (index,))
                    paths_in(child, path + (index,))

        paths_in(schema)
        for path in paths:
            for value in mutations:
                with self.subTest(path=path, type=type(value).__name__):
                    changed = copy.deepcopy(schema)
                    target = changed
                    for part in path[:-1]:
                        target = target[part]
                    target[path[-1]] = value
                    result = core.validate_schema(changed)
                    self.assertIsInstance(result["valid"], bool)
                    self.assertIsInstance(result["issues"], list)

    def test_two_thousand_attributes_supported_but_more_rejected(self):
        schema = sample()
        schema["entities"][0]["attributes"] = [attribute("id", "integer", primary_key=True)]
        schema["entities"][0]["attributes"] += [attribute("field_" + str(i), nullable=True) for i in range(1999)]
        self.assertTrue(core.validate_schema(schema)["valid"])
        schema["entities"][0]["attributes"].append(attribute("extra"))
        self.assertInvalid(schema)

    def test_schema_byte_limit_applies_to_multibyte_text(self):
        schema = sample()
        schema["entities"][0]["attributes"] += [attribute("field_" + str(i), "text", default="가" * 9000) for i in range(80)]
        result = core.validate_schema(schema)
        self.assertFalse(result["valid"])
        self.assertEqual(result["issues"][0]["code"], "input_too_large")

    def test_integer_literals_preserve_browser_round_trips(self):
        for number, expected in [(9007199254740991, True), (-9007199254740991, True),
                                 (9007199254740992, False), (-9007199254740992, False)]:
            schema = sample()
            schema["entities"][0]["attributes"].append(attribute("large_number", "bigint", default=number))
            self.assertEqual(core.validate_schema(schema)["valid"], expected)


class ExportTests(unittest.TestCase):
    def test_sqlite_literal_defaults_cannot_execute_sql(self):
        schema = sample()
        payload = "'); DROP TABLE items; -- \\ ' 한글"
        schema["entities"][0]["attributes"][1]["default"] = payload
        with sqlite3.connect(":memory:") as db:
            db.executescript(core.export_sql(schema))
            db.execute("INSERT INTO items(id) VALUES (1)")
            self.assertEqual(db.execute("SELECT label FROM items").fetchone()[0], payload)

    def test_mysql_literals_avoid_sql_mode_escaping(self):
        schema = sample("mysql")
        schema["entities"][0]["attributes"][1]["default"] = "\\'); DROP TABLE items; --"
        sql = core.export_sql(schema)
        self.assertNotIn("DROP TABLE", sql)
        self.assertIn("DEFAULT", sql)

    def test_sqlite_does_not_invent_an_assigned_primary_key(self):
        with sqlite3.connect(":memory:") as db:
            db.executescript(core.export_sql(sample()))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO items(label) VALUES ('Explicit id required')")

    def test_mysql_unsupported_defaults_and_indexes_are_explicit(self):
        schema = sample("mysql")
        schema["entities"][0]["attributes"][1] = attribute("label", "text", default="value")
        with self.assertRaisesRegex(ValueError, "^unsupported_default$"):
            core.export_sql(schema)
        schema["entities"][0]["attributes"][1] = attribute("label", "text", unique=True)
        with self.assertRaisesRegex(ValueError, "^unsupported_mysql_index$"):
            core.export_sql(schema)

    def test_java_package_prefix_field_is_safe_for_static_initializers(self):
        schema = sample()
        schema["entities"][0]["attributes"].extend([
            attribute("java"), attribute("created", "timestamp", default={"function": "current_timestamp"})])
        files = core.export_java(schema)
        entity = next(f["content"] for f in files if f["path"].endswith("/Items.java"))
        self.assertIn("String javaValue", entity)

    def test_cyclic_references_and_unique_index_execute_in_sqlite(self):
        schema = sample()
        schema["entities"][0]["attributes"].append(attribute("peer_id", "integer", nullable=True))
        schema["entities"].append({"name": "peers", "attributes": [
            attribute("id", "integer", primary_key=True), attribute("item_id", "integer", nullable=True)
        ], "indexes": []})
        schema["relations"] = [
            {"name": "fk_items_peers", "from": {"entity": "items", "columns": ["peer_id"]},
             "to": {"entity": "peers", "columns": ["id"]}, "on_delete": "set_null"},
            {"name": "fk_peers_items", "from": {"entity": "peers", "columns": ["item_id"]},
             "to": {"entity": "items", "columns": ["id"]}, "on_delete": "restrict"},
        ]
        for dialect in ("sqlite", "postgresql", "mysql"):
            schema["database"] = dialect
            sql = core.export_sql(schema)
            if dialect == "sqlite":
                with sqlite3.connect(":memory:") as db:
                    db.execute("PRAGMA foreign_keys=ON")
                    db.executescript(sql)
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute("INSERT INTO peers(id,item_id) VALUES (1,999)")
            else:
                self.assertGreater(sql.index("ALTER TABLE"), sql.index('CREATE TABLE ' + ('`peers`' if dialect == 'mysql' else '"peers"')))

    def test_composite_unique_fk_is_executable(self):
        schema = sample()
        schema["entities"][0]["indexes"] = [{"name": "uq_item_pair", "columns": ["id", "label"], "unique": True}]
        schema["entities"].append({"name": "links", "attributes": [
            attribute("id", "integer", primary_key=True), attribute("item_id", "integer"),
            attribute("item_label", length=100)], "indexes": []})
        schema["relations"] = [{"name": "fk_links_items", "from": {"entity": "links", "columns": ["item_id", "item_label"]},
                                "to": {"entity": "items", "columns": ["id", "label"]}, "on_delete": "restrict"}]
        with sqlite3.connect(":memory:") as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(core.export_sql(schema))
            db.execute("INSERT INTO items VALUES (1,'A')")
            db.execute("INSERT INTO links VALUES (1,1,'A')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO links VALUES (2,1,'B')")

    def test_java_entities_keep_table_names_and_scalar_foreign_keys(self):
        files = core.export_java(core.create_schema("booking", "Demo"), "org.example.demo")
        by_path = {f["path"]: f["content"] for f in files}
        entity = by_path["src/main/java/org/example/demo/Bookings.java"]
        self.assertIn('@Table(name = "bookings")', entity)
        self.assertIn("private Long customerId;", entity)
        self.assertNotIn("@ManyToOne", entity)
        self.assertIn("JpaRepository<Bookings, Long>", by_path["src/main/java/org/example/demo/BookingsRepository.java"])
        self.assertIn("setCustomerId", entity)

    def test_java_keyword_names_are_safe_and_collisions_rejected(self):
        schema = sample()
        schema["entities"][0]["attributes"][1]["name"] = "class"
        files = core.export_java(schema)
        content = next(f["content"] for f in files if f["path"].endswith("/Items.java"))
        self.assertIn("classValue", content)
        schema["entities"][0]["attributes"].append(attribute("class_value"))
        with self.assertRaisesRegex(ValueError, "^java_name_collision$"):
            core.export_java(schema)
        for package in ["../x", "com.class.app", "com..app", "foo; import secret", "a" * 1000, None]:
            with self.subTest(package=package), self.assertRaisesRegex(ValueError, "^invalid_java_package$"):
                core.export_java(sample(), package)

    def test_java_file_name_and_generated_symbol_collisions_rejected(self):
        for names in [("a_b", "a__b"), ("items", "items_repository"), ("a", "a_")]:
            schema = sample()
            schema["entities"][0]["name"] = names[0]
            second = copy.deepcopy(schema["entities"][0])
            second["name"] = names[1]
            schema["entities"].append(second)
            with self.assertRaisesRegex(ValueError, "^java_name_collision$"):
                core.export_java(schema)

    def test_java_json_requires_explicit_provider_mapping(self):
        schema = sample()
        schema["entities"][0]["attributes"].append(attribute("metadata", "json", nullable=True))
        with self.assertRaisesRegex(ValueError, "^java_json_mapping_unsupported$"):
            core.export_java(schema)

    @unittest.skipUnless(os.environ.get("CHANNELSHIFT_JAVA_TEST_CLASSPATH") and shutil.which("javac"),
                         "Set CHANNELSHIFT_JAVA_TEST_CLASSPATH to actual Jakarta/Spring Data jars and install javac")
    def test_actual_java_apis_compile_all_templates_dialects_and_literals(self):
        generated = []
        for template in ("membership", "content", "booking", "commerce"):
            for dialect in ("postgresql", "mysql", "sqlite"):
                generated += core.export_java(core.create_schema(template, "Compile", dialect), f"qa.{template}.{dialect}")
        for dialect in ("postgresql", "mysql", "sqlite"):
            schema = sample(dialect)
            schema["entities"][0]["name"] = "class"
            schema["entities"][0]["attributes"] += [
                attribute("java", default="Package prefix"),
                attribute("class", default="\"; System.exit(0); // \\u000a \n 한글"),
                attribute("record", "integer", default=-2147483648),
                attribute("sealed", "bigint", default=9007199254740991),
                attribute("when", "boolean", default=True),
                attribute("amount", "decimal", default=12.25),
                attribute("day", "date", default="2026-09-29"),
                attribute("at_time", "timestamp", default="2026-09-29 10:30:00.123456"),
                attribute("created", "timestamp", default={"function": "current_timestamp"}),
                attribute("token", "uuid", default="6114a486-a1e6-47f1-975c-286f9f4981a3"),
                attribute("notes", "text", default="Line\nTab\tSlash\\Quote\""),
            ]
            generated += core.export_java(schema, f"qa.literals.{dialect}")
        with tempfile.TemporaryDirectory(prefix="channelshift-javac-") as folder:
            files = []
            for file in generated:
                if file["path"].endswith(".java"):
                    destination = Path(folder, file["path"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(file["content"], encoding="utf-8")
                    files.append(str(destination))
            response = subprocess.run([shutil.which("javac"), "--release", "17", "-proc:none", "-encoding", "UTF-8",
                                       "-classpath", os.environ["CHANNELSHIFT_JAVA_TEST_CLASSPATH"], "-d", str(Path(folder, "classes")), *files],
                                      capture_output=True, text=True, timeout=90)
            self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
            self.assertEqual(len(list(Path(folder, "classes").rglob("*.class"))), 84)


if __name__ == "__main__":
    unittest.main()
