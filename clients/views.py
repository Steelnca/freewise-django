from rest_framework import status, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny

from .models import ClientProfile
from .serializers import ClientProfileSerializer, ClientProfileUpdateSerializer


class ClientProfileMeView(APIView):
    """
    GET /api/clients/me/  → my client profile
    PUT /api/clients/me/  → update my client profile
    """
    permission_classes = [IsAuthenticated]

    def _get_profile(self, request):
        account = getattr(request.user, 'account', None)
        if not account or not account.is_client:
            return None
        return getattr(account, 'client_profile', None)

    def get(self, request):
        profile = self._get_profile(request)
        if not profile:
            return Response({'detail': 'Client profile not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ClientProfileSerializer(profile, context={'request': request}).data)

    def put(self, request):
        profile = self._get_profile(request)
        if not profile:
            return Response({'detail': 'Client profile not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = ClientProfileUpdateSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ClientProfileSerializer(profile, context={'request': request}).data)


class ClientProfileDetailView(generics.RetrieveAPIView):
    """
    GET /api/clients/<slug>/  → public client profile
    """
    permission_classes = [AllowAny]
    serializer_class   = ClientProfileSerializer
    queryset           = ClientProfile.objects.select_related('account__user')
    lookup_field       = 'account__slug'
    lookup_url_kwarg   = 'slug'