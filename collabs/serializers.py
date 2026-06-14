
from rest_framework import serializers
from .models import CollabRequest, CollabApplication

class CollabRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = CollabRequest
        fields = [
            "public_id",
            "milestone",
            "created_by",
            "title",
            "description",
            "seat_label",
            "seats_needed",
            "visibility",
            "status",
            "seat_amount",
            "currency",
            "created_at",
            "updated_at",
        ]


class CollabApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CollabApplication
        fields = [
            "public_id",
            "request",
            "freelancer",
            "note",
            "status",
            "created_at",
            "updated_at",
        ]