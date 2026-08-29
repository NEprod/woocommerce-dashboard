import os


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = "sqlite:///../instance/site.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LOGIN_VIEW = "main.login"
