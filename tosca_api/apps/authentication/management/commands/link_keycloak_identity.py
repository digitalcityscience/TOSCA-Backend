"""Link a Keycloak identity (``sub``) to an existing Django user.

Sign-in never links a Keycloak user to an existing account that is already
linked to another Keycloak identity, or that has a local password (see
``authentication/identity.py``): usernames repeat across realms, so the app
cannot tell whether ``kose`` in a new realm is the same person. When it is,
an administrator links it explicitly. The refused sign-in logs the command
line to use.

    manage.py link_keycloak_identity <username> <sub> [--issuer URL]
"""

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.authentication.identity import KEYCLOAK_PROVIDER


class Command(BaseCommand):
    help = "Link a Keycloak identity (token 'sub') to an existing Django user."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Existing Django username")
        parser.add_argument("sub", help="Keycloak user id (the token's 'sub' claim)")
        parser.add_argument("--issuer", default="", help="Realm issuer URL, kept for reference")

    def handle(self, *args, username, sub, issuer, **options):
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"No user named {username!r}.")
        existing = SocialAccount.objects.filter(provider=KEYCLOAK_PROVIDER, uid=sub).select_related("user").first()
        if existing is not None:
            if existing.user_id == user.pk:
                self.stdout.write(f"Already linked: {username} <- {sub}")
                return
            raise CommandError(f"{sub} is already linked to user {existing.user.username!r}.")
        SocialAccount.objects.create(
            user=user,
            provider=KEYCLOAK_PROVIDER,
            uid=sub,
            extra_data={"linked_by": "link_keycloak_identity", "iss": issuer},
        )
        self.stdout.write(self.style.SUCCESS(f"Linked {username} <- {sub}"))
