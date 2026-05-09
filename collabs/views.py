from rest_framework import status, generics, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny

from .models import CollabPost, CollabApplication, CollabMember
from .serializers import (
    CollabPostSerializer, CollabPostCreateSerializer,
    CollabApplicationSerializer, CollabApplicationCreateSerializer,
)


class CollabPostListView(generics.ListAPIView):
    """GET /api/collabs/  → all open collab posts (public)"""
    permission_classes = [AllowAny]
    serializer_class   = CollabPostSerializer
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['title', 'description']
    ordering           = ['-created_at']

    def get_queryset(self):
        return CollabPost.objects.filter(status=CollabPost.Status.OPEN).select_related(
            'posted_by__account__user'
        ).prefetch_related('skills_needed', 'applications', 'members')


class CollabPostCreateView(APIView):
    """POST /api/collabs/create/  → freelancer creates a collab post"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        account    = getattr(request.user, 'account', None)
        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = CollabPostCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = serializer.save(posted_by=freelancer)
        return Response(CollabPostSerializer(post).data, status=status.HTTP_201_CREATED)


class CollabPostDetailView(generics.RetrieveAPIView):
    """GET /api/collabs/<id>/  → collab post detail"""
    permission_classes = [AllowAny]
    serializer_class   = CollabPostSerializer
    queryset           = CollabPost.objects.select_related(
        'posted_by__account__user'
    ).prefetch_related('skills_needed', 'applications', 'members')


class ApplyCollabView(APIView):
    """POST /api/collabs/<id>/apply/  → freelancer applies to a collab"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        account    = getattr(request.user, 'account', None)
        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            post = CollabPost.objects.get(pk=pk, status=CollabPost.Status.OPEN)
        except CollabPost.DoesNotExist:
            return Response({'detail': 'Collab post not found or closed.'}, status=status.HTTP_404_NOT_FOUND)

        if post.posted_by == freelancer:
            return Response({'detail': 'You cannot apply to your own collab.'}, status=status.HTTP_400_BAD_REQUEST)

        if CollabApplication.objects.filter(collab_post=post, applicant=freelancer).exists():
            return Response({'detail': 'You have already applied.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = CollabApplicationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = serializer.save(collab_post=post, applicant=freelancer)
        return Response(CollabApplicationSerializer(application).data, status=status.HTTP_201_CREATED)


class RespondCollabApplicationView(APIView):
    """
    POST /api/collabs/applications/<id>/accept/
    POST /api/collabs/applications/<id>/reject/
    Collab post owner accepts or rejects an application.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        account    = getattr(request.user, 'account', None)
        freelancer = getattr(account, 'freelancer_profile', None)
        if not freelancer:
            return Response({'detail': 'Freelancer profile required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            application = CollabApplication.objects.select_related(
                'collab_post__posted_by', 'applicant'
            ).get(pk=pk, collab_post__posted_by=freelancer, status=CollabApplication.Status.PENDING)
        except CollabApplication.DoesNotExist:
            return Response({'detail': 'Application not found.'}, status=status.HTTP_404_NOT_FOUND)

        if action == 'accept':
            application.status = CollabApplication.Status.ACCEPTED
            application.save(update_fields=['status'])
            CollabMember.objects.get_or_create(
                collab_post=application.collab_post,
                freelancer=application.applicant,
            )
            return Response({'detail': 'Application accepted.'})
        elif action == 'reject':
            application.status = CollabApplication.Status.REJECTED
            application.save(update_fields=['status'])
            return Response({'detail': 'Application rejected.'})
        else:
            return Response({'detail': 'Invalid action.'}, status=status.HTTP_400_BAD_REQUEST)