"""
Utility functions for the Blocks app.

With code from:

    https://github.com/openedx/edx-platform/blob/4752ed/openedx/core/djangoapps/xblock/utils.py
"""
import hmac
import math
import re
import time
from uuid import uuid4

import crum
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.reverse import reverse as drf_reverse


User = get_user_model()


# TODO: upstream have fixed a bug with this token generation, see https://github.com/openedx/edx-platform/pull/26224
# Incorporate this fix and determine a roll-out strategy that doesn't disrupt learners.
def get_secure_hash_for_xblock_handler(username: str, block_key_str: str, time_idx: int = 0):
    """
    Get a secure token (one-way hash) used kind of like a CSRF token
    to ensure that only authorized XBlock code in an IFrame we created
    is calling the secure_xblock_handler endpoint.

    For security, we need these hashes to have an expiration date. So:
    the hash incorporates the current time, rounded to the lowest TOKEN_PERIOD
    value. When checking this, you should check both time_idx=0 and time_idx=-1
    in case we just recently moved from one time period to another (i.e. at the
    stroke of midnight UTC or similar)
    """
    TOKEN_PERIOD = 24 * 60 * 60 * 2  # These URLs are valid for 2-4 days
    time_token = math.floor(time.time() / TOKEN_PERIOD)
    time_token += TOKEN_PERIOD * time_idx
    check_string = str(time_token) + ':' + username + ':' + block_key_str
    secure_key = hmac.new(settings.SECRET_KEY.encode('utf-8'), check_string.encode('utf-8'), 'blake2b').hexdigest()
    return secure_key[:20]


def get_secure_xblock_handler_url(request, block_key_str: str, handler_name: str) -> str:
    """
    Get an absolute URL that an XBlock can call, without any session cookies,
    to invoke the specified handler.
    """
    username = request.user.username
    secure_key = get_secure_hash_for_xblock_handler(username, block_key_str)
    return drf_reverse('api:v1:xblocks-secure-handler', request=request, kwargs={
        'pk': block_key_str,
        'username': username,
        'secure_key': secure_key,
        'handler_name': handler_name,
    })


def rewrite_blockstore_runtime_handler_urls(html: str, request) -> str:
    """
    Replace references to handler urls with versions proxied through the LX backend.

    Some XBlocks like video and problem do not always create handler URLs using the prefix
    provided by the frontend runtime but instead create them in the backend and include in
    the student_view html. Se we search for any in the html content of the student view and
    swap them.
    """
    handler_url_pattern = (
        # This regex will match handler URL patterns amid html like:
        # href=\"/api/xblock/v2/xblocks/.../handler/download\"
        # &#34;/api/xblock/v2/xblocks/.../handler/publish_completion&#34;
        # "\\/api\\/xblock\\/v2\\/xblocks\\/...\\/handler\\/publish_completion"
        # pylint: disable=line-too-long
        rf'{settings.LMS_ROOT_PUBLIC}/api/xblock/v2/xblocks/(?P<block_id>[^/"\']+)/handler/(?P<user_id>\w+)-(?P<secure_token>\w+)/(?P<handler_name>[\w\-]+)'
    )

    def get_secure_xblock_handler_url_for_match(match):
        """ Get handler_url for match in html. """
        new_url = get_secure_xblock_handler_url(
            request=request,
            block_key_str=match.group('block_id'),
            handler_name=match.group('handler_name')
        )
        return new_url

    html = re.sub(handler_url_pattern, get_secure_xblock_handler_url_for_match, html)

    # Make sure the regex is catching all variants otherwise we will lose data.
    assert f'{settings.LMS_ROOT_PUBLIC}/api/xblock/v2/xblocks/' not in html

    return html


def get_xblock_id_for_anonymous_user(user):
    """
    Get a unique string that identifies the current anonymous (not logged in)
    user. (This is different than the "anonymous user ID", which is an
    anonymized identifier for a logged in user.)
    Note that this ID is a string, not an int. It is guaranteed to be in a
    unique namespace that won't collide with "normal" user IDs, even when
    they are converted to a string.
    """
    if not user or not user.is_anonymous:
        raise TypeError("get_xblock_id_for_anonymous_user() is only for anonymous (not logged in) users.")
    if hasattr(user, 'xblock_id_for_anonymous_user'):
        # If code elsewhere (like the xblock_handler API endpoint) has stored
        # the key on the AnonymousUser object, just return that - it supersedes
        # everything else:
        return user.xblock_id_for_anonymous_user
    # We use the session to track (and create if needed) a unique ID for this anonymous user:
    current_request = crum.get_current_request()
    if current_request and current_request.session:
        # Make sure we have a key for this user:
        if "xblock_id_for_anonymous_user" not in current_request.session:
            new_id = f"anon{uuid4().hex[:20]}"
            current_request.session["xblock_id_for_anonymous_user"] = new_id
        return current_request.session["xblock_id_for_anonymous_user"]
    else:
        raise RuntimeError("Cannot get a user ID for an anonymous user outside of an HTTP request context.")
