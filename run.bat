@echo off
echo Installing dependencies...
pip install -r requirements.txt
echo.
echo Starting MedStar Analytics Dashboard...
echo Open your browser at: http://127.0.0.1:8050
echo.
python app.py
pause
