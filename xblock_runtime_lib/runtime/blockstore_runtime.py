"""
A runtime designed to work with Blockstore, reading and writing
XBlock field data directly from Blockstore.

With code from:

https://github.com/openedx/edx-platform/blob/ef8f841/openedx/core/djangoapps/xblock/runtime/blockstore_runtime.py
"""

import re
import logging

import crum
from django.conf import settings
from django.urls import reverse
from lxml import etree
from opaque_keys.edx.locator import BundleDefinitionLocator
from xblock.exceptions import NoSuchDefinition, NoSuchUsage
from xblock.fields import ScopeIds

from ..learning_context.manager import get_learning_context_impl
from .runtime import XBlockRuntime
from .olx_parsing import parse_xblock_include, BundleFormatException
from .serializer import serialize_xblock

log = logging.getLogger(__name__)


class UnsupportedBlockType(Exception):
    """
    Raised for block types which are not (yet) supported by XBlockRuntime.
    """


class BlockstoreXBlockRuntime(XBlockRuntime):
    """
    A runtime designed to work with Blockstore, reading and writing
    XBlock field data directly from Blockstore.
    """
    def parse_xml_file(self, fileobj, id_generator=None):
        raise NotImplementedError("Use parse_olx_file() instead")

    def get_block(self, usage_id, for_parent=None):
        """
        Create an XBlock instance in this runtime.
        Args:
            usage_key(OpaqueKey): identifier used to find the XBlock class and data.
            for_parent(OpaqueKey): optional identifier of the parent to load the XBlock under.
        """
        def_id = self.id_reader.get_definition_id(usage_id)
        if def_id is None:
            raise ValueError(f"Definition not found for usage {usage_id}")
        if not isinstance(def_id, BundleDefinitionLocator):
            raise TypeError("This runtime can only load blocks stored in Blockstore bundles.")
        try:
            block_type = self.id_reader.get_block_type(def_id)
        except NoSuchDefinition:
            raise NoSuchUsage(repr(usage_id))

        if block_type not in settings.XBLOCK_RUNTIME_SUPPORTED_BLOCK_TYPES:
            raise UnsupportedBlockType

        keys = ScopeIds(self.user_id, block_type, def_id, usage_id)
        if self.system.authored_data_store.has_cached_definition(usage_id):
            return self.construct_xblock(block_type, keys, for_parent=for_parent)
        else:
            # We need to load this block's field data from its OLX file in blockstore:
            xml_node = self.system.xml_for_usage(usage_id)
            if xml_node.get("url_name", None):
                log.warning("XBlock at %s should not specify an old-style url_name attribute.", def_id.olx_path)
            block_class = self.mixologist.mix(self.load_block_type(block_type))
            if hasattr(block_class, 'parse_xml_new_runtime'):
                # This is a (former) XModule with messy XML parsing code; let its parse_xml() method continue to work
                # as it currently does in the old runtime, but let this parse_xml_new_runtime() method parse the XML in
                # a simpler way that's free of tech debt, if defined.
                # In particular, XmlParserMixin doesn't play well with this new runtime, so this is mostly about
                # bypassing that mixin's code.
                # When a former XModule no longer needs to support the old runtime, its parse_xml_new_runtime method
                # should be removed and its parse_xml() method should be simplified to just call the super().parse_xml()
                # plus some minor additional lines of code as needed.
                block = block_class.parse_xml_new_runtime(xml_node, runtime=self, keys=keys)
            else:
                block = block_class.parse_xml(xml_node, runtime=self, keys=keys, id_generator=None)
            # Update field data with parsed values. We can't call .save() because it will call save_block(), below.
            block.force_save_fields(block._get_fields_to_save())  # pylint: disable=protected-access
            self.system.authored_data_store.cache_fields(block)
            # There is no way to set the parent via parse_xml, so do what
            # HierarchyMixin would do:
            if for_parent is not None:
                block._parent_block = for_parent  # pylint: disable=protected-access
                block._parent_block_id = for_parent.scope_ids.usage_id  # pylint: disable=protected-access
            return block

    def add_node_as_child(self, block, node, id_generator=None):
        """
        This runtime API should normally be used via
        runtime.get_block() -> block.parse_xml() -> runtime.add_node_as_child
        """
        try:
            parsed_include = parse_xblock_include(node)
        except BundleFormatException:
            # We need to log the XBlock ID or this will be hard to debug
            log.error("BundleFormatException when parsing XBlock %s", block.scope_ids.usage_id)
            raise  # Also log details and stack trace
        self.add_child_include(block, parsed_include)

    def add_child_include(self, block, parsed_include):
        """
        Given an XBlockInclude tuple that represents a new <xblock-include />
        node, add it as a child of the specified XBlock. This is the only
        supported API for adding a new child to an XBlock - one cannot just
        modify block.children to append a usage ID, since that doesn't provide
        enough information to serialize the block's <xblock-include /> elements.
        """
        self.system.children_data_store.append_include(block, parsed_include)
        block.children = self.system.children_data_store.get(block, 'children')

    def child_includes_of(self, block):
        """
        Get the list of <xblock-include /> directives that define the children
        of this block's definition.
        """
        return self.system.children_data_store.get_includes(block)

    def save_block(self, block):
        """
        Save any pending field data values to Blockstore.
        This gets called by block.save() - do not call this directly.
        """
        if not self.system.authored_data_store.has_changes(block):
            return  # No changes, so no action needed.
        definition_key = block.scope_ids.def_id
        if definition_key.draft_name is None:
            raise RuntimeError(
                "The Blockstore runtime does not support saving changes to blockstore without a draft. "
                "Are you making changes to UserScope.NONE fields from the LMS rather than Studio?"
            )
        # Verify that the user has permission to write to authored data in this
        # learning context:
        usage_id = block.scope_ids.usage_id
        if self.user is not None:
            learning_context = get_learning_context_impl(usage_id)
            if not learning_context.can_edit_block(self.user, usage_id):
                log.warning("User %s does not have permission to edit %s", self.user.username, usage_id)
                raise RuntimeError("You do not have permission to edit this XBlock")
        olx_str, static_files = serialize_xblock(block)
        # Write the OLX file to the block
        self.system.set_library_block_olx(usage_id, olx_str)
        # And the other files, if any:
        for fh in static_files:
            self.system.set_library_block_asset_file(usage_id, fh.name, fh.data)

    def _lookup_asset_url(self, block, asset_path):
        """
        Return an absolute URL for the specified static asset file that may
        belong to this XBlock.
        e.g. if the XBlock settings have a field value like "/static/foo.png"
        then this method will be called with asset_path="foo.png" and should
        return a URL like https://cdn.none/xblock/f843u89789/static/foo.png
        If the asset file is not recognized, return None
        """
        if '..' in asset_path:
            return None  # Illegal path
        usage_id = block.scope_ids.usage_id
        # Compute the full path to the static file in the bundle,
        # e.g. "problem/prob1/static/illustration.svg"
        asset_path = reverse('api:v1:xblocks-static-asset', kwargs={'pk': usage_id, 'filename': asset_path})
        current_request = crum.get_current_request()
        asset_url = current_request.build_absolute_uri(asset_path)

        # Make sure the URL is one that will work from the user's browser,
        # not one that only works from within a docker container:
        url = force_browser_url(asset_url)
        return url


REGEX_BROWSER_URL = re.compile(r'http://edx.devstack.(studio|lms):')


def force_browser_url(url):
    """
    Ensure that the given devstack URL is a URL accessible from the end user's browser.
    """
    # Hack: on some devstacks, we must necessarily use different URLs for
    # accessing Blockstore file data from within and outside of docker
    # containers, but Blockstore has no way of knowing which case any particular
    # request is for. So it always returns a URL suitable for use from within
    # the container. Only this edxapp can transform the URL at the last second,
    # knowing that in this case it's going to the user's browser and not being
    # read by edxapp.
    # In production, the same S3 URLs get used for internal and external access
    # so this hack is not necessary.
    return re.sub(REGEX_BROWSER_URL, 'http://localhost:', url)
