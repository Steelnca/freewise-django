
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from .constants import USERNAME_MIN_LENGTH, USERNAME_MAX_LENGTH
from .validators import username_regex, username_profanity, username_reserved_terms

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password  = serializers.CharField(write_only=True, min_length=8, style={'input_type': 'password'})
    password2 = serializers.CharField(write_only=True, style={'input_type': 'password'}, label='Confirm password')

    class Meta:
        model  = User
        fields = ('username', 'email', 'password', 'password2')

    def validate_username(self, value):
        value = value.lower()
        username_regex(value)
        username_profanity(value)
        username_reserved_terms(value)
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(_('A user with this email already exists.'))
        return value.lower()

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({'password2': _('Passwords do not match.')})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password2')
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
        )
        return user


class UserSerializer(serializers.ModelSerializer):
    """Read-only user info — safe to expose to frontend."""

    class Meta:
        model  = User
        fields = ('id', 'username', 'email', 'is_active', 'date_joined')
        read_only_fields = fields