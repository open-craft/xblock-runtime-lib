"""
Settings for the XBlock Runtime Lib app.
"""

from os.path import abspath, dirname, join


def root(*args):
    """
    Get the absolute path of the given path relative to the project root.
    """
    return join(abspath(dirname(__file__)), *args)


USE_TZ = True

INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'xblock_runtime_lib',
)

LOCALE_PATHS = [
    root('xblock_runtime_lib', 'conf', 'locale'),
]

SECRET_KEY = 'insecure-secret-key'
