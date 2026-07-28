"""Fixtures partagées par toute la suite."""
import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def reset_throttle_history():
    """Vide le cache entre chaque test.

    Les throttles DRF stockent leur historique dans le cache par défaut — un
    LocMemCache qui vit le temps du processus. Sans ce reset, les requêtes
    s'accumulent d'un test à l'autre : le throttle anonyme (30/min, indexé sur
    l'IP, la même pour tous) finit par renvoyer 429 à des tests qui ne
    demandaient rien de tel.

    Le seuil dépend de la vitesse d'exécution, puisque la fenêtre est glissante
    sur 60 s : la suite passait en local et échouait en CI, plus rapide. Ajouter
    un test n'importe où pouvait donc en casser un autre, ailleurs.
    """
    cache.clear()
    yield
    cache.clear()
