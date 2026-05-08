
from rest_framework import serializers
from .models import ClientProfile


class ClientProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='account.user.username', read_only=True)
    avatar   = serializers.ImageField(source='account.avatar',       read_only=True)
    slug     = serializers.CharField(source='account.slug',          read_only=True)

    class Meta:
        model  = ClientProfile
        fields = (
            'id', 'username', 'avatar', 'slug',
            'company_name', 'industry', 'website',
            'rating', 'total_spent', 'total_hires',
            'created_at',
        )
        read_only_fields = ('rating', 'total_spent', 'total_hires', 'created_at')


class ClientProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ClientProfile
        fields = ('company_name', 'industry', 'website')