
from rest_framework import serializers
from .models import FreelancerProfile, Skill, FreelancerSkill


class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Skill
        fields = ('id', 'name', 'slug')
        read_only_fields = ('slug',)


class FreelancerSkillSerializer(serializers.ModelSerializer):
    skill = SkillSerializer(read_only=True)

    class Meta:
        model  = FreelancerSkill
        fields = ('id', 'skill')


class FreelancerProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='account.user.username', read_only=True)
    avatar   = serializers.ImageField(source='account.avatar',       read_only=True)
    slug     = serializers.CharField(source='account.slug',          read_only=True)
    skills   = FreelancerSkillSerializer(many=True, read_only=True)

    class Meta:
        model  = FreelancerProfile
        fields = (
            'id', 'username', 'avatar', 'slug',
            'title', 'bio', 'hourly_rate', 'portfolio_url',
            'availability', 'rating', 'completed_jobs', 'total_earned',
            'skills', 'created_at',
        )
        read_only_fields = ('rating', 'completed_jobs', 'total_earned', 'created_at')


class FreelancerProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = FreelancerProfile
        fields = ('title', 'bio', 'hourly_rate', 'portfolio_url', 'availability')