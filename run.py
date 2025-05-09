# run.py
from app import create_app
import logging

logger = logging.getLogger(__name__) # Логирование

app = create_app()

def main():
    app.run(host="0.0.0.0", port=8080, debug=True, use_reloader=False)

if __name__ == '__main__':
    main()
