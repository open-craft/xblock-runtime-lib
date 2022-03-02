"""
Functions that can are used to modify XBlock fragments for use in the LMS and Studio

With code from:

    https://github.com/openedx/edx-platform/blob/dba7d78/openedx/core/lib/xblock_utils/__init__.py
"""
from django.contrib.staticfiles.storage import staticfiles_storage
from web_fragments.fragment import Fragment


def wrap_fragment(fragment, new_content):
    """
    Returns a new Fragment that has `new_content` and all
    as its content, and all of the resources from fragment
    """
    wrapper_frag = Fragment(content=new_content)
    wrapper_frag.add_fragment_resources(fragment)
    return wrapper_frag


def xblock_local_resource_url(block, uri):
    """
    Returns the URL for an XBlock's local resource.
    Note: the file is accessed as a static asset which may use a CDN in production.
    """
    xblock_class = getattr(block.__class__, 'unmixed_class', block.__class__)
    return staticfiles_storage.url('xblock/resources/{package_name}/{path}'.format(
        package_name=xblock_resource_pkg(xblock_class),
        path=uri
    ))


def xblock_resource_pkg(block):
    """
    Return the module name needed to find an XBlock's shared static assets.
    This method will return the full module name that is one level higher than
    the one the block is in. For instance, problem_builder.answer.AnswerBlock
    has a __module__ value of 'problem_builder.answer'. This method will return
    'problem_builder' instead. However, for edx-ora2's
    openassessment.xblock.openassessmentblock.OpenAssessmentBlock, the value
    returned is 'openassessment.xblock'.

    LX note: removed code related to XModules.
    """
    module_name = block.__module__
    return module_name.rsplit('.', 1)[0]
