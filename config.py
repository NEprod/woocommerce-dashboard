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
