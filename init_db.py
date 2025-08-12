from app import app, db
from models import User, Membre, Engagement, Paiement, CarteBapteme, Notification
from datetime import datetime, timedelta
import random

def init_database():
    with app.app_context():
        # Créer les tables
        db.create_all()

        # === 1. Création d'utilisateurs ===
        if not User.query.first():
            admin = User(username='admin', role='admin')
            admin.set_password('0714')
            user1 = User(username='secretaire', role='user')
            user1.set_password('1234')
            user2 = User(username='tresorier', role='user')
            user2.set_password('1234')
            db.session.add_all([admin, user1, user2])

        # === 2. Cartes de baptême ===
        noms_cartes = [
            ("CB001", "Kavira Marie", "Butembo", "F", datetime(1990, 5, 20)),
            ("CB002", "Kasereka Jean", "Beni", "M", datetime(1985, 8, 15)),
            ("CB003", "Mumbere Paul", "Lubero", "M", datetime(1992, 1, 10)),
            ("CB004", "Kambale Alice", "Butembo", "F", datetime(1995, 3, 12)),
            ("CB005", "Musavuli David", "Beni", "M", datetime(1988, 6, 5)),
            ("CB006", "Ngabu Chantal", "Oïcha", "F", datetime(1993, 9, 22)),
            ("CB007", "Baluku Joseph", "Beni", "M", datetime(1984, 7, 14)),
            ("CB008", "Masika Monique", "Butembo", "F", datetime(1991, 11, 3)),
            ("CB009", "Kambale Justin", "Lubero", "M", datetime(1989, 4, 17)),
            ("CB010", "Kavugho Esther", "Butembo", "F", datetime(1996, 12, 25))
        ]

        if CarteBapteme.query.count() == 0:
            cartes = []
            for numero, nom, adresse, sexe, date_naiss in noms_cartes:
                cartes.append(CarteBapteme(
                    numero=numero,
                    nom=nom,
                    adresse=adresse,
                    sexe=sexe,
                    date_naissance=date_naiss
                ))
            db.session.add_all(cartes)
            db.session.flush()

        # === 3. Membres ===
        groupes = ['Chorale', 'Jeunesse', 'Dames', 'Hommes', 'Enfants']
        if Membre.query.count() == 0:
            membres = []
            for idx, carte in enumerate(CarteBapteme.query.all(), start=1):
                membres.append(Membre(
                    code_membre=f"CBCA-VUL-{idx:04d}",
                    cate_id=carte.id,
                    telephone=f"+2439700000{idx:02d}",
                    groupe=random.choice(groupes),
                    statut="actif",
                    apikey_callmebot=f"APIKEY{idx:04d}"  # clé factice pour tests
                ))
            db.session.add_all(membres)
            db.session.flush()

        # === 4. Engagements ===
        if Engagement.query.count() == 0:
            engagements = []
            for membre in Membre.query.all():
                for _ in range(random.randint(1, 3)):  # 1 à 3 engagements par membre
                    montant = random.choice([50, 100, 150, 200, 250])
                    date_limite = datetime.utcnow().date() + timedelta(days=random.randint(15, 90))
                    engagements.append(Engagement(
                        membre_id=membre.id,
                        montant_total=montant,
                        date_limite=date_limite,
                        description="Engagement de test"
                    ))
            db.session.add_all(engagements)
            db.session.flush()

        # === 5. Paiements ===
        if Paiement.query.count() == 0:
            paiements = []
            for engagement in Engagement.query.all():
                if random.choice([True, False]):  # 50% ont déjà payé
                    montant = engagement.montant_total / random.choice([2, 1])  # partiel ou complet
                    paiements.append(Paiement(
                        engagement_id=engagement.id,
                        montant=montant,
                        date_paiement=datetime.utcnow().date()
                    ))
            db.session.add_all(paiements)

        # === 6. Notifications ===
        if Notification.query.count() == 0:
            notifications = []
            for engagement in Engagement.query.limit(5).all():  # quelques exemples
                notifications.append(Notification(
                    membre_id=engagement.membre_id,
                    engagement_id=engagement.id,
                    message=f"Rappel : votre engagement de {engagement.montant_total}$ expire le {engagement.date_limite.strftime('%d/%m/%Y')}.",
                    statut="envoyé"
                ))
            db.session.add_all(notifications)

        # Commit final
        db.session.commit()
        print("✅ Base de données initialisée avec jeu de données de test enrichi.")

if __name__ == '__main__':
    init_database()
