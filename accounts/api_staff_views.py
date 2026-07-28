"""Administration staff des comptes.

Surface volontairement minimale : rechercher un compte et basculer son accès
offert. Toute autre édition — email, mot de passe, désactivation, suppression —
passe par l'admin Django, où elle est tracée et où les garde-fous de Django
s'appliquent.

Transposé de Poker_server, où la même surface existe.
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .api_serializers import StaffUserSerializer
from .models import BypassGrantLog, User

SEARCH_LIMIT = 50


class IsSuperUser(permissions.BasePermission):
    """Reserve aux superusers, pas a tout compte `is_staff`.

    `IsAdminUser` de DRF se contente de `is_staff`, qui n'est ici que le droit
    d'ouvrir la zone d'administration -- consulter l'etat du backend, par
    exemple. Offrir un acces gratuit engage de l'argent : c'est un cran au-dessus,
    et les deux bouts (garde Angular et permission serveur) doivent exiger le
    meme niveau, sinon l'un des deux ment.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


class StaffUserListView(APIView):
    """GET ?q=<terme> — recherche par email.

    Sans `q`, renvoie les comptes ayant un accès offert : c'est la liste que le
    staff consulte en pratique, et elle répond à « à qui ai-je donné quoi ? ».
    """

    permission_classes = [IsSuperUser]

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = User.objects.filter(Q(email__icontains=q) | Q(userkey__icontains=q))
        else:
            qs = User.objects.filter(subscription_bypass=True)
        qs = qs.order_by("email")[:SEARCH_LIMIT]
        return Response({"results": StaffUserSerializer(qs, many=True).data})


class StaffUserDetailView(APIView):
    """PATCH {subscription_bypass?, bypass_note?} — bascule l'accès offert."""

    permission_classes = [IsSuperUser]

    @transaction.atomic
    def patch(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        etait_offert = user.subscription_bypass
        serializer = StaffUserSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Horodate à l'activation seulement : on garde la trace de l'octroi
        # initial même après une révocation.
        if user.subscription_bypass and not etait_offert:
            user.bypass_granted_at = timezone.now()
            user.save(update_fields=["bypass_granted_at"])

        # Journal append-only : l'état courant ne dit pas QUI a basculé le flag.
        # La bascule et l'écriture du journal partagent la même transaction : si
        # l'une échoue, l'autre est annulée avec elle.
        if user.subscription_bypass != etait_offert:
            BypassGrantLog.objects.create(
                actor=request.user,
                actor_label=request.user.email,
                target=user,
                target_label=user.email,
                granted=user.subscription_bypass,
                note=user.bypass_note,
            )
        return Response(StaffUserSerializer(user).data)
