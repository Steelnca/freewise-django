
from rest_framework import serializers
from .models import Review


class ReviewSerializer(serializers.ModelSerializer):
    reviewer_username = serializers.CharField(source='reviewer.user.username', read_only=True)
    reviewee_username = serializers.CharField(source='reviewee.user.username', read_only=True)

    class Meta:
        model  = Review
        fields = (
            'id', 'contract',
            'reviewer_username', 'reviewee_username',
            'rating', 'comment', 'created_at',
        )
        read_only_fields = ('created_at',)


class ReviewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Review
        fields = ('rating', 'comment')