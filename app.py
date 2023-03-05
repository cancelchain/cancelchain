import os

from cancelchain import create_app

app = create_app()


if __name__ == '__main__':
    app.run(
        debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080))
    )
