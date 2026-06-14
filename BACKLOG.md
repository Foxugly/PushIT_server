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

- [ ] **P3 — `deploy/nginx/pushit-api.conf` vs live** : le déploiement n'applique pas la conf nginx
  (root hors-bande). Intégrer l'install de la conf au pipeline pour stopper le drift. *(différé :
  infra-sensible — la conf live a déjà été réalignée à la main ; un changement du pipeline SSM/nginx
  est risqué pour un gain P3).*

## Audit multi-agents (2026-06-14) — traités le 2026-06-14

- [x] **P2 — Exactitude du schéma OpenAPI (lot)** — `COMPONENT_SPLIT_REQUEST` activé : les schémas
  *Request (create/write) n'incluent plus les champs `readOnly` dans `required`. Schéma régénéré.
  *(N.B. `effective_scheduled_for` `required`+`nullable` était un faux positif : valide en OpenAPI —
  `required` = clé présente, `nullable` = valeur peut être null ; orthogonaux.)*
- [x] **P2 — `DeviceLinkedApplication` contrat** — `logo` désormais `required` + nullable (drop
  `required=False`). `description` reste non-null (contrat backend correct ; c'est le modèle Kotlin
  qui s'aligne, cf. backlog mobile).
- [x] **P2 — `ApplicationRead.webhook_url`** — déclaré read-only → `required` + non-nullable
  (toujours présent, chaîne vide si non configuré).
- [x] **P2 — Cohérence pagination** — `OptionalPageNumberPagination` (opt-in `?page`/`?page_size`,
  tableau nu par défaut) désormais sur `/notifications`, `/notifications/future`, `/devices`, `/apps`.
- [x] **P2 — Race sur le statut de notification** — **vérifié sûr, pas de changement de logique** :
  l'`UPDATE ... WHERE status=previous` conditionnel est le garde atomique (exactement un appel concurrent
  passe, l'autre obtient 409) ; le `.delay()` Celery part après le commit (autocommit) donc le worker ne
  voit jamais un QUEUED non-committé. Étendre le `select_for_update` casserait le `task_id` synchrone dans
  la réponse pour un gain nul. Documenté dans la docstring de `_try_queue_notification`.
