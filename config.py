import os


class Config:
    # None preserves editable local-installation settings; explicit blank values
    # remain deployment-owned and report not-ready rather than falling back.
    PRODUCT_FOLDER = os.getenv("PRODUCT_FOLDER")
    OUTPUT_FOLDER = os.getenv("OUTPUT_FOLDER")
    URL_PREFIX = os.getenv("URL_PREFIX")
    INTAKE_ROOT = os.getenv("INTAKE_ROOT", "/intake")
    TAXONOMY_ROOT = os.getenv("TAXONOMY_ROOT", "/taxonomy")
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = "sqlite:///../instance/site.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LOGIN_VIEW = "main.login"
    # Bounded Orders history; read_orders alone does not grant older-than-60-day access.
    SHOPIFY_ORDERS_INITIAL_DAYS = int(os.getenv('SHOPIFY_ORDERS_INITIAL_DAYS', '30'))
    SHOPIFY_ORDERS_BATCH_SIZE = int(os.getenv('SHOPIFY_ORDERS_BATCH_SIZE', '5'))
    SHOPIFY_ORDERS_STARTUP_ENABLED = os.getenv('SHOPIFY_ORDERS_STARTUP_ENABLED','true').lower()=='true'
    # Optional outbound encrypted webhook queue. Never exposes the dashboard.
    SHOPIFY_ORDERS_WEBHOOK_URL = os.getenv('SHOPIFY_ORDERS_WEBHOOK_URL','')
    SHOPIFY_ORDERS_WEBHOOK_TOKEN = os.getenv('SHOPIFY_ORDERS_WEBHOOK_TOKEN','')
    SHOPIFY_ORDERS_WEBHOOK_KEY = os.getenv('SHOPIFY_ORDERS_WEBHOOK_KEY','')
    SHOPIFY_ORDERS_WEBHOOK_SECRET = os.getenv('SHOPIFY_ORDERS_WEBHOOK_SECRET','')
    SHOPIFY_ORDERS_WEBHOOK_DOMAIN = os.getenv('SHOPIFY_ORDERS_WEBHOOK_DOMAIN','')
    SHOPIFY_ORDERS_WEBHOOK_STORE_ID = os.getenv('SHOPIFY_ORDERS_WEBHOOK_STORE_ID','')
