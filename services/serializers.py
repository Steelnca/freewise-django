from rest_framework import serializers
from .models import Service, ServicePackage, Order


class ServicePackageSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ServicePackage
        fields = ('id', 'title', 'description', 'price', 'delivery_days', 'revisions', 'order')


class ServiceSerializer(serializers.ModelSerializer):
    freelancer_username = serializers.CharField(source='freelancer.account.user.username', read_only=True)
    freelancer_slug     = serializers.CharField(source='freelancer.account.slug',          read_only=True)
    freelancer_rating   = serializers.DecimalField(source='freelancer.rating', max_digits=3, decimal_places=2, read_only=True)
    packages            = ServicePackageSerializer(many=True, read_only=True)
    category            = serializers.SerializerMethodField()
    tags                = serializers.SerializerMethodField()

    class Meta:
        model  = Service
        fields = (
            'id', 'freelancer_username', 'freelancer_slug', 'freelancer_rating',
            'title', 'description', 'category', 'tags',
            'packages', 'status', 'created_at',
        )
        read_only_fields = ('created_at',)

    def get_category(self, obj):
        if obj.category:
            return {'id': obj.category.id, 'name': obj.category.name, 'slug': obj.category.slug}
        return None

    def get_tags(self, obj):
        return [{'id': t.id, 'name': t.name, 'slug': t.slug} for t in obj.tags.all()]


class ServiceCreateSerializer(serializers.ModelSerializer):
    packages = ServicePackageSerializer(many=True)

    class Meta:
        model  = Service
        fields = ('title', 'description', 'category', 'tags', 'status', 'packages')

    def create(self, validated_data):
        packages_data = validated_data.pop('packages')
        tags_data     = validated_data.pop('tags', [])
        service       = Service.objects.create(**validated_data)
        service.tags.set(tags_data)
        for i, pkg in enumerate(packages_data, start=1):
            ServicePackage.objects.create(service=service, order=i, **pkg)
        return service

    def update(self, instance, validated_data):
        packages_data = validated_data.pop('packages', None)
        tags_data     = validated_data.pop('tags', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if tags_data is not None:
            instance.tags.set(tags_data)
        if packages_data is not None:
            instance.packages.all().delete()
            for i, pkg in enumerate(packages_data, start=1):
                ServicePackage.objects.create(service=instance, order=i, **pkg)
        return instance


class OrderSerializer(serializers.ModelSerializer):
    service_title       = serializers.CharField(source='service.title',                    read_only=True)
    freelancer_username = serializers.CharField(source='service.freelancer.account.user.username', read_only=True)
    client_username     = serializers.CharField(source='client.account.user.username',     read_only=True)
    package             = ServicePackageSerializer(read_only=True)
    contract_id         = serializers.IntegerField(source='contract.id', read_only=True, allow_null=True)

    class Meta:
        model  = Order
        fields = (
            'id', 'service', 'service_title',
            'package', 'client_username', 'freelancer_username',
            'requirements', 'status', 'contract_id',
            'created_at', 'delivered_at', 'completed_at',
        )
        read_only_fields = ('status', 'created_at', 'delivered_at', 'completed_at', 'contract_id')


class OrderCreateSerializer(serializers.Serializer):
    package_id   = serializers.IntegerField()
    requirements = serializers.CharField(allow_blank=True)
