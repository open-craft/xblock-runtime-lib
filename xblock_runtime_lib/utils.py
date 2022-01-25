"""
Various utility functions related to XBlocks.

With code from:

    https://github.com/openedx/edx-platform/blob/9514cb57/openedx/core/lib/xblock_utils/__init__.py
    https://github.com/openedx/edx-platform/blob/9514cb57/openedx/core/djangoapps/common_views/xblock.py
    https://github.com/openedx/edx-platform/blob/9514cb57/common/djangoapps/static_replace/__init__.py
"""


import logging
import mimetypes
import re

from django.conf import settings
from django.http import Http404, HttpResponse
from django.urls import reverse
from web_fragments.fragment import Fragment
from xblock.core import XBlock
from xblock.plugin import default_select

log = logging.getLogger(__name__)

XBLOCK_STATIC_RESOURCE_PREFIX = '/static/xblock'


def wrap_fragment(fragment, new_content):
    """
    Returns a new Fragment that has `new_content` and all
    as its content, and all of the resources from fragment
    """
    wrapper_frag = Fragment(content=new_content)
    wrapper_frag.add_fragment_resources(fragment)
    return wrapper_frag


def xblock_resource(request, block_type, uri):
    """
    Return a package resource for the specified XBlock.
    """
    try:
        # Figure out what the XBlock class is from the block type, and
        # then open whatever resource has been requested.
        xblock_class = XBlock.load_class(block_type, select=default_select)
        content = xblock_class.open_local_resource(uri)
    except OSError:
        log.info('Failed to load xblock resource', exc_info=True)
        raise Http404
    except Exception:
        log.error('Failed to load xblock resource', exc_info=True)
        raise Http404

    mimetype, _ = mimetypes.guess_type(uri)
    return HttpResponse(content, content_type=mimetype)


def process_static_urls(text, replacement_function, data_dir=None):
    """
    Run an arbitrary replacement function on any urls matching the static file
    directory
    """
    def wrap_part_extraction(match):
        """
        Unwraps a match group for the captures specified in _url_replace_regex
        and forward them on as function arguments
        """
        original = match.group(0)
        prefix = match.group('prefix')
        quote = match.group('quote')
        rest = match.group('rest')

        # Don't rewrite XBlock resource links.  Probably wasn't a good idea that /static
        # works for actual static assets and for magical course asset URLs....
        full_url = prefix + rest

        starts_with_static_url = full_url.startswith(str(settings.STATIC_URL))
        starts_with_prefix = full_url.startswith(XBLOCK_STATIC_RESOURCE_PREFIX)
        contains_prefix = XBLOCK_STATIC_RESOURCE_PREFIX in full_url
        if starts_with_prefix or (starts_with_static_url and contains_prefix):
            return original

        return replacement_function(original, prefix, quote, rest)

    return re.sub(
        _url_replace_regex('(?:{static_url}|/static/)(?!{data_dir})'.format(
            static_url=settings.STATIC_URL,
            data_dir=data_dir
        )),
        wrap_part_extraction,
        text
    )
