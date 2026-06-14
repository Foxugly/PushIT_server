# BACKLOG — PushIT_server (Django REST)

Issu d'une revue de session (2026-06-14). Sévérités : **P1** important · **P2/P3** à nettoyer.
Le travail coché est commité/poussé sur `Foxugly/PushIT_server` (`main`, CI verte).

---

## ✅ Fait le 2026-06-14

- [x] **Logo par application** — `Application.logo` (ImageField), upload `POST /apps/<id>/logo/`,
  URL absolue dans `ApplicationRead` + `application_logo` sur les notifs.
- [x] **Inbox destinataire** — `GET /notifications/device/?push_token=` (Model B), unlink par app.
- [x] **Deep-link push** — payload FCM `data.notification_id`.
- [x] **Fenêtre par date d'envoi** — `GET /notifications/device/?sent_since=<ISO>` (filtre `sent_at>=`,
  omis = historique complet, rétro-compatible).
- [x] **Notifs par device (vue propriétaire)** — `GET /devices/<id>/notifications/` paginé,
  scopé propriétaire, filtre `application_id`, statut de livraison par device.
- [x] **Livraison par device sur le détail** — `GET /notifications/<id>/` renvoie `deliveries[]`
  (device_id, device_name, status, sent_at, attempt_count) via `NotificationDetailSerializer`.
- [x] **Ops nginx pushit-api** — `client_max_body_size 25m` (évitait un 413 sur upload logo),
  `/media/` ajouté, `/static/` caché (`access_log off; expires 30d`), `proxy_redirect off` ;
  conf live réalignée sur le template repo (gzip/brotli déjà actifs). *(édité live, root, hors-bande)*

## À faire

- [ ] **P2 — Cohérence pagination** : la plupart des list endpoints JWT sont en tableau nu
  (`pagination_class=None`, lu ainsi par le SPA), seuls `/devices/<id>/notifications/` et quelques
  autres paginent. Décider d'une stratégie cohérente et la documenter ; surtout, **`/notifications/`
  charge tout l'historique** — risque à volume élevé (le front le lit aussi sans pagination).
- [ ] **P3 — `deploy/nginx/pushit-api.conf` vs live** : le déploiement n'applique pas la conf nginx
  (root hors-bande). Envisager d'intégrer l'install de la conf au pipeline pour stopper le drift.

## Audit multi-agents (2026-06-14) — constats confirmés

- [ ] **P2 — Exactitude du schéma OpenAPI (lot)** : `drf-spectacular` marque des champs `readOnly`
  comme `required` dans les schémas *Create (requête) — `last_used_at` (ApplicationCreate),
  `sent_at` (NotificationCreate), `id`/`created_at`/`updated_at` (DeviceQuietPeriodWrite,
  ApplicationQuietPeriodWrite) — et `effective_scheduled_for` est `required` **et** `nullable` sur
  NotificationRead. Contradiction OpenAPI (les clients tolèrent aujourd'hui). *Fix probable unique :*
  activer `COMPONENT_SPLIT_REQUEST` (schémas requête/réponse séparés) puis régénérer `schema.yaml`.
  *(l'audit les notait P1 ; requalifiés P2 — cosmétique, pas de bug runtime).*
- [ ] **P2 — `DeviceLinkedApplication` contrat** : `logo` absent du `required` du schéma ; `description`
  non-null côté serializer (`CharField()` sans `allow_null`) alors que le modèle peut être vide et que
  les clients (Angular/Kotlin) l'attendent nullable. Durcir le serializer (`allow_null=True`) + régénérer.
- [ ] **P2 — `ApplicationRead.webhook_url`** : présent non-nullable mais absent du `required` → ambigu.
  Clarifier (nullable ou requis).
- [ ] **P2 — Race sur le statut de notification** : `_try_queue_notification()`
  (`notifications/api_views.py:625-693`) check-then-update sans `select_for_update()` quand appelé hors
  transaction (et bulk-send sans lock). Envelopper dans `transaction.atomic()` + `select_for_update()`
  systématiquement (cf. `_fetch_notification` qui a déjà le pattern), ou verrou optimiste (champ version).
