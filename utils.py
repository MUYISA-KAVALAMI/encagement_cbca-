import requests

def envoyer_whatsapp(numero, apikey, message):
    url = f"https://api.callmebot.com/whatsapp.php?phone={numero}&apikey={apikey}&text={message}"
    try:
        response = requests.get(url)
        if "Message sent successfully" in response.text:
            print("Notification envoyée")
        else:
            print("Échec de l'envoi : ", response.text)
    except Exception as e:
        print("Erreur lors de l'envoi WhatsApp :", str(e))
