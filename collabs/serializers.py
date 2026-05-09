
from rest_framework import serializers
from .models import CollabPost, CollabApplication, CollabMember


class CollabPostSerializer(serializers.ModelSerializer):
    posted_by_username = serializers.CharField(source='posted_by.account.user.username', read_only=True)
    posted_by_slug     = serializers.CharField(source='posted_by.account.slug',          read_only=True)
    skills_needed      = serializers.StringRelatedField(many=True, read_only=True)
    applicant_count    = serializers.IntegerField(source='applications.count', read_only=True)
    member_count       = serializers.IntegerField(source='members.count',      read_only=True)

    class Meta:
        model  = CollabPost
        fields = (
            'id', 'posted_by_username', 'posted_by_slug',
            'title', 'description', 'skills_needed',
            'spots', 'status', 'applicant_count', 'member_count',
            'created_at',
        )
        read_only_fields = ('status', 'created_at')


class CollabPostCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = CollabPost
        fields = ('title', 'description', 'skills_needed', 'spots')


class CollabApplicationSerializer(serializers.ModelSerializer):
    applicant_username = serializers.CharField(source='applicant.account.user.username', read_only=True)

    class Meta:
        model  = CollabApplication
        fields = ('id', 'collab_post', 'applicant_username', 'message', 'status', 'created_at')
        read_only_fields = ('status', 'created_at')


class CollabApplicationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = CollabApplication
        fields = ('message',)