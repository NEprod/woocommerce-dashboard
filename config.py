import os


class Config:
    TAXONOMY_ROOT = os.getenv("TAXONOMY_ROOT", "/taxonomy")
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = "sqlite:///../instance/site.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LOGIN_VIEW = "main.login"
