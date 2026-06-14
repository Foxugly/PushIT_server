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

> Les constats de l'audit multi-agents (2026-06-14) seront ajoutés ici après vérification.
