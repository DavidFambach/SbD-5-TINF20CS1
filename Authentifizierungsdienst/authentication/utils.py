from django.core.mail import EmailMessage
from django.core.mail import EmailMultiAlternatives

import threading


class EmailThread(threading.Thread):

    def __init__(self, email):
        self.email = email
        threading.Thread.__init__(self)

    def run(self):
        self.email.send()


class Util:
    @staticmethod
    def send_email(data):
        email = EmailMultiAlternatives(
            subject=data['email_subject'], body=data['text_body'], to=[data['to_email']])
        if 'html_body' in data:
            email.attach_alternative(data['html_body'], "text/html")
        EmailThread(email).start()
