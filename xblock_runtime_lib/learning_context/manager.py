"""
Helper methods for working with learning contexts.

With code from:

https://github.com/open-craft/edx-platform/blob/d3f6ed0/openedx/core/djangoapps/xblock/learning_context/manager.py
"""
from edx_django_utils.plugins import PluginManager
from edx_django_utils.plugins.plugin_manager import PluginError
from opaque_keys import OpaqueKey
from opaque_keys.edx.keys import LearningContextKey, UsageKeyV2

from ..apps import get_blocks_app_config


# TODO: using a plugin manager is probably overkill for us, since we only need to support a limited, known set of
# learning contexts. But since we share lx_pathway_plugin with Open edX, I've continued this approach here, which
# required adding backend/setup.py and setting up an entry_point for our 'lib' learning context class.

# TODO: Need to remove the openedx dependencies from lx_pathway_plugin so we can use its pathway context as a plugin.


class UnsupportedLearningContext(Exception):
    """
    Raised for learning context types which are not (yet) supported by LabXchange.
    """


class LearningContextPluginManager(PluginManager):
    """
    Plugin manager that uses stevedore extension points (entry points) to allow
    learning contexts to register as plugins.

    The key of the learning context must match the CANONICAL_NAMESPACE of its
    LearningContextKey
    """
    NAMESPACE = 'openedx.learning_context'


_learning_context_cache = {}


def get_learning_context_impl(key):
    """
    Given an opaque key, get the implementation of its learning context.

    Returns a subclass of LearningContext

    Raises TypeError if the specified key isn't a type that has a learning
    context.
    Raises UnsupportedLearningContext if there is any issue loading the context for the given key.
    """
    if isinstance(key, LearningContextKey):
        context_type = key.CANONICAL_NAMESPACE  # e.g. 'lib'
    elif isinstance(key, UsageKeyV2):
        context_type = key.context_key.CANONICAL_NAMESPACE
    else:
        # Maybe this is an older modulestore key etc.
        raise TypeError(f"key '{key}' is not an opaque key. You probably forgot [KeyType].from_string(...)")

    if context_type not in _learning_context_cache:
        # Load this learning context type.
        params = get_blocks_app_config().get_learning_context_params()
        try:
            _learning_context_cache[context_type] = LearningContextPluginManager.get_plugin(context_type)(**params)
        except PluginError as err:
            raise UnsupportedLearningContext(f"Unsupported context type: {context_type}") from err
    return _learning_context_cache[context_type]
