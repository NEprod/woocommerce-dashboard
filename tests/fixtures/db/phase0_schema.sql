CREATE TABLE category (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(191) NOT NULL UNIQUE,
    slug VARCHAR(191), woo_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME
);
CREATE INDEX ix_category_woo_id ON category (woo_id);
CREATE TABLE collection (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(191) NOT NULL, slug VARCHAR(191),
    root_path VARCHAR(1024) NOT NULL UNIQUE,
    sku_prefix VARCHAR(64) NOT NULL UNIQUE,
    shared_json_path VARCHAR(1024) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME
);
CREATE INDEX ix_collection_slug ON collection (slug);
CREATE TABLE service (
    id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(100), type VARCHAR(20),
    renewal_date DATE, auto_renew BOOLEAN, notes VARCHAR(255)
);
CREATE TABLE settings (
    id INTEGER NOT NULL PRIMARY KEY, product_folder VARCHAR(512),
    output_folder VARCHAR(512), url_prefix VARCHAR(512)
);
CREATE TABLE tag (
    id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(191) NOT NULL UNIQUE,
    slug VARCHAR(191), woo_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME
);
CREATE INDEX ix_tag_woo_id ON tag (woo_id);
CREATE TABLE user (
    id INTEGER NOT NULL PRIMARY KEY, email VARCHAR(120) NOT NULL UNIQUE,
    username VARCHAR(64) NOT NULL UNIQUE, password VARCHAR(128) NOT NULL,
    is_admin BOOLEAN
);
CREATE TABLE product (
    id INTEGER NOT NULL PRIMARY KEY, sku VARCHAR(64), title VARCHAR(255) NOT NULL,
    slug VARCHAR(255), product_type VARCHAR(20), collection_type VARCHAR(50),
    collection_id INTEGER REFERENCES collection (id), product_dir VARCHAR(1024),
    shared_json_path VARCHAR(1024), override_json_path VARCHAR(1024),
    effective_json_path VARCHAR(1024), regular_price NUMERIC(10, 2),
    sale_price NUMERIC(10, 2), sale_start DATETIME, sale_end DATETIME,
    manage_stock BOOLEAN, stock_quantity INTEGER, backorders VARCHAR(20),
    weight NUMERIC(10, 3), length NUMERIC(10, 3), width NUMERIC(10, 3),
    height NUMERIC(10, 3), shipping_class VARCHAR(64), short_description TEXT,
    description TEXT, external_url VARCHAR(255), button_text VARCHAR(100),
    upsell_ids TEXT, cross_sell_ids TEXT, status VARCHAR(20),
    catalog_visibility VARCHAR(20), reviews_allowed BOOLEAN, featured BOOLEAN,
    image_url VARCHAR(512), woo_id INTEGER, woo_synced_at DATETIME,
    woo_updated_at DATETIME, local_updated_at DATETIME, created_at DATETIME
);
CREATE UNIQUE INDEX ix_product_sku ON product (sku);
CREATE INDEX ix_product_collection_id ON product (collection_id);
CREATE INDEX ix_product_woo_id ON product (woo_id);
CREATE INDEX ix_product_product_dir ON product (product_dir);
CREATE TABLE product_categories (
    product_id INTEGER NOT NULL REFERENCES product (id),
    category_id INTEGER NOT NULL REFERENCES category (id),
    PRIMARY KEY (product_id, category_id)
);
CREATE TABLE product_image (
    id INTEGER NOT NULL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES product (id),
    url VARCHAR(512) NOT NULL, alt_text VARCHAR(255), position INTEGER,
    woo_id INTEGER
);
CREATE INDEX ix_product_image_product_id ON product_image (product_id);
CREATE INDEX ix_product_image_woo_id ON product_image (woo_id);
CREATE TABLE product_tags (
    product_id INTEGER NOT NULL REFERENCES product (id),
    tag_id INTEGER NOT NULL REFERENCES tag (id),
    PRIMARY KEY (product_id, tag_id)
);
CREATE TABLE variation (
    id INTEGER NOT NULL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES product (id), sku VARCHAR(64),
    regular_price NUMERIC(10, 2), sale_price NUMERIC(10, 2),
    sale_start DATETIME, sale_end DATETIME, manage_stock BOOLEAN,
    stock_quantity INTEGER, backorders VARCHAR(20), weight NUMERIC(10, 3),
    length NUMERIC(10, 3), width NUMERIC(10, 3), height NUMERIC(10, 3),
    image_url VARCHAR(512), is_default BOOLEAN, visible BOOLEAN,
    status VARCHAR(20), menu_order INTEGER, woo_id INTEGER,
    woo_synced_at DATETIME, woo_updated_at DATETIME, local_updated_at DATETIME
);
CREATE INDEX ix_variation_woo_id ON variation (woo_id);
CREATE UNIQUE INDEX ix_variation_sku ON variation (sku);
CREATE INDEX ix_variation_product_id ON variation (product_id);
CREATE TABLE product_asset (
    id INTEGER NOT NULL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES product (id),
    variation_id INTEGER REFERENCES variation (id), path VARCHAR(1024) NOT NULL,
    kind VARCHAR(50), label VARCHAR(255), is_primary BOOLEAN,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_product_asset_product_id ON product_asset (product_id);
CREATE INDEX ix_product_asset_variation_id ON product_asset (variation_id);
CREATE TABLE variation_attribute (
    id INTEGER NOT NULL PRIMARY KEY,
    variation_id INTEGER NOT NULL REFERENCES variation (id),
    name VARCHAR(100) NOT NULL, value VARCHAR(191) NOT NULL
);
CREATE INDEX ix_variation_attribute_variation_id ON variation_attribute (variation_id);
CREATE TABLE variation_image (
    id INTEGER NOT NULL PRIMARY KEY,
    variation_id INTEGER NOT NULL REFERENCES variation (id),
    url VARCHAR(512) NOT NULL, alt_text VARCHAR(255), position INTEGER
);
CREATE INDEX ix_variation_image_variation_id ON variation_image (variation_id);
