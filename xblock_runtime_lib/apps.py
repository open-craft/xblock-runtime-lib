"""
AppConfig for blocks app.

This app provides an api for rendering and interacting with LabXchange XBlocks.

With code from:

    https://github.com/openedx/edx-platform/blob/af6cab8/openedx/core/djangoapps/xblock/apps.py
"""
from django.apps import AppConfig, apps
from django.conf import settings


class XBlockRuntimeLib(AppConfig):
    """
    Sets up the blocks application.
    """
    name = 'xblock-runtime-lib.apps'
    label = 'xblock-runtime-lib'
    verbose_name = 'XBlockRuntimeLib'

    def get_runtime_system_params(self):
        """
        Get the BlockRuntimeSystem parameters appropriate for viewing and/or
        editing XBlock content in the frontend.
        """
        return dict(
            student_data_mode='persisted',
        )

    def get_site_root_url(self):
        """
        Get the absolute root URL to this site.
        Should not have any trailing slash.
        """
        return settings.LABXCHANGE_FRONTEND_URL

    def get_learning_context_params(self):
        """
        Get additional kwargs that are passed to learning context implementations
        (LearningContext subclass constructors). For example, this can be used to
        specify that a library learning context should load the library's list of
        blocks from the _draft_ version of the block, or from the published
        version of the library.
        """
        return {}


def get_blocks_app_config():
    """
    Returns the active blocks config (which lives in labxchange.apps.blocks.apps).
    """
    return apps.get_app_config('blocks')
