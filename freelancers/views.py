from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny

from .models import FreelancerProfile, Skill
from .serializers import FreelancerProfileSerializer, FreelancerProfileUpdateSerializer, SkillSerializer


class FreelancerProfileMeView(APIView):
    """
    GET /api/freelancers/me/  → my freelancer profile
    PUT /api/freelancers/me/  → update my freelancer profile
    """
    permission_classes = [IsAuthenticated]

    def _get_profile(self, request):
        account = getattr(request.user, 'account', None)
        if not account or not account.is_freelancer:
            return None
        return getattr(account, 'freelancer_profile', None)

    def get(self, request):
        profile = self._get_profile(request)
        if not profile:
            return Response({'detail': 'Freelancer profile not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(FreelancerProfileSerializer(profile, context={'request': request}).data)

    def put(self, request):
        profile = self._get_profile(request)
        if not profile:
            return Response({'detail': 'Freelancer profile not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = FreelancerProfileUpdateSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(FreelancerProfileSerializer(profile, context={'request': request}).data)


class FreelancerProfileDetailView(generics.RetrieveAPIView):
    """
    GET /api/freelancers/<slug>/  → public freelancer profile
    """
    permission_classes = [AllowAny]
    serializer_class   = FreelancerProfileSerializer
    queryset           = FreelancerProfile.objects.select_related('account__user')
    lookup_field       = 'account__slug'
    lookup_url_kwarg   = 'slug'


class SkillListView(generics.ListAPIView):
    """GET /api/freelancers/skills/"""
    permission_classes = [AllowAny]
    serializer_class   = SkillSerializer
    queryset           = Skill.objects.all().order_by('name')