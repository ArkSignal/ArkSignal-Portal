@echo off
rem Opens Chrome with its own profile and lets Element run on the plain-http test box (it insists on a secure page otherwise).
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="%TEMP%\matrixtest" --no-first-run --unsafely-treat-insecure-origin-as-secure=http://test-box.invalid:18430,http://test-box.invalid:18009,http://test-box.invalid:18008 http://test-box.invalid:18430/chat/
