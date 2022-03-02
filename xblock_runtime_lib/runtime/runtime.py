"""
Runtime for the LabXchange XBlocks.

With code from:

    https://github.com/openedx/edx-platform/blob/2173a98/openedx/core/djangoapps/xblock/runtime/runtime.py
"""
import logging
from urllib.parse import parse_qs, urljoin

from django.core.exceptions import PermissionDenied
from functools import lru_cache
from web_fragments.fragment import Fragment
from xblock.core import XBlock
from xblock.field_data import SplitFieldData
from xblock.fields import Scope
from xblock.runtime import MemoryIdManager, Runtime

from .blockstore_field_data import BlockstoreFieldData, BlockstoreChildrenData
from .id_managers import OpaqueKeyReader
from .static_replace import process_static_urls
from .xblock_utils import wrap_fragment, xblock_local_resource_url
from ..apps import get_blocks_app_config
from ..error_tracker import make_error_tracker
from ..utils import get_xblock_id_for_anonymous_user


log = logging.getLogger(__name__)

LX_BLOCK_TYPES_OVERRIDE = {
    'problem': 'lx_question',
    'video': 'lx_video',
    'html': 'lx_html',
}


class XBlockRuntime(Runtime):
    """
    Runtime for the LabXchange XBlocks.

    This class manages one or more instantiated XBlocks for a particular user,
    providing those XBlocks with the standard XBlock runtime API (and some
    Open edX-specific additions) so that it can interact with the platform,
    and the platform can interact with it.
    The main reason we cannot make the runtime a long-lived singleton is that
    the XBlock runtime API requires 'user_id' to be a property of the runtime,
    not an argument passed in when loading particular blocks.
    """

    # Feature flags:

    # This runtime can save state for users who aren't logged in:
    supports_state_for_anonymous_users = True

    def __init__(self, system, user):
        super().__init__(
            id_reader=system.id_reader,
            mixins=(),
            default_class=None,
            select=None,
            id_generator=system.id_generator,
        )
        self.system = system
        self.user = user
        # self.user_id must be set as a separate attribute since base class sets it:
        if self.user is None:
            self.user_id = None
        elif self.user.is_anonymous:
            self.user_id = get_xblock_id_for_anonymous_user(user)
        else:
            self.user_id = self.user.id
        self.block_field_datas = {}  # dict of FieldData stores for our loaded XBlocks. Key is the block's scope_ids.
        self.django_field_data_caches = {}  # dict of FieldDataCache objects for XBlock with database-based user state

    def handler_url(self, block, handler_name, suffix='', query='', thirdparty=False):
        """
        Returns the fully-qualified URL to invoke the given handler for the given block.
        """
        if thirdparty:
            log.warning("thirdparty handlers are not supported by this runtime for XBlock %s.", type(block))

        return self.system.handler_url(
            usage_key=block.scope_ids.usage_id,
            handler_name=handler_name,
            user=self.user,
            suffix=suffix,
            extra_params=parse_qs(query),
        )

    def resource_url(self, resource):
        raise NotImplementedError("resource_url is not supported by Open edX.")

    def local_resource_url(self, block, uri):
        """
        Get the absolute URL to a resource file (like a CSS/JS file or an image)
        that is part of an XBlock's python module.
        """
        relative_url = xblock_local_resource_url(block, uri)
        site_root_url = get_blocks_app_config().get_site_root_url()
        absolute_url = urljoin(site_root_url, relative_url)
        return absolute_url

    def publish(self, block, event_type, event_data):
        """ Handle XBlock events like grades and completion """
        # TODO: original code handled grades and completion events,
        # and logged all the others to the tracking log.
        # LX intercepts these events for its own purposes, but maybe it's better
        # to handle that here, and avoid munging the content links?

    def load_block_type(self, block_type):
        """
        Returns a subclass of :class:`.XBlock` that corresponds to the specified `block_type`.

        LabXchange has a number of custom block types which override the defaults; these are used here.
        """
        block_type = LX_BLOCK_TYPES_OVERRIDE.get(block_type, block_type)
        return XBlock.load_class(block_type, self.default_class, self.select)

    def service(self, block, service_name):
        """
        Return a service, or None.
        Services are objects implementing arbitrary other interfaces.
        """
        # Most common service is field-data so check that first:
        if service_name == "field-data":
            if block.scope_ids not in self.block_field_datas:
                try:
                    self.block_field_datas[block.scope_ids] = self._init_field_data_for_block(block)
                except Exception as e:
                    # Don't try again pointlessly every time another field is accessed
                    self.block_field_datas[block.scope_ids] = None
                    raise e
            return self.block_field_datas[block.scope_ids]

        # Check if the XBlockRuntimeSystem wants to handle this:
        service = self.system.get_service(block, service_name)

        # Fall back to the base implementation which loads services defined in the constructor:
        if service is None:
            service = super().service(block, service_name)
        return service

    def _init_field_data_for_block(self, block):  # pylint: disable=unused-argument
        """
        Initialize the FieldData implementation for the specified XBlock
        """
        # TODO: Implement for read/write XBlocks
        student_data_store = None
        return SplitFieldData({
            Scope.content: self.system.authored_data_store,
            Scope.settings: self.system.authored_data_store,
            Scope.parent: self.system.authored_data_store,
            Scope.children: self.system.children_data_store,
            Scope.user_state_summary: student_data_store,
            Scope.user_state: student_data_store,
            Scope.user_info: student_data_store,
            Scope.preferences: student_data_store,
        })

    def render(self, block, view_name, context=None):
        """
        Render a specific view of an XBlock.
        """
        # Users who aren't logged in are not allowed to view any views other
        # than public_view. They may call any handlers though.
        if (self.user is None or self.user.is_anonymous) and view_name != 'public_view':
            raise PermissionDenied
        # We also need to override this method because some XBlocks in the
        # edx-platform codebase use methods like add_webpack_to_fragment()
        # which create relative URLs (/static/studio/bundles/webpack-foo.js).
        # We want all resource URLs to be absolute, such as is done when
        # local_resource_url() is used.
        fragment = super().render(block, view_name, context)
        needs_fix = False
        for resource in fragment.resources:
            log.error("XBlockRuntime.render fragment resource: %s", resource.data)
            if resource.kind == 'url' and resource.data.startswith('/'):
                needs_fix = True
                break
        if needs_fix:
            log.warning("XBlock %s returned relative resource URLs, which are deprecated", block.scope_ids.usage_id)
            # The Fragment API is mostly immutable, so changing a resource requires this:
            frag_data = fragment.to_dict()
            for resource in frag_data['resources']:
                if resource['kind'] == 'url' and resource['data'].startswith('/'):
                    log.debug("-> Relative resource URL: %s", resource['data'])
                    resource['data'] = get_blocks_app_config().get_site_root_url() + resource['data']
            fragment = Fragment.from_dict(frag_data)

        # Apply any required transforms to the fragment.
        # We could move to doing this in wrap_xblock() and/or use an array of
        # wrapper methods like the ConfigurableFragmentWrapper mixin does.
        fragment = wrap_fragment(fragment, self.transform_static_paths_to_urls(block, fragment.content))

        return fragment

    def transform_static_paths_to_urls(self, block, html_str):
        """
        Given an HTML string, replace any static file paths like
            /static/foo.png
        (which are really pointing to block-specific assets stored in blockstore)
        with working absolute URLs like
            https://s3.example.com/blockstore/bundle17/this-block/assets/324.png
        See common/djangoapps/static_replace/__init__.py
        This is generally done automatically for the HTML rendered by XBlocks,
        but if an XBlock wants to have correct URLs in data returned by its
        handlers, the XBlock must call this API directly.
        Note that the paths are only replaced if they are in "quotes" such as if
        they are an HTML attribute or JSON data value. Thus, to transform only a
        single path string on its own, you must pass html_str=f'"{path}"'
        """

        def replace_static_url(original, prefix, quote, rest):  # pylint: disable=unused-argument
            """
            Replace a single matched url.
            """
            original_url = prefix + rest
            # Don't mess with things that end in '?raw'
            if rest.endswith('?raw'):
                new_url = original_url
            else:
                new_url = self._lookup_asset_url(block, rest) or original_url
            return "".join([quote, new_url, quote])

        return process_static_urls(html_str, replace_static_url)

    def _lookup_asset_url(self, block, asset_path):  # pylint: disable=unused-argument
        """
        Return an absolute URL for the specified static asset file that may
        belong to this XBlock.
        e.g. if the XBlock settings have a field value like "/static/foo.png"
        then this method will be called with asset_path="foo.png" and should
        return a URL like https://cdn.none/xblock/f843u89789/static/foo.png
        If the asset file is not recognized, return None
        """
        # Subclasses should override this
        return None


