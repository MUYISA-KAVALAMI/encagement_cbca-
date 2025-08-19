# app.py
import os, re
import random
import string
from datetime import datetime, timedelta, date

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, current_user, login_required
from flask_migrate import Migrate
from flask_apscheduler import APScheduler
from werkzeug.utils import secure_filename
from functools import wraps

from extensions import db
from utils import envoyer_whatsapp

# -----------------------
# App config
# -----------------------
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///cbca.db'
app.config['SECRET_KEY'] = os.urandom(24).hex()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

db.init_app(app)
migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

scheduler = APScheduler()
scheduler.init_app(app)

# Eviter double démarrage en mode debug
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    try:
        scheduler.start()
    except Exception:
        pass

# Late import models
with app.app_context():
    from models import User, Engagement, Paiement, Membre, CarteBapteme
    from tasks import notifier_engagements_proches

    # Tâche périodique toutes les 24h
    if 'rappel_whatsapp' not in [j.id for j in scheduler.get_jobs()]:
        scheduler.add_job(
            id='rappel_whatsapp',
            func=notifier_engagements_proches,
            trigger='interval',
            hours=24
        )

# -----------------------
# Helpers
# -----------------------
def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def telephone_valide(numero: str):
    return re.match(r'^\+?[0-9]{9,15}$', numero)

def generer_code_membre():
    dernier_membre = Membre.query.order_by(Membre.id.desc()).first()
    numero = 1 if not dernier_membre else (dernier_membre.id + 1)
    return f"CBCA-VUL-{numero:04d}"

# -----------------------
# Auth loader
# -----------------------
@login_manager.user_loader
def load_user(user_id):
    from models import User
    return User.query.get(int(user_id))

