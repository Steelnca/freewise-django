
from rest_framework import serializers
from .models import Job, Category, Tag


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model  = Category
        fields = ('id', 'name', 'slug', 'icon')


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Tag
        fields = ('id', 'name', 'slug')


class JobSerializer(serializers.ModelSerializer):
    client_username = serializers.CharField(source='client.account.user.username', read_only=True)
    client_slug     = serializers.CharField(source='client.account.slug',          read_only=True)
    category        = CategorySerializer(read_only=True)
    category_id     = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), source='category', write_only=True, required=False
    )
    tags            = TagSerializer(many=True, read_only=True)
    tag_ids         = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(), source='tags', write_only=True, many=True, required=False
    )
    proposal_count     = serializers.IntegerField(source='proposals.count', read_only=True)

    class Meta:
        model  = Job
        fields = (
            'id', 'client_username', 'client_slug',
            'title', 'description',
            'category', 'category_id',
            'tags', 'tag_ids',
            'experience_level',
            'budget_min', 'budget_max', 'deadline',
            'status', 'proposal_count',
            'created_at',
        )
        read_only_fields = ('status', 'created_at', 'proposal_count')


class JobCreateSerializer(serializers.ModelSerializer):
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), source='category', required=False, allow_null=True
    )
    tag_ids = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(), source='tags', many=True, required=False
    )

    class Meta:
        model  = Job
        fields = (
            'title', 'description', 'category_id', 'tag_ids',
            'experience_level', 'budget_min', 'budget_max', 'deadline',
        )