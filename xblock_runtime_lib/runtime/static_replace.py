"""
Provides methods for expanding static URLs in XBlock content.

With code from:

    https://github.com/openedx/edx-platform/blob/9aefd6f/common/djangoapps/static_replace/__init__.py
"""
import re

from django.conf import settings


def _url_replace_regex(prefix):
    """
    Match static urls in quotes that don't end in '?raw'.

    To anyone contemplating making this more complicated:
    http://xkcd.com/1171/
    """
    return """(?x)                # flags=re.VERBOSE
        (?P<quote>\\\\?['"])      # the opening quotes
        (?P<prefix>{prefix})      # the prefix
        (?P<rest>.*?)             # everything else in the url
        (?P=quote)                # the first matching closing quote
        """.format(prefix=prefix)


def process_static_urls(text, replacement_function, data_dir=None):
    """
    Run an arbitrary replacement function on any urls matching the static file
    directory

    LX note: edx-platform avoided modifying URLs that looked like normal static or XBlock resource links,
    but we had to, since the TinyMCE-edited HTML assets save URLs like /static/<filename>.
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

        return replacement_function(original, prefix, quote, rest)

    return re.sub(
        _url_replace_regex('(?:{static_url}|/static/)(?!{data_dir})'.format(
            static_url=settings.STATIC_URL,
            data_dir=data_dir
        )),
        wrap_part_extraction,
        text
    )