# -----------------------
# Decorators
# -----------------------
def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                flash("Accès non autorisé", "danger")
                return redirect(url_for('accueil'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Accès réservé aux administrateurs', 'danger')
            return redirect(url_for('accueil'))
        return f(*args, **kwargs)
    return decorated_function

# -----------------------
# Auth routes
# -----------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    from models import User
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            flash('Connexion réussie!', 'success')
            if user.role == 'membre':
                return redirect(url_for('accueil_membre'))
            return redirect(url_for('accueil'))
        flash('Identifiant ou mot de passe incorrect', 'danger')
    return render_template('login.html', title="Connexion")

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Vous avez été déconnecté', 'info')
    return redirect(url_for('login'))

# -----------------------
# Dashboard
# -----------------------
@app.route('/')
@login_required
def accueil():
    from models import Engagement, Paiement, Membre
    stats = {
        'total_membres': Membre.query.count(),
        'total_engagements': Engagement.query.count(),
        'paiements_mois': Paiement.query.filter(
            Paiement.date_paiement >= datetime.now().date() - timedelta(days=30)
        ).count(),
        'montant_mois': db.session.query(db.func.sum(Paiement.montant)).filter(
            Paiement.date_paiement >= datetime.now().date() - timedelta(days=30)
        ).scalar() or 0
    }
    return render_template('index.html', title="Tableau de bord", stats=stats)

# -----------------------
# Membres
# -----------------------
@app.route('/membres')
@login_required
def liste_membres():
    from models import Membre, CarteBapteme
    search = request.args.get('search', '').strip()
    query = Membre.query
    if search:
        query = query.join(CarteBapteme, isouter=True).filter(CarteBapteme.nom.ilike(f'%{search}%'))
    membres = query.order_by(Membre.id.desc()).all()
    cartes = CarteBapteme.query.order_by(CarteBapteme.nom).all()
    return render_template('membres/liste.html', title="Membres", membres=membres, cartes=cartes, search=search)

@app.route('/membres/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_membre():
    from models import Membre, CarteBapteme
    if request.method == 'POST':
        try:
            telephone = request.form.get('telephone', '').strip()
            carte_id = request.form.get('carte')
            groupe = request.form.get('groupe')
            api_key = request.form.get('api')

            if not telephone:
                flash('Le Téléphone est obligatoire', 'danger')
                return redirect(url_for('ajouter_membre'))
            if not telephone_valide(telephone):
                flash("Numéro de téléphone invalide. Exemple: +243970000000", 'warning')
                return redirect(url_for('ajouter_membre'))

            nouveau_membre = Membre(
                code_membre=generer_code_membre(),
                cate_id=int(carte_id) if carte_id else None,
                telephone=telephone,
                groupe=groupe,
                apikey_callmebot=api_key,
                statut='actif'
            )
            db.session.add(nouveau_membre)
            db.session.commit()

            from models import User

            # Vérifier si un utilisateur existe déjà avec ce téléphone
            if not User.query.filter_by(username=nouveau_membre.telephone).first():
                lettres = ''.join(random.choices(string.ascii_uppercase, k=2))
                chiffres = ''.join(random.choices(string.digits, k=4))
                mot_de_passe = lettres + chiffres

                nouvel_user = User(
                    username=nouveau_membre.telephone,
                    role='membre'
                )
                nouvel_user.set_password(mot_de_passe)
                db.session.add(nouvel_user)
                db.session.commit()

                # Envoi de la notification WhatsApp avec le mot de passe
                if nouveau_membre.apikey_callmebot and nouveau_membre.telephone:
                    nom = nouveau_membre.cartebapteme.nom if nouveau_membre.cartebapteme else nouveau_membre.code_membre
                    message = (
                        f"Bienvenue {nom} à la CBCA VULUMBI !\n"
                        f"Votre inscription a été enregistrée avec succès.\n"
                        f"Compte utilisateur créé :\n"
                        f"Identifiant : {nouveau_membre.telephone}\n"
                        f"Mot de passe : {mot_de_passe}\n"
                        f"Vous pouvez vous connecter ici https://encagement-cbca.onrender.com pour souscrire et suivre vos opérations."
        
                    )
                    envoyer_whatsapp(nouveau_membre.telephone, nouveau_membre.apikey_callmebot, message)
            else:
                flash("Un utilisateur existe déjà avec ce numéro. Aucun nouveau compte utilisateur créé.", "warning")
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur lors de l'ajout du membre: {str(e)}", 'danger')

    cartes = CarteBapteme.query.order_by(CarteBapteme.nom).all()
    groupes = ['Chorale', 'Jeunesse', 'Dames', 'Hommes', 'Enfants']
    return render_template('membres/ajouter.html', title="Ajouter un membre", groupes=groupes, cartes=cartes)

@app.route('/membres/modifier/<int:id>', methods=['GET', 'POST'])
@login_required
def modifier_membre(id):
    from models import Membre, CarteBapteme
    membre = Membre.query.get_or_404(id)

    cartes = CarteBapteme.query.order_by(CarteBapteme.nom).all()
    groupes = ['Chorale', 'Jeunesse', 'Dames', 'Hommes', 'Enfants']

    if request.method == 'POST':
        try:
            telephone = request.form.get('telephone', '').strip()
            carte_id = request.form.get('carte')
            groupe = request.form.get('groupe')
            api_key = request.form.get('api')

            if not telephone:
                flash('Le Téléphone est obligatoire', 'danger')
                return redirect(url_for('modifier_membre', id=id))
            if not telephone_valide(telephone):
                flash("Numéro de téléphone invalide. Exemple: +243970000000", 'warning')
                return redirect(url_for('modifier_membre', id=id))

            membre.telephone = telephone
            membre.carte_id = int(carte_id) if carte_id else None
            membre.groupe = groupe
            membre.apikey_callmebot = api_key
            db.session.commit()
            flash('Membre modifié avec succès', 'success')
            return redirect(url_for('liste_membres'))
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur lors de la modification du membre: {str(e)}", 'danger')

    return render_template('membres/modifier.html', title="Modifier membre", membre=membre, groupes=groupes, cartes=cartes)

@app.route('/membres/supprimer/<int:id>', methods=['POST'])
@login_required
@admin_required
def supprimer_membre(id):
    from models import Membre, User
    membre = Membre.query.get_or_404(id)
    try:
        # Supprimer l'utilisateur lié si existe
        user = User.query.filter_by(username=membre.telephone).first()
        if user:
            db.session.delete(user)
        db.session.delete(membre)
        db.session.commit()
        flash('Membre (et utilisateur lié) supprimé avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de la suppression du membre: {str(e)}", "danger")
    return redirect(url_for('liste_membres'))

@app.route('/membres/<int:id>')
@login_required
def detail_membre(id):
    from models import Membre, Engagement, Paiement
    membre = Membre.query.get_or_404(id)
    engagements = Engagement.query.filter_by(membre_id=id).order_by(Engagement.date_limite).all()
    # derniers paiements liés à ses engagements
    paiements = Paiement.query.join(Engagement).filter(Engagement.membre_id == id)\
        .order_by(Paiement.date_paiement.desc()).limit(10).all()
    return render_template('membres/detail.html', title="Détail membre", membre=membre, engagements=engagements, paiements=paiements)

# -----------------------
# Carte de baptême
# -----------------------
@app.route('/carte')
@login_required
def liste_carte():
    from models import CarteBapteme
    search = request.args.get('search', '').strip()
    query = CarteBapteme.query
    if search:
        query = query.filter(CarteBapteme.nom.ilike(f'%{search}%'))
    cartes = query.order_by(CarteBapteme.nom).all()
    return render_template('carte/liste.html', title="Cartes de baptême", cartes=cartes, search=search)

@app.route('/carte/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_carte():
    from models import CarteBapteme
    if request.method == 'POST':
        try:
            if not request.form.get('numero') or not request.form.get('nom') or not request.form.get('sexe'):
                flash('Les champs Numéro de la carte, Nom et Sexe sont obligatoires', 'danger')
                return redirect(url_for('ajouter_carte'))

            photo_filename = None
            if 'photo' in request.files:
                photo = request.files['photo']
                if photo and photo.filename and allowed_file(photo.filename):
                    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                    photo_filename = secure_filename(photo.filename)
                    photo.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))

            date_naissance = None
            if request.form.get('date_naissance'):
                try:
                    date_naissance = datetime.strptime(request.form['date_naissance'], '%Y-%m-%d').date()
                except ValueError:
                    flash('Format de date invalide', 'danger')
                    return redirect(url_for('ajouter_carte'))

            nouveau_carte = CarteBapteme(
                numero=request.form['numero'].strip(),
                nom=request.form['nom'].strip(),
                adresse=request.form.get('adresse', '').strip(),
                date_naissance=date_naissance,
                sexe=request.form['sexe'].strip(),
                photo=photo_filename,
            )
            db.session.add(nouveau_carte)
            db.session.commit()
            flash('Carte ajoutée avec succès!', 'success')
            return redirect(url_for('liste_carte'))
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur lors de l'ajout de la carte: {str(e)}", 'danger')
    return render_template('carte/ajouter.html', title="Ajouter une carte")

@app.route('/carte/<int:id>/modifier', methods=['POST'])
@login_required
def modifier_carte(id):
    from models import CarteBapteme
    carte = CarteBapteme.query.get_or_404(id)
    try:
        numero = request.form.get('numero', '').strip()
        nom = request.form.get('nom', '').strip()
        sexe = request.form.get('sexe', '').strip()
        if not numero or not nom or not sexe:
            flash("Numéro de la carte, Nom, et Sexe sont obligatoires", "danger")
            return redirect(url_for('liste_carte'))

        carte.numero = numero
        carte.nom = nom
        carte.sexe = sexe
        carte.adresse = request.form.get('adresse', '').strip()

        date_naissance = request.form.get('date_naissance', '').strip()
        if date_naissance:
            try:
                carte.date_naissance = datetime.strptime(date_naissance, '%Y-%m-%d').date()
            except ValueError:
                flash("Format de date invalide", "danger")
                return redirect(url_for('liste_carte'))

        if 'photo' in request.files:
            photo = request.files['photo']
            if photo and photo.filename and allowed_file(photo.filename):
                if carte.photo:
                    try:
                        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], carte.photo))
                    except Exception:
                        pass
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                photo_filename = secure_filename(photo.filename)
                photo.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_filename))
                carte.photo = photo_filename

        db.session.commit()
        flash("Carte modifiée avec succès !", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de la modification : {str(e)}", "danger")
    return redirect(url_for('liste_carte'))

# -----------------------
# Engagements
# -----------------------
@app.route('/engagements')
@login_required
def liste_engagements():
    from models import Engagement
    engagements = Engagement.query.order_by(Engagement.date_limite).all()
    membres = Membre.query.order_by(Membre.id.desc()).all()
    return render_template('engagements/liste.html', title="Engagements", engagements=engagements, membres=membres)

@app.route('/engagements/ajouter', methods=['GET', 'POST'])
@login_required
@admin_required
def ajouter_engagement():
    from models import Engagement, Membre
    if request.method == 'POST':
        try:
            membre_id = request.form.get('membre_id')
            montant_str = request.form.get('montant')
            date_str = request.form.get('date_limite')
            description = request.form.get('description', '').strip()

            membre = Membre.query.get(membre_id)
            if not membre:
                flash("Membre introuvable", "danger")
                return redirect(url_for('ajouter_engagement'))

            try:
                montant = float(montant_str)
                if montant <= 0:
                    raise ValueError("Montant invalide")
            except (ValueError, TypeError):
                flash("Le montant doit être un nombre positif", "warning")
                return redirect(url_for('ajouter_engagement'))

            try:
                date_limite = datetime.strptime(date_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                flash("Format de date incorrect", "danger")
                return redirect(url_for('ajouter_engagement'))

            nouvel_engagement = Engagement(
                membre_id=membre_id,
                montant_total=montant,
                date_limite=date_limite,
                description=description
            )
            db.session.add(nouvel_engagement)
            db.session.commit()

            if membre.apikey_callmebot:
                message = f"Bonjour {membre.cartebapteme.nom}, vous avez un nouvel engagement de {montant:.2f}$ à régler avant le {date_limite.strftime('%d/%m/%Y')}."
                envoyer_whatsapp(membre.telephone, membre.apikey_callmebot, message)

            flash('Engagement ajouté avec succès!', 'success')
            return redirect(url_for('liste_engagements'))
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur: {str(e)}", 'danger')

    membres = Membre.query.order_by(Membre.id.desc()).all()
    return render_template('engagements/ajouter.html', title="Ajouter un engagement", membres=membres)

@app.route('/engagements/<int:id>/modifier', methods=['POST'])
@login_required
@admin_required
def modifier_engagement(id):
    from models import Engagement
    engagement = Engagement.query.get_or_404(id)
    try:
        montant_str = request.form.get('montant_total')
        date_str = request.form.get('date_limite')
        description = request.form.get('description', '').strip()
        statut = request.form.get('statut', 'en cours').strip()

        try:
            montant = float(montant_str)
            if montant <= 0:
                raise ValueError("Montant invalide")
        except (ValueError, TypeError):
            flash("Le montant doit être un nombre positif", "warning")
            return redirect(url_for('liste_engagements'))

        try:
            date_limite = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            flash("Date invalide", "danger")
            return redirect(url_for('liste_engagements'))

        engagement.montant_total = montant
        engagement.date_limite = date_limite
        engagement.description = description
        engagement.statut = statut

        db.session.commit()
        flash("Engagement modifié avec succès", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de la modification : {str(e)}", "danger")
    return redirect(url_for('liste_engagements'))

# -----------------------
# Paiements
# -----------------------
@app.route('/paiements')
@login_required
def liste_paiements():
    from models import Paiement
    paiements = Paiement.query.order_by(Paiement.date_paiement.desc()).all()
    return render_template('paiements/liste.html', title="Paiements", paiements=paiements)

@app.route('/paiements/ajouter', methods=['GET', 'POST'])
@login_required
@admin_required
def ajouter_paiement():
    from models import Engagement, Paiement
    if request.method == 'POST':
        try:
            engagement_id = request.form.get('engagement_id')
            montant_str = request.form.get('montant')
            date_str = request.form.get('date_paiement')

            engagement = Engagement.query.get(engagement_id)
            if not engagement:
                flash("Engagement introuvable", "danger")
                return redirect(url_for('ajouter_paiement'))

            try:
                montant = float(montant_str)
                if montant <= 0:
                    raise ValueError("Montant invalide")
            except Exception:
                flash("Montant invalide", "warning")
                return redirect(url_for('ajouter_paiement'))

            try:
                date_paiement = datetime.strptime(date_str, '%Y-%m-%d').date()
            except Exception:
                date_paiement = datetime.utcnow().date()

            paiement = Paiement(
                engagement_id=engagement_id,
                montant=montant,
                date_paiement=date_paiement
            )
            db.session.add(paiement)
            db.session.commit()

            membre = engagement.membre
            if membre and membre.apikey_callmebot:
                msg = f"Bonjour {membre.cartebapteme.nom or membre.code_membre}, votre paiement de {montant:.2f}$ a été enregistré pour l'engagement prévu le {engagement.date_limite.strftime('%d/%m/%Y')}."
                envoyer_whatsapp(membre.telephone, membre.apikey_callmebot, msg)

            # Retourner les données pour le reçu
            return render_template('paiements/ajouter.html', 
                title="Ajouter un paiement",
                engagements=Engagement.query.order_by(Engagement.date_limite).all(),
                date=date,
                receipt_data={
                    'membre': membre.cartebapteme.nom,
                    'montant': montant,
                    'date_paiement': date_paiement.strftime('%d/%m/%Y'),
                    'engagement_date': engagement.date_limite.strftime('%d/%m/%Y'),
                    'total_engage': engagement.montant_total,
                    'reste': engagement.montant_restant()
                }
            )

        except Exception as e:
            db.session.rollback()
            flash(f"Erreur: {str(e)}", "danger")

    engagements = Engagement.query.all()
    engagements_data = [
        {
            "id": e.id,
            "montant_total": e.montant_total,
            "montant_paye": sum(p.montant for p in e.paiements)
        }
        for e in engagements
    ]
    return render_template(
        'paiements/ajouter.html',
        engagements=engagements,
        engagements_data=engagements_data,
        date=date  # <-- Ajoute ceci
    )
@app.route('/paiements/<int:id>/modifier', methods=['POST'])
@login_required
@admin_required
def modifier_paiement(id):
    from models import Paiement
    paiement = Paiement.query.get_or_404(id)
    try:
        montant = float(request.form.get('montant'))
        date_str = request.form.get('date_paiement')
        if montant <= 0:
            flash("Montant invalide", "warning")
            return redirect(url_for('liste_paiements'))

        try:
            paiement.date_paiement = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash("Date incorrecte", "danger")
            return redirect(url_for('liste_paiements'))

        paiement.montant = montant
        db.session.commit()
        flash("Paiement modifié avec succès", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur : {str(e)}", "danger")
    return redirect(url_for('liste_paiements'))

@app.route('/paiements/recu/<int:paiement_id>')
@login_required
@admin_required
def imprimer_recu(paiement_id):
    from models import Paiement
    paiement = Paiement.query.get_or_404(paiement_id)
    return render_template('paiements/recu.html', paiement=paiement)

# -----------------------
# Notifications
# -----------------------
@app.route('/test-job')
def test_job():
    from tasks import notifier_engagements_proches
    notifier_engagements_proches()
    return "✅ Job exécuté manuellement"
@app.route('/notifier/engagement/<int:id>', methods=['POST'])
@login_required
def notifier_membre_engagement(id):
    from models import Engagement, Notification
    engagement = Engagement.query.get_or_404(id)
    membre = engagement.membre

    if membre and membre.apikey_callmebot and membre.telephone:
        # Calcul du montant déjà payé
        total_paye = sum(p.montant for p in engagement.paiements)
        reste_a_payer = engagement.montant_restant()

        # Construction du message
        nom_affiche = membre.cartebapteme.nom if membre.cartebapteme and membre.cartebapteme.nom else membre.code_membre
        message = (
            f"*CBCA VULUMBI - RAPPEL D'ENGAGEMENT*\n\n"
            f"Cher(e) *{nom_affiche}*,\n\n"
            f"Vous avez souscrit un engagement de *{engagement.montant_total:.2f}$* "
            f"avec une date d'échéance fixée au *{engagement.date_limite.strftime('%d/%m/%Y')}*.\n\n"
            f"📊 *État de paiement* :\n"
            f"- Montant total engagé : {engagement.montant_total:.2f}$\n"
            f"- Montant déjà payé : {total_paye:.2f}$\n"
            f"- Reste à payer : {reste_a_payer:.2f}$\n\n"
            f"ℹ️ Pour toute question, contactez le trésorier au +243976543210.\n\n"
            f"*Merci pour votre contribution à notre communauté!*"
        )

        # Envoi du message via CallMeBot
        envoyer_whatsapp(membre.telephone, membre.apikey_callmebot, message)

        # Enregistrement de la notification
        nouvelle_notification = Notification(
            membre_id=membre.id,
            engagement_id=engagement.id,
            message=message,
            statut='envoyée',
            date_envoi=datetime.utcnow()
        )
        db.session.add(nouvelle_notification)
        db.session.commit()

        flash("Notification envoyée avec succès.", "success")
    else:
        flash("Notification impossible : coordonnées du membre incomplètes.", "warning")

    return redirect(url_for('liste_engagements'))


@app.route('/notifier/tous', methods=['GET'])
@login_required
def notifier_tous_membres():
    from models import Engagement
    engagements = Engagement.query.filter(Engagement.statut != 'payé').all()
    cpt = 0
    for e in engagements:
        membre = e.membre
        if membre and membre.apikey_callmebot and membre.telephone:
            msg = f"Bonjour {membre.cartebapteme.nom or membre.code_membre}, votre engagement de {e.montant_total:.2f}$ expire le {e.date_limite.strftime('%d/%m/%Y')}. Solde restant : {e.montant_restant():.2f}$."
            envoyer_whatsapp(membre.telephone, membre.apikey_callmebot, msg)
            cpt += 1
    flash(f"{cpt} notification(s) envoyée(s).", "info")
    return redirect(url_for('liste_engagements'))

# -----------------------
# Utilisateurs (admin)
# -----------------------
@app.route('/utilisateurs')
@login_required
@role_required('admin')
def liste_utilisateurs():
    from models import User
    users = User.query.order_by(User.username).all()
    return render_template('utilisateurs/liste.html', title="Utilisateurs", users=users)

@app.route('/utilisateurs/ajouter', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def ajouter_utilisateur():
    from models import User
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'lecteur').strip()
        if not username or not password or not role:
            flash("Champs obligatoires manquants", "danger")
            return redirect(url_for('ajouter_utilisateur'))
        if User.query.filter_by(username=username).first():
            flash("Nom d'utilisateur déjà pris", "danger")
            return redirect(url_for('ajouter_utilisateur'))

        nouvel_user = User(username=username, role=role)
        nouvel_user.set_password(password)
        try:
            db.session.add(nouvel_user)
            db.session.commit()
            flash("Utilisateur ajouté avec succès", "success")
            return redirect(url_for('liste_utilisateurs'))
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur lors de l'ajout : {str(e)}", "danger")

    roles = ['admin', 'secretaire', 'caissier', 'lecteur']
    return render_template('utilisateurs/ajouter.html', title="Ajouter un utilisateur", roles=roles)

@app.route('/utilisateurs/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def modifier_utilisateur(id):
    from models import User
    user = User.query.get_or_404(id)
    if request.method == 'POST':
        role = request.form.get('role', user.role).strip()
        password = request.form.get('password', '').strip()
        user.role = role
        if password:
            user.set_password(password)
        try:
            db.session.commit()
            flash("Utilisateur modifié", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Erreur : {str(e)}", "danger")
        return redirect(url_for('liste_utilisateurs'))

    roles = ['admin', 'secretaire', 'caissier', 'lecteur']
    return render_template('utilisateurs/modifier.html', title="Modifier utilisateur", user=user, roles=roles)

@app.route('/utilisateurs/<int:id>/supprimer', methods=['POST'])
@login_required
@role_required('admin')
def supprimer_utilisateur(id):
    from models import User
    user = User.query.get_or_404(id)
    try:
        db.session.delete(user)
        db.session.commit()
        flash("Utilisateur supprimé", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erreur : {str(e)}", "danger")
    return redirect(url_for('liste_utilisateurs'))

@app.route('/mes-engagements/souscrire', methods=['GET', 'POST'])
@login_required
@role_required('membre')
def souscrire_engagement_membre():
    from models import Engagement, Membre
    membre = Membre.query.filter_by(telephone=current_user.username).first()
    if not membre:
        flash("Votre profil membre n'est pas trouvé.", "danger")
        return redirect(url_for('accueil_membre'))

    if request.method == 'POST':
        montant_str = request.form.get('montant')
        date_str = request.form.get('date_limite')
        description = request.form.get('description', '').strip()

        try:
            montant = float(montant_str)
            if montant <= 0:
                raise ValueError("Montant invalide")
        except Exception:
            flash("Le montant doit être un nombre positif", "warning")
            return redirect(url_for('souscrire_engagement_membre'))

        try:
            date_limite = datetime.strptime(date_str, '%Y-%m-%d').date()
        except Exception:
            flash("Format de date incorrect", "danger")
            return redirect(url_for('souscrire_engagement_membre'))

        nouvel_engagement = Engagement(
            membre_id=membre.id,
            montant_total=montant,
            date_limite=date_limite,
            description=description
        )
        db.session.add(nouvel_engagement)
        db.session.commit()

        # Notification WhatsApp
        if membre.apikey_callmebot:
            nom = membre.cartebapteme.nom if membre.cartebapteme else membre.code_membre
            message = (
                f"Bonjour {nom}, votre engagement de {montant:.2f}$ a été enregistré. "
                f"Date limite : {date_limite.strftime('%d/%m/%Y')}."
            )
            envoyer_whatsapp(membre.telephone, membre.apikey_callmebot, message)

        flash("Engagement souscrit avec succès !", "success")
        return redirect(url_for('mes_engagements'))

    return render_template('engagements/souscrire_membre.html', title="Souscrire un engagement")

@app.route('/accueil-membre')
@login_required
@role_required('membre')
def accueil_membre():
    membre = Membre.query.filter_by(telephone=current_user.username).first()
    engagements = []
    paiements = []
    if membre:
        engagements = Engagement.query.filter_by(membre_id=membre.id).order_by(Engagement.date_limite.desc()).limit(3).all()
        paiements = Paiement.query.join(Engagement).filter(Engagement.membre_id == membre.id).order_by(Paiement.date_paiement.desc()).limit(3).all()
    return render_template('accueil_membre.html', membre=membre, engagements=engagements, paiements=paiements, title="Mon espace membre")

@app.route('/mes-engagements')
@login_required
@role_required('membre')
def mes_engagements():
    from models import Engagement, Membre
    membre = Membre.query.filter_by(telephone=current_user.username).first()
    engagements = Engagement.query.filter_by(membre_id=membre.id).order_by(Engagement.date_limite).all()
    return render_template('engagements/mes_engagements.html', title="Mes engagements", engagements=engagements)

# -----------------------
# Init DB + run
# -----------------------
def init_db():
    with app.app_context():
        db.create_all()
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        from models import User
        if not User.query.first():
            admin = User(username='admin', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()

if __name__ == '__main__':
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
