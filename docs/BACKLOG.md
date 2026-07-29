# Backlog PushIT

Ce qui reste après le lot de correctifs de l'audit du 2026-07-29
(`docs/audit-2026-07-29.md`). Chaque entrée dit **pourquoi ce n'est pas fait**,
parce que c'est l'information qui manque le plus quand on relit un backlog six
mois plus tard.

---

## Décisions

### ~~B1~~ — CLOS le 2026-07-30 : le jeton de rafraîchissement reste à 365 jours
`config/settings/base.py:269`, piloté par `JWT_REFRESH_DAYS` en SSM.

**Décision : ne pas changer.** Le confort d'usage — rester connecté comme dans une
messagerie — l'emporte sur le gain de sécurité, à cette échelle et pour ce
produit.

Ce qui rend la décision tenable :
- la CSP de la console est stricte (nonce, pas de `unsafe-inline` en script) ;
- les **deux XSS Angular** qui rendaient réellement ce choix dangereux ont été
  corrigées le 2026-07-30 (console #42) — c'était le vrai risque, pas la durée ;
- le backend **rote et blackliste** le jeton à chaque rafraîchissement : un jeton
  volé cesse de fonctionner dès que le client légitime en obtient un nouveau.

**À réouvrir si** une XSS est trouvée dans la console, ou si un client demande une
politique de session plus courte. La bascule reste un seul geste :
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

**Condition, désormais mesurable.** Elle ne l'était pas : seuls les *échecs* de ce
chemin étaient comptés, un enrôlement hérité réussi ne laissait aucune trace — la
condition n'aurait donc jamais pu être déclarée vraie. Le compteur existe
maintenant :

```
pushit_app_token_auth_total{outcome="legacy_enrolment"}   # doit rester à 0
pushit_app_token_auth_total{outcome="enrolment_code"}     # doit être le seul à bouger
```
plus le journal `legacy_enrolment_used` (qui nomme l'application concernée).

Quand `legacy_enrolment` ne bouge plus sur plusieurs semaines : supprimer les deux
colonnes, le drapeau `legacy_send_last_used_at`, le bandeau de la console et son
bloc de copie dans les 5 catalogues, en une seule PR.

### B4 — Couverture des branches à 48 % côté console
Les chemins d'erreur sont peu couverts : révélation indisponible (503), mot de
passe refusé, pannes réseau, expirations.

**Pas fait parce que** ce n'est pas un correctif ponctuel mais un travail de
fond ; il se traite en accompagnant chaque prochaine modification de la console.

### ~~B5~~ — CLOS le 2026-07-30 : `.subscribe()` dans deux services racine
`core/services/console-shell.service.ts` (8), `core/services/language-preference.service.ts` (1).

**Décision : ne pas faire.** Ces services sont `providedIn: 'root'` — ils vivent
aussi longtemps que l'application, il n'y a rien à annuler, et
`takeUntilDestroyed` y serait un contresens. Les 13 composants, eux, sont
corrigés. Ce n'était pas une tâche mais un constat mal classé.

### ~~B6~~ — CLOS le 2026-07-30 : reprise d'un terminal via son jeton FCM
`devices/api_views_app_token.py:40-45`.

**Décision : risque accepté.** Qui obtient le jeton FCM d'un tiers peut se
l'attribuer et couper ses notifications. Mais le jeton n'est pas devinable, et le
comportement est *voulu* : un terminal qui change de mains doit changer de
propriétaire. Une preuve de possession côté appareil serait disproportionnée.

À réouvrir seulement si un incident réel se présente.

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

- ~~**Scan de dépendances**~~ — **FAIT le 2026-07-29**, et il n'était pas vide :
  30 avis côté Python (26 sur **Pillow**, qui décode les logos téléversés) et
  **7 vulnérabilités de production** côté console, dont **deux XSS Angular**.
  Corrigé (serveur #31, console #42) ; les deux scans sont désormais propres sur
  ce qui part en production.
  **Reste :** 12 avis dans l'outillage de développement de la console (karma,
  vite, glob, `@angular/build`…). Leur correction demande des montées **majeures**
  pour des paquets jamais servis aux utilisateurs — à faire à l'occasion d'une
  montée d'Angular, pas en forçant.
  **À automatiser :** Dependabot est **désactivé** sur les trois dépôts (vérifié :
  HTTP 403). L'activer coûte un clic et remplace ce scan manuel.
- **Chaîne d'envoi FCM** (`notifications/services.py`, `push.py`, ~1 500 lignes) —
  parcourue en surface seulement. Les périodes de silence, les reprises et les
  états de livraison mériteraient une passe dédiée.
- **iOS** — jamais compilé. La migration Keychain du code d'enrôlement n'est
  vérifiée que par lecture.
- **RGPD / conservation** — rien n'a été regardé sur ce qui est gardé (jetons
  push, adresses e-mail entrantes, journaux) ni combien de temps.
