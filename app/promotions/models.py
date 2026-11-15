from app import db


class Promotion(db.Model):
    __tablename__ = 'promotion'
    id = db.Column(db.String(32), primary_key=True)
    document = db.Column(db.Text, nullable=False)
    revision = db.Column(db.Integer, nullable=False, default=1)
    state = db.Column(db.String(24), nullable=False, default='local')
    receipt = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, nullable=False)
