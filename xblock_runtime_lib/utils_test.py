"""
Tests for the LMS utils module
"""
from unittest import TestCase
from unittest.mock import patch

import ddt
from django.conf import settings

from .utils import (
    rewrite_blockstore_runtime_handler_urls,
)


def mock_get_secure_xblock_handler_url(request, block_key_str, handler_name):
    """
    A mock for get_secure_xblock_handler_url()
    Note that this must return a trailing slash, like the real version does.
    """
    return f'https://absolute/url/to/secure/handler/{block_key_str}/{handler_name}'


@ddt.ddt
class TestBlockstoreRuntimeUrlsRewrite(TestCase):
    """
    Tests for rewrite_blockstore_runtime_handler_urls
    """
    # pylint: disable=line-too-long
    maxDiff = None

    def setUp(self):
        super().setUp()
        patcher = patch('labxchange.apps.blocks.utils.get_secure_xblock_handler_url')
        handler_url_mock = patcher.start()
        self.addCleanup(patcher.stop)
        handler_url_mock.side_effect = mock_get_secure_xblock_handler_url

    @ddt.data(
        (
            f'<a href="{settings.LMS_ROOT_PUBLIC}/api/xblock/v2/xblocks/lb:LabXchange:e9531f21:drag-and-drop-v2:6-3/handler/8-7b9057ed0a4f3383496f/publish_event">FB</a>',
            '<a href="https://absolute/url/to/secure/handler/lb:LabXchange:e9531f21:drag-and-drop-v2:6-3/publish_event">FB</a>',
        ),
        (
            # A handler with a suffix, like video's /handler/translation/en handler URLs
            f'&#34;url&#34;: &#34;{settings.LMS_ROOT_PUBLIC}/api/xblock/v2/xblocks/lb:LabXchange:05081956:video:1/handler/20083373-8d9321bf57a0886a2a02/xmodule_handler/save_user_state?q&#34;',
            '&#34;url&#34;: &#34;https://absolute/url/to/secure/handler/lb:LabXchange:05081956:video:1/xmodule_handler/save_user_state?q&#34;',
        ),
    )
    @ddt.unpack
    def test_rewrite_blockstore_runtime_handler_url(self, url_html, expected):
        self.assertEqual(rewrite_blockstore_runtime_handler_urls(url_html, request=None), expected)

    def test_rewrite_blockstore_runtime_handler_urls(self):
        """ All-in-one test of rewrite_blockstore_runtime_handler_urls() """
        # Semi-realistic data from the actual xblock_view JSON for a video and a drag and drop XBlock.
        # Lots of handler URLs in here that need to be rewritten.
        in_data = """
        <div class=\"xblock xblock-student_view xblock-student_view-video\" data-graded=\"False\"
            data-has-score=\"False\" data-runtime-class=\"LmsRuntime\" data-init=\"VideoXBlock\"
            data-course-id=\"course-v1:edX+DemoX+Demo_Course\" data-request-token=\"0498670e2fdd11e9b1d30242ac12000f\"
            data-runtime-version=\"1\" data-usage-id=\"block-v1:edX+DemoX+Demo_Course+type@video+block@0b9e39477\"
            data-block-type=\"video\"
        >
            <div data-metadata='{&#34;savedVideoPosition&#34;: 0.0,
                &#34;publishCompletionUrl&#34;: &#34;http://localhost:18000/api/xblock/v2/xblocks/lb:LabXchange:05081956:video:1/handler/20083373-8d9321bf57a0886a2a02/publish_completion/&#34;,
                &#34;ytApiUrl&#34;: &#34;https://www.youtube.com/iframe_api&#34;,
                &#34;http://localhost:18000/api/xblock/v2/xblocks/lb:LabXchange:05081956:video:1/handler/20083373-8d9321bf57a0886a2a02/xmodule_handler/save_user_state&#34;,
                &#34;transcriptTranslationUrl&#34;: &#34;http://localhost:18000/api/xblock/v2/xblocks/lb:LabXchange:05081956:video:1/handler/20083373-8d9321bf57a0886a2a02/xmodule_handler/translation/__lang__&#34;,
                }' data-bumper-metadata='null'
            >
                <a class=\"btn btn-link\" href=\"http://localhost:18000/api/xblock/v2/xblocks/lb:LabXchange:05081956:video:1/handler/20083373-8d9321bf57a0886a2a02/handler/download\" data-value=\"srt\">Download SubRip (.srt) file</a>
            </div>
        </div>
        <div id="problem_lb:LabXchange:5072a8f0:problem:1-2" class=\"problems-wrapper\" role=\"group\"
            aria-labelledby=\"lb:LabXchange:5072a8f0:problem:1-2-problem-title\"
            data-problem-id=\"lb:LabXchange:5072a8f0:problem:1-2\"
            data-url=\"http://localhost:18000/api/xblock/v2/xblocks/lb:LabXchange:775deddf:problem:cbe42ddf238848c0beb440b7e4afb542/handler/20083373-84f92a53a9d71a25cdfa/xmodule_handler\"
            data-problem-score=\"0\" data-problem-total-possible=\"1\" data-attempts-used=\"10\" data-graded=\"False\"">
        </div>
        """.replace('http://localhost:18000', settings.LMS_ROOT_PUBLIC)

        out_data = """
        <div class=\"xblock xblock-student_view xblock-student_view-video\" data-graded=\"False\"
            data-has-score=\"False\" data-runtime-class=\"LmsRuntime\" data-init=\"VideoXBlock\"
            data-course-id=\"course-v1:edX+DemoX+Demo_Course\" data-request-token=\"0498670e2fdd11e9b1d30242ac12000f\"
            data-runtime-version=\"1\" data-usage-id=\"block-v1:edX+DemoX+Demo_Course+type@video+block@0b9e39477\"
            data-block-type=\"video\"
        >
            <div data-metadata='{&#34;savedVideoPosition&#34;: 0.0,
                &#34;publishCompletionUrl&#34;: &#34;https://absolute/url/to/secure/handler/lb:LabXchange:05081956:video:1/publish_completion/&#34;,
                &#34;ytApiUrl&#34;: &#34;https://www.youtube.com/iframe_api&#34;,
                &#34;https://absolute/url/to/secure/handler/lb:LabXchange:05081956:video:1/xmodule_handler/save_user_state&#34;,
                &#34;transcriptTranslationUrl&#34;: &#34;https://absolute/url/to/secure/handler/lb:LabXchange:05081956:video:1/xmodule_handler/translation/__lang__&#34;,
                }' data-bumper-metadata='null'
            >
                <a class="btn btn-link" href="https://absolute/url/to/secure/handler/lb:LabXchange:05081956:video:1/handler/download" data-value="srt">Download SubRip (.srt) file</a>
            </div>
        </div>
        <div id="problem_lb:LabXchange:5072a8f0:problem:1-2" class=\"problems-wrapper\" role=\"group\"
            aria-labelledby=\"lb:LabXchange:5072a8f0:problem:1-2-problem-title\"
            data-problem-id=\"lb:LabXchange:5072a8f0:problem:1-2\"
            data-url="https://absolute/url/to/secure/handler/lb:LabXchange:775deddf:problem:cbe42ddf238848c0beb440b7e4afb542/xmodule_handler"
            data-problem-score=\"0\" data-problem-total-possible=\"1\" data-attempts-used=\"10\" data-graded=\"False\"">
        </div>
        """.replace('https://lms.domain', settings.LMS_ROOT_PUBLIC)
        self.assertEqual(rewrite_blockstore_runtime_handler_urls(in_data, request=None), out_data)
