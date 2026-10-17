"""Local package catalogue and retained Shopify association evidence."""
from datetime import datetime
from app import db


class Package(db.Model):
    __tablename__ = 'packaging_package'
    id = db.Column(db.String(36), primary_key=True)
    seed_key = db.Column(db.String(60), unique=True)
    name = db.Column(db.String(191), nullable=False, index=True)
    archived = db.Column(db.Boolean, nullable=False, default=False)
    version = db.Column(db.Integer, nullable=False, default=1)
    specification = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PackageLink(db.Model):
    __tablename__ = 'packaging_link'
    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.String(36), db.ForeignKey('packaging_package.id'), nullable=False)
    store_id = db.Column(db.String(96), nullable=False)
    remote_id = db.Column(db.String(96), nullable=False)
    observed = db.Column(db.Text, nullable=False)
    checked_at = db.Column(db.DateTime, nullable=False)
    __table_args__ = (db.UniqueConstraint('store_id','remote_id'), db.UniqueConstraint('store_id','package_id'))


class PackagingEvidence(db.Model):
    __tablename__ = 'packaging_evidence'
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    checked_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
