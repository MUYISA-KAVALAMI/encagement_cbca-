from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from flask_login import UserMixin

# Modèles de données
class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), default='user')
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
class CarteBapteme(db.Model):
    __tablename__='cartebapteme'
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(100), nullable=False) 
    nom = db.Column(db.String(100), nullable=False) 
    adresse = db.Column(db.String(200))
    date_naissance = db.Column(db.Date)
    sexe = db.Column(db.String(1))
    photo = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    membre = db.relationship('Membre', backref='cartebapteme', lazy=True)

class Membre(db.Model):
    __tablename__ = 'membres'
    id = db.Column(db.Integer, primary_key=True)
    code_membre = db.Column(db.String(20), unique=True)
    telephone = db.Column(db.String(20),nullable=False)
    groupe = db.Column(db.String(50))
    statut = db.Column(db.String(20), default='actif')
    apikey_callmebot = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    cate_id = db.Column(db.Integer, db.ForeignKey('cartebapteme.id'), nullable=False)
    # Relation avec les engagements
    engagements = db.relationship('Engagement', backref='membre', lazy=True)


class Engagement(db.Model):
    __tablename__ = 'engagements'
    id = db.Column(db.Integer, primary_key=True)
    membre_id = db.Column(db.Integer, db.ForeignKey('membres.id'), nullable=False)
    montant_total = db.Column(db.Float, nullable=False)
    date_engagement = db.Column(db.DateTime, default=datetime.utcnow)
    date_limite = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200))
    statut = db.Column(db.String(20), default='En cours')

    paiements = db.relationship('Paiement', backref='engagement', lazy=True)

    def montant_restant(self):
        """Montant restant à ce jour"""
        return self.montant_total - sum(p.montant for p in self.paiements)

    def montant_restant_au(self, date_reference):
        return self.montant_total - sum(p.montant for p in self.paiements if p.date_paiement <= date_reference)


class Paiement(db.Model):
    __tablename__ = 'paiements'
    id = db.Column(db.Integer, primary_key=True)
    engagement_id = db.Column(db.Integer, db.ForeignKey('engagements.id'), nullable=False)
    montant = db.Column(db.Float, nullable=False)
    date_paiement = db.Column(db.Date, default=datetime.utcnow)

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    membre_id = db.Column(db.Integer, db.ForeignKey('membres.id'), nullable=False)
    engagement_id = db.Column(db.Integer, db.ForeignKey('engagements.id'), nullable=True)
    date_envoi = db.Column(db.DateTime, default=datetime.utcnow)
    statut = db.Column(db.String(20), default='envoyé')  # ou 'échoué'
    message = db.Column(db.Text)

    membre = db.relationship('Membre', backref='notifications')
    engagement = db.relationship('Engagement', backref='notifications')
