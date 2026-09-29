"""Four small database starters authored for ChannelShift's native model."""


def _field(name, kind="varchar", nullable=False, **options):
    return {"name": name, "type": kind, "nullable": nullable, **options}


def _id():
    return _field("id", "bigint", primary_key=True)


def _created():
    return _field("created_at", "timestamp", default={"function": "current_timestamp"})


def _entity(name, description, attributes, indexes=None):
    return {"name": name, "description": description, "attributes": attributes, "indexes": indexes or []}


def _index(name, columns, unique=False):
    return {"name": name, "columns": columns, "unique": unique}


def _relation(name, source, column, target, on_delete="restrict"):
    return {"name": name, "from": {"entity": source, "columns": [column]},
            "to": {"entity": target, "columns": ["id"]}, "on_delete": on_delete}


def _customers():
    return _entity("customers", "Customer contact records", [
        _id(), _field("display_name", length=120), _field("email", length=254, unique=True),
        _field("phone", length=40, nullable=True), _created(),
    ])


STARTERS = {
    "membership": {
        "title": "회원·권한", "description": "회원, 역할, 역할 할당의 기본 구조입니다.",
        "entities": [
            _entity("users", "Application members; authentication lives in the application's identity provider", [
                _id(), _field("email", length=254, unique=True), _field("display_name", length=120),
                _field("active", "boolean", default=True), _created(),
            ]),
            _entity("roles", "Named application roles", [
                _id(), _field("name", length=80, unique=True), _field("description", "text", nullable=True),
            ]),
            _entity("user_roles", "A member's role assignments", [
                _id(), _field("user_id", "bigint"), _field("role_id", "bigint"), _created(),
            ], [_index("uq_user_roles_pair", ["user_id", "role_id"], True), _index("idx_user_roles_role", ["role_id"])]),
        ],
        "relations": [
            _relation("fk_user_roles_user", "user_roles", "user_id", "users", "cascade"),
            _relation("fk_user_roles_role", "user_roles", "role_id", "roles", "cascade"),
        ],
    },
    "content": {
        "title": "콘텐츠·게시물", "description": "작성자, 분류, 게시물을 관리하는 기본 구조입니다.",
        "entities": [
            _entity("authors", "People credited as content authors", [
                _id(), _field("display_name", length=120), _field("bio", "text", nullable=True),
            ]),
            _entity("categories", "Editorial categories", [
                _id(), _field("name", length=120), _field("slug", length=160, unique=True),
            ]),
            _entity("posts", "Draft and published content", [
                _id(), _field("author_id", "bigint"), _field("category_id", "bigint", nullable=True),
                _field("title", length=240), _field("slug", length=240, unique=True),
                _field("body", "text"), _field("status", length=24, default="draft"),
                _created(), _field("published_at", "timestamp", nullable=True),
            ], [_index("idx_posts_author", ["author_id"]), _index("idx_posts_category", ["category_id"])]),
        ],
        "relations": [
            _relation("fk_posts_author", "posts", "author_id", "authors"),
            _relation("fk_posts_category", "posts", "category_id", "categories", "set_null"),
        ],
    },
    "booking": {
        "title": "예약·서비스", "description": "고객, 서비스, 예약을 연결하는 기본 구조입니다.",
        "entities": [
            _customers(),
            _entity("services", "Bookable services", [
                _id(), _field("name", length=160), _field("duration_minutes", "integer"),
                _field("price", "decimal", precision=19, scale=2), _field("active", "boolean", default=True),
            ]),
            _entity("bookings", "Scheduled service requests; capacity rules belong to the application", [
                _id(), _field("customer_id", "bigint"), _field("service_id", "bigint"),
                _field("starts_at", "timestamp"), _field("status", length=24, default="requested"),
                _field("notes", "text", nullable=True), _created(),
            ], [_index("idx_bookings_customer", ["customer_id"]), _index("idx_bookings_service_start", ["service_id", "starts_at"])]),
        ],
        "relations": [
            _relation("fk_bookings_customer", "bookings", "customer_id", "customers"),
            _relation("fk_bookings_service", "bookings", "service_id", "services"),
        ],
    },
    "commerce": {
        "title": "상품·주문", "description": "고객, 상품, 주문과 주문 항목의 기본 구조입니다.",
        "entities": [
            _customers(),
            _entity("products", "Saleable catalog entries", [
                _id(), _field("sku", length=80, unique=True), _field("name", length=200),
                _field("unit_price", "decimal", precision=19, scale=2),
                _field("active", "boolean", default=True),
            ]),
            _entity("orders", "Customer order headers without payment credentials", [
                _id(), _field("customer_id", "bigint"), _field("status", length=24, default="draft"),
                _field("currency", length=3, default="KRW"), _created(),
            ], [_index("idx_orders_customer", ["customer_id"])]),
            _entity("order_items", "Price snapshots for ordered catalog entries", [
                _id(), _field("order_id", "bigint"), _field("product_id", "bigint"),
                _field("quantity", "integer", default=1), _field("unit_price", "decimal", precision=19, scale=2),
            ], [_index("idx_order_items_order", ["order_id"]), _index("idx_order_items_product", ["product_id"])]),
        ],
        "relations": [
            _relation("fk_orders_customer", "orders", "customer_id", "customers"),
            _relation("fk_order_items_order", "order_items", "order_id", "orders", "cascade"),
            _relation("fk_order_items_product", "order_items", "product_id", "products"),
        ],
    },
}
