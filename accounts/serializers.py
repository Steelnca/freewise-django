from rest_framework import serializers
from .models import Account


class AccountSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    email    = serializers.CharField(source='user.email',    read_only=True)

    class Meta:
        model  = Account
        fields = (
            'id', 'username', 'email',
            'avatar', 'bio', 'slug', 'country', 'birthday',
            'phone', 'locale', 'theme',
            'is_client', 'is_freelancer',
            'email_verified', 'phone_verified',
            'joined_at',
        )
        read_only_fields = (
            'slug', 'is_client', 'is_freelancer',
            'email_verified', 'phone_verified', 'joined_at',
        )


class AccountUpdateSerializer(serializers.ModelSerializer):
    """Only the fields the user is allowed to edit."""

    class Meta:
        model  = Account
        fields = ('avatar', 'bio', 'country', 'birthday', 'phone', 'locale', 'theme')