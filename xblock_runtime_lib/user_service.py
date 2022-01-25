"""
Converting a Django user to an XBlock user

With code from:

    https://github.com/openedx/edx-platform/blob/9514cb57/common/djangoapps/xblock_django/user_service.py
"""


from django.contrib.auth.models import User
from xblock.reference.user_service import UserService, XBlockUser

from .models import anonymous_id_for_user, get_user_by_username_or_email, user_by_anonymous_id


ATTR_KEY_ANONYMOUS_USER_ID = 'edx-platform.anonymous_user_id'
ATTR_KEY_REQUEST_COUNTRY_CODE = 'edx-platform.request_country_code'
ATTR_KEY_IS_AUTHENTICATED = 'edx-platform.is_authenticated'
ATTR_KEY_USER_ID = 'edx-platform.user_id'
ATTR_KEY_USERNAME = 'edx-platform.username'
ATTR_KEY_USER_IS_STAFF = 'edx-platform.user_is_staff'
ATTR_KEY_USER_ROLE = 'edx-platform.user_role'


class DjangoXBlockUserService(UserService):
    """
    A user service that converts Django users to XBlockUser
    """
    def __init__(self, django_user, **kwargs):
        """
        Constructs a DjangoXBlockUserService object.

        Args:
            user_is_staff(bool): optional - whether the user is staff in the course
            user_role(str): optional -- user's role in the course ('staff', 'instructor', or 'student')
            anonymous_user_id(str): optional - anonymous_user_id for the user in the course
            request_country_code(str): optional -- country code determined from the user's request IP address.
        """
        super().__init__(**kwargs)
        self._django_user = django_user
        self._user_is_staff = kwargs.get('user_is_staff', False)
        self._user_role = kwargs.get('user_role', 'student')
        self._anonymous_user_id = kwargs.get('anonymous_user_id', None)
        self._request_country_code = kwargs.get('request_country_code', None)

    def get_current_user(self):
        """
        Returns the currently-logged in user, as an instance of XBlockUser
        """
        return self._convert_django_user_to_xblock_user(self._django_user)

    def get_anonymous_user_id(self, username, course_id):
        """
        Get the anonymous user id for a user.

        Args:
            username(str): username of a user.

        Returns:
            A unique anonymous_user_id for the user.
            None for Non-staff users.
        """
        if not self.get_current_user().opt_attrs.get(ATTR_KEY_USER_IS_STAFF):
            return None

        try:
            user = get_user_by_username_or_email(username_or_email=username)
        except User.DoesNotExist:
            return None

        course_id = CourseKey.from_string(course_id)
        return anonymous_id_for_user(user=user, course_id=course_id)

    def get_user_by_anonymous_id(self, uid=None):
        """
        Returns the Django User object corresponding to the given anonymous user id.

        Returns None if there is no user with the given anonymous user id.

        If no `uid` is provided, then the current anonymous user ID is used.
        """
        return user_by_anonymous_id(uid or self._anonymous_user_id)

    def _convert_django_user_to_xblock_user(self, django_user):
        """
        A function that returns an XBlockUser from the current Django request.user
        """
        xblock_user = XBlockUser(is_current_user=True)

        if django_user is not None and django_user.is_authenticated:
            # This full_name is dependent on edx-platform's profile implementation
            if hasattr(django_user, 'profile'):
                full_name = django_user.profile.name
            else:
                full_name = None
            xblock_user.full_name = full_name
            xblock_user.emails = [django_user.email]
            xblock_user.opt_attrs[ATTR_KEY_ANONYMOUS_USER_ID] = self._anonymous_user_id
            xblock_user.opt_attrs[ATTR_KEY_IS_AUTHENTICATED] = True
            xblock_user.opt_attrs[ATTR_KEY_REQUEST_COUNTRY_CODE] = self._request_country_code
            xblock_user.opt_attrs[ATTR_KEY_USER_ID] = django_user.id
            xblock_user.opt_attrs[ATTR_KEY_USERNAME] = django_user.username
            xblock_user.opt_attrs[ATTR_KEY_USER_IS_STAFF] = self._user_is_staff
            xblock_user.opt_attrs[ATTR_KEY_USER_ROLE] = self._user_role
        else:
            xblock_user.opt_attrs[ATTR_KEY_IS_AUTHENTICATED] = False
            xblock_user.opt_attrs[ATTR_KEY_REQUEST_COUNTRY_CODE] = self._request_country_code

        return xblock_user
