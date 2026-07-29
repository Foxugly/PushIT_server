"""Chiffrement réversible des jetons d'émission.

Le jeton d'émission doit pouvoir t'être **remontré** dans la console — c'est ce
qui permet une page d'exemples toujours prête à copier. Il est donc chiffré, et
non haché : chiffré, pas en clair, pour qu'un dump de base seul ne livre rien
(la clé vit dans SSM, pas dans la base).

Ce module ne participe **jamais** à l'authentification. Celle-ci passe par
l'empreinte, qui reste à sens unique : un défaut ici ne peut donc pas affaiblir
le contrôle d'accès. Corollaire heureux : perdre les clés rend les jetons
illisibles mais **toujours fonctionnels**.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class RevealUnavailable(Exception):
    """Aucune clé configurée : on ne peut pas remontrer un jeton."""


def _keys() -> list[str]:
    return [k for k in getattr(settings, "APP_TOKEN_ENCRYPTION_KEYS", []) if k]


def is_configured() -> bool:
    return bool(_keys())


def _cipher():
    """Plusieurs clés : la première chiffre, toutes déchiffrent.

    `MultiFernet` et non `Fernet`, dès le premier jour. Avec une clé unique, la
    faire tourner un jour rendrait d'un coup TOUS les jetons illisibles — et le
    moment où l'on change une clé est rarement un moment calme. Rotation = poser
    la nouvelle en tête, re-chiffrer, retirer l'ancienne.
    """
    from cryptography.fernet import Fernet, MultiFernet

    cles = _keys()
    if not cles:
        raise RevealUnavailable("APP_TOKEN_ENCRYPTION_KEYS n'est pas configuré.")
    try:
        return MultiFernet([Fernet(k.encode()) for k in cles])
    except (ValueError, TypeError) as exc:
        # Une clé mal formée est une erreur de déploiement, pas une erreur
        # d'exécution : mieux vaut le dire clairement que chiffrer de travers.
        raise ImproperlyConfigured(f"Clé de chiffrement invalide : {exc}") from exc


def encrypt(raw: str) -> bytes:
    """Chiffre un jeton. Renvoie b"" si aucune clé n'est posée.

    Ne jamais retomber sur du clair : sans clé, on préfère perdre la relecture
    plutôt que stocker le secret en clair sans que personne ne s'en aperçoive.
    """
    if not is_configured():
        return b""
    return _cipher().encrypt(raw.encode())


def decrypt(blob: bytes) -> str:
    if not blob:
        raise RevealUnavailable("Ce jeton n'a pas de copie déchiffrable.")
    from cryptography.fernet import InvalidToken

    try:
        return _cipher().decrypt(bytes(blob)).decode()
    except InvalidToken as exc:
        # Chiffré avec une clé qui n'est plus dans la liste : le jeton reste
        # utilisable (l'authentification passe par l'empreinte), il n'est plus
        # que illisible. On le dit tel quel.
        raise RevealUnavailable("Aucune clé ne déchiffre ce jeton.") from exc
