web: gunicorn app:server --bind 0.0.0.0:$PORT --workers 2 --timeout 120
api: uvicorn api.main:app --host 0.0.0.0 --port ${API_PORT:-8000} --workers 2
