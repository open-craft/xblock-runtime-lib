"""
Helpful methods to use when parsing OLX (XBlock XML)

With code from:

    https://github.com/openedx/edx-platform/blob/25b275b/openedx/core/djangoapps/xblock/runtime/olx_parsing.py
"""

from collections import namedtuple


class BundleFormatException(Exception):
    """
    Raised when certain errors are found when parsing the OLX in a content
    library bundle.
    """


XBlockInclude = namedtuple('XBlockInclude', ['link_id', 'block_type', 'definition_id', 'usage_hint'])


def parse_xblock_include(include_node):
    """
    Given an etree XML node that represents an <xblock-include /> element,
    parse it and return the BundleDefinitionLocator that it points to.
    """
    # An XBlock include looks like:
    # <xblock-include source="link_id" definition="block_type/definition_id" usage="alias" />
    # Where "source" and "usage" are optional.
    if include_node.tag != 'xblock-include':
        # xss-lint: disable=python-wrap-html
        raise BundleFormatException(f"Expected an <xblock-include /> XML node, but got <{include_node.tag}>")
    try:
        definition_path = include_node.attrib['definition']
    except KeyError:
        raise BundleFormatException("<xblock-include> is missing the required definition=\"...\" attribute")
    usage_hint = include_node.attrib.get("usage", None)
    link_id = include_node.attrib.get("source", None)
    # This is pointing to another definition in the same bundle. It looks like:
    # <xblock-include definition="block_type/definition_id" />
    try:
        block_type, definition_id = definition_path.split("/")
    except ValueError:
        raise BundleFormatException(f"Invalid definition attribute: {definition_path}")
    return XBlockInclude(link_id=link_id, block_type=block_type, definition_id=definition_id, usage_hint=usage_hint)
