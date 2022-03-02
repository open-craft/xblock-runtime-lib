"""
Python API for the Blocks app.

With code from:

    https://github.com/openedx/edx-platform/blob/ef8f841/openedx/core/djangoapps/xblock/api.py
"""
import logging
import threading
from typing import Callable
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.http import Http404
from django.urls import reverse
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from xblock.exceptions import NoSuchViewError

from .apps import get_blocks_app_config
from .learning_context.manager import get_learning_context_impl, UnsupportedLearningContext
from .runtime.blockstore_runtime import BlockstoreXBlockRuntime, UnsupportedBlockType
from .runtime.runtime import XBlockRuntimeSystem
from .utils import get_xblock_id_for_anonymous_user, get_secure_hash_for_xblock_handler


log = logging.getLogger(__name__)
User = get_user_model()


def get_runtime_system():
    """
    Get the BlockRuntime, which is a single long-lived factory that can
    create user-specific runtimes.
    The Runtime System isn't always needed (e.g. for management commands), so to
    keep application startup faster, it's only initialized when first accessed
    via this method.
    """
    # The runtime system should not be shared among threads, as there is currently a race condition when parsing XML
    # that can lead to duplicate children.
    # (In BlockstoreXBlockRuntime.get_block(), has_cached_definition(def_id) returns false so parse_xml is called, but
    # meanwhile another thread parses the XML and caches the definition; then when parse_xml gets to XML nodes for
    # child blocks, it appends them to the children already cached by the other thread and saves the doubled list of
    # children; this happens only occasionally but is very difficult to avoid in a clean way due to the API of parse_xml
    # and XBlock field data in general [does not distinguish between setting initial values during parsing and changing
    # values at runtime due to user interaction], and how it interacts with BlockstoreFieldData. Keeping the caches
    # local to each thread completely avoids this problem.)
    cache_name = f'_system_{threading.get_ident()}'
    if not hasattr(get_runtime_system, cache_name):
        params = dict(
            handler_url=get_handler_url,
            runtime_class=BlockstoreXBlockRuntime,
        )
        params.update(get_blocks_app_config().get_runtime_system_params())
        setattr(get_runtime_system, cache_name, XBlockRuntimeSystem(**params))
    return getattr(get_runtime_system, cache_name)


def load_block(usage_key: UsageKey, user: User):
    """
    Load the specified XBlock for the given user.

    Returns an instantiated XBlock.

    Exceptions:
        NotFound - if the XBlock doesn't exist or if the user doesn't have the
                   necessary permissions
        TypeError - if the usage_key is not a supported LearningContextKey

    Args:
        usage_key(OpaqueKey): block identifier
        user(User): user requesting the block
    """
    # Is this block part of a course, a library, or what?
    # Get the Learning Context Implementation based on the usage key
    context_impl = get_learning_context_impl(usage_key)
    # Now, check if the block exists in this context and if the user has
    # permission to render this XBlock view:
    if user is not None and not context_impl.can_view_block(user, usage_key):
        # We do not know if the block was not found or if the user doesn't have
        # permission, but we want to return the same result in either case:
        raise NotFound(f"XBlock {usage_key} does not exist, or you don't have permission to view it.")

    runtime = get_runtime_system().get_runtime(user=user)

    return runtime.get_block(usage_key)


def render_block_view(request: Request, pk: str, view_name: str, get_block_metadata: Callable):
    """
    Loads the given block with the XBlockRuntime, and returns the rendered content and metadata.
    """
    try:
        usage_key = UsageKey.from_string(pk)
    except InvalidKeyError as e:
        raise Http404 from e

    block = load_block(usage_key, request.user)

    # Render the requested view, falling back if the view is not found
    try:
        fragment = block.render(view_name)
    except NoSuchViewError:
        fallback_view = None
        if view_name == 'author_view':
            fallback_view = 'student_view'
        if fallback_view:
            fragment = block.render(fallback_view)
        else:
            raise

    response_data = get_block_metadata(block.scope_ids.usage_id)
    response_data.update(fragment.to_dict())
    return response_data


# TODO: reconcile this function with .utils.get_secure_xblock_handler_url
def get_handler_url(usage_key, handler_name, user, extra_params=None):
    """
    A method for getting the URL to any XBlock handler. The URL must be usable
    without any authentication (no cookie, no OAuth/JWT), and may expire. (So
    that we can render the XBlock in a secure IFrame without any access to
    existing cookies.)
    The returned URL will contain the provided handler_name, but is valid for
    any other handler on the same XBlock. Callers may replace any occurrences of
    the handler name in the resulting URL with the name of any other handler and
    the URL will still work. (This greatly reduces the number of calls to this
    API endpoint that are needed to interact with any given XBlock.)
    Params:
        usage_key       - Usage Key (Opaque Key object or string)
        handler_name    - Name of the handler or a dummy name like 'any_handler'
        user            - Django User (registered or anonymous)
        extra_params    - Optional extra params to append to the handler_url (dict)
    This view does not check/care if the XBlock actually exists.
    """
    usage_key_str = str(usage_key)
    site_root_url = get_blocks_app_config().get_site_root_url()
    if not user:  # lint-amnesty, pylint: disable=no-else-raise
        raise TypeError("Cannot get handler URLs without specifying a specific user ID.")
    elif user.is_authenticated:
        user_id = user.username
    elif user.is_anonymous:
        user_id = get_xblock_id_for_anonymous_user(user)
    else:
        raise ValueError("Invalid user value")
    # Now generate a token-secured URL for this handler, specific to this user
    # and this XBlock:
    secure_token = get_secure_hash_for_xblock_handler(user_id, usage_key_str)
    # Now generate the URL to that handler:
    path = reverse('api:v1:xblocks-secure-handler', kwargs={
        'pk': usage_key_str,
        'username': user_id,
        'secure_key': secure_token,
        'handler_name': handler_name,
    })
    qstring = urlencode(extra_params) if extra_params else ''
    if qstring:
        qstring = '?' + qstring
    # We must return an absolute URL. We can't just use
    # rest_framework.reverse.reverse to get the absolute URL because this method
    # can be called by the XBlock from python as well and in that case we don't
    # have access to the request.
    return site_root_url + path + qstring
