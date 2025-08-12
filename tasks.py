from datetime import date, timedelta, datetime
from extensions import db
from models import Engagement, Notification
from utils import envoyer_whatsapp

def notifier_engagements_proches():
    aujourd_hui = date.today()
    dans_3_jours = aujourd_hui + timedelta(days=3)

    engagements = Engagement.query.filter(
        Engagement.date_limite >= aujourd_hui,
        Engagement.date_limite <= dans_3_jours,
        Engagement.statut != 'payé'
    ).all()

    print(f"📡 {len(engagements)} engagement(s) proche(s) à notifier.")

    for e in engagements:
        membre = e.membre
        reste = e.montant_restant()

        if membre.apikey_callmebot and membre.telephone:
            message = (
                f"Rappel CBCA Vulumbi 📌\n"
                f"Bonjour {membre.nom},\n"
                f"Votre engagement de {e.montant_total:.2f} $ expire le {e.date_limite.strftime('%d/%m/%Y')}.\n"
                f"Montant restant : {reste:.2f} $"
            )

            try:
                envoyer_whatsapp(membre.telephone, membre.apikey_callmebot, message)
                statut = "envoyé"
                print(f"✅ Message envoyé à {membre.nom}")
            except Exception as err:
                statut = "échoué"
                print(f"❌ Envoi échoué pour {membre.nom} : {err}")

            # 📜 Enregistrement en base
            notif = Notification(
                membre_id=membre.id,
                engagement_id=e.id,
                message=message,
                statut=statut,
                date_envoi=datetime.utcnow()
            )
            db.session.add(notif)

    db.session.commit()
    print("📣 Job de rappel WhatsApp terminé.")
