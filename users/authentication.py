

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from django.utils.translation import gettext_lazy as _

class VersionedJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        token_version = validated_token.get("token_version")
        if token_version is None or token_version != user.token_version:
            raise InvalidToken(_("Token is no longer valid. Please sign in again."))

        return user