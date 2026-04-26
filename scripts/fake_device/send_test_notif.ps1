param(
    [int]$DeviceId = 2,
    [int]$AppId = 1,
    [string]$Title = "Test FCM",
    [string]$Message = "Hello from PushIT",
    [string]$Email = "demo@example.com",
    [string]$Password = "VeryStr0ngPassword123!",
    [string]$ApiBase = "http://127.0.0.1:8000/api/v1"
)

$ErrorActionPreference = "Stop"

$loginBody = @{ email = $Email; password = $Password } | ConvertTo-Json -Compress
$login = Invoke-RestMethod -Uri "$ApiBase/auth/login/" -Method POST -ContentType "application/json" -Body $loginBody
$jwt = $login.access
Write-Host "Logged in OK"

$notifBody = @{
    application_id = $AppId
    device_ids     = @($DeviceId)
    title          = $Title
    message        = $Message
} | ConvertTo-Json -Compress

$notif = Invoke-RestMethod -Uri "$ApiBase/notifications/" -Method POST `
    -ContentType "application/json" `
    -Headers @{ Authorization = "Bearer $jwt" } `
    -Body $notifBody
$notifId = $notif.id
Write-Host "Created notif id=$notifId"

$send = Invoke-RestMethod -Uri "$ApiBase/notifications/$notifId/send/" -Method POST `
    -Headers @{ Authorization = "Bearer $jwt" }
Write-Host "Send response:"
$send | ConvertTo-Json -Depth 5
