# Copyright 2023 Thomas Bohmbach, Jr.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import click
from flask import Flask
from flask.cli import FlaskGroup

__version__ = "1.3.2"


def create_app(app=None, register_browser=True, test_config=None):
    from .application import init_app
    from .cache import cache
    from .config import EnvAppSettings
    from .database import db
    from .tasks import init_tasks

    app = app or Flask(__name__)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    if not test_config:
        app.config.from_object(EnvAppSettings.from_env())
        app.config.from_envvar('CANCELCHAIN_SETTINGS', silent=True)
    else:
        app.config.from_object(EnvAppSettings())
        app.config.from_mapping(test_config)

    init_app(app, register_browser=register_browser)
    try:
        db.init_app(app)
    except RuntimeError as e:
        app.logger.error(e)
    cache.init_app(app)
    init_tasks(app)

    @app.shell_context_processor
    def make_shell_context():
        return {'app': app, 'db': db}

    return app


@click.version_option(package_name='cancelchain')
@click.group(cls=FlaskGroup, create_app=create_app, add_version_option=False)
def cli():
    pass
