# ChannelShift independent implementation contract

This is a new implementation of a database starter generator and editor. Do not read, copy, translate, import or bundle drawDB code, its UI, schema validators or the sibling AGPL prototype. Implement the following requirements directly. Existing user applications and their files stay untouched. Independent third-party libraries retain their notices; no claim of copyright immunity or formal clean-room certification is made.

## Package and model

Python package `src/channelshift/`, Python >=3.10. Standard library for core, storage and loopback UI; official `mcp==2.2.0` only for stdio MCP. No frontend dependencies, external fonts or assets. No SQL execution, credentials, shell tools, arbitrary file path tools or automatic network sending.

Native model, `schema` below is an object, never a file path:

```json
{"format":"channelshift.schema/v1","name":"Demo","database":"postgresql","entities":[{"name":"customers","description":"Customers","attributes":[{"name":"id","type":"uuid","primary_key":true,"nullable":false,"unique":false},{"name":"email","type":"varchar","length":254,"nullable":false,"unique":true}],"indexes":[]}],"relations":[]}
```

Entity/attribute/index/relation names use lowercase `[a-z][a-z0-9_]{0,62}`. Names are unique in their relevant scope. Project name is ordinary bounded display text. Attribute types: `uuid`, `varchar`, `text`, `integer`, `bigint`, `decimal`, `boolean`, `date`, `timestamp`, `json`. Optional varchar `length` (1..65535), decimal `precision` (1..38) and `scale` (0..precision). Attributes require `name`, `type`, `nullable`; primary_key and unique default false. Each entity has exactly one non-null primary key, a scalar compatible with Java ID types. Optional default values are typed scalar literals or the object `{"function":"current_timestamp"}` for timestamp, never raw expressions. No record data, arbitrary expression/check SQL or live connection settings.

Index: `{"name":"idx_customer_email","columns":["email"],"unique":false}`. Relation: `{"name":"fk_booking_customer","from":{"entity":"bookings","columns":["customer_id"]},"to":{"entity":"customers","columns":["id"]},"on_delete":"restrict"}`. Allowed delete actions restrict, cascade, set_null, no_action. Foreign-key field types must match; target must be a primary/unique key; set_null needs nullable source. Empty schema allowed for editor; each nonempty entity must be complete before export. Reject unknown properties, nonfinite values, identifiers/duplicate IDs, unresolved refs, more than 100 entities/2000 total attributes, strings >10000 chars and body >2 MiB. Validation returns structured issues without throwing input contents.

Supported SQL dialects `postgresql`, `mysql`, `sqlite`. The same logical model is mapped to supported native types. Pure generation only. Quote identifiers for the selected dialect; literal defaults escaped, no DROP/data statements. SQLite ordering and FK syntax must work with `PRAGMA foreign_keys=ON`. Refuse or explicitly report unsupported conversions rather than silently change meaning.

## Core API (owned by core agent)

`core.py`: `list_templates() -> list[dict]` (id,title,description,tableCount); `create_schema(template_id, project, database="postgresql") -> schema`; `validate_schema(schema) -> {"valid":bool,"issues":[{"path":str,"code":str,"message":str}]}`; `export_sql(schema) -> str`; `export_java(schema, package="com.example.app") -> list[{"path":str,"content":str}]`. Invalid creation/export raises a `ValueError` with a constant safe code. Built-in templates are independently authored membership (users/roles/user_roles), content (authors/categories/posts), booking (customers/services/bookings), commerce (customers/products/orders/order_items). No password/payment credential fields. Generic DTO-style Java/Jakarta JPA entities and Spring Data repositories, no raw schema expressions in code. Existing project version/conventions still must be checked before using output. Document Java target baseline; table/column names retained, fields camelCase/classes PascalCase, Java keyword/collision handling deterministic or rejected. FK columns stay scalar in Java entities to avoid implicit cascade/loading assumptions.

## Persistence (root)

`store.py`: own local state under `CHANNELSHIFT_HOME` or `~/.channelshift`. Topic/project folder names generated safely. Stored project version IDs are SHA-256 of canonical native schema. Save immutable files; no overwrite under same ID; read only validated files by hash. MCP/UI never accept filesystem paths. Basic history listing and exact schema reads. No automatic SSH or connection to old application state. The previous installed app and private sync configuration remain separate.

## HTTP and browser UI (root HTTP, UI agent static files)

`web.py` loopback-only `127.0.0.1:5187` by default. Serve packaged `web/index.html`, `web/app.js`, `web/style.css`. Index has placeholder `__CHANNELSHIFT_TOKEN__` replaced with per-process CSRF token; JavaScript reads `<meta name="channelshift-token" content="...">`. POST JSON must include `X-ChannelShift-Token` and exact same-origin Origin. Host checked; no CORS, no external requests, CSP script/style self, no eval; all dynamic content via textContent.

- GET `/api/templates` -> `{ok:true,items:list_templates()}`
- GET `/api/projects` -> `{ok:true,items:[{id,name,database,updatedAt,tableCount}]}`
- GET `/api/projects/<64hex>` -> `{ok:true,id,schema}`
- POST `/api/generate` body `{templateId,project,database}` -> `{ok:true,schema}`
- POST `/api/validate` body `{schema}` -> `{ok:true,valid,issues}`
- POST `/api/export` body `{schema,format:"sql"|"java",package?}` -> `{ok:true,files:[{path,content}]}` (SQL filename schema.sql; Java multiple files downloadable separately or combined through JSON export).
- POST `/api/save` body `{schema}` -> `{ok:true,id,stored}`
- Errors `{ok:false,error:constant_code}` appropriate HTTP 4xx, no tracebacks or paths.

UI agent owns ONLY `src/channelshift/web/`. Standalone original white/charcoal/sage professional Korean workspace. Do not copy old visuals, components or assets. Template chooser and DB dialect switch, project name, entity/attribute editing (add/remove entities/attributes, type/nullable/PK), relationship list with add/remove controls, validation feedback, save/load versions, native JSON import/export, SQL preview/download, Java file preview/download. Sidebar project/template areas; spacious central entity cards, right output pane; responsive; keyboard labels. Initial empty state uses an original simple CSS/schema symbol, no mascot asset dependency. Explicit actions, no fabricated success, no remote sync claims. Autoload templates/projects only; user must generate/save/export. Core validation determines correctness. Removing a field/entity should remove attached references or show validation issues; never hide loss silently.

## MCP (root)

Official SDK v2 `from mcp.server import MCPServer`, `mcp_types` CallToolResult. stdio only, no stdout logs. Eight tools: list_templates, create_schema_from_template, validate_schema, export_sql, export_java, save_project, list_projects, get_project. Effects annotations reflect writes; none uses network. Direct schema objects are arguments. Descriptive structured JSON results and constant errors; arbitrary strings in schema are data, not instructions. SQLite/Java validation and actual SDK client stdio tests before publishing.

## Release

New GitHub repo already exists at hayansnapofficial-cmd/channelshift but no code pushed. Fresh independent folder is this repo's eventual source. Do not copy old AGPL LICENSE or relabel old source. New independently written code can be MIT; third-party notices and dependency license inventory included. Deliver wheel/sdist, standalone skill ZIP (no old editor), source and checksums. Installer must preserve existing skills/app profiles. Skill applies templates to existing Java/Spring, JS/TS or Python project using its current ORM and migration conventions. NoSQL is outside v0.1.0.
