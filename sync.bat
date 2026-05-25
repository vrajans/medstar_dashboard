@echo off
:: sync.bat — copies latest files from Claude session folder to C:\Projects\medstar_dashboard
:: Run this in PowerShell after Claude makes any code change

set SRC=C:\Users\Varat\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\local-agent-mode-sessions\1865ac88-def6-4105-93f0-b3e9e84d58f3\3ea31bad-eeef-4251-be76-71ac7f765625\local_8f0227cf-286e-4201-9c12-a745a10d89e7\outputs\medstar_dashboard
set DST=C:\Projects\medstar_dashboard

echo.
echo ── Syncing MedStar files to %DST% ──

copy /Y "%SRC%\app.py"              "%DST%\app.py"
copy /Y "%SRC%\data_loader.py"      "%DST%\data_loader.py"
copy /Y "%SRC%\assets\style.css"    "%DST%\assets\style.css"
copy /Y "%SRC%\requirements.txt"    "%DST%\requirements.txt"

echo.
echo Done! Now run:  python app.py
echo Then open:     http://127.0.0.1:8050
echo.
pause
