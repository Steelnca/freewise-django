from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from .models import Account
from .serializers import AccountSerializer, AccountUpdateSerializer


class AccountMeView(APIView):
    """
    GET  /api/accounts/me/  → current user's full account
    PUT  /api/accounts/me/  → update account fields
    """
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(AccountSerializer(account, context={'request': request}).data)

    def put(self, request):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = AccountUpdateSerializer(account, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AccountSerializer(account, context={'request': request}).data)


class ActivateRoleView(APIView):
    """
    POST /api/accounts/activate-role/
    Body: { "role": "client" } or { "role": "freelancer" }
    Activates a role for the current user and creates the matching profile.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        role = request.data.get('role', '').lower()
        account = getattr(request.user, 'account', None)

        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)

        if role == 'client':
            account.activate_client()
            return Response({'detail': 'Client role activated.'})
        elif role == 'freelancer':
            account.activate_freelancer()
            return Response({'detail': 'Freelancer role activated.'})
        else:
            return Response(
                {'detail': 'Invalid role. Choose "client" or "freelancer".'},
                status=status.HTTP_400_BAD_REQUEST,
            )