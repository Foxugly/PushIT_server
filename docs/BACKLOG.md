# Backlog PushIT

Ce qui reste après le lot de correctifs de l'audit du 2026-07-29
(`docs/audit-2026-07-29.md`). Chaque entrée dit **pourquoi ce n'est pas fait**,
parce que c'est l'information qui manque le plus quand on relit un backlog six
mois plus tard.

---

## Décisions à prendre (pas des correctifs)

### B1 — Durée du jeton de rafraîchissement : 365 jours
`config/settings/base.py:269`, piloté par `JWT_REFRESH_DAYS` en SSM.

Une XSS dans la console donne un an d'accès. Le compromis est assumé dans le code
(« rester connecté » façon messagerie) et la CSP réduit fortement le risque, mais
la vente d'abonnements approche.

**Pas fait parce que** c'est un arbitrage produit, pas un défaut : raccourcir
déconnecte des utilisateurs réels. Le paramètre existe déjà, la bascule est un
geste ops :
```
aws ssm put-parameter --name /pushit/prod/JWT_REFRESH_DAYS --value 90 \
  --type String --overwrite --region eu-west-1
```
puis `sudo systemctl restart pushit-env-fetch pushit-gunicorn`.

### B2 — Exiger DMARC sur l'e-mail entrant
`INBOUND_EMAIL_REQUIRE_DMARC`, `notifications/serializers.py`.

Le mécanisme complet existe et le collecteur récupère bien
`Authentication-Results`. **Le lot a livré le mode observation** : le journal
`inbound_dmarc_would_reject` dit désormais ce que l'activation refuserait.

**Pas activé parce que** un message interne au tenant M365 n'a souvent aucun
en-tête `Authentication-Results` — il ne franchit pas la frontière SMTP — et
c'est justement le cas d'usage principal (le propriétaire écrit depuis son
compte). Activer à l'aveugle casserait l'ingestion e-mail.

**Condition :** lire le journal sur du trafic réel. Si `has_auth_results` est
vrai partout, activer ; sinon, ne pas activer et fermer cette entrée en disant
pourquoi.

---

## Dette identifiée, non corrigée

### B3 — Colonnes `app_token_hash` / `app_token_prefix`, et le drapeau devenu inerte
Étape 3 de la tâche 6 du plan de séparation.

Depuis l'extinction, `legacy_send_last_used_at` ne bouge plus et le bandeau de la
console ne peut plus s'allumer : c'est du code inerte, à retirer **avec** les
colonnes.

**Pas fait parce que** la suppression est irréversible et couperait aussi
l'**enrôlement** hérité : un vieux QR imprimé, ou une réinstallation de
l'application mobile avec l'ancien jeton en poche, ne pourrait plus se rattacher.
À faire quand plus aucun terminal n'en dépend.

### B4 — Couverture des branches à 48 % côté console
Les chemins d'erreur sont peu couverts : révélation indisponible (503), mot de
passe refusé, pannes réseau, expirations.

**Pas fait parce que** ce n'est pas un correctif ponctuel mais un travail de
fond ; il se traite en accompagnant chaque prochaine modification de la console.

### B5 — `.subscribe()` non gardé dans deux services racine
`core/services/console-shell.service.ts` (8), `core/services/language-preference.service.ts` (1).

**Pas fait parce que** ces services sont `providedIn: 'root'` : ils vivent aussi
longtemps que l'application, il n'y a rien à annuler, et `takeUntilDestroyed` y
serait un contresens. Les 13 composants, eux, sont corrigés.

### B6 — Reprise d'un terminal par connaissance de son jeton FCM
`devices/api_views_app_token.py:40-45`.

Qui obtient le jeton FCM d'un tiers peut se l'attribuer et couper les
notifications de la victime (déni de service ciblé).

**Pas fait parce que** le jeton n'est pas devinable et le comportement actuel est
volontaire (un terminal qui change de mains doit changer de propriétaire). Une
protection demanderait une preuve de possession côté appareil — disproportionné
au risque.

### B7 — Test d'intégration : vert en CI, bloqué sous Windows
`tests/test_full_flow_integration.py`.

Le lot a réparé le `PYTHONPATH` et remis le script à jour ; le test est branché
en CI sur un job dédié. En local (Windows) il expire encore à l'inscription,
cause non identifiée — probablement la combinaison `runserver` + sous-processus.

**Pas fait parce que** l'environnement qui compte (CI, Ubuntu) est couvert ; le
confort local viendra avec une session dédiée.

---

## Ce que l'audit n'a pas couvert

À traiter comme des audits à part entière, pas comme des tickets.

- **Scan de dépendances** — ni `pip-audit` ni `npm audit` n'ont été lancés. Le
  plus rentable des quatre, et le plus rapide.
- **Chaîne d'envoi FCM** (`notifications/services.py`, `push.py`, ~1 500 lignes) —
  parcourue en surface seulement. Les périodes de silence, les reprises et les
  états de livraison mériteraient une passe dédiée.
- **iOS** — jamais compilé. La migration Keychain du code d'enrôlement n'est
  vérifiée que par lecture.
- **RGPD / conservation** — rien n'a été regardé sur ce qui est gardé (jetons
  push, adresses e-mail entrantes, journaux) ni combien de temps.