class XBlockRuntimeSystem:
    """
    This class is essentially a factory for XBlockRuntimes. This is a
    long-lived object which provides the behavior specific to the application
    that wants to use XBlocks. Unlike XBlockRuntime, a single instance of this
    class can be used with many different XBlocks, whereas each XBlock gets its
    own instance of XBlockRuntime.
    """
    STUDENT_DATA_EPHEMERAL = 'ephemeral'
    STUDENT_DATA_PERSISTED = 'persisted'

    def __init__(
        self,
        handler_url,  # type: Callable[[UsageKey, str, Union[int, ANONYMOUS_USER]], str]
        get_olx_hash_for_usage_id,  # type: Callable[UsageKey]
        set_library_block_olx,   # type: Callable[UsageKey, str]
        set_library_block_asset_file,  # type: Callable[UsageKey, str, str]
        xml_for_usage,  # type: Callable[UsageKey]
        student_data_mode,  # type: Union[STUDENT_DATA_EPHEMERAL, STUDENT_DATA_PERSISTED]
        runtime_class,  # type: XBlockRuntime
        services=None,  # type: Dict[str, xblock.reference.plugins.Service]
    ):
        """
        args:
            handler_url: A method to get URLs to call XBlock handlers. It must
                implement this signature:
                handler_url(
                    usage_key: UsageKey,
                    handler_name: str,
                    user: User,
                )
            student_data_mode: Specifies whether student data should be kept
                in a temporary in-memory store (e.g. Studio) or persisted
                forever in the database.
            runtime_class: What runtime to use, e.g. BlockstoreXBlockRuntime
        """
        # Callback functions
        self.handler_url = handler_url
        self.get_olx_hash_for_usage_id = get_olx_hash_for_usage_id
        self.set_library_block_olx = set_library_block_olx
        self.set_library_block_asset_file = set_library_block_asset_file
        self.xml_for_usage = xml_for_usage

        self.id_reader = OpaqueKeyReader()
        self.id_generator = MemoryIdManager()  # We don't really use id_generator until we need to support asides
        self.runtime_class = runtime_class
        self.authored_data_store = BlockstoreFieldData(get_olx_hash_for_usage_id=self.get_olx_hash_for_usage_id)
        self.children_data_store = BlockstoreChildrenData(self.authored_data_store)
        assert student_data_mode in (self.STUDENT_DATA_EPHEMERAL, self.STUDENT_DATA_PERSISTED)
        self.student_data_mode = student_data_mode
        self._error_trackers = {}
        self._services = services or {}

    def get_runtime(self, user):
        """
        Get the XBlock runtime for the specified Django user. The user can be
        a regular user, an AnonymousUser, or None.
        """
        return self.runtime_class(self, user)

    def get_service(self, block, service_name):
        """
        Get a runtime service

        Runtime services may come from this XBlockRuntimeSystem,
        or if this method returns None, they may come from the
        XBlockRuntime.
        """
        if service_name == 'error_tracker':
            return self.get_error_tracker_for_context(block.scope_ids.usage_id.context_key)
        return self._services.get(service_name, None)  # None means see if XBlockRuntime offers this service

    @lru_cache(maxsize=32)
    def get_error_tracker_for_context(self, context_key):  # pylint: disable=unused-argument
        """
        Get an error tracker for the specified context.
        lru_cache makes this error tracker long-lived, for
        up to 32 contexts that have most recently been used.
        """
        return make_error_tracker()
